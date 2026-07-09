"""
Servidor local para a extensão Chrome importar pedidos do painel já aberto.

Uso:
  py -3 importar_servidor.py
  (ou Servidor Extensao CLIPP.bat)

A extensão envia o HTML da guia atual — sem abrir outro Chrome.
Embutido em servidor_app.py na porta configurada em [extensao].
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config as app_config
import rpa_tiaocards as rpa

VERSAO_SERVIDOR = "2026.06.25-1"


# ---------------------------------------------------------------------------
# Helpers — recarregar módulos, porta e número do pedido no payload JSON
# ---------------------------------------------------------------------------

def _modulo_importar_core():
    """Importa importador do zero (evita bytecode antigo preso no sys.modules)."""
    import importlib
    import sys

    for nome in ("parser_pedido", "db", "rpa_tiaocards", "importar_core"):
        sys.modules.pop(nome, None)
    return importlib.import_module("importar_core")


def _porta_servidor(cfg) -> int:
    if cfg.has_section("extensao"):
        return int(cfg["extensao"].get("porta", "8765"))
    return 8765


def _extrair_numero_pedido(payload: dict) -> str | None:
    numero = (payload.get("numero_pedido") or payload.get("numero") or "").strip()
    numero = numero.lstrip("#")
    if numero:
        return numero
    url = payload.get("url") or ""
    m = re.search(r"[?&]cod=(\d+)", url)
    if m:
        return m.group(1)
    html = payload.get("html") or ""
    m = re.search(r"#(\d{7,9})", html[:50000])
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Handler HTTP — rotas GET /ping e POST /import (CORS para extensão Chrome)
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ImportadorCLIPP/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[servidor] {self.address_string()} — {fmt % args}", flush=True)

    def _enviar_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/ping", "/health"):
            core = _modulo_importar_core()
            self._enviar_json(
                200,
                {
                    "ok": True,
                    "servico": "importador-clipp",
                    "versao": VERSAO_SERVIDOR,
                    "versao_importador": getattr(core, "VERSAO", VERSAO_SERVIDOR),
                    "controle_local": str(app_config.pedidos_controle_path()),
                    "filtro_status": rpa.STATUS_IMPORTAR,
                },
            )
            return
        self._enviar_json(404, {"ok": False, "mensagem": "Rota não encontrada."})

    def _limpar_controle_local(self, payload: dict) -> None:
        numero = (payload.get("numero_pedido") or payload.get("numero") or "").strip()
        numero = numero.lstrip("#")
        if numero:
            removido = rpa.remover_pedido_controle(numero)
            if removido:
                msg = f"Pedido #{numero} removido do controle local."
            else:
                msg = f"Pedido #{numero} não estava no controle local."
            self._enviar_json(200, {"ok": True, "mensagem": msg, "numero_pedido": numero})
            return

        n = rpa.limpar_todo_controle_local()
        self._enviar_json(
            200,
            {
                "ok": True,
                "mensagem": f"Controle local limpo ({n} pedido(s) removido(s)).",
                "removidos": n,
            },
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._enviar_json(400, {"ok": False, "mensagem": "JSON inválido."})
            return

        if self.path in ("/controle/limpar", "/limpar-controle"):
            self._limpar_controle_local(payload)
            return

        if self.path not in ("/import", "/importar"):
            self._enviar_json(404, {"ok": False, "mensagem": "Use POST /import"})
            return

        resolver = payload.get("resolver_produto")
        if resolver:
            cfg = app_config.load_config()
            msg_cfg = app_config.mensagem_config_banco(cfg)
            if msg_cfg:
                self._enviar_json(503, {"ok": False, "mensagem": msg_cfg})
                return
            db_cfg = app_config.get_db_config(cfg)
            logs: list[str] = []

            def on_log_resolver(msg: str) -> None:
                logs.append(msg)
                print(msg, flush=True)

            try:
                id_venda = int(resolver.get("id_venda") or 0)
                id_prod = int(resolver.get("id_identificador") or 0)
                item = resolver.get("item") or {}
            except (TypeError, ValueError):
                self._enviar_json(
                    400,
                    {"ok": False, "mensagem": "resolver_produto inválido."},
                )
                return
            if not id_venda or not id_prod or not item:
                self._enviar_json(
                    400,
                    {
                        "ok": False,
                        "mensagem": "Informe id_venda, id_identificador e item.",
                    },
                )
                return
            print(
                f"\n--- Resolver item venda #{id_venda} → produto #{id_prod} ---",
                flush=True,
            )
            try:
                core = _modulo_importar_core()
                resultado = core.resolver_produto_faltante(
                    db_cfg,
                    id_venda=id_venda,
                    item=item,
                    id_identificador=id_prod,
                    on_log=on_log_resolver,
                )
            except Exception as exc:
                print(f"ERRO: {exc}", flush=True)
                self._enviar_json(
                    503,
                    {"ok": False, "mensagem": str(exc), "logs": logs},
                )
                return
            resultado["logs"] = logs
            status = 200 if resultado.get("ok") else 422
            self._enviar_json(status, resultado)
            return

        html = payload.get("html") or ""
        texto_pagina = payload.get("texto") or payload.get("innerText") or ""
        idiomas_por_ref = payload.get("idiomas_por_ref") or payload.get("idiomas") or {}
        selados_extensao = payload.get("selados") or payload.get("skus_selados") or []
        reprints_por_ref = payload.get("reprints_por_ref") or payload.get("reprints") or {}
        itens_extensao = payload.get("itens") or []
        cliente_id_escolhido = payload.get("cliente_id_escolhido")
        try:
            cliente_id_escolhido = (
                int(cliente_id_escolhido) if cliente_id_escolhido else None
            )
        except (TypeError, ValueError):
            cliente_id_escolhido = None
        numero = _extrair_numero_pedido(payload)
        if not numero:
            self._enviar_json(
                400,
                {
                    "ok": False,
                    "mensagem": "Número do pedido não encontrado na página.",
                },
            )
            return
        if not html:
            self._enviar_json(
                400, {"ok": False, "mensagem": "HTML da página não enviado."}
            )
            return

        cfg = app_config.load_config()
        msg_cfg = app_config.mensagem_config_banco(cfg)
        if msg_cfg:
            self._enviar_json(503, {"ok": False, "mensagem": msg_cfg})
            return

        db_cfg = app_config.get_db_config(cfg)
        nfv_cfg = app_config.get_nfvenda_config(cfg)
        vend_cfg = app_config.get_venda_config(cfg)

        logs: list[str] = []

        def on_log(msg: str) -> None:
            logs.append(msg)
            print(msg, flush=True)

        print(f"\n--- Importar #{numero} (extensão) ---", flush=True)
        try:
            core = _modulo_importar_core()
            resultado = core.importar_de_html(
                numero,
                html,
                db_cfg,
                nfv_cfg,
                vend_cfg,
                texto_pagina=texto_pagina,
                idiomas_por_ref=idiomas_por_ref,
                selados_extensao=selados_extensao,
                reprints_por_ref=reprints_por_ref,
                itens_extensao=itens_extensao,
                cliente_id_escolhido=cliente_id_escolhido,
                on_log=on_log,
            )
        except Exception as exc:
            print(f"ERRO: {exc}", flush=True)
            self._enviar_json(
                503,
                {
                    "ok": False,
                    "mensagem": (
                        f"Erro ao conectar/gravar no CLIPP:\n{exc}\n\n"
                        "Verifique config.ini (caminho do .FDB, host Firebird, fbclient)."
                    ),
                    "logs": logs,
                },
            )
            return
        resultado["logs"] = logs
        resultado["numero_pedido"] = numero
        status = 200 if resultado.get("ok") else 422
        self._enviar_json(status, resultado)


# ---------------------------------------------------------------------------
# Factory e entrypoints — servidor embutido na bandeja ou console isolado
# ---------------------------------------------------------------------------

def criar_servidor(host: str = "127.0.0.1", porta: int | None = None):
    """Cria ThreadingHTTPServer sem iniciar (para bandeja / serviço)."""
    cfg = app_config.load_config()
    if porta is None:
        porta = _porta_servidor(cfg)
    msg_cfg = app_config.mensagem_config_banco(cfg)
    if not msg_cfg:
        try:
            import schema_app

            schema_app.garantir_schema_apppedidos(
                app_config.get_db_config(cfg),
                on_log=lambda m: print(m, flush=True),
            )
        except Exception as exc:
            print(f"Aviso schema AppPedidos: {exc}", flush=True)
    httpd = ThreadingHTTPServer((host, porta), Handler)
    return httpd, porta


def main() -> int:
    host = "127.0.0.1"
    httpd, porta = criar_servidor(host)
    print(f"Servidor da extensão em http://{host}:{porta}", flush=True)
    print("Instale a pasta extensao_chrome no Chrome e abra um pedido no painel.", flush=True)
    print("Ctrl+C para encerrar.\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.", flush=True)
    return 0


def main_console() -> int:
    return main()


if __name__ == "__main__":
    sys.exit(main())
