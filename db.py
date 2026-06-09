"""Operações Firebird para importação de pedidos."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import fdb

_fbclient_carregado: str | None = None

import limites_campos as lim
from parser_pedido import (
    ItemPedido,
    PedidoExtraido,
    digitos_documento,
    formatar_documento,
    linha_e_apelido_nick,
    set_usa_grupo2,
    _normalizar_raridade,
)


@dataclass
class RegistroDesfazer:
    """Dados gravados no banco para permitir desfazer uma importação."""

    arquivo: str
    id_cliente: int | None = None
    cliente_novo: bool = False
    cliente_alteracoes: dict | None = None
    id_venda: int | None = None
    ids_itens: list[int] = field(default_factory=list)
    nf_numero: int | None = None
    nfvenda_cfg: dict | None = None

    @property
    def pode_desfazer(self) -> bool:
        return bool(
            self.id_venda
            or self.cliente_novo
            or self.cliente_alteracoes
        )

    def resumo(self) -> str:
        partes = []
        if self.id_venda:
            partes.append(f"venda #{self.id_venda}")
        if self.ids_itens:
            partes.append(f"{len(self.ids_itens)} item(ns)")
        if self.nf_numero is not None:
            partes.append(f"NF nº {self.nf_numero}")
        if self.cliente_novo and self.id_cliente:
            partes.append(f"cliente novo #{self.id_cliente}")
        elif self.cliente_alteracoes and self.id_cliente:
            partes.append(f"ajustes no cliente #{self.id_cliente}")
        return ", ".join(partes) if partes else "registro vazio"


@dataclass
class ItemFaltante:
    """Carta do pedido que não foi vinculada ao estoque CLIPP."""

    referencia: str
    referencia_site: str
    quantidade: int
    preco_unitario: float
    descricao: str = ""
    idioma: str | None = None
    raridade: str | None = None

    @property
    def preco_total(self) -> float:
        return self.quantidade * self.preco_unitario

    def as_dict(self) -> dict:
        return {
            "referencia": self.referencia,
            "referencia_site": self.referencia_site,
            "quantidade": self.quantidade,
            "preco_unitario": self.preco_unitario,
            "preco_total": self.preco_total,
            "descricao": self.descricao,
            "idioma": self.idioma,
            "raridade": self.raridade,
        }

    def linha_log(self) -> str:
        nome = (self.descricao or "").strip() or "—"
        extras = []
        if self.idioma:
            extras.append(f"idioma={self.idioma}")
        if self.raridade:
            extras.append(f"raridade={self.raridade}")
        sufixo = f" | {', '.join(extras)}" if extras else ""
        return (
            f"  qtd={self.quantidade} {self.referencia} | "
            f"R$ {self.preco_unitario:.2f} un. | {nome}{sufixo}"
        )


@dataclass
class ResultadoImportacao:
    arquivo: str
    sucesso: bool
    id_venda: int | None = None
    id_cliente: int | None = None
    mensagem: str = ""
    itens_nao_encontrados: list[ItemFaltante] | None = None
    desfazer: RegistroDesfazer | None = None


@dataclass
class ResultadoDesfazer:
    sucesso: bool
    mensagem: str


def carregar_fbclient(fbclient_path: str) -> None:
    """Carrega fbclient.dll 64-bit de uma pasta local (sem instalar Firebird no sistema)."""
    global _fbclient_carregado
    path = (fbclient_path or "").strip()
    if not path:
        return
    if _fbclient_carregado == path:
        return
    if not os.path.isfile(path):
        raise RuntimeError(f"fbclient.dll não encontrado:\n{path}")

    pasta = os.path.dirname(os.path.abspath(path))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(pasta)

    fdb.load_api(path)
    _fbclient_carregado = path


def _parametros_conexao(db_config: dict) -> dict:
    """
    Conexão via servidor (localhost) permite IBExpert, sistema e importador ao mesmo tempo.
    Caminho direto do .FDB bloqueia o arquivo para um processo só.
    """
    db_path = db_config["database"]
    usar_servidor = db_config.get("use_server", True)
    host = (db_config.get("host") or "").strip()

    params = {
        "user": db_config["user"],
        "password": db_config["password"],
        "charset": db_config["charset"],
    }

    if usar_servidor and host:
        params["host"] = host
        params["port"] = int(db_config.get("port", 3050))
        params["database"] = db_path
    else:
        params["database"] = db_path

    return params


def conectar(db_config: dict):
    carregar_fbclient(db_config.get("fbclient_path", ""))
    params = _parametros_conexao(db_config)
    try:
        return fdb.connect(**params)
    except OSError as exc:
        raise RuntimeError(
            "Cliente Firebird (fbclient.dll) não carregou. Informe o caminho da DLL 64-bit "
            "em Arquivo → Configurar (fbclient.dll) — mesma arquitetura do seu Python."
        ) from exc
    except fdb.Error as exc:
        msg = str(exc)
        if "-902" in msg and (
            "sendo usado" in msg.lower() or "being used" in msg.lower()
        ):
            raise RuntimeError(
                "O banco está bloqueado para conexão direta ao arquivo. "
                "Marque 'Conectar via servidor Firebird' e use host localhost (porta 3050) "
                "— assim funciona com IBExpert e o sistema abertos."
            ) from exc
        raise


def encerrar_conexao(con, commit: bool = True) -> None:
    """Commit ou rollback pendente antes de fechar — evita transação aberta no Firebird."""
    if con is None:
        return
    try:
        if commit:
            con.commit()
        else:
            con.rollback()
    except fdb.Error:
        try:
            con.rollback()
        except fdb.Error:
            pass
    try:
        con.close()
    except fdb.Error:
        pass


def _fechar_conexao(con) -> None:
    if con is None:
        return
    try:
        con.close()
    except fdb.Error:
        pass


def _commit_gravacao(
    con,
    on_log: Callable[[str], None] | None = None,
    *,
    contexto: str = "",
) -> None:
    """Commit simples."""
    rotulo = f" ({contexto})" if contexto else ""
    con.commit()
    if on_log:
        on_log(f"  Commit{rotulo} — OK")


def _commit_estilo_ibexpert(
    con,
    on_log: Callable[[str], None] | None = None,
    *,
    contexto: str = "",
) -> None:
    """
    UPDATE no IBExpert + Commit retaining + commit final.
    Libera visibilidade no CLIPP com o sistema aberto (mesmo efeito de editar no IBExpert).
    """
    rotulo = f" ({contexto})" if contexto else ""
    con.commit(retaining=True)
    if on_log:
        on_log(f"  Commit retaining{rotulo} — OK")
    con.commit()
    if on_log:
        on_log(f"  Commit final{rotulo} — OK")


def _touch_registros_clipp(
    cur,
    *,
    id_cliente: int | None = None,
    id_venda: int | None = None,
) -> None:
    """
    UPDATE estilo IBExpert — libera visibilidade no CLIPP com o sistema aberto.
    Reatribui campos do lookup (nome/CPF/ID_CLIENTE) para forçar refresh na tela.
    """
    if id_cliente is not None:
        id_cliente = int(id_cliente)
        cur.execute(
            """
            UPDATE TB_CLIENTE
            SET NOME = TRIM(NOME),
                UPDATED_INTEGRADORA = CURRENT_TIMESTAMP
            WHERE ID_CLIENTE = ?
            """,
            (id_cliente,),
        )
        cur.execute(
            """
            UPDATE TB_CLI_PF
            SET CPF = TRIM(CPF)
            WHERE ID_CLIENTE = ?
              AND CPF IS NOT NULL
            """,
            (id_cliente,),
        )
        cur.execute(
            """
            UPDATE TB_CLI_PJ
            SET CNPJ = TRIM(CNPJ)
            WHERE ID_CLIENTE = ?
              AND CNPJ IS NOT NULL
            """,
            (id_cliente,),
        )
    if id_venda is not None and id_cliente is not None:
        cur.execute(
            """
            UPDATE TB_NFVENDA_2
            SET ID_CLIENTE = ?
            WHERE ID_NFVENDA = ?
            """,
            (int(id_cliente), int(id_venda)),
        )


def _finalizar_gravacao_visivel_clipp(
    con,
    cur,
    on_log: Callable[[str], None] | None = None,
    *,
    id_cliente: int | None = None,
    id_venda: int | None = None,
    contexto: str = "",
) -> None:
    """UPDATE + commits estilo IBExpert na mesma conexão da gravação."""
    _touch_registros_clipp(cur, id_cliente=id_cliente, id_venda=id_venda)
    if on_log:
        det = []
        if id_cliente is not None:
            det.append(f"cliente #{id_cliente}")
        if id_venda is not None and id_cliente is not None:
            det.append(f"venda #{id_venda} ID_CLIENTE={id_cliente}")
        rotulo = f" ({contexto})" if contexto else ""
        on_log(f"  UPDATE visibilidade CLIPP{rotulo} — {', '.join(det)}")
    _commit_estilo_ibexpert(con, on_log, contexto=contexto or "visibilidade CLIPP")


def _processo_commit_ibconsole(
    con,
    on_log: Callable[[str], None] | None = None,
    *,
    contexto: str = "",
) -> None:
    _commit_estilo_ibexpert(con, on_log, contexto=contexto)


def _finalizar_conexao_ibconsole(
    con,
    on_log: Callable[[str], None] | None = None,
    *,
    contexto: str = "",
    rollback: bool = False,
) -> None:
    """Fecha conexão garantindo commit IBConsole ou rollback explícito."""
    if con is None:
        return
    try:
        if rollback:
            con.rollback()
        else:
            _processo_commit_ibconsole(con, on_log, contexto=contexto)
    except fdb.Error:
        try:
            con.rollback()
        except fdb.Error:
            pass
    _fechar_conexao(con)


def _pulso_visibilidade_clipp(
    db_config: dict,
    *,
    id_cliente: int | None = None,
    id_venda: int | None = None,
    on_log: Callable[[str], None] | None = None,
) -> None:
    """
    UPDATE + commit em conexão nova (reforço — mesmo padrão do IBExpert).
    """
    if id_cliente is None and id_venda is None:
        return

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = conectar(db_config)
    cur = con.cursor()
    try:
        _finalizar_gravacao_visivel_clipp(
            con,
            cur,
            log,
            id_cliente=id_cliente,
            id_venda=id_venda,
            contexto="pulso IBExpert",
        )
    except fdb.Error as exc:
        try:
            con.rollback()
        except fdb.Error:
            pass
        log(f"  Aviso: pulso IBExpert falhou ({exc})")
    finally:
        cur.close()
        _fechar_conexao(con)


def _so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _montar_obs(pedido: PedidoExtraido) -> str:
    partes = []
    if pedido.numero_pedido:
        partes.append(f"Pedido site #{pedido.numero_pedido}")
    if pedido.pagamento:
        partes.append(f"Pagamento: {pedido.pagamento}")
    if pedido.envio:
        partes.append(f"Envio: {pedido.envio}")
    return " | ".join(partes)[:5000]


def _telefones_cliente(row) -> set[str]:
    """row: (ddd_resid, fone_resid, ddd_comer, fone_comer, ddd_celul, fone_celul)"""
    tels = set()
    pares = [(row[0], row[1]), (row[2], row[3]), (row[4], row[5])]
    for ddd, fone in pares:
        if fone:
            tels.add(_so_digitos(f"{ddd or ''}{fone}"))
    return {t for t in tels if len(t) >= 10}


_NOMES_BLOQUEADOS = frozenset(
    {
        "avaliar cliente",
        "padrão cliente",
        "padrao cliente",
        "cliente",
        "consumidor final",
        "endereco do cliente",
        "endereço do cliente",
    }
)


def _nome_cliente_placeholder(nome: str) -> bool:
    n = (nome or "").strip().lower()
    if not n:
        return True
    if n in _NOMES_BLOQUEADOS:
        return True
    return n.startswith("avaliar ") or n.startswith("padrão") or n.startswith("padrao ")


def _deve_atualizar_nome(nome_atual: str, nome_novo: str) -> bool:
    nome_novo = (nome_novo or "").strip()
    if not nome_novo or _nome_cliente_placeholder(nome_novo):
        return False
    nome_atual = (nome_atual or "").strip()
    if not nome_atual or _nome_cliente_placeholder(nome_atual):
        return True
    return nome_atual.upper() != nome_novo.upper()


def _buscar_cliente_por_documento(cur, documento: str) -> int | None:
    doc_digitos = digitos_documento(documento)
    if not doc_digitos:
        return None
    doc_mascara = formatar_documento(doc_digitos)
    if len(doc_digitos) == 11:
        cur.execute(
            """
            SELECT ID_CLIENTE FROM TB_CLI_PF
            WHERE TRIM(CPF) = ? OR TRIM(CPF) = ?
               OR REPLACE(REPLACE(REPLACE(TRIM(CPF), '.', ''), '-', ''), '/', '') = ?
            """,
            (doc_mascara, doc_digitos, doc_digitos),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    if len(doc_digitos) == 14:
        cur.execute(
            """
            SELECT ID_CLIENTE FROM TB_CLI_PJ
            WHERE TRIM(CNPJ) = ? OR TRIM(CNPJ) = ?
               OR REPLACE(REPLACE(REPLACE(TRIM(CNPJ), '.', ''), '-', ''), '/', '') = ?
            """,
            (doc_mascara, doc_digitos, doc_digitos),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    return None


def _cliente_documento_confere(cur, id_cliente: int, documento: str) -> bool:
    doc_digitos = digitos_documento(documento)
    if not doc_digitos:
        return True
    encontrado = _buscar_cliente_por_documento(cur, documento)
    return encontrado == int(id_cliente)


def buscar_cliente(cur, nome: str, telefone: str = "", documento: str = "") -> int | None:
    nome = (nome or "").strip()
    if nome.lower() in _NOMES_BLOQUEADOS:
        nome = ""

    # CPF/CNPJ é o critério mais confiável (evita «Avaliar Cliente» etc.)
    if documento:
        id_doc = _buscar_cliente_por_documento(cur, documento)
        if id_doc:
            return id_doc

    if not nome:
        return None

    tel_busca = _so_digitos(telefone)

    cur.execute(
        """
        SELECT c.ID_CLIENTE, c.NOME,
               c.DDD_RESID, c.FONE_RESID, c.DDD_COMER, c.FONE_COMER,
               c.DDD_CELUL, c.FONE_CELUL
        FROM TB_CLIENTE c
        WHERE UPPER(TRIM(c.NOME)) = UPPER(TRIM(?))
        """,
        (nome,),
    )
    candidatos = cur.fetchall()

    if tel_busca and len(tel_busca) >= 10:
        for row in candidatos:
            if tel_busca in _telefones_cliente(row[2:]):
                return row[0]

    if documento:
        doc_digitos = digitos_documento(documento)
        for row in candidatos:
            if _cliente_documento_confere(cur, int(row[0]), documento):
                return row[0]
        return None

    if len(candidatos) == 1:
        cid = int(candidatos[0][0])
        if documento:
            if _cliente_documento_confere(cur, cid, documento):
                return cid
            return _buscar_cliente_por_documento(cur, documento)
        if not tel_busca:
            return cid
        if tel_busca in _telefones_cliente(candidatos[0][2:]):
            return cid
        return None

    return None


ID_PAIS_PADRAO = lim.ID_PAIS


def buscar_id_cidade(cur, cidade: str, uf: str) -> str | None:
    """Busca ID_CIDADE em TB_CIDADE_SIS (NOME + SIGLA_UF)."""
    cidade = (cidade or "").strip()
    uf = (uf or "").strip().upper()
    if not cidade or not uf:
        return None

    cur.execute(
        """
        SELECT FIRST 1 ID_CIDADE
        FROM TB_CIDADE_SIS
        WHERE UPPER(TRIM(NOME)) = UPPER(TRIM(?))
          AND UPPER(TRIM(SIGLA_UF)) = UPPER(TRIM(?))
        """,
        (cidade, uf),
    )
    row = cur.fetchone()
    if row:
        return str(row[0]).strip()[: lim.ID_CIDADE]

    cur.execute(
        """
        SELECT FIRST 1 ID_CIDADE
        FROM TB_CIDADE_SIS
        WHERE UPPER(TRIM(NOME)) CONTAINING UPPER(TRIM(?))
          AND UPPER(TRIM(SIGLA_UF)) = UPPER(TRIM(?))
        """,
        (cidade, uf),
    )
    row = cur.fetchone()
    return str(row[0]).strip()[: lim.ID_CIDADE] if row else None


def _resolver_localizacao_cliente(cur, cli: dict) -> None:
    if not cli.get("id_cidade"):
        id_cidade = buscar_id_cidade(cur, cli.get("cidade", ""), cli.get("uf", ""))
        if id_cidade:
            cli["id_cidade"] = id_cidade


_TIPOS_LOGRADOURO = (
    (re.compile(r"^avenida\s+", re.I), "Avenida"),
    (re.compile(r"^av\.?\s+", re.I), "Av."),
    (re.compile(r"^travessa\s+", re.I), "Travessa"),
    (re.compile(r"^alameda\s+", re.I), "Alameda"),
    (re.compile(r"^rod\.?\s+", re.I), "Rod."),
    (re.compile(r"^estrada\s+", re.I), "Estrada"),
    (re.compile(r"^r\.?\s+", re.I), "Rua"),
    (re.compile(r"^rua\s+", re.I), "Rua"),
)


def _separar_tipo_logradouro(texto: str) -> tuple[str | None, str]:
    texto = (texto or "").strip()
    if not texto:
        return None, ""
    for pattern, tipo in _TIPOS_LOGRADOURO:
        m = pattern.match(texto)
        if m:
            return tipo[: lim.END_TIPO], texto[m.end() :].strip()[: lim.END_LOGRAD]
    return None, texto[: lim.END_LOGRAD]


def _linha_apelido_site(lin: str) -> bool:
    return bool(re.match(r"^\([^)]+\)\s*$", (lin or "").strip()))


def _linha_lixo_endereco(lin: str, apelido: str | None = None) -> bool:
    s = (lin or "").strip()
    if not s:
        return True
    if re.match(r"^[\(\)]+$", s):
        return True
    if _linha_apelido_site(s):
        return True
    if apelido:
        core = re.sub(r"[\(\)]", "", s).strip()
        if core.lower() == apelido.lower():
            return True
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{1,39}\)\s*$", s):
        return True
    if re.match(r"^\([A-Za-z0-9][A-Za-z0-9_\-\.]{1,39}\s*$", s):
        return True
    return False


def _montar_linha_endereco_site(cli: dict) -> str:
    """«Avenida B, Número 07 - Mercadinho Mais Econômico» — padrão CLIPP."""
    salvo = (cli.get("endereco_completo_site") or "").strip()
    if salvo and not re.match(r"^[\(\),\s-]+$", salvo):
        return salvo
    tipo = (cli.get("end_tipo") or "").strip()
    lograd = (cli.get("end_lograd") or "").strip()
    if lograd in ("(", ")"):
        lograd = ""
    parte1 = f"{tipo} {lograd}".strip() if tipo else lograd
    if cli.get("end_numero"):
        parte1 = f"{parte1}, Número {cli['end_numero']}".strip(", ")
    comple = (cli.get("texto_complemento_site") or "").strip()
    if parte1 and comple:
        return f"{parte1} - {comple}"
    return parte1 or comple


def _montar_observacao_padrao_clipp(cli: dict) -> str:
    """Bloco multilinha igual ao cadastro manual (2º print)."""
    linhas: list[str] = []
    nome = (cli.get("nome") or "").strip()
    if nome:
        linhas.append(nome)
    linha_end = _montar_linha_endereco_site(cli)
    if linha_end:
        linhas.append(linha_end)
    if cli.get("end_bairro"):
        linhas.append(str(cli["end_bairro"]).strip())
    if cli.get("cidade") and cli.get("uf"):
        linhas.append(f"{cli['cidade']} - {cli['uf']}")
    if cli.get("end_cep"):
        linhas.append(f"CEP {cli['end_cep']}")
    ddd = re.sub(r"\D", "", str(cli.get("ddd_celul") or ""))[:2]
    fone = re.sub(r"\D", "", str(cli.get("fone_celul") or ""))
    if len(ddd) == 2 and len(fone) >= 8:
        linhas.append(f"({ddd}) {fone[:9]}")
    doc = digitos_documento(cli.get("documento") or "")
    if len(doc) == 11:
        linhas.append(f"CPF: {doc}")
    elif len(doc) == 14:
        linhas.append(f"CNPJ: {doc}")
    return "\n".join(linhas)[: lim.OBSERVACAO]


def _sanitizar_campos_endereco_cli(cli: dict) -> None:
    """Remove lixo «(», «)», apelido nos campos de endereço."""
    apelido = (cli.get("apelido_site") or "").strip()
    for chave in ("end_lograd", "end_numero", "end_bairro"):
        val = (cli.get(chave) or "").strip()
        if not val or re.match(r"^[\(\)]+$", val):
            cli.pop(chave, None)
            continue
        if apelido and re.sub(r"[\(\)]", "", val).lower() == apelido.lower():
            cli.pop(chave, None)
            continue
        if chave == "end_lograd" and (
            _linha_lixo_endereco(val, apelido) or linha_e_apelido_nick(val, apelido)
        ):
            cli.pop(chave, None)
    apelido = (cli.get("apelido_site") or "").strip()
    comple = (cli.get("end_comple") or "").strip()
    if apelido and comple == apelido:
        cli.pop("end_comple", None)
    if comple and re.match(r"^[\(\)]+$", comple):
        cli.pop("end_comple", None)


def _montar_endereco_completo_site(cli: dict) -> str:
    return _montar_linha_endereco_site(cli)


def _texto_endereco_para_observacao(cli: dict) -> str:
    return _montar_observacao_padrao_clipp(cli)


def _preparar_complemento_e_observacao(cli: dict) -> None:
    """
    Complemento longo só na OBSERVACAO (multilinha).
    END_COMPLE no banco fica vazio (padrão CLIPP); máx. 29 se vier curto e válido.
    """
    linha_site = _montar_linha_endereco_site(cli)
    if linha_site:
        cli["endereco_completo_site"] = linha_site

    _sanitizar_campos_endereco_cli(cli)

    comple = (cli.get("end_comple") or "").strip()
    if comple and len(comple) <= lim.END_COMPLE and not re.match(r"^[\(\)]+$", comple):
        cli["end_comple"] = comple[: lim.END_COMPLE]
    else:
        cli.pop("end_comple", None)

    obs = _montar_observacao_padrao_clipp(cli)
    if obs:
        cli["observacao"] = obs


def normalizar_endereco_cliente(cli: dict) -> None:
    """Alinha END_TIPO + END_LOGRAD; não inventa «Rua» nem mistura apelido no endereço."""
    tipo = (cli.get("end_tipo") or "").strip()
    lograd = (cli.get("end_lograd") or "").strip()
    if not tipo and lograd:
        tipo_novo, lograd_novo = _separar_tipo_logradouro(lograd)
        if tipo_novo:
            cli["end_tipo"] = tipo_novo
            cli["end_lograd"] = lograd_novo
    if cli.get("end_tipo"):
        cli["end_tipo"] = str(cli["end_tipo"])[: lim.END_TIPO]
    if cli.get("end_lograd"):
        cli["end_lograd"] = str(cli["end_lograd"])[: lim.END_LOGRAD]
    if cli.get("end_bairro"):
        cli["end_bairro"] = str(cli["end_bairro"])[: lim.END_BAIRRO]
    _preparar_complemento_e_observacao(cli)


def _campos_endereco_para_db(cli: dict) -> dict:
    normalizar_endereco_cliente(cli)
    return {
        "END_CEP": (cli.get("end_cep") or "")[: lim.END_CEP] or None,
        "END_TIPO": (cli.get("end_tipo") or "")[: lim.END_TIPO] or None,
        "END_LOGRAD": (cli.get("end_lograd") or "")[: lim.END_LOGRAD] or None,
        "END_NUMERO": (cli.get("end_numero") or "")[: lim.END_NUMERO] or None,
        "END_BAIRRO": (cli.get("end_bairro") or "")[: lim.END_BAIRRO] or None,
        "END_COMPLE": (cli.get("end_comple") or "")[: lim.END_COMPLE] or None,
    }


def _corrigir_endereco_clipp_no_banco(
    end_tipo_atual: str | None, end_lograd_atual: str | None
) -> dict[str, str]:
    """Corrige cadastros antigos com 'Rua X' só em END_LOGRAD."""
    if (end_tipo_atual or "").strip():
        return {}
    lograd = (end_lograd_atual or "").strip()
    if not lograd or re.match(r"^[\(\)]+$", lograd):
        return {}
    tipo, lograd_novo = _separar_tipo_logradouro(lograd)
    if not tipo or lograd_novo == lograd or re.match(r"^[\(\)]+$", lograd_novo):
        return {}
    return {"END_TIPO": tipo, "END_LOGRAD": lograd_novo}


def _observacao_cliente(cli: dict) -> str | None:
    obs = (cli.get("observacao") or "").strip()
    return obs[: lim.OBSERVACAO] if obs else None


def _formatar_fone_celul_clipp(fone: str) -> str:
    """Formato CLIPP: «98527 0029» (9 dígitos) ou «3456 7890» (8 dígitos)."""
    d = re.sub(r"\D", "", fone or "")
    if len(d) == 9:
        return f"{d[:5]} {d[5:]}"
    if len(d) == 8:
        return f"{d[:4]} {d[4:]}"
    return d[: lim.FONE_CELUL]


def _telefone_para_db(cli: dict) -> tuple[str | None, str | None]:
    """DDD + FONE_CELUL no padrão CLIPP (espaço no meio do celular)."""
    digitos = re.sub(r"\D", "", str(cli.get("telefone") or ""))
    ddd = re.sub(r"\D", "", str(cli.get("ddd_celul") or ""))[: lim.DDD_CELUL]
    fone_raw = re.sub(r"\D", "", str(cli.get("fone_celul") or ""))

    if len(digitos) >= 10:
        ddd = digitos[:2]
        fone_raw = digitos[2:]
    elif len(fone_raw) >= 10 and not ddd:
        ddd = fone_raw[:2]
        fone_raw = fone_raw[2:]
    elif len(ddd) != 2 or len(fone_raw) < 8:
        return None, None

    fone = _formatar_fone_celul_clipp(fone_raw)
    if len(re.sub(r"\D", "", fone)) < 8:
        return None, None
    return ddd, fone[: lim.FONE_CELUL]


def _obter_empcadastro(cur) -> str | None:
    """CNPJ da loja (TB_EMITENTE) — mesmo valor que o CLIPP grava em EMPCADASTRO."""
    cur.execute(
        "SELECT FIRST 1 TRIM(CNPJ) FROM TB_EMITENTE WHERE CNPJ IS NOT NULL"
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    return str(row[0]).strip()[:18]


def _obter_id_tipo_padrao(cur) -> int | None:
    """Tipo de cliente — usado por V_CLIENTES (join TB_CLI_TIPO_SIS)."""
    try:
        cur.execute(
            """
            SELECT FIRST 1 ID_TIPO
            FROM TB_CLI_TIPO_SIS
            ORDER BY ID_TIPO
            """
        )
        row = cur.fetchone()
        if row and row[0]:
            return int(row[0])
    except fdb.Error:
        pass
    try:
        cur.execute(
            """
            SELECT FIRST 1 ID_TIPO
            FROM TB_CLIENTE
            WHERE ID_TIPO IS NOT NULL
            ORDER BY ID_CLIENTE DESC
            """
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] else None
    except fdb.Error:
        return None


def _defaults_clipp_cliente(cur) -> dict:
    """Campos que o cadastro manual do CLIPP sempre preenche (V_CLIENTES / TB_CLIENTE)."""
    defaults = {
        "LIMITE": 0,
        "DT_MELHOR_VENCTO": 0,
        "PERFIL_DE_RISCO": "N",
        "PRODUTOR_RURAL": "N",
    }
    emp = _obter_empcadastro(cur)
    if emp:
        defaults["EMPCADASTRO"] = emp
    return defaults


def _campo_cliente_vazio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str):
        return not valor.strip()
    return False


def _mesclar_alteracoes(
    base: dict | None, extra: dict | None
) -> dict | None:
    if not extra:
        return base
    if not base:
        return dict(extra)
    merged = dict(base)
    merged.update(extra)
    return merged


def _aplicar_defaults_clipp_cliente(cur, id_cliente: int) -> dict | None:
    """Completa campos vazios exigidos pelo CLIPP / V_CLIENTES."""
    defaults = _defaults_clipp_cliente(cur)
    cur.execute(
        """
        SELECT EMPCADASTRO, LIMITE, DT_MELHOR_VENCTO, PERFIL_DE_RISCO, PRODUTOR_RURAL,
               DT_CADASTRO, ID_TIPO
        FROM TB_CLIENTE WHERE ID_CLIENTE = ?
        """,
        (id_cliente,),
    )
    row = cur.fetchone()
    if not row:
        return None

    updates: dict = {}
    alteracoes: dict = {}
    for idx, campo in enumerate(
        ("EMPCADASTRO", "LIMITE", "DT_MELHOR_VENCTO", "PERFIL_DE_RISCO", "PRODUTOR_RURAL")
    ):
        if campo in defaults and _campo_cliente_vazio(row[idx]):
            updates[campo] = defaults[campo]
            alteracoes[campo] = row[idx]

    dt_cadastro_sql = ""
    if _campo_cliente_vazio(row[5]):
        dt_cadastro_sql = "DT_CADASTRO = CURRENT_DATE"
        alteracoes["DT_CADASTRO"] = row[5]

    if not updates and not dt_cadastro_sql:
        return None

    set_parts = [f"{k} = ?" for k in updates]
    if dt_cadastro_sql:
        set_parts.append(dt_cadastro_sql)
    sets = ", ".join(set_parts)
    cur.execute(
        f"UPDATE TB_CLIENTE SET {sets}, UPDATED_INTEGRADORA = CURRENT_TIMESTAMP "
        f"WHERE ID_CLIENTE = ?",
        (*updates.values(), id_cliente),
    )
    return alteracoes


def _garantir_cliente_pronto_clipp(cur, id_cliente: int) -> None:
    """Completa defaults obrigatórios antes de gravar a venda (sem forçar ID_TIPO)."""
    _aplicar_defaults_clipp_cliente(cur, id_cliente)
    cur.execute(
        """
        SELECT END_TIPO, DT_CADASTRO, EMPCADASTRO
        FROM TB_CLIENTE WHERE ID_CLIENTE = ?
        """,
        (int(id_cliente),),
    )
    row = cur.fetchone()
    if not row:
        return

    end_tipo, dt_cad, emp = row
    defaults = _defaults_clipp_cliente(cur)
    updates: dict = {}
    if dt_cad is None:
        cur.execute(
            """
            UPDATE TB_CLIENTE
            SET DT_CADASTRO = CURRENT_DATE,
                UPDATED_INTEGRADORA = CURRENT_TIMESTAMP
            WHERE ID_CLIENTE = ?
            """,
            (int(id_cliente),),
        )
    if not (emp or "").strip() and defaults.get("EMPCADASTRO"):
        updates["EMPCADASTRO"] = defaults["EMPCADASTRO"]
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        cur.execute(
            f"UPDATE TB_CLIENTE SET {sets}, UPDATED_INTEGRADORA = CURRENT_TIMESTAMP "
            f"WHERE ID_CLIENTE = ?",
            (*updates.values(), int(id_cliente)),
        )


def _completar_cliente(cur, id_cliente: int, pedido: PedidoExtraido) -> dict | None:
    cli = pedido.cliente
    cur.execute(
        """
        SELECT NOME, END_CEP, END_TIPO, END_LOGRAD, END_NUMERO, END_BAIRRO, END_COMPLE,
               DDD_CELUL, FONE_CELUL, OBSERVACAO, ID_CIDADE
        FROM TB_CLIENTE WHERE ID_CLIENTE = ?
        """,
        (id_cliente,),
    )
    row = cur.fetchone()
    if not row:
        return None

    updates = {}
    alteracoes: dict = {}
    nome_pdf = (cli.get("nome") or "").strip()[: lim.NOME]
    nome_atual = (row[0] or "").strip()
    if nome_pdf and _deve_atualizar_nome(nome_atual, nome_pdf):
        updates["NOME"] = nome_pdf

    idx_campo = {
        "END_CEP": 1,
        "END_TIPO": 2,
        "END_LOGRAD": 3,
        "END_NUMERO": 4,
        "END_BAIRRO": 5,
        "END_COMPLE": 6,
    }
    for campo, valor in _campos_endereco_para_db(cli).items():
        if valor:
            updates[campo] = valor

    for campo, valor in _corrigir_endereco_clipp_no_banco(row[2], row[3]).items():
        if valor:
            updates[campo] = valor

    obs = _observacao_cliente(cli)
    if obs:
        updates["OBSERVACAO"] = obs

    _resolver_localizacao_cliente(cur, cli)
    if cli.get("id_cidade"):
        updates["ID_CIDADE"] = cli["id_cidade"][: lim.ID_CIDADE]

    ddd, fone = _telefone_para_db(cli)
    if ddd and fone:
        updates["DDD_CELUL"] = ddd
        updates["FONE_CELUL"] = fone

    if updates:
        idx_row = {
            "NOME": 0,
            "END_CEP": 1,
            "END_TIPO": 2,
            "END_LOGRAD": 3,
            "END_NUMERO": 4,
            "END_BAIRRO": 5,
            "END_COMPLE": 6,
            "DDD_CELUL": 7,
            "FONE_CELUL": 8,
            "OBSERVACAO": 9,
            "ID_CIDADE": 10,
        }
        for campo in updates:
            alteracoes[campo] = row[idx_row[campo]]
        sets = ", ".join(f"{k} = ?" for k in updates)
        cur.execute(
            f"UPDATE TB_CLIENTE SET {sets}, UPDATED_INTEGRADORA = CURRENT_TIMESTAMP WHERE ID_CLIENTE = ?",
            (*updates.values(), id_cliente),
        )

    return _mesclar_alteracoes(
        alteracoes or None, _aplicar_defaults_clipp_cliente(cur, id_cliente)
    )


def _garantir_documento_cliente(cur, id_cliente: int, pedido: PedidoExtraido) -> None:
    doc_digitos = digitos_documento(pedido.cliente.get("documento", ""))
    if not doc_digitos:
        return
    doc_db = formatar_documento(doc_digitos)
    if len(doc_digitos) <= 11:
        cur.execute("SELECT 1 FROM TB_CLI_PF WHERE ID_CLIENTE = ?", (id_cliente,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO TB_CLI_PF (ID_CLIENTE, CPF) VALUES (?, ?)",
                (id_cliente, doc_db),
            )
    else:
        cur.execute("SELECT 1 FROM TB_CLI_PJ WHERE ID_CLIENTE = ?", (id_cliente,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO TB_CLI_PJ (ID_CLIENTE, CNPJ) VALUES (?, ?)",
                (id_cliente, doc_db),
            )


def cadastrar_cliente(cur, pedido: PedidoExtraido) -> int:
    cli = pedido.cliente
    nome = cli.get("nome", "")[: lim.NOME]
    doc_digitos = digitos_documento(cli.get("documento", ""))
    doc_db = formatar_documento(doc_digitos) if doc_digitos else ""
    ddd, fone = _telefone_para_db(cli)
    end = _campos_endereco_para_db(cli)
    obs = _observacao_cliente(cli)
    _resolver_localizacao_cliente(cur, cli)
    id_cidade = cli.get("id_cidade")
    defaults = _defaults_clipp_cliente(cur)

    cur.execute(
        """
        INSERT INTO TB_CLIENTE (
            NOME, ID_PAIS, ID_CIDADE, STATUS, UPDATED_INTEGRADORA,
            END_TIPO, DDD_CELUL, FONE_CELUL,
            END_CEP, END_LOGRAD, END_NUMERO, END_BAIRRO, END_COMPLE, OBSERVACAO,
            EMPCADASTRO, LIMITE, DT_MELHOR_VENCTO, PERFIL_DE_RISCO, PRODUTOR_RURAL,
            DT_CADASTRO
        )
        VALUES (?, ?, ?, 'A', CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_DATE)
        RETURNING ID_CLIENTE
        """,
        (
            nome,
            ID_PAIS_PADRAO,
            id_cidade,
            end["END_TIPO"],
            ddd,
            fone,
            end["END_CEP"],
            end["END_LOGRAD"],
            end["END_NUMERO"],
            end["END_BAIRRO"],
            end["END_COMPLE"],
            obs,
            defaults.get("EMPCADASTRO"),
            defaults["LIMITE"],
            defaults["DT_MELHOR_VENCTO"],
            defaults["PERFIL_DE_RISCO"],
            defaults["PRODUTOR_RURAL"],
        ),
    )
    id_cliente = cur.fetchone()[0]

    if doc_digitos:
        if len(doc_digitos) <= 11:
            cur.execute(
                "INSERT INTO TB_CLI_PF (ID_CLIENTE, CPF) VALUES (?, ?)",
                (id_cliente, doc_db),
            )
        else:
            cur.execute(
                "INSERT INTO TB_CLI_PJ (ID_CLIENTE, CNPJ) VALUES (?, ?)",
                (id_cliente, doc_db),
            )

    _garantir_cliente_pronto_clipp(cur, id_cliente)

    return id_cliente


def resolver_cliente(cur, pedido: PedidoExtraido) -> tuple[int, bool, dict | None]:
    cli = pedido.cliente
    id_cliente = buscar_cliente(
        cur,
        nome=cli.get("nome", ""),
        telefone=cli.get("telefone", ""),
        documento=cli.get("documento", ""),
    )
    if id_cliente and cli.get("documento"):
        if not _cliente_documento_confere(cur, int(id_cliente), cli["documento"]):
            id_cliente = None
    if id_cliente:
        alteracoes = _completar_cliente(cur, id_cliente, pedido)
        _garantir_documento_cliente(cur, id_cliente, pedido)
        return id_cliente, False, alteracoes
    return cadastrar_cliente(cur, pedido), True, None


def resolver_cliente_pedido(cur, pedido: PedidoExtraido) -> tuple[int, bool, dict | None]:
    """Usa ID_CLIENTE já gravado na leitura do PDF ou busca/cadastra agora."""
    id_existente = pedido.cliente.get("id_cliente")
    if id_existente:
        id_cliente = int(id_existente)
        cur.execute(
            "SELECT ID_CLIENTE FROM TB_CLIENTE WHERE ID_CLIENTE = ?",
            (id_cliente,),
        )
        if cur.fetchone():
            alteracoes = _completar_cliente(cur, id_cliente, pedido)
            _garantir_documento_cliente(cur, id_cliente, pedido)
            return id_cliente, False, alteracoes
    return resolver_cliente(cur, pedido)


def _log_v_clientes(
    cur,
    id_cliente: int,
    on_log: Callable[[str], None] | None = None,
) -> None:
    """Confere o que V_CLIENTES expõe — mesma fonte do lookup do CLIPP."""
    if not on_log:
        return
    try:
        cur.execute(
            """
            SELECT TRIM(NOME), TRIM(CPF), TRIM(CPF_CNPJ), TRIM(STATUS),
                   TRIM(CIDADE), TRIM(UF), TRIM(DESCRICAO), DT_CADASTRO
            FROM V_CLIENTES
            WHERE ID_CLIENTE = ?
            """,
            (int(id_cliente),),
        )
        row = cur.fetchone()
    except fdb.Error as exc:
        on_log(f"  V_CLIENTES: consulta falhou ({exc})")
        return

    if not row:
        on_log(f"  V_CLIENTES: cliente #{id_cliente} sem linha na view")
        return

    nome, cpf, cpf_cnpj, status, cidade, uf, tipo, dt_cad = row
    on_log(
        f"  V_CLIENTES: NOME={nome or '—'}, CPF={cpf or '—'}, "
        f"CPF_CNPJ={cpf_cnpj or '—'}, STATUS={status or '—'}"
    )
    if cidade or uf:
        on_log(f"  V_CLIENTES: CIDADE={cidade or '—'}/{uf or '—'}")
    if tipo:
        on_log(f"  V_CLIENTES: TIPO={tipo}")
    if dt_cad:
        on_log(f"  V_CLIENTES: DT_CADASTRO={dt_cad}")


def preparar_cliente_no_banco(db_config: dict, pedido: PedidoExtraido) -> tuple[int, bool]:
    """
    Na leitura do PDF: busca ou cadastra cliente, commit e encerra conexão.
    Retorna (id_cliente, cliente_novo).
    """
    id_cliente, cliente_novo, alteracoes = importar_cliente_fase(db_config, pedido)
    pedido.cliente["id_cliente"] = int(id_cliente)
    pedido.cliente["cliente_novo"] = cliente_novo
    if alteracoes:
        pedido.cliente["cliente_alteracoes"] = alteracoes
    return id_cliente, cliente_novo


def validar_cliente_para_importacao(
    db_config: dict,
    pedido: PedidoExtraido,
    on_log: Callable[[str], None] | None = None,
) -> int:
    """
    Antes da venda: valida o cliente e confirma transação (padrão IBConsole).
    Conexão nova — equivalente ao Post + Commit retaining do IBConsole no cliente,
    para o CLIPP enxergar nome/CPF ao abrir a saída pendente.
    """

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    id_cliente = pedido.cliente.get("id_cliente")
    if not id_cliente:
        raise RuntimeError(
            "Cliente não vinculado. Leia o PDF antes de importar "
            "(a leitura cadastra ou localiza o cliente e grava no banco)."
        )

    id_cliente = int(id_cliente)
    con = conectar(db_config)
    cur = con.cursor()
    try:
        log("Pré-venda: confirmando cliente (conexão nova, sem transação pendente)...")
        cur.execute(
            """
            SELECT TRIM(c.NOME), c.END_TIPO, pf.CPF, pj.CNPJ
            FROM TB_CLIENTE c
            LEFT JOIN TB_CLI_PF pf ON pf.ID_CLIENTE = c.ID_CLIENTE
            LEFT JOIN TB_CLI_PJ pj ON pj.ID_CLIENTE = c.ID_CLIENTE
            WHERE c.ID_CLIENTE = ?
            """,
            (id_cliente,),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(
                f"Cliente #{id_cliente} não encontrado. Leia o PDF novamente."
            )
        nome = str(row[0] or "").strip()
        if not nome:
            raise RuntimeError(
                f"Cliente #{id_cliente} sem nome no banco. Leia o PDF novamente."
            )
        doc = str(row[2] or row[3] or "").strip()
        log(f"  Cliente #{id_cliente} ({nome}) — OK no banco")
        if doc:
            log(f"  CPF/CNPJ={doc}")

        _touch_registros_clipp(cur, id_cliente=id_cliente)
        _processo_commit_ibconsole(con, log, contexto="pré-venda cliente")
        _log_v_clientes(cur, id_cliente, on_log=on_log)
        log("  Cliente confirmado — pronto para gravar venda")
        return id_cliente
    except Exception:
        try:
            con.rollback()
        except fdb.Error:
            pass
        raise
    finally:
        cur.close()
        _fechar_conexao(con)


def importar_cliente_fase(
    db_config: dict,
    pedido: PedidoExtraido,
    on_log: Callable[[str], None] | None = None,
) -> tuple[int, bool, dict | None]:
    """Fase 1: grava cliente, commit e fecha a transação/conexão."""

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = conectar(db_config)
    cur = con.cursor()
    ok = False
    id_cliente: int | None = None
    try:
        log("Leitura: gravando cliente (TB_CLIENTE + CPF/CNPJ)...")
        id_cliente, cliente_novo, alteracoes = resolver_cliente_pedido(cur, pedido)
        id_cliente = int(id_cliente)
        pedido.cliente["id_cliente"] = id_cliente
        _garantir_cliente_pronto_clipp(cur, id_cliente)
        _finalizar_gravacao_visivel_clipp(
            con,
            cur,
            log,
            id_cliente=id_cliente,
            contexto="TB_CLIENTE (leitura)",
        )
        ok = True

        cur.execute(
            """
            SELECT c.NOME, c.END_TIPO, c.EMPCADASTRO, pf.CPF
            FROM TB_CLIENTE c
            LEFT JOIN TB_CLI_PF pf ON pf.ID_CLIENTE = c.ID_CLIENTE
            WHERE c.ID_CLIENTE = ?
            """,
            (id_cliente,),
        )
        row = cur.fetchone()
        nome_db = str(row[0] or "").strip() if row else ""
        end_tipo = str(row[1] or "").strip() if row else ""
        emp = str(row[2] or "").strip() if row else ""
        cpf_db = str(row[3] or "").strip() if row else ""
        nome_pdf = (pedido.cliente.get("nome") or "").strip()
        log(
            f"  Cliente #{id_cliente} gravado"
            f" ({nome_db or nome_pdf or 'sem nome'}) — transação encerrada"
        )
        if end_tipo:
            log(f"  END_TIPO={end_tipo}")
        if emp:
            log(f"  EMPCADASTRO={emp}")
        if cpf_db:
            log(f"  CPF/CNPJ={cpf_db}")
        _log_v_clientes(cur, id_cliente, on_log=on_log)
        return id_cliente, cliente_novo, alteracoes
    except Exception:
        try:
            con.rollback()
        except fdb.Error:
            pass
        raise
    finally:
        cur.close()
        _fechar_conexao(con)
        if ok and id_cliente is not None:
            _pulso_visibilidade_clipp(
                db_config,
                id_cliente=id_cliente,
                on_log=on_log,
            )


def importar_pedido_fase(
    db_config: dict,
    pedido: PedidoExtraido,
    nfvenda_cfg: dict | None = None,
    venda_cfg: dict | None = None,
    on_log: Callable[[str], None] | None = None,
) -> tuple[int, int, int, bool, dict | None, list[int], list[str]]:
    """
    Cliente + venda + itens na MESMA transação (como INSERT manual no IBExpert).
    Retorna (id_venda, nf_numero, id_cliente, cliente_novo, alteracoes, ids_itens, nao_encontrados).
    """

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = conectar(db_config)
    cur = con.cursor()
    ok = False
    id_venda: int | None = None
    id_cliente: int | None = None
    cliente_novo = False
    alteracoes: dict | None = None
    try:
        if not (pedido.cliente.get("nome") or "").strip():
            raise RuntimeError(
                "Cliente sem nome no PDF. Leia o PDF antes de importar."
            )

        log(
            "Importando pedido — cliente + venda + itens "
            "(uma transação, commit único como IBExpert)..."
        )

        sem_vinculo: list[ItemFaltante] = []
        if any(
            not it.id_identificador and ((it.referencia or "").strip() or it.sku)
            for it in pedido.itens
        ):
            sem_vinculo = vincular_produtos_pedido(con, pedido)
        if sem_vinculo:
            log(
                f"Aviso: {len(sem_vinculo)} referência(s) não vinculada(s) "
                f"antes da gravação."
            )

        id_cliente, cliente_novo, alteracoes = resolver_cliente_pedido(cur, pedido)
        id_cliente = int(id_cliente)
        pedido.cliente["id_cliente"] = id_cliente
        pedido.cliente["cliente_novo"] = cliente_novo
        if alteracoes:
            pedido.cliente["cliente_alteracoes"] = alteracoes

        _garantir_cliente_pronto_clipp(cur, id_cliente)

        cur.execute(
            """
            SELECT TRIM(c.NOME), pf.CPF, pj.CNPJ
            FROM TB_CLIENTE c
            LEFT JOIN TB_CLI_PF pf ON pf.ID_CLIENTE = c.ID_CLIENTE
            LEFT JOIN TB_CLI_PJ pj ON pj.ID_CLIENTE = c.ID_CLIENTE
            WHERE c.ID_CLIENTE = ?
            """,
            (id_cliente,),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Cliente #{id_cliente} não encontrado após gravar.")
        nome_cli = str(row[0] or "").strip()
        doc_cli = str(row[1] or row[2] or "").strip()
        log(
            f"  Cliente #{id_cliente} ({nome_cli or 'sem nome'})"
            + (f" — {doc_cli}" if doc_cli else "")
            + (" — novo" if cliente_novo else " — existente")
        )
        if alteracoes and "NOME" in alteracoes:
            antigo = (alteracoes.get("NOME") or "").strip() or "—"
            log(f"  Nome atualizado no cadastro: «{antigo}» → «{nome_cli}»")
        _log_v_clientes(cur, id_cliente, on_log=on_log)

        _, vlr_frete, vlr_desconto, _ = _totais_venda(pedido)
        id_venda, nf_numero = inserir_venda(
            cur, id_cliente, pedido, nfvenda_cfg, venda_cfg
        )
        if vlr_frete:
            log(f"  Frete (VLR_BC_FRETE): R$ {vlr_frete:.2f}")
        if vlr_desconto:
            log(f"  Desconto: R$ {vlr_desconto:.2f}")
        vend = venda_cfg or {}
        log(
            f"  Venda #{id_venda} (NF nº {nf_numero}) — "
            f"ID_CLIENTE={id_cliente}, "
            f"ID_VENDEDOR={vend.get('id_vendedor', 17)}, "
            f"XX_VENDEDOR={vend.get('xx_vendedor', 17)}"
        )

        nao_encontrados, ultimo_id_item, ids_itens = inserir_itens(
            cur, id_venda, pedido.itens, on_log=on_log
        )

        if nao_encontrados and len(nao_encontrados) == len(pedido.itens):
            con.rollback()
            raise RuntimeError(
                "Venda não gravada: nenhum item com referência cadastrada em "
                "TB_EST_PRODUTO_2 / V_ESTOQUE_2. Cadastre os produtos no CLIPP "
                "ou confira se o idioma da carta (EN/PT) bate com o estoque."
            )

        if nao_encontrados:
            _logar_itens_faltantes(nao_encontrados, on_log=on_log)
            try:
                import schema_app

                qtd = schema_app.gravar_itens_faltantes_import(
                    cur,
                    nao_encontrados,
                    numero_pedido=pedido.numero_pedido or pedido.arquivo,
                    id_venda=id_venda,
                    nf_numero=nf_numero,
                )
                if qtd:
                    log(f"  {qtd} item(ns) faltante(s) registrados em {schema_app.TABELA_ERRO}")
            except Exception as exc:
                log(f"  Aviso: não foi possível gravar faltantes no banco: {exc}")

        total_gravado, total_nota = _sincronizar_totais_venda(
            cur, id_venda, vlr_frete, vlr_desconto
        )
        log(
            f"  Totais da venda: produtos R$ {total_gravado:.2f} "
            f"+ frete R$ {vlr_frete:.2f}"
            + (f" - desconto R$ {vlr_desconto:.2f}" if vlr_desconto else "")
            + f" = R$ {total_nota:.2f}"
        )

        _finalizar_gravacao_visivel_clipp(
            con,
            cur,
            log,
            id_cliente=id_cliente,
            id_venda=id_venda,
            contexto="cliente + venda + itens",
        )
        ok = True
        log(
            f"  Pedido gravado — venda #{id_venda}, "
            f"{len(ids_itens)} item(ns), cliente #{id_cliente}"
        )
        return (
            id_venda,
            nf_numero,
            id_cliente,
            cliente_novo,
            alteracoes,
            ids_itens,
            nao_encontrados,
        )
    except Exception:
        try:
            con.rollback()
        except fdb.Error:
            pass
        raise
    finally:
        cur.close()
        _fechar_conexao(con)
        if ok and id_venda is not None and len(pedido.itens) <= 120:
            _pulso_visibilidade_clipp(
                db_config,
                id_cliente=int(id_cliente) if id_cliente else None,
                id_venda=id_venda,
                on_log=on_log,
            )


def _criar_registro_desfazer(
    arquivo: str,
    *,
    id_cliente: int | None,
    cliente_novo: bool,
    cliente_alteracoes: dict | None,
    id_venda: int | None,
    ids_itens: list[int] | None,
    nf_numero: int | None,
    nfvenda_cfg: dict | None,
) -> RegistroDesfazer | None:
    reg = RegistroDesfazer(
        arquivo=arquivo,
        id_cliente=id_cliente,
        cliente_novo=cliente_novo,
        cliente_alteracoes=cliente_alteracoes,
        id_venda=id_venda,
        ids_itens=ids_itens or [],
        nf_numero=nf_numero,
        nfvenda_cfg=nfvenda_cfg,
    )
    return reg if reg.pode_desfazer else None


def consultar_venda_clipp(cur, id_nfvenda: int) -> dict | None:
    cur.execute(
        """
        SELECT ID_NFVENDA, TRIM(FIM), TRIM(STATUS), TRIM(OBS)
        FROM TB_NFVENDA_2
        WHERE ID_NFVENDA = ?
        """,
        (int(id_nfvenda),),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id_nfvenda": int(row[0]),
        "fim": str(row[1] or "").strip(),
        "status": str(row[2] or "").strip(),
        "obs": str(row[3] or "").strip(),
    }


def consultar_venda_por_numero_pedido(cur, numero: str) -> dict | None:
    vendas = listar_vendas_por_numero_pedido(cur, numero)
    return vendas[0] if vendas else None


def listar_vendas_por_numero_pedido(cur, numero: str) -> list[dict]:
    num = str(numero).strip().lstrip("#")
    cur.execute(
        """
        SELECT ID_NFVENDA, TRIM(FIM), TRIM(STATUS), TRIM(OBS)
        FROM TB_NFVENDA_2
        WHERE OBS CONTAINING ?
        ORDER BY ID_NFVENDA DESC
        """,
        (num,),
    )
    out: list[dict] = []
    for row in cur.fetchall():
        out.append(
            {
                "id_nfvenda": int(row[0]),
                "fim": str(row[1] or "").strip(),
                "status": str(row[2] or "").strip(),
                "obs": str(row[3] or "").strip(),
            }
        )
    return out


def venda_esta_cancelada(venda: dict | None) -> bool:
    """FIM cancelado ou STATUS = C / X → permite reimportar o pedido."""
    if not venda:
        return False
    fim = (venda.get("fim") or "").strip().upper()
    status = (venda.get("status") or "").strip().upper()
    if fim == "CANCELADA" or "CANCEL" in fim:
        return True
    return status in ("C", "X")


def _buscar_venda_ativa_pedido(
    cur, numero: str, id_controle: int | None = None
) -> dict | None:
    """Venda não cancelada ligada ao pedido (OBS ou id do controle local)."""
    for venda in listar_vendas_por_numero_pedido(cur, numero):
        if not venda_esta_cancelada(venda):
            return venda
    if id_controle:
        venda = consultar_venda_clipp(cur, int(id_controle))
        if venda and not venda_esta_cancelada(venda):
            return venda
    return None


def sincronizar_controle_pedido_clipp(db_cfg: dict, numero: str) -> str | None:
    """
    Se não houver venda ativa no CLIPP, remove o pedido do controle local.
    Retorna mensagem de log ou None.
    """
    import rpa_tiaocards as rpa

    numero = str(numero).strip().lstrip("#")
    registro = rpa.obter_registro_controle(numero)
    if not registro:
        return None

    id_controle = registro.get("id_nfvenda")
    id_controle_int = int(id_controle) if id_controle else None

    con = conectar(db_cfg)
    cur = con.cursor()
    try:
        venda_ativa = _buscar_venda_ativa_pedido(cur, numero, id_controle_int)
        if venda_ativa is not None:
            return None

        rpa.remover_pedido_controle(numero)
        if id_controle_int:
            v_reg = consultar_venda_clipp(cur, id_controle_int)
            if v_reg is None:
                return (
                    f"Controle local liberado — venda #{id_controle_int} "
                    "não existe mais no CLIPP."
                )
            if venda_esta_cancelada(v_reg):
                return (
                    f"Controle local liberado — venda #{id_controle_int} "
                    "cancelada no CLIPP."
                )

        vendas = listar_vendas_por_numero_pedido(cur, numero)
        if vendas:
            ids = ", ".join(f"#{v['id_nfvenda']}" for v in vendas)
            return f"Controle local liberado — venda(s) {ids} cancelada(s) no CLIPP."
        return "Controle local liberado — nenhuma venda ativa no CLIPP."
    finally:
        cur.close()
        _fechar_conexao(con)


def mensagem_bloqueio_reimportacao(
    db_cfg: dict,
    numero: str,
    id_controle: int | None = None,
    *,
    tem_controle_local: bool = False,
) -> str | None:
    """
    None = pode importar (pedido novo ou venda anterior cancelada).
    str = mensagem quando deve ignorar (venda ainda ativa no CLIPP).
    """
    numero = str(numero).strip().lstrip("#")

    con = conectar(db_cfg)
    cur = con.cursor()
    try:
        venda_ativa = _buscar_venda_ativa_pedido(cur, numero, id_controle)
        if not venda_ativa:
            return None
        id_v = venda_ativa["id_nfvenda"]
        if tem_controle_local:
            return f"Pedido #{numero} já importado (venda #{id_v} ativa no CLIPP)."
        return f"Pedido #{numero} já no CLIPP (venda #{id_v})."
    finally:
        cur.close()
        _fechar_conexao(con)


def _item_faltante_de_pedido(item: ItemPedido) -> ItemFaltante:
    return ItemFaltante(
        referencia=(item.referencia or item.referencia_original or "").strip(),
        referencia_site=(item.referencia_original or item.referencia or "").strip(),
        quantidade=int(item.quantidade or 1),
        preco_unitario=float(item.preco_unitario or 0),
        descricao=(item.descricao or "").strip(),
        idioma=item.idioma,
        raridade=item.raridade,
    )


def _logar_itens_faltantes(
    faltantes: list[ItemFaltante],
    on_log: Callable[[str], None] | None = None,
) -> None:
    if not faltantes or not on_log:
        return
    qtd = sum(f.quantidade for f in faltantes)
    total = sum(f.preco_total for f in faltantes)
    on_log(
        f"Cartas faltantes ({len(faltantes)} linha(s), {qtd} un.) "
        f"— lance manualmente no CLIPP:"
    )
    for i, faltante in enumerate(faltantes, 1):
        on_log(f" {i}.{faltante.linha_log().strip()}")
    on_log(f"  Subtotal pendente: R$ {total:.2f}")


def _moeda_br(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_itens_faltantes_notepad(
    faltantes: list[ItemFaltante],
    *,
    numero_pedido: str = "",
    id_venda: int | None = None,
    nf_numero: int | None = None,
) -> str:
    linhas = ["Cartas faltantes — lance manualmente no CLIPP", ""]
    pedido = str(numero_pedido or "").strip().lstrip("#")
    if pedido:
        linhas.append(f"Pedido site: #{pedido}")
    if id_venda:
        linhas.append(f"Venda CLIPP: #{id_venda}")
    if nf_numero:
        linhas.append(f"NF: {nf_numero}")
    if pedido or id_venda or nf_numero:
        linhas.append("")

    linhas.append(
        f"{'Carta':<28} {'Qtd':>4}  {'Unitário':>12}  {'Total':>12}"
    )
    linhas.append("-" * 62)
    for faltante in faltantes:
        carta = (faltante.referencia or faltante.referencia_site or "").strip()
        nome = (faltante.descricao or "").strip()
        if nome and nome.upper() not in carta.upper():
            carta = f"{carta} — {nome}"[:60]
        linhas.append(
            f"{carta:<28} {faltante.quantidade:>4}  "
            f"{_moeda_br(faltante.preco_unitario):>12}  "
            f"{_moeda_br(faltante.preco_total):>12}"
        )

    subtotal = sum(f.preco_total for f in faltantes)
    qtd = sum(f.quantidade for f in faltantes)
    linhas.extend(
        [
            "",
            f"Linhas: {len(faltantes)} | Unidades: {qtd} | Subtotal pendente: {_moeda_br(subtotal)}",
        ]
    )
    return "\r\n".join(linhas) + "\r\n"


def abrir_bloco_notas_faltantes(
    faltantes: list[ItemFaltante],
    *,
    numero_pedido: str = "",
    id_venda: int | None = None,
    nf_numero: int | None = None,
    on_log: Callable[[str], None] | None = None,
) -> Path | None:
    """Grava lista de faltantes e abre no Bloco de Notas (Windows)."""
    if not faltantes:
        return None

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    try:
        from config import dados_dir

        pasta = dados_dir() / "faltantes"
        pasta.mkdir(parents=True, exist_ok=True)
        pedido = re.sub(r"[^\w\-#]", "_", str(numero_pedido or "pedido").strip())
        pedido = pedido.lstrip("#") or "pedido"
        if id_venda:
            nome = f"pedido_{pedido}_venda_{id_venda}_faltantes.txt"
        else:
            nome = f"pedido_{pedido}_faltantes.txt"
        arquivo = pasta / nome
        arquivo.write_text(
            "\ufeff"
            + formatar_itens_faltantes_notepad(
                faltantes,
                numero_pedido=numero_pedido,
                id_venda=id_venda,
                nf_numero=nf_numero,
            ),
            encoding="utf-8",
        )
        if sys.platform == "win32":
            subprocess.Popen(
                ["notepad.exe", str(arquivo)],
                close_fds=True,
            )
            log(f"Bloco de Notas aberto — {arquivo.name}")
        else:
            log(f"Lista de faltantes salva em: {arquivo}")
        return arquivo
    except Exception as exc:
        log(f"Não foi possível abrir o Bloco de Notas: {exc}")
        return None


def _detalhe_de_row_estoque(row) -> dict:
    return {
        "id_identificador": int(row[0]),
        "prod_serv": str(row[1] or "").strip()[:60],
        "prc_venda": float(row[2] or 0),
    }


def _escolher_detalhe_rows(
    rows: list,
    preco_alvo: float | None,
) -> dict | None:
    if not rows:
        return None
    if preco_alvo and preco_alvo > 0 and len(rows) > 1:
        escolhido = min(
            rows,
            key=lambda r: abs(float(r[3] or 0) - preco_alvo),
        )
        if abs(float(escolhido[3] or 0) - preco_alvo) <= 0.05:
            return _detalhe_de_row_estoque(escolhido[:3])
    return _detalhe_de_row_estoque(rows[0][:3])


def _buscar_detalhe_referencia_sufixo(
    cur,
    ref: str,
    *,
    id_grupo: int | None = None,
    preco_alvo: float | None = None,
) -> dict | None:
    """Reprint CLIPP (ex.: 2025RP02-EN003) termina com a ref do site."""
    ref_u = (ref or "").strip().upper()
    if not ref_u:
        return None
    sql = """
        SELECT FIRST 20 ID_IDENTIFICADOR, PROD_SERV, PRC_VENDA, PRC_VENDA
        FROM V_ESTOQUE_2
        WHERE UPPER(TRIM(REFERENCIA)) LIKE ?
    """
    params: list = [f"%{ref_u}"]
    if id_grupo is not None:
        sql += " AND ID_GRUPO = ?"
        params.append(int(id_grupo))
    sql += " ORDER BY ID_IDENTIFICADOR"
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        return None
    det = _escolher_detalhe_rows(rows, preco_alvo)
    if not det:
        return None
    cur.execute(
        "SELECT FIRST 1 UPPER(TRIM(REFERENCIA)) FROM V_ESTOQUE_2 WHERE ID_IDENTIFICADOR = ?",
        (det["id_identificador"],),
    )
    row = cur.fetchone()
    if row:
        det["_referencia_clipp"] = str(row[0])
    return det


def _buscar_detalhe_por_nome_referencia(
    cur,
    ref: str,
    descricao: str | None = None,
    *,
    id_grupo: int | None = None,
    preco_alvo: float | None = None,
) -> dict | None:
    """Descrição CLIPP costuma ser «NOME DA CARTA-REF» (ex.: LIGHTFORCE SWORD-RP02-EN003)."""
    ref_u = (ref or "").strip().upper()
    if not ref_u:
        return None
    padroes: list[str] = [f"%-{ref_u}", f"%{ref_u}"]
    nome = (descricao or "").strip().upper()
    if nome and ref_u in nome:
        padroes.insert(0, nome)
    for padrao in padroes:
        sql = """
            SELECT FIRST 20 ID_IDENTIFICADOR, PROD_SERV, PRC_VENDA, PRC_VENDA
            FROM V_ESTOQUE_2
            WHERE UPPER(TRIM(PROD_SERV)) LIKE ?
        """
        params: list = [padrao]
        if id_grupo is not None:
            sql += " AND ID_GRUPO = ?"
            params.append(int(id_grupo))
        sql += " ORDER BY ID_IDENTIFICADOR"
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            continue
        det = _escolher_detalhe_rows(rows, preco_alvo)
        if not det:
            continue
        cur.execute(
            "SELECT FIRST 1 UPPER(TRIM(REFERENCIA)) FROM V_ESTOQUE_2 WHERE ID_IDENTIFICADOR = ?",
            (det["id_identificador"],),
        )
        row = cur.fetchone()
        if row:
            det["_referencia_clipp"] = str(row[0])
        return det
    return None


def buscar_produto(
    cur,
    referencia: str,
    referencia_original: str | None = None,
    *,
    idioma: str | None = None,
    raridade: str | None = None,
    sku: str | None = None,
    clipp_cfg: dict | None = None,
) -> int | None:
    det = buscar_produto_detalhe(
        cur,
        referencia,
        referencia_original=referencia_original,
        idioma=idioma,
        raridade=raridade,
        sku=sku,
        clipp_cfg=clipp_cfg,
    )
    return det["id_identificador"] if det else None


def _candidatos_referencia_produto(
    referencia: str,
    referencia_original: str | None = None,
    *,
    idioma: str | None = None,
) -> list[str]:
    """Ordem: convertida, original; EN↔PT só se idioma não estiver definido."""
    vistos: set[str] = set()
    candidatos: list[str] = []

    def add(valor: str | None) -> None:
        u = (valor or "").strip().upper()
        if not u or u in vistos:
            return
        vistos.add(u)
        candidatos.append(u)
        compacto = re.sub(r"[^A-Z0-9]", "", u)
        if compacto and compacto not in vistos:
            vistos.add(compacto)
            candidatos.append(compacto)

    add(referencia)
    add(referencia_original)
    idioma_u = (idioma or "").upper()
    if idioma_u == "PT":
        candidatos[:] = [c for c in candidatos if "-EN" not in c]
    elif idioma_u == "EN":
        candidatos[:] = [c for c in candidatos if "-PT" not in c]
    elif not idioma_u:
        for u in list(candidatos):
            if "-PT" in u:
                add(u.replace("-PT", "-EN", 1))
            elif "-EN" in u:
                add(u.replace("-EN", "-PT", 1))
    return candidatos


def _id_grupo2_por_raridade(
    cur,
    id_grupo: int,
    raridade: str,
    cache: dict[tuple[int, str], int | None],
) -> int | None:
    norm = _normalizar_raridade(raridade)
    chave = (id_grupo, norm)
    if chave in cache:
        return cache[chave]

    cur.execute(
        """
        SELECT FIRST 1 ID
        FROM TB_EST_GRUPO_SUB
        WHERE ID_GRUPO = ?
          AND UPPER(TRIM(DESCRICAO)) = ?
        """,
        (id_grupo, norm),
    )
    row = cur.fetchone()
    if row:
        cache[chave] = int(row[0])
        return cache[chave]

    cur.execute(
        """
        SELECT FIRST 1 ID
        FROM TB_EST_GRUPO_SUB
        WHERE ID_GRUPO = ?
          AND UPPER(TRIM(DESCRICAO)) CONTAINING ?
        ORDER BY CHAR_LENGTH(DESCRICAO)
        """,
        (id_grupo, norm),
    )
    row = cur.fetchone()
    cache[chave] = int(row[0]) if row else None
    return cache[chave]


def _contar_produtos_ref(
    cur,
    ref: str,
    *,
    id_grupo: int | None = None,
    id_grupo2: int | None = None,
) -> int:
    sql = """
        SELECT COUNT(*)
        FROM V_ESTOQUE_2
        WHERE UPPER(TRIM(REFERENCIA)) = ?
    """
    params: list = [ref.upper()]
    if id_grupo is not None:
        sql += " AND ID_GRUPO = ?"
        params.append(id_grupo)
    if id_grupo2 is not None:
        sql += " AND ID_GRUPO2 = ?"
        params.append(id_grupo2)
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def _buscar_detalhe_v_estoque(
    cur,
    ref: str,
    *,
    id_grupo: int | None = None,
    id_grupo2: int | None = None,
) -> dict | None:
    sql = """
        SELECT FIRST 1 ID_IDENTIFICADOR, PROD_SERV, PRC_VENDA
        FROM V_ESTOQUE_2
        WHERE UPPER(TRIM(REFERENCIA)) = ?
    """
    params: list = [ref.upper()]
    if id_grupo is not None:
        sql += " AND ID_GRUPO = ?"
        params.append(id_grupo)
    if id_grupo2 is not None:
        sql += " AND ID_GRUPO2 = ?"
        params.append(id_grupo2)
    sql += " ORDER BY ID_IDENTIFICADOR"
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id_identificador": int(row[0]),
        "prod_serv": str(row[1] or "").strip()[:60],
        "prc_venda": float(row[2] or 0),
    }


def _buscar_detalhe_sku(cur, sku: str) -> dict | None:
    """Produto selado: SKU do site = REFERENCIA em TB_EST_PRODUTO_2."""
    sku_u = sku.strip().upper()
    if not sku_u:
        return None
    det = _buscar_detalhe_ref_exata(cur, sku_u)
    if det:
        det = dict(det)
        det["_referencia_clipp"] = sku_u
        return det
    cur.execute(
        """
        SELECT FIRST 1 ID_IDENTIFICADOR
        FROM TB_EST_PRODUTO_2
        WHERE UPPER(TRIM(REFERENCIA)) = ?
        ORDER BY ID_IDENTIFICADOR
        """,
        (sku_u,),
    )
    row = cur.fetchone()
    if row:
        det = _detalhe_produto_por_id(cur, int(row[0]))
        det["_referencia_clipp"] = sku_u
        return det
    return None


def _detalhe_produto_por_id(cur, id_prod: int) -> dict:
    cur.execute(
        """
        SELECT FIRST 1 PROD_SERV, PRC_VENDA
        FROM V_ESTOQUE_2
        WHERE ID_IDENTIFICADOR = ?
        """,
        (id_prod,),
    )
    det = cur.fetchone()
    if not det:
        return {
            "id_identificador": id_prod,
            "prod_serv": "",
            "prc_venda": 0.0,
        }
    return {
        "id_identificador": id_prod,
        "prod_serv": str(det[0] or "").strip()[:60],
        "prc_venda": float(det[1] or 0),
    }


def _buscar_detalhe_ref_exata(
    cur,
    ref: str,
    *,
    id_grupo: int | None = None,
    id_grupo2: int | None = None,
) -> dict | None:
    det = _buscar_detalhe_v_estoque(
        cur, ref, id_grupo=id_grupo, id_grupo2=id_grupo2
    )
    if det:
        return det

    cur.execute(
        """
        SELECT FIRST 1 ID_IDENTIFICADOR
        FROM TB_EST_PRODUTO_2
        WHERE UPPER(TRIM(REFERENCIA)) = ?
        """,
        (ref.upper(),),
    )
    row = cur.fetchone()
    if row:
        return _detalhe_produto_por_id(cur, int(row[0]))
    return None


def _ids_grupo2_do_grupo(
    cur,
    id_grupo: int,
    cache_listas: dict[int, list[int]] | None = None,
) -> list[int]:
    if cache_listas is not None and id_grupo in cache_listas:
        return cache_listas[id_grupo]
    cur.execute(
        "SELECT ID FROM TB_EST_GRUPO_SUB WHERE ID_GRUPO = ? ORDER BY ID",
        (int(id_grupo),),
    )
    ids = [int(r[0]) for r in cur.fetchall()]
    if cache_listas is not None:
        cache_listas[id_grupo] = ids
    return ids


def _escolher_detalhe_por_preco(
    matches: list[tuple[str, dict]],
    preco_alvo: float | None,
) -> tuple[str, dict] | None:
    if not matches:
        return None
    if preco_alvo and preco_alvo > 0:
        cand, det = min(
            matches,
            key=lambda x: abs(float(x[1].get("prc_venda") or 0) - preco_alvo),
        )
        diff = abs(float(det.get("prc_venda") or 0) - preco_alvo)
        if diff <= 0.05:
            return cand, det
    return matches[0]


def _coletar_detalhes_candidatos(
    cur,
    referencia: str,
    referencia_original: str | None,
    *,
    idioma: str | None,
    id_grupo: int | None,
    id_grupo2: int | None,
    usa_grupo2: bool,
    cache_grupo2_listas: dict[int, list[int]] | None = None,
) -> list[tuple[str, dict]]:
    matches: list[tuple[str, dict]] = []
    for cand in _candidatos_referencia_produto(
        referencia, referencia_original, idioma=idioma
    ):
        if usa_grupo2 and id_grupo is not None:
            grupo2_ids = (
                [int(id_grupo2)]
                if id_grupo2
                else _ids_grupo2_do_grupo(
                    cur, int(id_grupo), cache_listas=cache_grupo2_listas
                )
            )
            for g2 in grupo2_ids:
                det = _buscar_detalhe_ref_exata(
                    cur,
                    cand,
                    id_grupo=int(id_grupo),
                    id_grupo2=int(g2),
                )
                if det:
                    matches.append((cand, det))
            continue

        if id_grupo2 is not None and id_grupo is not None:
            det = _buscar_detalhe_ref_exata(
                cur,
                cand,
                id_grupo=int(id_grupo),
                id_grupo2=int(id_grupo2),
            )
        elif id_grupo is not None and usa_grupo2:
            det = _buscar_detalhe_ref_exata(cur, cand, id_grupo=int(id_grupo))
            if det and _contar_produtos_ref(
                cur, cand, id_grupo=int(id_grupo)
            ) > 1:
                det = None
        else:
            det = _buscar_detalhe_ref_exata(cur, cand)

        if det:
            matches.append((cand, det))
    return matches


def _idioma_da_referencia(ref: str) -> str | None:
    ref_u = (ref or "").upper()
    for lang, token in (("PT", "-PT"), ("EN", "-EN"), ("FR", "-FR")):
        if token in ref_u:
            return lang
    return None


def _aplicar_referencia_encontrada(item: ItemPedido, ref_encontrada: str) -> None:
    ref = ref_encontrada.strip().upper()
    if not ref:
        return
    item.referencia = ref
    idioma = _idioma_da_referencia(ref)
    if idioma:
        item.idioma = idioma


def buscar_produto_detalhe(
    cur,
    referencia: str,
    cache: dict[str, dict | None] | None = None,
    referencia_original: str | None = None,
    *,
    idioma: str | None = None,
    raridade: str | None = None,
    sku: str | None = None,
    descricao: str | None = None,
    clipp_cfg: dict | None = None,
    cache_grupo2: dict[tuple[int, str], int | None] | None = None,
    preco_alvo: float | None = None,
    cache_grupo2_listas: dict[int, list[int]] | None = None,
    _sem_filtro_raridade: bool = False,
) -> dict | None:
    """Busca produto em V_ESTOQUE_2 / TB_EST_PRODUTO_2 com filtros de idioma e raridade."""
    if clipp_cfg is None:
        from config import get_clipp_config

        clipp_cfg = get_clipp_config()

    sku_u = (sku or "").strip().upper()
    if sku_u:
        if cache is not None and sku_u in cache:
            return cache[sku_u]
        det = _buscar_detalhe_sku(cur, sku_u)
        if cache is not None:
            cache[sku_u] = det
        if det:
            return det
        ref_u = (referencia or "").strip().upper()
        if not ref_u or ref_u == sku_u:
            return None

    ref = (referencia or "").strip().upper()
    if not ref:
        return None

    if cache is not None and ref in cache:
        return cache[ref]

    id_grupo = clipp_cfg.get("id_grupo_yugioh")
    sets_grupo2 = clipp_cfg.get("sets_grupo2")

    if idioma == "PT" and "-EN" in ref:
        referencia = ref.replace("-EN", "-PT", 1)
        ref = referencia.upper()
    elif idioma == "EN" and "-PT" in ref:
        referencia = ref.replace("-PT", "-EN", 1)
        ref = referencia.upper()
    elif idioma and ref == (referencia_original or ref).upper():
        from parser_pedido import montar_referencia_clipp

        ref_conv = montar_referencia_clipp(
            referencia_original or referencia,
            idioma,
            raridade=raridade,
            sets_grupo2=sets_grupo2,
            sets_legacy_pt_prefix=clipp_cfg.get("sets_legacy_pt_prefix"),
        )
        if ref_conv.upper() != ref:
            referencia = ref_conv
            ref = referencia.upper()

    usa_grupo2 = set_usa_grupo2(ref, sets_grupo2)
    id_grupo2 = None
    if raridade and id_grupo is not None and not _sem_filtro_raridade:
        if cache_grupo2 is None:
            cache_grupo2 = {}
        id_grupo2 = _id_grupo2_por_raridade(
            cur, int(id_grupo), raridade, cache_grupo2
        )

    if usa_grupo2 and id_grupo is not None:
        matches = _coletar_detalhes_candidatos(
            cur,
            referencia,
            referencia_original,
            idioma=idioma,
            id_grupo=int(id_grupo),
            id_grupo2=id_grupo2,
            usa_grupo2=True,
            cache_grupo2_listas=cache_grupo2_listas,
        )
        escolhido = _escolher_detalhe_por_preco(matches, preco_alvo)
        if escolhido:
            cand, det = escolhido
            det = dict(det)
            det["_referencia_clipp"] = cand
            if cache is not None:
                cache[ref] = det
                cache[cand] = det
            return det
        return _buscar_produto_detalhe_fallback(
            cur,
            referencia,
            referencia_original,
            cache=cache,
            idioma=idioma,
            raridade=raridade,
            descricao=descricao,
            clipp_cfg=clipp_cfg,
            cache_grupo2=cache_grupo2,
            preco_alvo=preco_alvo,
            cache_grupo2_listas=cache_grupo2_listas,
            ref_cache=ref,
        )

    matches = _coletar_detalhes_candidatos(
        cur,
        referencia,
        referencia_original,
        idioma=idioma,
        id_grupo=int(id_grupo) if id_grupo is not None else None,
        id_grupo2=id_grupo2,
        usa_grupo2=bool(usa_grupo2 and id_grupo is not None),
        cache_grupo2_listas=cache_grupo2_listas,
    )
    if not matches:
        # fallback legado com cache por candidato
        for cand in _candidatos_referencia_produto(
            referencia, referencia_original, idioma=idioma
        ):
            if cache is not None and cand in cache:
                det = cache[cand]
                if det:
                    if cand != ref:
                        cache[ref] = det
                    return det
                continue

            if id_grupo2 is not None and id_grupo is not None:
                det = _buscar_detalhe_ref_exata(
                    cur,
                    cand,
                    id_grupo=int(id_grupo),
                    id_grupo2=id_grupo2,
                )
            elif id_grupo is not None and usa_grupo2:
                det = _buscar_detalhe_ref_exata(cur, cand, id_grupo=int(id_grupo))
                if det and _contar_produtos_ref(
                    cur, cand, id_grupo=int(id_grupo)
                ) > 1:
                    det = None
            else:
                det = _buscar_detalhe_ref_exata(cur, cand)

            if cache is not None:
                cache[cand] = det
            if det:
                if cand != ref:
                    cache[ref] = det
                return det

        return _buscar_produto_detalhe_fallback(
            cur,
            referencia,
            referencia_original,
            cache=cache,
            idioma=idioma,
            raridade=raridade,
            descricao=descricao,
            clipp_cfg=clipp_cfg,
            cache_grupo2=cache_grupo2,
            preco_alvo=preco_alvo,
            cache_grupo2_listas=cache_grupo2_listas,
            ref_cache=ref,
        )

    escolhido = _escolher_detalhe_por_preco(matches, preco_alvo)
    if not escolhido:
        return _buscar_produto_detalhe_fallback(
            cur,
            referencia,
            referencia_original,
            cache=cache,
            idioma=idioma,
            raridade=raridade,
            descricao=descricao,
            clipp_cfg=clipp_cfg,
            cache_grupo2=cache_grupo2,
            preco_alvo=preco_alvo,
            cache_grupo2_listas=cache_grupo2_listas,
            ref_cache=ref,
        )
    cand, det = escolhido
    det = dict(det)
    det["_referencia_clipp"] = cand
    if cache is not None:
        cache[cand] = det
        if cand != ref:
            cache[ref] = det
    return det


def _buscar_produto_detalhe_fallback(
    cur,
    referencia: str,
    referencia_original: str | None,
    *,
    cache: dict[str, dict | None] | None,
    idioma: str | None,
    raridade: str | None,
    descricao: str | None,
    clipp_cfg: dict | None,
    cache_grupo2: dict[tuple[int, str], int | None] | None,
    preco_alvo: float | None,
    cache_grupo2_listas: dict[int, list[int]] | None,
    ref_cache: str,
) -> dict | None:
    if cache is not None:
        cache.pop(ref_cache, None)
    if clipp_cfg is None:
        from config import get_clipp_config

        clipp_cfg = get_clipp_config()
    id_grupo = clipp_cfg.get("id_grupo_yugioh")

    if raridade:
        det = buscar_produto_detalhe(
            cur,
            referencia,
            cache,
            referencia_original=referencia_original,
            idioma=idioma,
            raridade=None,
            descricao=descricao,
            clipp_cfg=clipp_cfg,
            cache_grupo2=cache_grupo2,
            preco_alvo=preco_alvo,
            cache_grupo2_listas=cache_grupo2_listas,
            _sem_filtro_raridade=True,
        )
        if det:
            return det

    grupos_busca = (
        [int(id_grupo), None] if id_grupo is not None else [None]
    )

    for cand in _candidatos_referencia_produto(
        referencia, referencia_original, idioma=idioma
    ):
        for grupo_filtro in grupos_busca:
            det = _buscar_detalhe_referencia_sufixo(
                cur,
                cand,
                id_grupo=grupo_filtro,
                preco_alvo=preco_alvo,
            )
            if det:
                if cache is not None:
                    cache[ref_cache] = det
                    ref_clipp = det.get("_referencia_clipp")
                    if ref_clipp:
                        cache[str(ref_clipp).upper()] = det
                return det

    for grupo_filtro in grupos_busca:
        det = _buscar_detalhe_por_nome_referencia(
            cur,
            referencia,
            descricao,
            id_grupo=grupo_filtro,
            preco_alvo=preco_alvo,
        )
        if det:
            if cache is not None:
                cache[ref_cache] = det
                ref_clipp = det.get("_referencia_clipp")
                if ref_clipp:
                    cache[str(ref_clipp).upper()] = det
            return det

    if cache is not None:
        cache[ref_cache] = None
    return None


def _aplicar_preco_estoque(item: ItemPedido, detalhe: dict | None) -> None:
    if not detalhe:
        return
    prc = float(detalhe.get("prc_venda") or 0)
    if item.preco_unitario <= 0 and prc > 0:
        item.preco_unitario = prc
        item.preco_total = item.quantidade * prc
    prod_serv = (detalhe.get("prod_serv") or "").strip()
    if prod_serv:
        item.descricao = prod_serv[:60]


def _referencias_busca_item(item: ItemPedido) -> list[str]:
    refs: list[str] = []
    for valor in (item.sku, item.referencia, item.referencia_original):
        u = (valor or "").strip().upper()
        if u and u not in refs:
            refs.append(u)
    return refs


def _precarregar_cache_referencias_exatas(
    cur,
    itens: list[ItemPedido],
    cache: dict[str, dict | None],
) -> int:
    """Carrega referências exatas em lote (menos round-trips ao Firebird)."""
    refs: list[str] = []
    vistos: set[str] = set()
    for item in itens:
        for ref in _referencias_busca_item(item):
            if ref not in vistos:
                vistos.add(ref)
                refs.append(ref)
    if not refs:
        return 0

    carregados = 0
    for i in range(0, len(refs), 80):
        chunk = refs[i : i + 80]
        marcadores = ",".join("?" * len(chunk))
        cur.execute(
            f"""
            SELECT ID_IDENTIFICADOR, PROD_SERV, PRC_VENDA, UPPER(TRIM(REFERENCIA))
            FROM V_ESTOQUE_2
            WHERE UPPER(TRIM(REFERENCIA)) IN ({marcadores})
            """,
            chunk,
        )
        for row in cur.fetchall():
            ref = str(row[3] or "").strip().upper()
            if not ref:
                continue
            det = {
                "id_identificador": int(row[0]),
                "prod_serv": str(row[1] or "").strip()[:60],
                "prc_venda": float(row[2] or 0),
                "_referencia_clipp": ref,
            }
            cache[ref] = det
            carregados += 1
    return carregados


def vincular_produtos_pedido(con, pedido: PedidoExtraido) -> list[ItemFaltante]:
    """Após ler o PDF/site, resolve ID_IDENTIFICador, descrição e preço de cada item."""
    from config import get_clipp_config

    faltantes: list[ItemFaltante] = []
    faltantes_refs: set[str] = set()
    cache: dict[str, dict | None] = {}
    cache_grupo2: dict[tuple[int, str], int | None] = {}
    cache_grupo2_listas: dict[int, list[int]] = {}
    clipp_cfg = get_clipp_config()
    cur = con.cursor()
    try:
        _precarregar_cache_referencias_exatas(cur, pedido.itens, cache)
        for item in pedido.itens:
            det = buscar_produto_detalhe(
                cur,
                item.sku or item.referencia,
                cache,
                referencia_original=item.referencia_original or item.sku or item.referencia,
                idioma=None if item.sku else item.idioma,
                raridade=None if item.sku else item.raridade,
                sku=item.sku,
                descricao=item.descricao,
                clipp_cfg=clipp_cfg,
                cache_grupo2=cache_grupo2,
                preco_alvo=item.preco_unitario or None,
                cache_grupo2_listas=cache_grupo2_listas,
            )
            if det:
                ref_clipp = det.get("_referencia_clipp")
                if ref_clipp:
                    _aplicar_referencia_encontrada(item, str(ref_clipp))
                item.id_identificador = det["id_identificador"]
                _aplicar_preco_estoque(item, det)
            else:
                ref = (item.referencia or item.sku or "").strip().upper()
                chave = ref or (item.referencia_original or "").strip().upper()
                if chave and chave not in faltantes_refs:
                    faltantes_refs.add(chave)
                    faltantes.append(_item_faltante_de_pedido(item))
    finally:
        cur.close()
    return faltantes


def proximo_id_nfvenda(cur) -> int:
    cur.execute("SELECT COALESCE(MAX(ID_NFVENDA), 0) + 1 FROM TB_NFVENDA_2")
    return int(cur.fetchone()[0])


def consultar_nf_proximo(cur, gen_cfg: dict) -> tuple[int, str, str]:
    """Somente leitura de NF_PROXIMA (não incrementa)."""
    id_sermod = int(gen_cfg["id_sermod"])
    nf_serie = str(gen_cfg["nf_serie"]).strip()
    nf_modelo = str(gen_cfg["nf_modelo"]).strip()
    nf_tipo = str(gen_cfg.get("nf_tipo", "S")).strip()

    cur.execute(
        """
        SELECT NF_PROXIMA, NF_SERIE, NF_MODELO
        FROM TB_NFVENDA_GEN_ID
        WHERE ID_SERMOD = ?
          AND TRIM(NF_SERIE) = TRIM(?)
          AND TRIM(NF_MODELO) = TRIM(?)
          AND TRIM(NF_TIPO) = TRIM(?)
          AND NF_STATUS = 'A'
        """,
        (id_sermod, nf_serie, nf_modelo, nf_tipo),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"Série não encontrada em TB_NFVENDA_GEN_ID "
            f"(ID_SERMOD={id_sermod}, série={nf_serie}, modelo={nf_modelo}, tipo={nf_tipo})."
        )
    return int(row[0]), str(row[1]).strip(), str(row[2]).strip()


def _proximo_nf_livre_pedido(cur, nf_inicial: int) -> int:
    """
    XX_INC_PDV_PEDV usa NF_NUMERO como ID_PEDIDO em TB_PEDIDO_VENDA.
    Pula números já usados lá para evitar TB_PEDIDO_PK.
    """
    nf = int(nf_inicial)
    for _ in range(10000):
        cur.execute(
            "SELECT 1 FROM TB_PEDIDO_VENDA WHERE ID_PEDIDO = ?",
            (nf,),
        )
        if not cur.fetchone():
            return nf
        nf += 1
    raise RuntimeError(
        f"Sem NF nº livre em TB_PEDIDO_VENDA a partir de {nf_inicial}."
    )


def reservar_nf_numero(cur, gen_cfg: dict) -> tuple[int, str, str]:
    """Lê NF_PROXIMA, evita colisão com TB_PEDIDO_VENDA, incrementa generator."""
    id_sermod = int(gen_cfg["id_sermod"])
    nf_serie = str(gen_cfg["nf_serie"]).strip()
    nf_modelo = str(gen_cfg["nf_modelo"]).strip()
    nf_tipo = str(gen_cfg.get("nf_tipo", "S")).strip()

    cur.execute(
        """
        SELECT NF_PROXIMA, NF_SERIE, NF_MODELO
        FROM TB_NFVENDA_GEN_ID
        WHERE ID_SERMOD = ?
          AND TRIM(NF_SERIE) = TRIM(?)
          AND TRIM(NF_MODELO) = TRIM(?)
          AND TRIM(NF_TIPO) = TRIM(?)
          AND NF_STATUS = 'A'
        """,
        (id_sermod, nf_serie, nf_modelo, nf_tipo),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"Série não encontrada em TB_NFVENDA_GEN_ID "
            f"(ID_SERMOD={id_sermod}, série={nf_serie}, modelo={nf_modelo}, tipo={nf_tipo})."
        )

    nf_candidato = int(row[0])
    nf_numero = _proximo_nf_livre_pedido(cur, nf_candidato)
    proxima = nf_numero + 1
    cur.execute(
        """
        UPDATE TB_NFVENDA_GEN_ID
        SET NF_PROXIMA = ?
        WHERE ID_SERMOD = ?
          AND TRIM(NF_SERIE) = TRIM(?)
          AND TRIM(NF_MODELO) = TRIM(?)
          AND TRIM(NF_TIPO) = TRIM(?)
          AND NF_STATUS = 'A'
        """,
        (proxima, id_sermod, nf_serie, nf_modelo, nf_tipo),
    )
    return nf_numero, str(row[1]).strip(), str(row[2]).strip()


def _set_generator(cur, nome: str, ultimo_id: int) -> None:
    try:
        cur.execute(f"SET GENERATOR {nome} TO {int(ultimo_id)}")
    except fdb.Error:
        pass


def _sincronizar_generator_nfvenda(cur, id_nfvenda: int) -> None:
    _set_generator(cur, "GEN_TB_NFVENDA_ID_2", id_nfvenda)


def proximo_id_nfvitem(cur) -> int:
    cur.execute("SELECT COALESCE(MAX(ID_NFVITEM), 0) + 1 FROM TB_NFV_ITEM_2")
    return int(cur.fetchone()[0])


def _sincronizar_generator_nfvitem(cur, id_nfvitem: int) -> None:
    _set_generator(cur, "GEN_TB_NFV_ITEM_ID_2", id_nfvitem)


def _totais_venda(pedido: PedidoExtraido) -> tuple[float, float, float, float]:
    """Retorna (total_produtos, frete, desconto, total_nota)."""
    frete = float(pedido.resumo.get("valor_frete") or 0)
    desconto = float(pedido.resumo.get("valor_desconto") or 0)
    produtos = pedido.resumo.get("valor_itens")
    if produtos is None:
        produtos = sum(it.preco_total for it in pedido.itens)
    produtos = float(produtos or 0)
    total = pedido.resumo.get("valor_total")
    if total is None:
        total = produtos + frete - desconto
    else:
        total = float(total)
    return produtos, frete, desconto, total


def _sincronizar_totais_venda(
    cur,
    id_venda: int,
    vlr_frete: float,
    valor_desconto: float = 0,
) -> tuple[float, float]:
    """TOTALPRODUTOS = soma dos itens gravados; TOTALNOTA = produtos + frete - desconto."""
    cur.execute(
        "SELECT COALESCE(SUM(VLR_TOTAL), 0) FROM TB_NFV_ITEM_2 WHERE ID_NFVENDA = ?",
        (id_venda,),
    )
    total_produtos = float(cur.fetchone()[0] or 0)
    desconto = max(float(valor_desconto or 0), 0.0)
    total_nota = round(total_produtos + float(vlr_frete or 0) - desconto, 2)
    cur.execute(
        """
        UPDATE TB_NFVENDA_2
        SET TOTALPRODUTOS = ?, TOTALNOTA = ?, DESCONTO = ?
        WHERE ID_NFVENDA = ?
        """,
        (total_produtos, total_nota, desconto, id_venda),
    )
    return total_produtos, total_nota


def inserir_venda(
    cur,
    id_cliente: int,
    pedido: PedidoExtraido,
    nfvenda_cfg: dict | None = None,
    venda_cfg: dict | None = None,
) -> tuple[int, int]:
    total_produtos, vlr_frete, vlr_desconto, total_nota = _totais_venda(pedido)
    obs = _montar_obs(pedido)
    gen_cfg = nfvenda_cfg or {}
    vend_cfg = venda_cfg or {}
    id_vendedor = int(vend_cfg.get("id_vendedor", 17))
    xx_vendedor = int(vend_cfg.get("xx_vendedor", 17))
    id_planoconta = int(vend_cfg.get("id_planoconta", 22))
    cc_custo = int(vend_cfg.get("cc_custo", 23))

    id_nfvenda = proximo_id_nfvenda(cur)
    nf_numero, nf_serie, nf_modelo = reservar_nf_numero(cur, gen_cfg)

    cur.execute(
        """
        INSERT INTO TB_NFVENDA_2 (
            ID_NFVENDA, ID_CLIENTE, ID_VENDEDOR, XX_VENDEDOR,
            ID_NATOPE, DT_EMISSAO, DT_SAIDA, HR_SAIDA,
            TOTALNOTA, TOTALPRODUTOS,
            ID_FMAPGTO, ID_PARCELA, STATUS, ENT_SAI,
            NF_NUMERO, NF_SERIE, NF_MODELO,
            TIPO_FRETE, ENDERECO_ENTREGA, FIM, XX_ID_CAMP,
            ID_PLANOCONTA, CC_CUSTO, VLR_BC_FRETE, OBS, XX_EMISSOR
        ) VALUES (
            ?, ?, ?, ?,
            0, CURRENT_DATE, CURRENT_DATE, CURRENT_TIME,
            ?, ?,
            1, 1, 'A', 'S',
            ?, ?, ?,
            '0', 'S', 'Pendente', 2,
            ?, ?, ?, ?, 1
        )
        """,
        (
            id_nfvenda,
            id_cliente,
            id_vendedor,
            xx_vendedor,
            total_nota,
            total_produtos,
            nf_numero,
            nf_serie,
            nf_modelo,
            id_planoconta,
            cc_custo,
            vlr_frete,
            obs,
        ),
    )
    _sincronizar_generator_nfvenda(cur, id_nfvenda)
    return id_nfvenda, nf_numero


def reverter_reserva_nf_numero(cur, gen_cfg: dict, nf_numero_usado: int) -> None:
    """Recoloca NF_PROXIMA no número que foi consumido pela importação desfeita."""
    id_sermod = int(gen_cfg["id_sermod"])
    nf_serie = str(gen_cfg["nf_serie"]).strip()
    nf_modelo = str(gen_cfg["nf_modelo"]).strip()
    nf_tipo = str(gen_cfg.get("nf_tipo", "S")).strip()

    cur.execute(
        """
        SELECT NF_PROXIMA
        FROM TB_NFVENDA_GEN_ID
        WHERE ID_SERMOD = ?
          AND TRIM(NF_SERIE) = TRIM(?)
          AND TRIM(NF_MODELO) = TRIM(?)
          AND TRIM(NF_TIPO) = TRIM(?)
          AND NF_STATUS = 'A'
        """,
        (id_sermod, nf_serie, nf_modelo, nf_tipo),
    )
    row = cur.fetchone()
    if not row:
        return

    proxima = int(row[0])
    if proxima == nf_numero_usado + 1:
        cur.execute(
            """
            UPDATE TB_NFVENDA_GEN_ID
            SET NF_PROXIMA = ?
            WHERE ID_SERMOD = ?
              AND TRIM(NF_SERIE) = TRIM(?)
              AND TRIM(NF_MODELO) = TRIM(?)
              AND TRIM(NF_TIPO) = TRIM(?)
              AND NF_STATUS = 'A'
            """,
            (nf_numero_usado, id_sermod, nf_serie, nf_modelo, nf_tipo),
        )


def desfazer_importacao(
    con,
    registro: RegistroDesfazer,
    on_log: Callable[[str], None] | None = None,
) -> ResultadoDesfazer:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    if not registro.pode_desfazer:
        return ResultadoDesfazer(False, "Nada para desfazer neste pedido.")

    cur = con.cursor()
    try:
        ids_itens = list(registro.ids_itens or [])

        if ids_itens:
            log(f"Removendo {len(ids_itens)} item(ns)...")
            for id_item in ids_itens:
                cur.execute(
                    "DELETE FROM TB_NFV_ITEM_2 WHERE ID_NFVITEM = ?",
                    (id_item,),
                )
            cur.execute("SELECT COALESCE(MAX(ID_NFVITEM), 0) FROM TB_NFV_ITEM_2")
            _sincronizar_generator_nfvitem(cur, int(cur.fetchone()[0]))
            log("  Itens removidos — generator de itens atualizado")

        if registro.id_venda:
            log(f"Removendo venda #{registro.id_venda}...")
            cur.execute(
                "DELETE FROM TB_NFV_ITEM_2 WHERE ID_NFVENDA = ?",
                (registro.id_venda,),
            )
            cur.execute(
                "DELETE FROM TB_NFVENDA_2 WHERE ID_NFVENDA = ?",
                (registro.id_venda,),
            )
            cur.execute("SELECT COALESCE(MAX(ID_NFVENDA), 0) FROM TB_NFVENDA_2")
            _sincronizar_generator_nfvenda(cur, int(cur.fetchone()[0]))
            log("  Venda removida — generator de venda atualizado")

            if registro.nf_numero is not None and registro.nfvenda_cfg:
                reverter_reserva_nf_numero(
                    cur, registro.nfvenda_cfg, registro.nf_numero
                )
                log(f"  NF_PROXIMA recolocado para {registro.nf_numero}")

        if registro.cliente_alteracoes and registro.id_cliente:
            log(f"Restaurando cliente #{registro.id_cliente}...")
            sets = ", ".join(f"{k} = ?" for k in registro.cliente_alteracoes)
            cur.execute(
                f"UPDATE TB_CLIENTE SET {sets}, UPDATED_INTEGRADORA = CURRENT_TIMESTAMP "
                f"WHERE ID_CLIENTE = ?",
                (*registro.cliente_alteracoes.values(), registro.id_cliente),
            )
            log("  Campos do cliente restaurados")

        if registro.cliente_novo and registro.id_cliente:
            log(f"Removendo cliente novo #{registro.id_cliente}...")
            cur.execute(
                "DELETE FROM TB_CLI_PF WHERE ID_CLIENTE = ?",
                (registro.id_cliente,),
            )
            cur.execute(
                "DELETE FROM TB_CLI_PJ WHERE ID_CLIENTE = ?",
                (registro.id_cliente,),
            )
            cur.execute(
                "DELETE FROM TB_CLIENTE WHERE ID_CLIENTE = ?",
                (registro.id_cliente,),
            )
            log("  Cliente novo removido")

        _commit_gravacao(con, log, contexto="desfazer importação")
        return ResultadoDesfazer(
            True,
            f"Importação desfeita: {registro.resumo()}.",
        )
    except Exception as exc:
        try:
            con.rollback()
        except fdb.Error:
            pass
        log(f"ERRO ao desfazer: {exc}")
        return ResultadoDesfazer(False, str(exc))
    finally:
        cur.close()


def inserir_itens(
    cur,
    id_venda: int,
    itens: list[ItemPedido],
    on_log: Callable[[str], None] | None = None,
) -> tuple[list[ItemFaltante], int | None, list[int]]:
    from config import get_clipp_config

    faltantes: list[ItemFaltante] = []
    faltantes_refs: set[str] = set()
    ultimo_id_item: int | None = None
    ids_inseridos: list[int] = []
    cache: dict[str, dict | None] = {}
    cache_grupo2: dict[tuple[int, str], int | None] = {}
    cache_grupo2_listas: dict[int, list[int]] = {}
    clipp_cfg = get_clipp_config()
    total = len(itens)
    log_detalhe = total <= 20

    cur.execute("SELECT COALESCE(MAX(ID_NFVITEM), 0) FROM TB_NFV_ITEM_2")
    proximo_id = int(cur.fetchone()[0]) + 1

    for num, item in enumerate(itens, start=1):
        ref = (item.referencia or "").strip().upper()
        det = None
        if item.id_identificador:
            id_prod = int(item.id_identificador)
            det = {
                "id_identificador": id_prod,
                "prod_serv": (item.descricao or "")[:60],
                "prc_venda": item.preco_unitario,
            }
            if ref:
                cache.setdefault(ref, det)
        elif item.sku or ref:
            ref_busca = item.sku or item.referencia
            det = buscar_produto_detalhe(
                cur,
                ref_busca,
                cache,
                referencia_original=item.referencia_original or ref_busca,
                idioma=None if item.sku else item.idioma,
                raridade=None if item.sku else item.raridade,
                sku=item.sku,
                descricao=item.descricao,
                clipp_cfg=clipp_cfg,
                cache_grupo2=cache_grupo2,
                preco_alvo=item.preco_unitario or None,
                cache_grupo2_listas=cache_grupo2_listas,
            )
            if det:
                ref_clipp = det.get("_referencia_clipp")
                if ref_clipp:
                    _aplicar_referencia_encontrada(item, str(ref_clipp))
                    ref = (item.referencia or "").strip().upper()
                id_prod = det["id_identificador"]
            else:
                id_prod = None
        else:
            id_prod = None

        if not id_prod:
            chave = ref or (item.referencia_original or "").strip().upper()
            if chave and chave not in faltantes_refs:
                faltantes_refs.add(chave)
                faltantes.append(_item_faltante_de_pedido(item))
            if on_log and log_detalhe:
                on_log(
                    f"  Item {num}: {item.referencia} — "
                    f"ID_IDENTIFICADOR não encontrado em TB_EST_PRODUTO_2"
                )
            continue

        _aplicar_preco_estoque(item, det)
        vlr_unit = item.preco_unitario
        vlr_total = item.preco_total or item.quantidade * vlr_unit
        prod_nome = ((det or {}).get("prod_serv") or item.descricao or "")[:60]

        id_nfvitem = proximo_id
        proximo_id += 1
        cur.execute(
            """
            INSERT INTO TB_NFV_ITEM_2 (
                ID_NFVITEM, ID_NFVENDA, ID_IDENTIFICADOR, QTD_ITEM, VLR_UNIT, VLR_TOTAL,
                CFOP, NUM_ITEM, VLR_FRETE, INCLUIR_FATURA, PRODNOME, UNI_MEDIDA
            ) VALUES (?, ?, ?, ?, ?, ?, '5102', ?, 0, 'S', ?, 'UN')
            """,
            (
                id_nfvitem,
                id_venda,
                id_prod,
                item.quantidade,
                vlr_unit,
                vlr_total,
                num,
                prod_nome or None,
            ),
        )
        ultimo_id_item = id_nfvitem
        ids_inseridos.append(id_nfvitem)
        if on_log and log_detalhe:
            on_log(
                f"  Item {num}: {item.referencia} → ID {id_prod}, "
                f"qtd={item.quantidade}, R$ {vlr_unit:.2f} = R$ {vlr_total:.2f}"
            )
        elif on_log and num % 25 == 0:
            on_log(f"  ... {num}/{total} itens gravados")

    if ultimo_id_item is not None:
        _sincronizar_generator_nfvitem(cur, ultimo_id_item)

    if on_log and total > 20:
        on_log(
            f"  {len(ids_inseridos)} item(ns) gravados"
            + (f", {len(faltantes)} sem estoque" if faltantes else "")
        )

    return faltantes, ultimo_id_item, ids_inseridos


def importar_pedido(
    db_config: dict,
    pedido: PedidoExtraido,
    nfvenda_cfg: dict | None = None,
    venda_cfg: dict | None = None,
    on_log: Callable[[str], None] | None = None,
    on_etapa: Callable[[], None] | None = None,
) -> ResultadoImportacao:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    def etapa() -> None:
        if on_etapa:
            on_etapa()

    if pedido.erros and not pedido.itens:
        return ResultadoImportacao(
            arquivo=pedido.arquivo,
            sucesso=False,
            mensagem="; ".join(pedido.erros),
        )

    id_cliente: int | None = None
    cliente_novo = bool(pedido.cliente.get("cliente_novo"))
    cliente_alteracoes = pedido.cliente.get("cliente_alteracoes")
    id_venda: int | None = None
    nf_numero: int | None = None
    ids_itens: list[int] = []
    nao_encontrados: list[ItemFaltante] = []
    gen_cfg = nfvenda_cfg or {}

    def registro_atual() -> RegistroDesfazer | None:
        return _criar_registro_desfazer(
            pedido.arquivo,
            id_cliente=id_cliente,
            cliente_novo=cliente_novo,
            cliente_alteracoes=cliente_alteracoes,
            id_venda=id_venda,
            ids_itens=ids_itens,
            nf_numero=nf_numero,
            nfvenda_cfg=gen_cfg if nf_numero is not None else None,
        )

    try:
        etapa()

        if pedido.cliente.get("id_cliente"):
            id_cliente = validar_cliente_para_importacao(
                db_config, pedido, on_log=on_log
            )
            pedido.cliente["id_cliente"] = id_cliente

        (
            id_venda,
            nf_numero,
            id_cliente,
            cliente_novo,
            cliente_alteracoes,
            ids_itens,
            nao_encontrados,
        ) = importar_pedido_fase(
            db_config,
            pedido,
            nfvenda_cfg,
            venda_cfg,
            on_log=on_log,
        )
        pedido.cliente["id_cliente"] = id_cliente
        pedido.cliente["cliente_novo"] = cliente_novo
        etapa()

        msg = f"Venda #{id_venda} importada (NF nº {nf_numero}, cliente #{id_cliente})."
        if nao_encontrados:
            msg += (
                f" {len(nao_encontrados)} item(ns) não encontrado(s) "
                f"(R$ {sum(i.preco_total for i in nao_encontrados):.2f} para lançar manualmente)."
            )
            abrir_bloco_notas_faltantes(
                nao_encontrados,
                numero_pedido=pedido.numero_pedido or pedido.arquivo,
                id_venda=id_venda,
                nf_numero=nf_numero,
                on_log=on_log,
            )
        return ResultadoImportacao(
            arquivo=pedido.arquivo,
            sucesso=True,
            id_venda=id_venda,
            id_cliente=id_cliente,
            mensagem=msg,
            itens_nao_encontrados=nao_encontrados or None,
            desfazer=registro_atual(),
        )
    except Exception as exc:
        log(f"ERRO: {exc}")
        extra = ""
        if id_venda:
            extra = f" (venda #{id_venda} pode já estar no banco)"
        elif id_cliente:
            extra = f" (cliente #{id_cliente} já gravado na fase 1)"
        desf = registro_atual()
        if desf:
            log("  Use «Desfazer importação» para reverter o que já foi gravado.")
        return ResultadoImportacao(
            arquivo=pedido.arquivo,
            sucesso=False,
            id_cliente=id_cliente,
            id_venda=id_venda,
            mensagem=f"{exc}{extra}",
            desfazer=desf,
        )
