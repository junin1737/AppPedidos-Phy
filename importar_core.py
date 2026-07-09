"""Lógica compartilhada: HTML do painel → CLIPP (RPA ou extensão Chrome).

Orquestra validação, bloqueio de reimportação, gravação no Firebird e
resposta JSON para a extensão. Usado por importar_servidor e importar_site.
"""

from __future__ import annotations

VERSAO = "2026.07.02-3"

from collections.abc import Callable

import db as firebird_db
import rpa_tiaocards as rpa


# ---------------------------------------------------------------------------
# Consultas auxiliares (pedido já no banco / venda cancelada)
# ---------------------------------------------------------------------------

def pedido_no_clipp(db_cfg: dict, numero: str) -> int | None:
    con = firebird_db.conectar(db_cfg)
    cur = con.cursor()
    try:
        venda = firebird_db.consultar_venda_por_numero_pedido(cur, numero)
        return venda["id_nfvenda"] if venda else None
    finally:
        cur.close()
        firebird_db._fechar_conexao(con)


def _venda_anterior_cancelada(db_cfg: dict, numero: str) -> bool:
    """True se existe venda do pedido no CLIPP, mas cancelada (reimportação)."""
    con = firebird_db.conectar(db_cfg)
    cur = con.cursor()
    try:
        venda = firebird_db.consultar_venda_por_numero_pedido(cur, numero)
        return bool(venda and firebird_db.venda_esta_cancelada(venda))
    finally:
        cur.close()
        firebird_db._fechar_conexao(con)


# ---------------------------------------------------------------------------
# Itens sem estoque — candidatos para escolha manual na extensão Chrome
# ---------------------------------------------------------------------------

def _enriquecer_faltantes(db_cfg: dict, faltantes: list) -> list[dict]:
    """Inclui candidatos do estoque para escolha manual na extensão."""
    saida: list[dict] = []
    for indice, faltante in enumerate(faltantes):
        item = faltante.as_dict()
        item["indice"] = indice
        try:
            item["candidatos"] = firebird_db.listar_produtos_semelhantes(
                db_cfg,
                descricao=faltante.descricao,
                referencia=faltante.referencia or faltante.referencia_site,
                preco_alvo=faltante.preco_unitario or None,
            )
        except Exception:
            item["candidatos"] = []
        saida.append(item)
    return saida


def resolver_produto_faltante(
    db_cfg: dict,
    *,
    id_venda: int,
    item: dict,
    id_identificador: int,
    on_log: Callable[[str], None] | None = None,
) -> dict:
    """Grava na venda um item que o usuário escolheu na extensão."""
    return firebird_db.inserir_item_resolvido_venda(
        db_cfg,
        int(id_venda),
        item,
        int(id_identificador),
        on_log=on_log,
    )


# ---------------------------------------------------------------------------
# Relatório de conferência (importado vs site) — log e payload para extensão
# ---------------------------------------------------------------------------

def _logar_relatorio_conferencia(relatorio: dict, log: Callable[[str], None]) -> None:
    pendentes = relatorio.get("itens_nao_importados") or []
    conf = relatorio.get("conferencia") or {}
    if pendentes:
        log(f"Relatório de conferência — {len(pendentes)} item(ns) NÃO importado(s):")
        for i, it in enumerate(pendentes, 1):
            motivo = (
                "sem estoque"
                if it.get("motivo") == "sem_estoque"
                else "não lida pela extensão"
            )
            ref = it.get("referencia") or it.get("referencia_site") or "?"
            log(
                f"  {i}. [{motivo}] qtd={it.get('quantidade', 1)} {ref} "
                f"| R$ {float(it.get('preco_total') or 0):.2f}"
            )
        log(f"  Subtotal pendente: R$ {float(relatorio.get('subtotal_pendente') or 0):.2f}")
    elif conf.get("avisos"):
        log(
            "Relatório de conferência disponível na aba do Chrome "
            "(importado vs site)."
        )
    else:
        log("Relatório de conferência: todos os itens importados.")


# ---------------------------------------------------------------------------
# Gravação no Firebird — pedido já parseado (PDF, HTML ou RPA)
# ---------------------------------------------------------------------------

