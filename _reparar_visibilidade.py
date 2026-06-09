"""Reaplica UPDATE visibilidade CLIPP em venda/cliente existentes."""
from __future__ import annotations

import sys

import config as app_config
import db as firebird_db


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: py -3 _reparar_visibilidade.py ID_NFVENDA ID_CLIENTE")
        sys.exit(1)

    id_venda = int(sys.argv[1])
    id_cliente = int(sys.argv[2])
    cfg = app_config.load_config()
    db_cfg = app_config.get_db_config(cfg)

    def log(msg: str) -> None:
        print(msg)

    firebird_db._pulso_visibilidade_clipp(
        db_cfg,
        id_cliente=id_cliente,
        id_venda=id_venda,
        on_log=log,
    )
    print(f"Visibilidade reaplicada — venda #{id_venda}, cliente #{id_cliente}")


if __name__ == "__main__":
    main()
