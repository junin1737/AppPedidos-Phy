"""Tabelas auxiliares do AppPedidos (erros / itens faltantes de importação)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import fdb

if TYPE_CHECKING:
    from db import ItemFaltante

TABELA_ERRO = "TB_APPPEDIDOS_IMPORT_ERRO"
GENERATOR_ERRO = "GEN_TB_APPPEDIDOS_IMPORT_ERRO"

_DDL_TABELA = f"""
CREATE TABLE {TABELA_ERRO} (
    ID_ERRO INTEGER NOT NULL,
    DT_REGISTRO TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    NUMERO_PEDIDO VARCHAR(24),
    ID_VENDA INTEGER,
    NF_NUMERO INTEGER,
    TIPO CHAR(1) NOT NULL,
    REFERENCIA VARCHAR(80),
    REFERENCIA_SITE VARCHAR(80),
    QTD_ITEM INTEGER,
    VLR_UNIT DOUBLE PRECISION,
    VLR_TOTAL DOUBLE PRECISION,
    DESCRICAO VARCHAR(120),
    IDIOMA VARCHAR(4),
    RARIDADE VARCHAR(40),
    MENSAGEM VARCHAR(250),
    PRIMARY KEY (ID_ERRO)
)
"""

_DDL_GENERATOR = f"CREATE GENERATOR {GENERATOR_ERRO}"


def _tabela_existe(cur, nome: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM RDB$RELATIONS
        WHERE TRIM(RDB$RELATION_NAME) = ?
          AND RDB$VIEW_BLR IS NULL
        """,
        (nome.upper(),),
    )
    return cur.fetchone() is not None


def _generator_existe(cur, nome: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM RDB$GENERATORS
        WHERE TRIM(RDB$GENERATOR_NAME) = ?
        """,
        (nome.upper(),),
    )
    return cur.fetchone() is not None


def _proximo_id_erro(cur) -> int:
    cur.execute(f"SELECT GEN_ID({GENERATOR_ERRO}, 1) FROM RDB$DATABASE")
    return int(cur.fetchone()[0])


def garantir_schema_app_pedidos(
    db_config: dict,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """
    Cria TB_APPPEDIDOS_IMPORT_ERRO se ainda não existir.
    Chamado ao iniciar o servidor.
    """
    import db as firebird_db

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = None
    try:
        con = firebird_db.conectar(db_config)
        cur = con.cursor()
        criou = False
        if not _generator_existe(cur, GENERATOR_ERRO):
            cur.execute(_DDL_GENERATOR)
            criou = True
        if not _tabela_existe(cur, TABELA_ERRO):
            cur.execute(_DDL_TABELA)
            criou = True
        if criou:
            con.commit()
            log(f"Schema AppPedidos: tabela {TABELA_ERRO} pronta.")
        return True
    except fdb.Error as exc:
        log(f"Aviso: não foi possível garantir schema AppPedidos: {exc}")
        if con:
            try:
                con.rollback()
            except fdb.Error:
                pass
        return False
    finally:
        if con:
            firebird_db.encerrar_conexao(con)


def gravar_itens_faltantes_import(
    cur,
    faltantes: list[ItemFaltante],
    *,
    numero_pedido: str = "",
    id_venda: int | None = None,
    nf_numero: int | None = None,
) -> int:
    """Persiste itens não encontrados na importação (TIPO='F')."""
    if not faltantes or not _tabela_existe(cur, TABELA_ERRO):
        return 0

    pedido = str(numero_pedido or "").strip().lstrip("#")
    gravados = 0
    for faltante in faltantes:
        id_erro = _proximo_id_erro(cur)
        cur.execute(
            f"""
            INSERT INTO {TABELA_ERRO} (
                ID_ERRO, NUMERO_PEDIDO, ID_VENDA, NF_NUMERO, TIPO,
                REFERENCIA, REFERENCIA_SITE, QTD_ITEM, VLR_UNIT, VLR_TOTAL,
                DESCRICAO, IDIOMA, RARIDADE, MENSAGEM
            ) VALUES (?, ?, ?, ?, 'F', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_erro,
                pedido or None,
                id_venda,
                nf_numero,
                (faltante.referencia or "")[:80] or None,
                (faltante.referencia_site or "")[:80] or None,
                int(faltante.quantidade or 1),
                float(faltante.preco_unitario or 0),
                float(faltante.preco_total),
                (faltante.descricao or "")[:120] or None,
                (faltante.idioma or "")[:4] or None,
                (faltante.raridade or "")[:40] or None,
                "Item não encontrado no estoque CLIPP",
            ),
        )
        gravados += 1
    return gravados


def gravar_erro_import(
    cur,
    mensagem: str,
    *,
    numero_pedido: str = "",
    id_venda: int | None = None,
    nf_numero: int | None = None,
) -> None:
    """Persiste erro geral da importação (TIPO='E')."""
    if not _tabela_existe(cur, TABELA_ERRO):
        return
    pedido = str(numero_pedido or "").strip().lstrip("#")
    id_erro = _proximo_id_erro(cur)
    cur.execute(
        f"""
        INSERT INTO {TABELA_ERRO} (
            ID_ERRO, NUMERO_PEDIDO, ID_VENDA, NF_NUMERO, TIPO, MENSAGEM
        ) VALUES (?, ?, ?, ?, 'E', ?)
        """,
        (
            id_erro,
            pedido or None,
            id_venda,
            nf_numero,
            (mensagem or "")[:250],
        ),
    )