def importar_pedido_extraido(
    pedido,
    numero: str,
    db_cfg: dict,
    nfv_cfg: dict,
    vend_cfg: dict,
    *,
    cliente_id_escolhido: int | None = None,
    on_log: Callable[[str], None] | None = None,
) -> dict:
    """Grava pedido já parseado. Retorna dict com ok, mensagem, detalhes."""
    numero = str(numero).strip().lstrip("#")

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    liberacao = firebird_db.sincronizar_controle_pedido_clipp(db_cfg, numero)
    if liberacao:
        log(liberacao)

    registro_local = rpa.obter_registro_controle(numero)
    id_controle = (
        int(registro_local["id_nfvenda"])
        if registro_local and registro_local.get("id_nfvenda")
        else None
    )
    bloqueio = firebird_db.mensagem_bloqueio_reimportacao(
        db_cfg,
        numero,
        id_controle,
        tem_controle_local=rpa.pedido_ja_registrado(numero),
    )
    if bloqueio:
        log(bloqueio)
        return {"ok": False, "mensagem": bloqueio, "ignorado": True}

    if liberacao or _venda_anterior_cancelada(db_cfg, numero):
        log(
            f"Reimportando pedido #{numero} "
            "(venda anterior cancelada no CLIPP)."
        )

    bloqueios = [
        e
        for e in pedido.erros
        if (
            "Status não é" in e
            or "CPF" in e
            or "inválido" in e
            or "não identificado" in e
        )
        # CPF ausente na página (retirada no balcão) NÃO bloqueia: vira escolha
        # manual de cliente logo abaixo.
        and "não encontrado na página" not in e
    ]
    # Conferência de totais NÃO bloqueia mais: importa o que casar e
    # reporta as divergências (o usuário lança o que faltar manualmente).
    avisos_conf = [
        e.replace("AVISO: ", "") for e in pedido.erros if e.startswith("AVISO:")
    ]
    if avisos_conf:
        log("Conferência (não bloqueia — importa o que casar e lista o que faltar):")
        for av in avisos_conf:
            log(f"  • {av}")
    nao_lidos_raw = pedido.resumo.get("itens_nao_lidos") or []
    if nao_lidos_raw:
        nao_lidos_pre = [
            firebird_db._item_faltante_de_pedido(it) for it in nao_lidos_raw
        ]
        firebird_db._logar_itens_nao_lidos(nao_lidos_pre, on_log=log)
    if bloqueios:
        log(f"IGNORADO: {bloqueios[0]}")
        return {"ok": False, "mensagem": bloqueios[0], "ignorado": True}

    if cliente_id_escolhido:
        pedido.cliente["id_cliente"] = int(cliente_id_escolhido)
        log(f"Cliente escolhido manualmente: ID_CLIENTE={int(cliente_id_escolhido)}")
    elif not (pedido.cliente.get("documento") or "").strip():
        # Retirada no balcão: a página não traz CPF/CNPJ. Em vez de bloquear,
        # devolve clientes com nome parecido para o usuário escolher.
        nome_cli = (pedido.cliente.get("nome") or "").strip()
        candidatos: list[dict] = []
        if nome_cli:
            try:
                candidatos = firebird_db.listar_clientes_semelhantes(db_cfg, nome_cli)
            except Exception as exc:
                log(f"Falha ao buscar clientes semelhantes: {exc}")
        log(
            f"Sem CPF/CNPJ na página — {len(candidatos)} cliente(s) parecido(s) "
            f"com «{nome_cli or '?'}» para escolha manual."
        )
        return {
            "ok": False,
            "escolher_cliente": True,
            "cliente_nome": nome_cli,
            "candidatos": candidatos,
            "mensagem": (
                f"Pedido sem CPF/CNPJ (retirada no balcão). "
                f"Escolha o cliente para «{nome_cli or 'pedido'}»."
            ),
        }

    if not pedido.itens:
        msg = "; ".join(pedido.erros) or "Nenhum item encontrado."
        log(f"ERRO: {msg}")
        return {"ok": False, "mensagem": msg}

    log(f"Cliente: {pedido.cliente.get('nome')} | Itens: {len(pedido.itens)}")
    if pedido.cliente.get("documento"):
        log(f"  CPF/CNPJ: {pedido.cliente.get('documento')}")
    if pedido.cliente.get("cidade"):
        log(
            f"  Endereço: {pedido.cliente.get('end_lograd', '')} "
            f"{pedido.cliente.get('end_numero', '')} — "
            f"{pedido.cliente.get('cidade')}/{pedido.cliente.get('uf', '')}"
        )
    n_selados = sum(1 for it in pedido.itens if it.sku)
    if n_selados:
        log(f"  Selados (SKU): {n_selados}")
    for n, it in enumerate(pedido.itens, 1):
        if len(pedido.itens) > 40 and n > 3 and not it.sku:
            if n == 4:
                log(f"  … {len(pedido.itens)} linha(s) no pedido (detalhe omitido no log)")
            continue
        if len(pedido.itens) > 40 and n > 40:
            break
        tipo = " [selado/SKU]" if it.sku else ""
        log(
            f"  {n}. qtd={it.quantidade} {it.referencia}{tipo} "
            f"(site: {it.referencia_original}, idioma={it.idioma or '?'}, "
            f"raridade={it.raridade or '?'}) "
            f"R$ {it.preco_unitario:.2f} = R$ {it.preco_total:.2f}"
        )
    if len(pedido.itens) > 40:
        log(f"  … {len(pedido.itens)} linha(s) no pedido (detalhe omitido no log)")
    qtd_cartas = sum(it.quantidade for it in pedido.itens)
    log(f"  Total: {len(pedido.itens)} linha(s), {qtd_cartas} carta(s), R$ {sum(it.preco_total for it in pedido.itens):.2f}")
    for av in pedido.erros:
        if av.startswith("AVISO:"):
            log(av)

    res = firebird_db.importar_pedido(
        db_cfg,
        pedido,
        nfvenda_cfg=nfv_cfg,
        venda_cfg=vend_cfg,
        on_log=on_log,
    )

    if res.sucesso:
        log(f"OK — {res.mensagem}")
        if res.itens_nao_encontrados:
            firebird_db._logar_itens_faltantes(res.itens_nao_encontrados, on_log=log)
        rpa.registrar_pedido_importado(numero, int(res.id_venda))
        payload = {
            "ok": True,
            "mensagem": res.mensagem,
            "id_venda": int(res.id_venda),
            "itens": len(pedido.itens),
            "cliente_nome": pedido.cliente.get("nome"),
        }
        if res.itens_nao_encontrados:
            payload["itens_faltantes"] = _enriquecer_faltantes(
                db_cfg, res.itens_nao_encontrados
            )
            payload["subtotal_faltante"] = sum(
                item.preco_total for item in res.itens_nao_encontrados
            )
            com_candidatos = [
                f for f in payload["itens_faltantes"] if f.get("candidatos")
            ]
            if com_candidatos:
                primeiro = com_candidatos[0]
                payload["escolher_produto"] = True
                payload["item_escolha"] = primeiro
                payload["mensagem"] += (
                    f" {len(com_candidatos)} item(ns) com sugestões no estoque "
                    f"— escolha na extensão."
                )
        if res.arquivo_faltantes:
            payload["arquivo_faltantes"] = res.arquivo_faltantes
            log(f"Lista de faltantes: {res.arquivo_faltantes}")
        ctx = pedido.resumo.get("ctx_conferencia") or {}
        relatorio = rpa.montar_relatorio_conferencia(
            pedido,
            ctx.get("fonte_itens") or "",
            ctx.get("bloco_itens") or "",
            None,
            id_venda=int(res.id_venda) if res.id_venda else None,
            itens_sem_estoque=res.itens_nao_encontrados,
        )
        payload["relatorio"] = relatorio
        _logar_relatorio_conferencia(relatorio, log)
        nao_imp = [
            it
            for it in relatorio.get("itens_nao_importados") or []
            if it.get("motivo") == "nao_lida"
        ]
        if nao_imp:
            payload["itens_nao_lidos"] = nao_imp
            payload["subtotal_nao_lidos"] = sum(
                float(it.get("preco_total") or 0) for it in nao_imp
            )
        if avisos_conf:
            payload["avisos_conferencia"] = avisos_conf
        return payload

    log(f"FALHA — {res.mensagem}")
    return {"ok": False, "mensagem": res.mensagem}


