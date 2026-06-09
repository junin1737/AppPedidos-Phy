"""RPA — extrai pedidos do painel Tiao Cards / LigaMagic e monta PedidoExtraido."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import limites_campos as lim
from parser_pedido import (
    ItemPedido,
    PedidoExtraido,
    REF_COM_HASH,
    REF_PADRAO,
    _extrair_raridade_bloco,
    _idioma_da_linha,
    _idioma_do_html,
    _idioma_sigla_bloco,
    _idioma_sigla_html,
    _inferir_quantidade_preco,
    _parse_moeda,
    _preservar_marcadores_html,
    _raridade_do_html,
    digitos_documento,
    formatar_documento,
    linha_e_apelido_nick,
    montar_referencia_clipp,
    normalizar_referencia_site,
)

from db import _separar_tipo_logradouro, normalizar_endereco_cliente

STATUS_IMPORTAR = "Pagamento efetuado - Aguardando envio"
REF_HASH = REF_COM_HASH

ROOT = Path(__file__).resolve().parent
PERFIL_RPA = ROOT / ".rpa_profile"
SESSAO_RPA = PERFIL_RPA / "session.json"


def _arquivo_controle_pedidos() -> Path:
    from config import pedidos_controle_path

    return pedidos_controle_path()


def url_pedidos(base_url: str, cod: int | str | None = None) -> str:
    params: dict[str, str] = {"view": "ecom/admin/pedidos"}
    if cod is not None:
        params["cod"] = str(cod)
    sep = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('/')}{sep}{urlencode(params)}"


def _carregar_controle() -> dict:
    arquivo = _arquivo_controle_pedidos()
    if not arquivo.is_file():
        return {"importados": {}}
    try:
        with open(arquivo, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"importados": {}}


def _salvar_controle(data: dict) -> None:
    arquivo = _arquivo_controle_pedidos()
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def obter_registro_controle(numero: str) -> dict | None:
    return _carregar_controle().get("importados", {}).get(str(numero))


def pedido_ja_registrado(numero: str) -> bool:
    return str(numero) in _carregar_controle().get("importados", {})


def registrar_pedido_importado(numero: str, id_venda: int) -> None:
    data = _carregar_controle()
    data.setdefault("importados", {})[str(numero)] = {
        "id_nfvenda": int(id_venda),
        "importado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _salvar_controle(data)


def remover_pedido_controle(numero: str) -> bool:
    """Remove pedido do controle local (ex.: venda cancelada no CLIPP)."""
    data = _carregar_controle()
    importados = data.get("importados", {})
    if str(numero) not in importados:
        return False
    del importados[str(numero)]
    _salvar_controle(data)
    return True


def limpar_todo_controle_local() -> int:
    """Remove todos os pedidos do arquivo pedidos_rpa.json. Retorna quantidade removida."""
    data = _carregar_controle()
    n = len(data.get("importados", {}))
    data["importados"] = {}
    _salvar_controle(data)
    return n


def _limpar_nome_cliente(texto: str) -> str:
    nome = re.sub(r"\s*\([^)]+\)\s*$", "", (texto or "").strip())
    return nome.strip()


_LINHA_NOME_IGNORAR = re.compile(
    r"^avaliar|^padr[aã]o\s+cliente$|^endere[cç]o\s+do\s+cliente$|^cliente$|"
    r"^forma\s+de|^confirma|^itens\s+do|^pagamento$|^envio$|^imprimir",
    re.I,
)


def _linha_apelido_site(lin: str) -> bool:
    return bool(re.match(r"^\([^)]+\)\s*$", (lin or "").strip()))


def _parse_linha_endereco_compacto(lin: str) -> dict | None:
    """«Rua X, 714 - complemento» em uma linha."""
    s = (lin or "").strip()
    if not s or "," not in s:
        return None
    m = re.match(r"^(.+?),\s*(\d+[A-Za-z]?)\s*(?:-\s*(.+))?\s*$", s)
    if not m:
        return None
    rua = m.group(1).strip()
    if not rua or linha_e_apelido_nick(rua):
        return None
    tipo, lograd = _separar_tipo_logradouro(rua)
    out: dict = {"end_numero": m.group(2)[: lim.END_NUMERO]}
    if tipo:
        out["end_tipo"] = tipo
    out["end_lograd"] = (lograd or rua)[: lim.END_LOGRAD]
    if m.group(3):
        out["texto_complemento_site"] = m.group(3).strip()[:200]
    parte_rua = f"{tipo} {out['end_lograd']}".strip() if tipo else out["end_lograd"]
    linha_site = f"{parte_rua}, Número {out['end_numero']}"
    if out.get("texto_complemento_site"):
        linha_site = f"{linha_site} - {out['texto_complemento_site']}"
    out["endereco_completo_site"] = linha_site.strip()
    return out


def _escolher_telefone_celular(telefones: list[str]) -> str:
    nums = [re.sub(r"\D", "", t) for t in telefones if len(re.sub(r"\D", "", t)) >= 10]
    for d in nums:
        if len(d) >= 11 and d[2] == "9":
            return d
    for d in nums:
        if len(d) >= 11:
            return d
    return nums[-1] if nums else ""


def _apelido_do_bloco(linhas_cli: list[str]) -> str | None:
    """Apelido entre parênteses na linha do nome ou nick em linha isolada."""
    for lin in linhas_cli[:8]:
        s = (lin or "").strip()
        m = re.search(r"\(([^)]+)\)\s*$", s)
        if m:
            return m.group(1).strip()
        if _linha_apelido_site(s):
            return s.strip("() ").strip()
        if linha_e_apelido_nick(s):
            return s
    return None


def _linha_lixo_endereco(lin: str, apelido: str | None = None) -> bool:
    s = (lin or "").strip()
    if not s:
        return True
    if re.match(r"^[\(\)]+$", s):
        return True
    if linha_e_apelido_nick(s, apelido):
        return True
    # Apelido partido em duas linhas no HTML: «(» e «Raiden1994)»
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{1,39}\)\s*$", s):
        return True
    if re.match(r"^\([A-Za-z0-9][A-Za-z0-9_\-\.]{1,39}\s*$", s):
        return True
    return False


def _parse_bloco_endereco_cliente(linhas_cli: list[str]) -> dict:
    """
    Layout Tiao Cards (uma informação por linha):
    Nome (apelido) | Logradouro | Número | Complemento | Bairro | Cidade-UF | CEP | Tel | CPF
    """
    cli: dict = {}
    restantes: list[str] = []
    telefones: list[str] = []
    apelido_bloco = _apelido_do_bloco(linhas_cli)

    for linha in linhas_cli:
        lin = linha.strip()
        if not lin:
            continue

        cep_m = re.search(r"CEP\s*(\d{5})-?(\d{3})", lin, re.I)
        if not cep_m:
            cep_m = re.match(r"^(\d{5})-?(\d{3})\s*$", lin)
        if cep_m:
            cli["end_cep"] = f"{cep_m.group(1)}-{cep_m.group(2)}"
            continue

        if re.search(r"\bCPF\b", lin, re.I) or re.search(r"\bCNPJ\b", lin, re.I):
            digitos = re.sub(r"\D", "", lin)
            if len(digitos) >= 11:
                cli["documento"] = formatar_documento(
                    digitos[:14] if len(digitos) > 11 else digitos[:11]
                )
            continue

        digitos_tel = re.sub(r"\D", "", lin)
        if (
            re.search(r"\(\d{2}\)", lin)
            or (len(digitos_tel) >= 10 and not re.search(r"\bCEP\b", lin, re.I))
        ) and len(digitos_tel) >= 10:
            telefones.append(digitos_tel)
            continue

        num_m = re.match(r"N[uú]mero\s+(\S+)", lin, re.I)
        if num_m:
            cli["end_numero"] = num_m.group(1)[: lim.END_NUMERO]
            continue

        cid_m = re.match(r"^(.+?)\s*-\s*([A-Z]{2})\s*$", lin)
        if cid_m and len(cid_m.group(2)) == 2:
            cli["cidade"] = cid_m.group(1).strip()[:40]
            cli["uf"] = cid_m.group(2).upper()
            continue

        if _LINHA_NOME_IGNORAR.search(lin) or _linha_lixo_endereco(lin, apelido_bloco):
            continue

        restantes.append(lin)

    if telefones:
        tel = _escolher_telefone_celular(telefones)
        if tel:
            cli["telefone"] = tel
            cli["ddd_celul"] = tel[:2]
            cli["fone_celul"] = tel[2:][: lim.FONE_CELUL]

    if not restantes:
        return cli

    nome_lin = restantes[0]
    m_apel = re.search(r"\(([^)]+)\)\s*$", nome_lin)
    if m_apel:
        cli["apelido_site"] = m_apel.group(1).strip()[:40]
    elif apelido_bloco:
        cli["apelido_site"] = apelido_bloco[:40]
    cli["nome"] = _limpar_nome_cliente(nome_lin)

    idx = 1
    apelido = cli.get("apelido_site") or apelido_bloco
    while idx < len(restantes) and _linha_lixo_endereco(restantes[idx], apelido):
        lin_skip = restantes[idx].strip()
        if not cli.get("apelido_site") and linha_e_apelido_nick(lin_skip):
            cli["apelido_site"] = lin_skip[:40]
        idx += 1

    if idx < len(restantes):
        lin_log = restantes[idx]
        if not _linha_lixo_endereco(lin_log, apelido):
            parsed = _parse_linha_endereco_compacto(lin_log)
            if parsed:
                for chave in (
                    "end_tipo",
                    "end_lograd",
                    "end_numero",
                    "texto_complemento_site",
                    "endereco_completo_site",
                ):
                    if parsed.get(chave):
                        cli[chave] = parsed[chave]
            else:
                tipo, lograd = _separar_tipo_logradouro(lin_log)
                if tipo:
                    cli["end_tipo"] = tipo
                lograd_ok = (lograd or lin_log).strip()
                if lograd_ok and not linha_e_apelido_nick(lograd_ok, apelido):
                    cli["end_lograd"] = lograd_ok[: lim.END_LOGRAD]
            idx += 1

    if idx < len(restantes) and re.match(r"N[uú]mero\s+", restantes[idx], re.I):
        if not cli.get("end_numero"):
            num_m = re.match(r"N[uú]mero\s+(\S+)", restantes[idx], re.I)
            if num_m:
                num = num_m.group(1).strip("()")
                if num:
                    cli["end_numero"] = num[: lim.END_NUMERO]
        idx += 1

    if idx < len(restantes):
        lin_pos = restantes[idx].strip()
        if lin_pos and not _linha_lixo_endereco(lin_pos, apelido):
            if not cli.get("texto_complemento_site"):
                cli["texto_complemento_site"] = lin_pos
            elif not cli.get("end_bairro"):
                cli["end_bairro"] = lin_pos[: lim.END_BAIRRO]
        idx += 1

    if idx < len(restantes):
        lin_b = restantes[idx].strip()
        if (
            lin_b
            and not _linha_lixo_endereco(lin_b, apelido)
            and not cli.get("end_bairro")
        ):
            cli["end_bairro"] = lin_b[: lim.END_BAIRRO]
        idx += 1

    seg_site: list[str] = []
    if cli.get("end_tipo") or cli.get("end_lograd"):
        seg_site.append(
            f"{(cli.get('end_tipo') or '').strip()} {(cli.get('end_lograd') or '').strip()}".strip()
        )
    if cli.get("end_numero"):
        seg_site.append(f"Número {cli['end_numero']}")
    if cli.get("texto_complemento_site"):
        seg_site.append(cli["texto_complemento_site"])
    if len(seg_site) >= 2:
        cli["endereco_completo_site"] = (
            f"{seg_site[0]}, {seg_site[1]}"
            + (f" - {seg_site[2]}" if len(seg_site) >= 3 else "")
        )
    elif seg_site:
        cli["endereco_completo_site"] = seg_site[0]

    return cli


def _extrair_resumo_valores_pedido(texto: str, pedido: PedidoExtraido) -> None:
    """Frete, desconto e totais no rodapé do pedido."""
    m_itens = re.search(
        r"Valor\s+dos\s+Itens[^\d]*R\$\s*([\d.,]+)", texto, re.I
    )
    if m_itens:
        pedido.resumo["valor_itens"] = _parse_moeda(m_itens.group(1))

    m_frete = re.search(r"Frete[^\d]*R\$\s*([\d.,]+)", texto, re.I)
    if m_frete:
        pedido.resumo["valor_frete"] = _parse_moeda(m_frete.group(1))

    m_desc = re.search(
        r"(?:Desconto|Cupom|Voucher)[^\d]*R\$\s*([\d.,]+)", texto, re.I
    )
    if m_desc:
        pedido.resumo["valor_desconto"] = _parse_moeda(m_desc.group(1))

    m_total = re.search(r"Valor\s+Total[^\d]*R\$\s*([\d.,]+)", texto, re.I)
    if m_total:
        pedido.resumo["valor_total"] = _parse_moeda(m_total.group(1))

    produtos = float(pedido.resumo.get("valor_itens") or 0)
    frete = float(pedido.resumo.get("valor_frete") or 0)
    desconto = float(pedido.resumo.get("valor_desconto") or 0)
    total = float(pedido.resumo.get("valor_total") or 0)
    if "valor_frete" not in pedido.resumo and total and produtos:
        pedido.resumo["valor_frete"] = round(max(total - produtos + desconto, 0), 2)
    elif produtos and not total:
        pedido.resumo["valor_total"] = round(produtos + frete - desconto, 2)


def _extrair_nome_cliente_site(linhas_cli: list[str], texto: str) -> str:
    """Evita pegar rótulos do painel (ex.: «Avaliar Cliente») como nome."""
    for linha in linhas_cli:
        if _LINHA_NOME_IGNORAR.search(linha):
            continue
        if re.search(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\(\d{2}\)|\d{5}-?\d{3}", linha):
            continue
        nome = _limpar_nome_cliente(linha)
        if len(nome) >= 3 and not nome.isdigit():
            return nome

    m = re.search(
        r"Endere[cç]o do Cliente\s*\n\s*([^\n]+?)(?:\s*\n\s*(?:CPF|Telefone|CEP|\())",
        texto,
        re.I,
    )
    if m:
        nome = _limpar_nome_cliente(m.group(1))
        if nome and not _LINHA_NOME_IGNORAR.search(nome):
            return nome
    return ""


def _parse_endereco_linha(linha: str) -> dict:
    out: dict[str, str] = {}
    linha = linha.strip()
    if not linha:
        return out

    partes = [p.strip() for p in linha.split(",") if p.strip()]
    if not partes:
        return out

    tipo, lograd = _separar_tipo_logradouro(partes[0])
    if tipo:
        out["end_tipo"] = tipo
    out["end_lograd"] = lograd or partes[0]

    if len(partes) >= 2:
        meio = partes[1]
        num_m = re.search(r"N[úu]mero\s*(\S+)", meio, re.I)
        if num_m:
            out["end_numero"] = num_m.group(1)
            compl = re.sub(r"N[úu]mero\s*\S+\s*-?\s*", "", meio, flags=re.I).strip(" -")
            if compl:
                out["end_comple"] = compl[:30]
        else:
            out["end_comple"] = meio[:30]

    if len(partes) >= 3:
        out["end_bairro"] = partes[2][:40]

    if len(partes) >= 4:
        cid_m = re.match(r"(.+?)\s*-\s*([A-Z]{2})\s*$", partes[3])
        if cid_m:
            out["cidade"] = cid_m.group(1).strip()
            out["uf"] = cid_m.group(2).upper()

    return out


def _extrair_secao(texto: str, titulo: str, proximos: tuple[str, ...]) -> str:
    idx = texto.find(titulo)
    if idx < 0:
        return ""
    trecho = texto[idx + len(titulo) :]
    fim = len(trecho)
    for prox in proximos:
        p = trecho.find(prox)
        if p >= 0:
            fim = min(fim, p)
    return trecho[:fim].strip()


def _html_para_texto(html: str) -> str:
    texto = re.sub(r"<[^>]+>", "\n", html)
    texto = re.sub(r"\n{2,}", "\n", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto


def _texto_pagina_normalizado(texto_pagina: str | None) -> str:
    """innerText com quebras de linha preservadas (sigla PT/NM fica em linhas distintas)."""
    if not (texto_pagina or "").strip():
        return ""
    bruto = texto_pagina.replace("\r\n", "\n").replace("\r", "\n")
    linhas = [re.sub(r"[ \t]+", " ", ln).strip() for ln in bruto.split("\n")]
    return "\n".join(ln for ln in linhas if ln)


def aplicar_idiomas_site(
    pedido: PedidoExtraido, mapa: dict | None
) -> None:
    """Aplica idioma lido pela extensão (sigla PT/EN/FR no texto do item) em cada item."""
    if not mapa:
        return
    normalizado: dict[str, str] = {}
    for chave, valor in mapa.items():
        ref = str(chave).strip().upper().lstrip("#")
        idioma = str(valor or "").strip().upper()
        if ref and idioma in ("PT", "EN", "FR"):
            normalizado[ref] = idioma
    if not normalizado:
        return

    for item in pedido.itens:
        ref_orig = (item.referencia_original or item.referencia or "").strip().upper()
        idioma = normalizado.get(ref_orig)
        if not idioma:
            continue
        item.idioma = idioma
        item.referencia = montar_referencia_clipp(
            item.referencia_original or ref_orig,
            idioma,
            raridade=item.raridade,
        )


def parsear_html_pedido(
    html: str,
    numero_pedido: str,
    texto_pagina: str | None = None,
    *,
    idiomas_por_ref: dict | None = None,
) -> PedidoExtraido:
    """Converte HTML/texto da página admin em PedidoExtraido."""
    html_proc = _preservar_marcadores_html(html or "")
    texto_html = _html_para_texto(html_proc)
    texto_pagina_norm = _texto_pagina_normalizado(texto_pagina)
    texto = texto_html
    if texto_pagina_norm and len(texto_pagina_norm) > len(texto) * 0.5:
        texto = texto_pagina_norm

    pedido = PedidoExtraido(
        arquivo=f"site:#{numero_pedido}",
        numero_pedido=str(numero_pedido),
    )

    if STATUS_IMPORTAR not in texto:
        pedido.erros.append(
            f"Status não é «{STATUS_IMPORTAR}» — pedido ignorado."
        )

    m_data = re.search(
        r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})", texto
    )
    if m_data:
        pedido.data_pedido = m_data.group(1)

    bloco_cli = _extrair_secao(
        texto,
        "Endereço do Cliente",
        ("Forma de Envio", "Forma de Pagamento", "Itens do Pedido"),
    )
    linhas_cli = [l.strip() for l in bloco_cli.split("\n") if l.strip()]
    pedido.cliente.update(_parse_bloco_endereco_cliente(linhas_cli))

    if not pedido.cliente.get("nome"):
        nome_fb = _extrair_nome_cliente_site(linhas_cli, texto)
        if nome_fb:
            pedido.cliente["nome"] = nome_fb

    bloco_envio = _extrair_secao(
        texto, "Forma de Envio", ("Forma de Pagamento", "Confirmação", "Itens do Pedido")
    )
    envio_linha = bloco_envio.split("\n")[0].strip() if bloco_envio else ""
    if envio_linha:
        pedido.envio = envio_linha.split("(")[0].strip()

    bloco_pag = _extrair_secao(
        texto,
        "Forma de Pagamento",
        ("Confirmação de Pagamento", "Itens do Pedido", "Nota Fiscal"),
    )
    pag_linha = bloco_pag.split("\n")[0].strip() if bloco_pag else ""
    if pag_linha:
        pedido.pagamento = pag_linha.split("#")[0].strip()

    bloco_conf = _extrair_secao(
        texto, "Confirmação de Pagamento", ("Itens do Pedido", "Nota Fiscal")
    )
    for linha in bloco_conf.split("\n"):
        if "Valor Total" in linha or "Valor total" in linha:
            vals = re.findall(r"R\$\s*([\d.,]+)", linha)
            if vals:
                pedido.resumo["valor_total"] = _parse_moeda(vals[-1])

    fonte_itens = texto_pagina_norm if texto_pagina_norm else texto
    bloco_itens = _extrair_secao(
        fonte_itens,
        "Itens do Pedido",
        ("Nota Fiscal", "Valor dos Itens", "Valor Total"),
    )
    bloco_itens_html = _extrair_secao(
        texto_html,
        "Itens do Pedido",
        ("Nota Fiscal", "Valor dos Itens", "Valor Total"),
    )
    if not bloco_itens:
        bloco_itens = (
            bloco_itens_html
            if bloco_itens_html
            else (texto[texto.find("Itens do Pedido") :] if "Itens do Pedido" in texto else texto)
        )
    pedido.itens = _extrair_itens_bloco(bloco_itens, html=html_proc)
    aplicar_idiomas_site(pedido, idiomas_por_ref)

    _extrair_resumo_valores_pedido(texto, pedido)

    produtos = sum(it.preco_total for it in pedido.itens)
    if produtos and not pedido.resumo.get("valor_itens"):
        pedido.resumo["valor_itens"] = produtos
    total = float(pedido.resumo.get("valor_total") or 0)
    if total and produtos and "valor_frete" not in pedido.resumo:
        pedido.resumo["valor_frete"] = round(max(total - produtos, 0), 2)

    _validar_totais_pedido_site(pedido, bloco_itens)

    if not pedido.cliente.get("nome"):
        pedido.erros.append("Cliente não identificado na página.")
    elif pedido.cliente.get("nome", "").strip().lower() in (
        "avaliar cliente",
        "padrão cliente",
        "padrao cliente",
    ):
        pedido.erros.append(
            "Nome do cliente inválido (rótulo do site). Abra o detalhe completo do pedido."
        )
    if not pedido.cliente.get("documento"):
        pedido.erros.append("CPF/CNPJ do cliente não encontrado na página.")
    if not pedido.itens:
        pedido.erros.append("Nenhum item encontrado na página.")

    normalizar_endereco_cliente(pedido.cliente)
    return pedido


def _indice_inicio_bloco_item(linhas: list[str], indice: int) -> int:
    """Início do bloco do item (após o subtotal anterior)."""
    for j in range(indice - 1, -1, -1):
        if re.search(r"subtotal", linhas[j], re.I):
            return j + 1
    return 0


def _extrair_quantidade_item_site(linhas: list[str], indice: int) -> int:
    """«3x» no bloco do item (entre subtotals), sem confundir com item anterior."""
    linha = linhas[indice]
    m = re.search(r"\b(\d+)\s*x\b", linha, re.I)
    if m and not re.match(r"^R\$\s", linha, re.I):
        return max(int(m.group(1)), 1)

    inicio = _indice_inicio_bloco_item(linhas, indice)
    for j in range(indice - 1, inicio - 1, -1):
        prev = linhas[j].strip()
        if not prev:
            continue
        if re.search(r"unid|subtotal", prev, re.I):
            continue
        if re.match(r"^R\$\s*[\d,.]+", prev, re.I):
            continue
        m = re.match(r"^(\d+)\s*x\s*$", prev, re.I)
        if m:
            return int(m.group(1))
        m = re.match(r"^(\d+)\s*x\b", prev, re.I)
        if m:
            return int(m.group(1))
    return 1


def _extrair_totais_cabecalho_itens(bloco: str) -> dict:
    """«163 ITENS - PEDIDO» no topo da lista."""
    out: dict = {}
    m = re.search(r"(\d+)\s+ITENS?\s*-\s*PEDIDO", bloco, re.I)
    if m:
        out["qtd_cartas_site"] = int(m.group(1))
    return out


def _validar_totais_pedido_site(pedido: PedidoExtraido, bloco_itens: str) -> None:
    """Compara soma das qtd/valores com o que o site declara."""
    avisos: list[str] = []
    cab = _extrair_totais_cabecalho_itens(bloco_itens)
    qtd_site = cab.get("qtd_cartas_site")
    qtd_parse = sum(it.quantidade for it in pedido.itens)
    prod_parse = round(sum(it.preco_total for it in pedido.itens), 2)
    prod_site = float(pedido.resumo.get("valor_itens") or 0)
    total_site = float(pedido.resumo.get("valor_total") or 0)

    if qtd_site and qtd_site != qtd_parse:
        avisos.append(
            f"Quantidade: site declara {qtd_site} cartas, parser leu {qtd_parse} "
            f"(diferença {qtd_site - qtd_parse})."
        )
    if prod_site and abs(prod_site - prod_parse) > 0.02:
        avisos.append(
            f"Valor dos itens: site R$ {prod_site:.2f}, parser R$ {prod_parse:.2f} "
            f"(diferença R$ {abs(prod_site - prod_parse):.2f})."
        )
    if total_site and prod_parse:
        frete = float(pedido.resumo.get("valor_frete") or 0)
        total_calc = round(prod_parse + frete, 2)
        if abs(total_site - total_calc) > 0.02:
            avisos.append(
                f"Valor total: site R$ {total_site:.2f}, calculado R$ {total_calc:.2f}."
            )

    if avisos:
        pedido.resumo["avisos_conferencia"] = avisos
        for av in avisos:
            pedido.erros.append(f"AVISO: {av}")


def _truncar_bloco_itens(bloco: str) -> str:
    """Para no rodapé (Valor dos Itens / Total) — evita reler a última ref."""
    out: list[str] = []
    for lin in bloco.split("\n"):
        s = lin.strip()
        if re.search(
            r"^valor\s+dos\s+itens|^valor\s+total|^nota\s+fiscal|^frete\s*:",
            s,
            re.I,
        ):
            break
        out.append(lin)
    return "\n".join(out)


def _linha_parece_item_valido(linhas: list[str], indice: int) -> bool:
    """Ignora ref solta no rodapé sem preço/qty (duplicata fantasma)."""
    linha = linhas[indice]
    if re.search(r"\bc[oó]digo\b", linha, re.I) or "(#" in linha:
        return True
    inicio = _indice_inicio_bloco_item(linhas, indice)
    trecho = linhas[inicio : min(indice + 8, len(linhas))]
    texto = " ".join(trecho).lower()
    if re.search(r"\b(\d+)\s*x\b", " ".join(trecho), re.I):
        return True
    if "unid" in texto or "subtotal" in texto:
        return True
    if re.findall(r"R\$\s*[\d,.]+", " ".join(trecho)):
        return True
    return False


def _indice_fim_bloco_item(linhas: list[str], indice: int) -> int:
    """Até a linha de subtotal deste item (não invade o próximo)."""
    for j in range(indice + 1, len(linhas)):
        if re.search(r"subtotal", linhas[j], re.I):
            return j
        if re.match(r"^\d+\s*x\b", linhas[j], re.I):
            return j - 1
    return min(indice + 7, len(linhas) - 1)


def _refs_candidatas_bloco(linhas_bloco: list[str]) -> list[tuple[str, bool]]:
    """(referência normalizada, veio da linha Código)."""
    candidatas: list[tuple[str, bool]] = []
    vistos: set[str] = set()
    for lin in linhas_bloco:
        eh_codigo = bool(re.search(r"\bc[oó]digo\b", lin, re.I))
        if eh_codigo:
            m_cod = re.search(
                r"(?:C[ÓO]DIGO|CODIGO)\s*:\s*([A-Z0-9\-]+)",
                lin,
                re.I,
            )
            if m_cod:
                norm = normalizar_referencia_site(m_cod.group(1))
                if norm and norm not in vistos:
                    vistos.add(norm)
                    candidatas.append((norm, True))
        for m in REF_COM_HASH.finditer(lin):
            norm = normalizar_referencia_site(m.group(0))
            if norm and norm not in vistos:
                vistos.add(norm)
                candidatas.append((norm, eh_codigo))
    return candidatas


def _extrair_sku_bloco(linhas_bloco: list[str]) -> str | None:
    """Produto selado: linha «SKU: …» abaixo do item (ref = TB_EST_PRODUTO_2.REFERENCIA)."""
    for i, lin in enumerate(linhas_bloco):
        m = re.search(r"\bSKU\s*:\s*(\S+)", lin, re.I)
        if m:
            return m.group(1).strip().upper()
        m = re.match(r"^\s*SKU\s+(\S+)\s*$", lin, re.I)
        if m:
            return m.group(1).strip().upper()
        if re.match(r"^\s*SKU\s*$", lin, re.I) and i + 1 < len(linhas_bloco):
            prox = linhas_bloco[i + 1].strip()
            if prox and not re.search(r"\b(?:subtotal|unid\.|c[oó]digo)\b", prox, re.I):
                return prox.upper()
        if not re.search(r"\bc[oó]digo\b", lin, re.I):
            continue
        m_cod = re.search(
            r"(?:C[ÓO]DIGO|CODIGO)\s*:\s*([A-Z0-9][A-Z0-9\-\.]*)",
            lin,
            re.I,
        )
        if not m_cod:
            continue
        bruto = m_cod.group(1).strip()
        if normalizar_referencia_site(bruto) or REF_PADRAO.search(bruto.upper()):
            continue
        return bruto.upper()
    return None


def _montar_item_bloco(
    bloco_linhas: list[str],
    offset: int,
    linhas: list[str],
    *,
    ref_original: str | None = None,
    sku: str | None = None,
    idioma: str | None = None,
    raridade: str | None = None,
) -> ItemPedido | None:
    if ref_original:
        idx_ref = 0
        for j, lin in enumerate(bloco_linhas):
            if normalizar_referencia_site(lin) == ref_original or (
                ref_original in lin.upper()
            ):
                idx_ref = j
                break
        indice_global = offset + idx_ref
        linha_nome = bloco_linhas[idx_ref]
        descricao = re.sub(r"\(#.*?\)", "", linha_nome)
        descricao = re.sub(r"^\d+\s*x\s*", "", descricao, flags=re.I).strip()[:60]
        ref_conv = montar_referencia_clipp(ref_original, idioma, raridade=raridade)
    elif sku:
        indice_global = offset
        for j, lin in enumerate(bloco_linhas):
            if sku in lin.upper():
                indice_global = offset + j
                break
        descricao = bloco_linhas[0][:60] if bloco_linhas else sku[:60]
        ref_original = sku
        ref_conv = sku
    else:
        return None

    qtd = _extrair_quantidade_item_site(linhas, indice_global)

    preco_unit = 0.0
    preco_total = 0.0
    for l2 in bloco_linhas:
        vals = re.findall(r"R\$\s*([\d.,]+)", l2)
        if not vals:
            continue
        low = l2.lower()
        if "unid" in low:
            preco_unit = _parse_moeda(vals[0])
        elif "subtotal" in low:
            preco_total = _parse_moeda(vals[-1])
            break

    if preco_unit > 0 and preco_total > 0:
        qtd_sub = _inferir_quantidade_preco(1, preco_unit, preco_total)
        if abs(preco_total - preco_unit * qtd_sub) < 0.02:
            qtd = qtd_sub
    elif qtd <= 1:
        qtd = _inferir_quantidade_preco(qtd, preco_unit, preco_total)

    if preco_unit > 0:
        preco_total = round(preco_unit * qtd, 2)

    return ItemPedido(
        quantidade=qtd,
        referencia_original=ref_original,
        referencia=ref_conv,
        preco_unitario=preco_unit,
        preco_total=preco_total,
        descricao=descricao,
        idioma=idioma,
        raridade=raridade,
        sku=sku,
    )


def _escolher_ref_bloco(
    candidatas: list[tuple[str, bool]], idioma: str | None
) -> str | None:
    if not candidatas:
        return None
    sufixo = {"EN": "-EN", "PT": "-PT", "FR": "-FR"}.get((idioma or "").upper())
    if sufixo:
        for ref, cod in candidatas:
            if sufixo in ref and cod:
                return ref
        for ref, _ in candidatas:
            if sufixo in ref:
                return ref
    for ref, cod in candidatas:
        if cod:
            return ref
    return candidatas[0][0]


def _iter_blocos_item(linhas: list[str]):
    inicio = 0
    for i, lin in enumerate(linhas):
        if re.search(r"subtotal", lin, re.I):
            if i >= inicio:
                yield linhas[inicio : i + 1], inicio
            inicio = i + 1
    if inicio < len(linhas):
        yield linhas[inicio:], inicio


def _extrair_itens_bloco(bloco: str, html: str | None = None) -> list[ItemPedido]:
    bloco = _truncar_bloco_itens(bloco)
    itens: list[ItemPedido] = []
    linhas = [l.strip() for l in bloco.split("\n") if l.strip()]
    chaves_no_pedido: set[str] = set()

    for bloco_linhas, offset in _iter_blocos_item(linhas):
        contexto = " ".join(bloco_linhas)
        idioma = _idioma_sigla_bloco(bloco_linhas) or _idioma_da_linha(contexto)
        raridade = _extrair_raridade_bloco(contexto)

        sku = _extrair_sku_bloco(bloco_linhas)
        ref_original = None
        if not sku:
            candidatas = _refs_candidatas_bloco(bloco_linhas)
            if candidatas:
                ref_original = _escolher_ref_bloco(candidatas, idioma)

        if html and ref_original and not sku:
            idioma = (
                idioma
                or _idioma_sigla_html(html, ref_original)
                or _idioma_do_html(html, ref_original)
            )
            raridade = raridade or _raridade_do_html(html, ref_original)

        chave = ref_original or sku
        if not chave or chave in chaves_no_pedido:
            continue
        chaves_no_pedido.add(chave)

        item = _montar_item_bloco(
            bloco_linhas,
            offset,
            linhas,
            ref_original=ref_original,
            sku=sku,
            idioma=idioma,
            raridade=raridade,
        )
        if item:
            itens.append(item)

    return itens


CDP_PADRAO = "http://127.0.0.1:9222"


def _chrome_cdp_disponivel(url: str) -> bool:
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/json/version",
            headers={"User-Agent": "AppPedidosPhy-RPA"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def _resolver_url_cdp(cfg_rpa: dict) -> tuple[str | None, bool]:
    """
    Retorna (url_cdp, obrigatorio).
    obrigatorio=True quando chrome_debug_url está no config — não abrir Chrome novo.
    """
    explicito = (cfg_rpa.get("chrome_debug_url") or "").strip()
    if explicito:
        if _chrome_cdp_disponivel(explicito):
            return explicito, True
        return None, True
    if _chrome_cdp_disponivel(CDP_PADRAO):
        return CDP_PADRAO, False
    return None, False


def _pontuar_guia_admin(page, base: str) -> int:
    url = (page.url or "").lower()
    host = base.lower().replace("https://", "").replace("http://", "").split("/")[0]
    score = 0
    if "view=ecom/admin" in url or "admin/pedidos" in url:
        score += 100
    if host and host in url:
        score += 30
    if "tiaocards" in url or "ligamagic" in url:
        score += 20
    if "from_redir" in url:
        score += 15
    return score


def _obter_pagina_preferida(context, base: str = ""):
    """No Chrome já aberto, reutiliza a guia do painel em vez de abrir outra."""
    paginas = [p for p in context.pages if not p.is_closed()]
    if not paginas:
        return context.new_page()
    return max(paginas, key=lambda p: _pontuar_guia_admin(p, base))


def _salvar_sessao(context) -> None:
    PERFIL_RPA.mkdir(parents=True, exist_ok=True)
    try:
        context.storage_state(path=str(SESSAO_RPA))
    except Exception:
        pass


def _fechar_guia_extras(context) -> None:
    """Uma guia só — evita abrir login em aba nova sem cookies."""
    paginas = list(context.pages)
    for pg in paginas[1:]:
        try:
            pg.close()
        except Exception:
            pass


def _obter_pagina_unica(context, *, cdp: bool = False, base: str = ""):
    if cdp:
        return _obter_pagina_preferida(context, base)
    _fechar_guia_extras(context)
    if context.pages:
        return context.pages[0]
    return context.new_page()


class RpaNavegador:
    """Gerencia Chrome: reutiliza janela aberta (CDP) ou perfil persistente."""

    def __init__(
        self,
        cfg_rpa: dict,
        *,
        headless: bool = False,
        on_log: Callable[[str], None] | None = None,
    ):
        self.cfg = cfg_rpa
        self.headless = headless
        self.on_log = on_log
        self._pw = None
        self.context = None
        self.page = None
        self._cdp = False

    def _log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        base = self.cfg.get("base_url", "https://www.tiaocards.com.br/").strip()
        cdp_url, cdp_obrigatorio = _resolver_url_cdp(self.cfg)

        if cdp_url:
            try:
                self._cdp = True
                self.context = self._pw.chromium.connect_over_cdp(cdp_url)
                self.page = _obter_pagina_unica(self.context, cdp=True, base=base)
                self._log(
                    f"Reutilizando Chrome já aberto ({cdp_url}) — "
                    f"guia: {self.page.url or 'nova'}"
                )
                return self
            except Exception as exc:
                if cdp_obrigatorio:
                    raise RuntimeError(
                        "Não consegui conectar ao Chrome já aberto.\n"
                        f"URL configurada: {cdp_url}\n"
                        f"Detalhe: {exc}\n\n"
                        "1) Execute Abrir Chrome RPA.bat\n"
                        "2) Faça login nessa janela e mantenha-a aberta\n"
                        "3) Depois rode: py -3 importar_site.py NUMERO_PEDIDO\n\n"
                        "O Chrome normal (sem depuração remota) não pode ser "
                        "controlado pelo script."
                    ) from exc
                self._log(
                    f"Chrome em {cdp_url} indisponível — abrindo perfil .rpa_profile"
                )

        PERFIL_RPA.mkdir(parents=True, exist_ok=True)
        self._log("Abrindo Chrome dedicado (.rpa_profile)...")
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PERFIL_RPA),
            channel=self.cfg.get("browser_channel", "chrome") or "chrome",
            headless=self.headless,
            locale="pt-BR",
            viewport=None,
            args=[
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
            ],
        )
        self.page = _obter_pagina_unica(self.context, cdp=False, base=base)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.context and not self._cdp:
            _salvar_sessao(self.context)
            try:
                self.context.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()


def _cod_pedido_da_url(url: str) -> str | None:
    m = re.search(r"[?&]cod=(\d+)", url or "")
    return m.group(1) if m else None


def _esta_no_painel_pedidos(page) -> bool:
    body = _texto_pagina(page)
    url = (page.url or "").lower()
    if "Gerenciamento de Pedidos" in body:
        return True
    if "admin/pedidos" in url and "Efetuar login" not in body[:800]:
        return True
    return False


def _clicar_rotulo(
    page,
    rotulos: tuple[str, ...],
    *,
    timeout_ms: int = 8000,
) -> bool:
    for rotulo in rotulos:
        candidatos = (
            page.get_by_role("link", name=re.compile(re.escape(rotulo), re.I)),
            page.get_by_role("button", name=re.compile(re.escape(rotulo), re.I)),
            page.get_by_text(re.compile(rf"^\s*{re.escape(rotulo)}\s*$", re.I)),
            page.locator(f"a:has-text('{rotulo}')"),
            page.locator(f"button:has-text('{rotulo}')"),
            page.locator(f"li:has-text('{rotulo}')"),
            page.locator(f"span:has-text('{rotulo}')"),
        )
        for loc in candidatos:
            try:
                alvo = loc.first
                if alvo.count() == 0:
                    continue
                if not alvo.is_visible(timeout=600):
                    continue
                try:
                    alvo.hover(timeout=1500)
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                alvo.click(timeout=timeout_ms)
                page.wait_for_timeout(1200)
                return True
            except Exception:
                continue
    return False


def _navegar_menu_admin(
    page,
    base: str,
    url_destino: str,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Minha conta → Dashboard administrativo → Pedidos → Gerenciamento de pedidos."""
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    if _esta_no_painel_pedidos(page):
        log("Já no painel Gerenciamento de Pedidos.")
        cod = _cod_pedido_da_url(url_destino)
        if cod and f"cod={cod}" not in (page.url or ""):
            log(f"Abrindo pedido #{cod}...")
            page.goto(
                url_pedidos(base, cod),
                wait_until="domcontentloaded",
                timeout=90000,
            )
            page.wait_for_timeout(2000)
        return True

    home = base.rstrip("/") + "/"
    url_atual = (page.url or "").lower()
    if "ligamagic" in url_atual or "from_redir" in url_atual:
        log("Redirect LigaMagic detectado — abrindo www.tiaocards.com.br...")
        page.goto(home, wait_until="domcontentloaded", timeout=90000)
        _aceitar_cookies(page)
        page.wait_for_timeout(1500)
    elif "tiaocards" not in url_atual:
        log("Abrindo www.tiaocards.com.br...")
        page.goto(home, wait_until="domcontentloaded", timeout=90000)
        _aceitar_cookies(page)
        page.wait_for_timeout(1500)

    passos: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Minha conta", ("Minha conta", "Minha Conta")),
        (
            "Dashboard administrativo",
            (
                "Dashboard administrativo",
                "Dashboard Administrativo",
                "Dashboard",
            ),
        ),
        ("Pedidos", ("Pedidos",)),
        (
            "Gerenciamento de pedidos",
            (
                "Gerenciamento de Pedidos",
                "Gerenciamento de pedidos",
            ),
        ),
    )

    for nome_passo, rotulos in passos:
        log(f"Menu: {nome_passo}...")
        if _clicar_rotulo(page, rotulos):
            page.wait_for_timeout(1500)
            if _esta_no_painel_pedidos(page):
                break
        else:
            log(f"  «{nome_passo}» não encontrado — seguindo...")

    if not _esta_no_painel_pedidos(page):
        return False

    log("Painel Gerenciamento de Pedidos aberto.")
    cod = _cod_pedido_da_url(url_destino)
    if cod:
        log(f"Abrindo pedido #{cod}...")
        page.goto(
            url_pedidos(base, cod),
            wait_until="domcontentloaded",
            timeout=90000,
        )
        page.wait_for_timeout(2000)
    return True


