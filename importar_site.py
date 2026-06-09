"""
Importa pedido(s) direto do painel Tiao Cards → CLIPP.

Uso:
  py -3 importar_site.py --login              # 1ª vez: login + 2FA no Chrome
  py -3 importar_site.py 11058754             # um pedido
  py -3 importar_site.py --pendentes          # todos «Aguardando envio» na lista
"""

from __future__ import annotations

import argparse
import sys

import config as app_config
import db as firebird_db
import importar_core
import rpa_tiaocards as rpa


def log(msg: str) -> None:
    print(msg, flush=True)


def importar_um(numero: str, cfg_rpa: dict, db_cfg: dict, nfv_cfg: dict, vend_cfg: dict) -> bool:
    numero = str(numero).strip().lstrip("#")

    pedido = rpa.extrair_pedido_site(cfg_rpa, numero, headless=False, on_log=log)
    resultado = importar_core.importar_pedido_extraido(
        pedido, numero, db_cfg, nfv_cfg, vend_cfg, on_log=log
    )
    return bool(resultado.get("ok"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Importar pedido do site Tiao Cards")
    parser.add_argument("numero", nargs="?", help="Número do pedido no site (ex. 11058754)")
    parser.add_argument(
        "--pendentes",
        action="store_true",
        help="Importar pedidos da listagem (valida status em cada um)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Abrir Chrome para login / 2FA (salva sessão em .rpa_profile)",
    )
    args = parser.parse_args()

    cfg = app_config.load_config()
    if not app_config.is_configured(cfg):
        log("Configure o banco em config.ini antes de importar.")
        return 1

    cfg_rpa = app_config.get_rpa_config(cfg)
    db_cfg = app_config.get_db_config(cfg)
    nfv_cfg = app_config.get_nfvenda_config(cfg)
    vend_cfg = app_config.get_venda_config(cfg)

    if cfg_rpa.get("chrome_debug_url"):
        log(
            "Modo Chrome aberto — conectando em "
            f"{cfg_rpa['chrome_debug_url']} (nao abre Chrome novo)."
        )
    else:
        log("Modo perfil .rpa_profile — abrira Chrome dedicado.")

    if args.login:
        log("Modo login — use o Chrome já aberto (Abrir Chrome RPA.bat) ou aguarde abrir.")
        rpa.salvar_login_interativo(cfg_rpa, on_log=log)
        return 0

    if args.pendentes:
        numeros = rpa.listar_pedidos_pendentes(cfg_rpa, headless=False, on_log=log)
        ok = 0
        for num in numeros:
            log(f"\n--- Pedido #{num} ---")
            if importar_um(num, cfg_rpa, db_cfg, nfv_cfg, vend_cfg):
                ok += 1
        log(f"\nConcluído: {ok}/{len(numeros)}")
        return 0

    if not args.numero:
        parser.print_help()
        return 1

    return 0 if importar_um(args.numero, cfg_rpa, db_cfg, nfv_cfg, vend_cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