# ---------------------------------------------------------------------------
# Entrada da extensão Chrome — HTML da guia → parsear → importar_pedido_extraido
# ---------------------------------------------------------------------------

def importar_de_html(
    numero: str,
    html: str,
    db_cfg: dict,
    nfv_cfg: dict,
    vend_cfg: dict,
    *,
    texto_pagina: str | None = None,
    idiomas_por_ref: dict | None = None,
    selados_extensao: list[dict] | None = None,
    reprints_por_ref: dict | None = None,
    itens_extensao: list[dict] | None = None,
    cliente_id_escolhido: int | None = None,
    on_log: Callable[[str], None] | None = None,
) -> dict:
    numero = str(numero).strip().lstrip("#")
    pedido = rpa.parsear_html_pedido(
        html,
        numero,
        texto_pagina=texto_pagina,
        idiomas_por_ref=idiomas_por_ref,
        selados_extensao=selados_extensao,
        reprints_por_ref=reprints_por_ref,
        itens_extensao=itens_extensao,
    )
    if on_log:
        on_log(f"[importador {VERSAO}] {__file__}")
        if itens_extensao:
            on_log(f"Itens (extensão DOM): {len(itens_extensao)} carta(s)")
        else:
            on_log("Itens: parsing por texto (fallback servidor)")
        on_log(f"Idiomas lidos na página: {idiomas_por_ref or {}}")
        if selados_extensao:
            on_log(f"Selados (extensão DOM): {selados_extensao}")
        if reprints_por_ref:
            on_log(f"Reprints (extensão DOM): {reprints_por_ref}")
    return importar_pedido_extraido(
        pedido,
        numero,
        db_cfg,
        nfv_cfg,
        vend_cfg,
        cliente_id_escolhido=cliente_id_escolhido,
        on_log=on_log,
    )