def _navegar_painel(
    page,
    base: str,
    url: str,
    on_log: Callable[[str], None] | None = None,
) -> None:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    log(
        "Navegando pelo site: Minha conta → Dashboard administrativo "
        "→ Pedidos → Gerenciamento de pedidos..."
    )
    if _navegar_menu_admin(page, base, url, on_log):
        return

    log("Menu não concluiu — tentando URL direta do painel...")
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    _aceitar_cookies(page)
    page.wait_for_timeout(1500)

    if not _esta_no_painel_pedidos(page):
        log("Redirecionando para o painel de pedidos...")
        page.goto(url_pedidos(base), wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(1500)


def _aceitar_cookies(page) -> None:
    for texto in ("Permitir Todos os Cookies", "Aceitar", "Accept"):
        try:
            btn = page.get_by_role("button", name=re.compile(texto, re.I))
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                return
        except Exception:
            pass


def _texto_pagina(page) -> str:
    try:
        return page.inner_text("body", timeout=8000)
    except Exception:
        return ""


def _diagnostico_sessao(page) -> str:
    url = page.url or ""
    texto = _texto_pagina(page).replace("\n", " ")[:200]
    return f"URL={url} | texto={texto!r}"


def _eh_redirecionamento_pos_login(url: str) -> bool:
    """Após 2FA, LigaMagic redireciona para ligamagic.com.br/?from_redir=true."""
    u = (url or "").lower()
    return "ligamagic.com.br" in u and "from_redir" in u


def _corrigir_redirecionamento_pos_login(
    page,
    base: str,
    url_destino: str,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Detecta pós-2FA e navega ao painel de pedidos sem fechar o Chrome."""
    if not _eh_redirecionamento_pos_login(page.url or ""):
        return False

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    destino = (
        url_destino
        if "admin/pedidos" in (url_destino or "")
        else url_pedidos(base)
    )
    log("2FA concluído — indo ao Gerenciamento de Pedidos pelo menu...")
    base_home = base.rstrip("/") + "/"
    page.goto(base_home, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    _aceitar_cookies(page)
    if _navegar_menu_admin(page, base, destino, on_log):
        return True
    log("Menu falhou após 2FA — tentando URL direta...")
    page.goto(destino, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    _aceitar_cookies(page)
    return True


def _aguardar_pos_2fa_ou_login(
    page,
    base: str,
    url_destino: str,
    *,
    timeout_s: int = 180,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Monitora a guia após login/2FA e corrige o redirect da LigaMagic."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _corrigir_redirecionamento_pos_login(page, base, url_destino, on_log):
            return True
        if _esta_logado(page):
            return True
        page.wait_for_timeout(2000)
    return False


def _esta_logado(page) -> bool:
    """Detecta sessão no painel admin da loja (não vitrine pública)."""
    try:
        url = (page.url or "").lower()
        body = _texto_pagina(page)

        if not body:
            return False

        # Vitrine pública — não logado
        if "Efetuar login" in body and "Minha Loja" not in body:
            if "view=ecom/admin" not in url:
                return False

        # Painel admin (lista ou detalhe do pedido)
        marcadores_admin = (
            "Gerenciamento de Pedidos",
            "Dashboard administrativo",
            "Dashboard Administrativo",
            "Minha Loja",
            "Endereço do Cliente",
            "Itens do Pedido",
            "Forma de Pagamento",
            "Imprimir Pedido",
            "Confirmação de Pagamento",
        )
        if any(m in body for m in marcadores_admin):
            return True

        if "view=ecom/admin" in url or "admin/pedidos" in url:
            if "Efetuar login" not in body[:800]:
                return True

        return False
    except Exception:
        return False


def _tentar_preencher_login(page, usuario: str, senha: str) -> bool:
    """Preenche formulário se estiver visível (login automático parcial)."""
    if not usuario or not senha:
        return False
    try:
        if page.locator('input[type="password"]').count() == 0:
            return False
    except Exception:
        return False

    for selector in (
        'input[name="login"]',
        'input[name="usuario"]',
        'input[name="user"]',
        'input[name="email"]',
        "#login",
        "#usuario",
    ):
        try:
            el = page.locator(selector).first
            if el.count() and el.is_visible():
                el.fill(usuario)
                break
        except Exception:
            continue

    for selector in (
        'input[name="senha"]',
        'input[name="password"]',
        'input[type="password"]',
        "#senha",
    ):
        try:
            el = page.locator(selector).first
            if el.count() and el.is_visible():
                el.fill(senha)
                break
        except Exception:
            continue

    for selector in (
        'button[type="submit"]',
        'input[type="submit"]',
        "button:has-text('Entrar')",
        "button:has-text('Login')",
        "button:has-text('Acessar')",
    ):
        try:
            el = page.locator(selector).first
            if el.count() and el.is_visible():
                el.click()
                page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    return False


def _aguardar_login_manual(
    page,
    base: str,
    url_destino: str,
    on_log: Callable[[str], None] | None = None,
) -> None:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    log("")
    log("=== LOGIN MANUAL ===")
    log("1) Na janela do Chrome, faça login + 2FA se pedir")
    log("2) Navegue: Minha conta → Dashboard administrativo → Pedidos")
    log("3) Após o 2FA o script tenta esse caminho sozinho")
    log("4) Se não redirecionar, abra Gerenciamento de Pedidos e pressione ENTER")
    log("")

    try:
        import sys

        pronto = threading.Event()

        def _aguardar_enter() -> None:
            try:
                if sys.stdin.isatty():
                    input(
                        "ENTER quando estiver no painel Admin > Pedidos "
                        "(ou aguarde redirecionamento automático)... "
                    )
            except EOFError:
                pass
            pronto.set()

        if sys.stdin.isatty():
            threading.Thread(target=_aguardar_enter, daemon=True).start()
            deadline = time.time() + 300
            while time.time() < deadline and not pronto.is_set():
                if _corrigir_redirecionamento_pos_login(
                    page, base, url_destino, on_log
                ):
                    if _esta_logado(page):
                        log("Login OK — redirecionamento automático após 2FA.")
                        return
                elif _esta_logado(page):
                    log("Login OK — painel admin detectado.")
                    return
                page.wait_for_timeout(2000)
        else:
            _aguardar_pos_2fa_ou_login(
                page, base, url_destino, timeout_s=180, on_log=on_log
            )
    except EOFError:
        pass

    log("Verificando sessão...")
    _corrigir_redirecionamento_pos_login(page, base, url_destino, on_log)
    _navegar_painel(page, base, url_destino, on_log)
    page.wait_for_timeout(2500)

    if _esta_logado(page):
        log("Login OK — painel admin detectado.")
        return

    url = (page.url or "").lower()
    if "view=ecom/admin" in url:
        log("URL admin detectada — continuando (sessão aceita).")
        return

    raise RuntimeError(
        "Painel admin não detectado após login manual.\n"
        f"{_diagnostico_sessao(page)}\n"
        "Dica: use Abrir Chrome RPA.bat + chrome_debug_url no config.ini"
    )


def _fazer_login(
    page,
    usuario: str,
    senha: str,
    base: str,
    url_destino: str,
    on_log: Callable[[str], None] | None = None,
) -> None:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    if _tentar_preencher_login(page, usuario, senha):
        log("Credenciais enviadas — conclua 2FA no Chrome se pedir.")
        if not _aguardar_pos_2fa_ou_login(
            page, base, url_destino, timeout_s=120, on_log=on_log
        ):
            page.wait_for_timeout(4000)
        _navegar_painel(page, base, url_destino, on_log)
        if _esta_logado(page):
            log("Login OK.")
            _salvar_sessao(page.context)
            return

    _aguardar_login_manual(page, base, url_destino, on_log)
    _salvar_sessao(page.context)


def _garantir_sessao(
    nav: RpaNavegador,
    cfg_rpa: dict,
    url: str,
    on_log: Callable[[str], None] | None = None,
) -> None:
    base = cfg_rpa.get("base_url", "https://www.tiaocards.com.br/").strip()
    usuario = cfg_rpa.get("usuario", "").strip()
    senha = cfg_rpa.get("senha", "").strip()
    page = nav.page

    _navegar_painel(page, base, url, on_log)

    if _esta_logado(page):
        if on_log:
            on_log("Sessão ativa — reutilizando login salvo.")
        return

    if _corrigir_redirecionamento_pos_login(page, base, url, on_log):
        if _esta_logado(page):
            if on_log:
                on_log("Sessão ativa após redirecionamento pós-2FA.")
            return

    if SESSAO_RPA.is_file():
        page.wait_for_timeout(2000)
        _navegar_painel(page, base, url, on_log)
        if _esta_logado(page):
            if on_log:
                on_log("Sessão restaurada do arquivo salvo.")
            return

    if not usuario or not senha:
        raise RuntimeError(
            "Não logado. Configure [rpa] no config.ini ou rode: "
            "py -3 importar_site.py --login"
        )

    _fazer_login(page, usuario, senha, base, url, on_log)
    _salvar_sessao(nav.context)
    _navegar_painel(page, base, url, on_log)

    if not _esta_logado(page):
        url_lower = (page.url or "").lower()
        if "view=ecom/admin" not in url_lower:
            raise RuntimeError(
                "Painel admin inacessível após login.\n"
                f"{_diagnostico_sessao(page)}"
            )


def salvar_login_interativo(
    cfg_rpa: dict,
    on_log: Callable[[str], None] | None = None,
) -> None:
    """Só abre o painel e salva sessão — use com --login."""
    base = cfg_rpa.get("base_url", "https://www.tiaocards.com.br/").strip()
    url = url_pedidos(base)

    with RpaNavegador(cfg_rpa, headless=False, on_log=on_log) as nav:
        _navegar_painel(nav.page, base, url, on_log)
        _aguardar_login_manual(nav.page, base, url, on_log)
        _salvar_sessao(nav.context)
        if on_log:
            on_log("Sessão salva — próximas importações reutilizam o Chrome aberto.")


def extrair_pedido_site(
    cfg_rpa: dict,
    numero_pedido: str | int,
    *,
    headless: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> PedidoExtraido:
    """Abre o painel, lê o pedido e retorna PedidoExtraido."""
    numero = str(numero_pedido).strip().lstrip("#")
    base = cfg_rpa.get("base_url", "https://www.tiaocards.com.br/").strip()
    url = url_pedidos(base, numero)

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    with RpaNavegador(cfg_rpa, headless=headless, on_log=log) as nav:
        _garantir_sessao(nav, cfg_rpa, url, on_log)
        log(f"Lendo pedido #{numero}...")
        nav.page.wait_for_timeout(2000)
        html = nav.page.content()
        pedido = parsear_html_pedido(html, numero)
        log(
            f"Extraído: {len(pedido.itens)} item(ns), "
            f"cliente={pedido.cliente.get('nome', '—')}"
        )
        return pedido


def listar_pedidos_pendentes(
    cfg_rpa: dict,
    *,
    headless: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> list[str]:
    """Retorna números de pedidos na listagem admin."""
    base = cfg_rpa.get("base_url", "https://www.tiaocards.com.br/").strip()
    url = url_pedidos(base)

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    numeros: list[str] = []

    with RpaNavegador(cfg_rpa, headless=headless, on_log=log) as nav:
        _garantir_sessao(nav, cfg_rpa, url, on_log)
        nav.page.wait_for_timeout(3000)
        html = nav.page.content()
        texto = re.sub(r"<[^>]+>", " ", html)
        for m in re.finditer(r"#(\d{7,9})", texto):
            numeros.append(m.group(1))
        numeros = list(dict.fromkeys(numeros))
        log(f"Encontrados {len(numeros)} pedido(s) na listagem.")
        return numeros
