"""
Compara venda IMPORTADA (SQL) vs venda CLIPP (cliente selecionado na tela).

Uso:
  python comparar_vendas_clipp.py ID_IMPORTADA ID_CLIPP

Exemplo:
  python comparar_vendas_clipp.py 117317 117320
"""
from __future__ import annotations

import os
import sys

import fdb

import config as app_config


def conectar():
    cfg = app_config.load_config()
    db = app_config.get_db_config(cfg)
    path = db.get("fbclient_path", "")
    if path:
        pasta = os.path.dirname(os.path.abspath(path))
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(pasta)
        fdb.load_api(path)
    params = {
        "user": db["user"],
        "password": db["password"],
        "charset": db["charset"],
    }
    if db.get("use_server") and db.get("host"):
        params["host"] = db["host"]
        params["port"] = int(db.get("port", 3050))
        params["database"] = db["database"]
    else:
        params["database"] = db["database"]
    return fdb.connect(**params)


def colunas(cur, tabela: str) -> list[str]:
    cur.execute(
        """
        SELECT TRIM(RDB$FIELD_NAME)
        FROM RDB$RELATION_FIELDS
        WHERE RDB$RELATION_NAME = ?
        ORDER BY RDB$FIELD_POSITION
        """,
        (tabela.upper(),),
    )
    return [r[0] for r in cur.fetchall()]


def linha(cur, tabela: str, id_nfvenda: int) -> dict | None:
    cols = colunas(cur, tabela)
    cur.execute(
        f"SELECT {', '.join(cols)} FROM {tabela} WHERE ID_NFVENDA = ?",
        (id_nfvenda,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def v_clientes(cur, id_cliente: int):
    cur.execute(
        """
        SELECT TRIM(NOME), TRIM(CPF), TRIM(CPF_CNPJ), TRIM(STATUS)
        FROM V_CLIENTES WHERE ID_CLIENTE = ?
        """,
        (id_cliente,),
    )
    return cur.fetchone()


def tabelas_filhas_nfvenda(cur) -> list[str]:
    cur.execute(
        """
        SELECT DISTINCT TRIM(rf.RDB$RELATION_NAME)
        FROM RDB$RELATION_FIELDS rf
        WHERE UPPER(TRIM(rf.RDB$FIELD_NAME)) = 'ID_NFVENDA'
          AND rf.RDB$RELATION_NAME <> 'TB_NFVENDA_2'
          AND rf.RDB$SYSTEM_FLAG = 0
        ORDER BY 1
        """
    )
    return [r[0] for r in cur.fetchall()]


def comparar(id_imp: int, id_clipp: int) -> int:
    con = conectar()
    cur = con.cursor()

    print(f"=== TB_NFVENDA_2: importada #{id_imp} vs CLIPP #{id_clipp} ===\n")
    imp = linha(cur, "TB_NFVENDA_2", id_imp)
    clp = linha(cur, "TB_NFVENDA_2", id_clipp)
    if not imp:
        print(f"ERRO: venda importada #{id_imp} nao encontrada.")
        return 1
    if not clp:
        print(f"ERRO: venda CLIPP #{id_clipp} nao encontrada.")
        return 1

    diffs = []
    for col in imp:
        vi, vc = imp[col], clp.get(col)
        if vi != vc:
            diffs.append((col, vi, vc))

    print(f"Campos iguais: {len(imp) - len(diffs)} / {len(imp)}")
    print(f"Campos diferentes: {len(diffs)}\n")
    for col, vi, vc in diffs:
        print(f"  {col}:")
        print(f"    importada: {vi!r}")
        print(f"    CLIPP:     {vc!r}")

    for label, row, cid_key in (
        ("IMPORTADA", imp, id_imp),
        ("CLIPP", clp, id_clipp),
    ):
        cid = row.get("ID_CLIENTE")
        print(f"\n--- Cliente venda {label} #{cid_key} -> ID_CLIENTE={cid} ---")
        print("  V_CLIENTES:", v_clientes(cur, int(cid)) if cid else None)

    print("\n=== Tabelas filhas (registros por ID_NFVENDA) ===")
    for tbl in tabelas_filhas_nfvenda(cur):
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE ID_NFVENDA = ?", (id_imp,))
            c1 = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE ID_NFVENDA = ?", (id_clipp,))
            c2 = cur.fetchone()[0]
            if c1 or c2:
                print(f"  {tbl}: importada={c1} CLIPP={c2}")
                if c1:
                    cur.execute(f"SELECT * FROM {tbl} WHERE ID_NFVENDA = ?", (id_imp,))
                    print(f"    importada row: {cur.fetchone()}")
                if c2:
                    cur.execute(f"SELECT * FROM {tbl} WHERE ID_NFVENDA = ?", (id_clipp,))
                    print(f"    CLIPP row:     {cur.fetchone()}")
        except fdb.Error as exc:
            print(f"  {tbl}: erro ao consultar ({exc})")

    print("\n=== Itens TB_NFV_ITEM_2 ===")
    for vid, label in ((id_imp, "importada"), (id_clipp, "CLIPP")):
        cur.execute(
            """
            SELECT COUNT(*), MIN(NUM_ITEM), MAX(NUM_ITEM)
            FROM TB_NFV_ITEM_2 WHERE ID_NFVENDA = ?
            """,
            (vid,),
        )
        print(f"  {label} #{vid}:", cur.fetchone())

    con.close()
    return 0


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    id_imp = int(sys.argv[1])
    id_clipp = int(sys.argv[2])
    sys.exit(comparar(id_imp, id_clipp))


if __name__ == "__main__":
    main()
