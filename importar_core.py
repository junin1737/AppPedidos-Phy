"""Lógica compartilhada: HTML do painel → CLIPP (RPA ou extensão Chrome)."""

from __future__ import annotations

VERSAO = "2026.06.06-14"

from collections.abc import Callable

import db as firebird_db
import rpa_tiaocards as rpa


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


def importar_pedido_extraido(
    pedido,
    numero: str,
    db_cfg: dict,
    nfv_cfg: dict,
    vend_cfg: dict,
    *,
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
        if "Status não é" in e
        or "CPF" in e
        or "inválido" in e
        or "não identificado" in e
    ]
    avisos_conf = [
        e for e in pedido.erros if e.startswith("AVISO:")
    ]
    if avisos_conf:
        msg = avisos_conf[0].replace("AVISO: ", "")
        log(f"BLOQUEADO — conferência: {msg}")
        if len(avisos_conf) > 1:
            for av in avisos_conf[1:]:
                log(av)
        return {
            "ok": False,
            "mensagem": (
                "Importação bloqueada — totais do site não batem com o parser. "
                + msg
            ),
            "ignorado": True,
        }
    if bloqueios:
        log(f"IGNORADO: {bloqueios[0]}")
        return {"ok": False, "mensagem": bloqueios[0], "ignorado": True}

    if not (pedido.cliente.get("documento") or "").strip():
        msg = "CPF/CNPJ obrigatório para localizar o cliente no CLIPP."
        log(f"IGNORADO: {msg}")
        return {"ok": False, "mensagem": msg, "ignorado": True}

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
    for n, it in enumerate(pedido.itens, 1):
        if len(pedido.itens) > 40:
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
            payload["itens_faltantes"] = [
                item.as_dict() for item in res.itens_nao_encontrados
            ]
            payload["subtotal_faltante"] = sum(
                item.preco_total for item in res.itens_nao_encontrados
            )
        return payload

    log(f"FALHA — {res.mensagem}")
    return {"ok": False, "mensagem": res.mensagem}


def importar_de_html(
    numero: str,
    html: str,
    db_cfg: dict,
    nfv_cfg: dict,
    vend_cfg: dict,
    *,
    texto_pagina: str | None = None,
    idiomas_por_ref: dict | None = None,
    on_log: Callable[[str], None] | None = None,
) -> dict:
    numero = str(numero).strip().lstrip("#")
    pedido = rpa.parsear_html_pedido(
        html,
        numero,
        texto_pagina=texto_pagina,
        idiomas_por_ref=idiomas_por_ref,
    )
    if on_log:
        on_log(f"[importador {VERSAO}] {__file__}")
        on_log(f"Idiomas lidos na página: {idiomas_por_ref or {}}")
    return importar_pedido_extraido(
        pedido, numero, db_cfg, nfv_cfg, vend_cfg, on_log=on_log
    )
