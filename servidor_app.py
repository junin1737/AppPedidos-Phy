"""
AppPedidos CLIPP — servidor local com janela visual + ícone na bandeja.

Responsabilidades:
  - HTTP local (importar_servidor) para a extensão Chrome
  - Abas Tkinter: Servidor, Postagens, Financeiro, Consulta, Embalagens
  - Log em arquivo, início com Windows, hot-reload de módulos Python

Uso:
  py -3w servidor_app.py
  ou duplo clique em «AppPedidos CLIPP» / «Servidor CLIPP (bandeja).bat»
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
import winreg
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import config as app_config

APP_DIR = Path(__file__).resolve().parent
os.chdir(APP_DIR)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

LOG_FILE = app_config.log_path()
REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "AppPedidosCLIPP"
EXTENSAO_DIR = APP_DIR / "extensao_chrome"
APP_TITLE = "AppPedidos CLIPP"
APP_ICON_ICO = APP_DIR / "app_icon.ico"
APP_ICON_PNG = APP_DIR / "app_icon.png"

# Repositório GitHub — atualização pelo botão «Atualizar do GitHub»
GITHUB_REPO = "junin1737/AppPedidos-Phy"
GITHUB_BRANCH = "main"
SCRIPT_ATUALIZAR_GIT = APP_DIR / "atualizar_github.py"
SCRIPT_ATUALIZAR_GIT_PS1 = APP_DIR / "atualizar_github.ps1"


def _pythonw_atualizacao() -> str:
    """pythonw embutido ou do sistema para abrir a janela de atualização."""
    embutido = APP_DIR / "python" / "pythonw.exe"
    if embutido.is_file():
        return str(embutido)
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        pyw = exe.with_name("pythonw.exe")
        if pyw.is_file():
            return str(pyw)
    return str(exe)

_servidor_httpd = None
_servidor_thread: threading.Thread | None = None
_porta = 8765
_tray_icon = None
_janela: "JanelaServidor | None" = None
_recarregar_lock = threading.Lock()

_MODULOS_RECARREGAR = (
    "config",
    "limites_campos",
    "parser_pedido",
    "db",
    "rpa_tiaocards",
    "importar_core",
    "importar_servidor",
    "schema_app",
)


# ---------------------------------------------------------------------------
# Log e redirecionamento de stdout (pythonw não tem console)
# ---------------------------------------------------------------------------

class _LogStream:
    """Redireciona print do servidor HTTP para o arquivo de log (pythonw não tem console)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, data: str) -> None:
        if not data:
            return
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(data)

    def flush(self) -> None:
        pass


def _configurar_log() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Nova sessão: arquivo zerado — a janela mostra só o desta execução.
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("")
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
    logging.info("=" * 56)
    logging.info("Nova sessão — %s", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    logging.info("Pasta de dados: %s", app_config.dados_dir())
    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        sys.stdout = _LogStream(LOG_FILE)
    if sys.stderr is None or not hasattr(sys.stderr, "write"):
        sys.stderr = _LogStream(LOG_FILE)


# ---------------------------------------------------------------------------
# Ícone da bandeja, atalhos e início automático com o Windows
# ---------------------------------------------------------------------------

def _criar_icone_tray():
    from PIL import Image

    if APP_ICON_PNG.is_file():
        img = Image.open(APP_ICON_PNG).convert("RGBA")
        return img.resize((64, 64), Image.Resampling.LANCZOS)
    if APP_ICON_ICO.is_file():
        return Image.open(APP_ICON_ICO).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)

    from PIL import ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(21, 101, 192, 255))
    draw.rectangle((20, 28, 44, 46), fill=(255, 255, 255, 255))
    draw.rectangle((28, 20, 36, 28), fill=(255, 255, 255, 255))
    return img


def _comando_inicio() -> str:
    bat = APP_DIR / "AppPedidos CLIPP.bat"
    if bat.is_file():
        return f'"{bat}"'
    pyw = APP_DIR / "python" / "pythonw.exe"
    if pyw.is_file():
        return f'"{pyw}" "{APP_DIR / "servidor_app.py"}"'
    vbs = APP_DIR / "Iniciar Servidor CLIPP.vbs"
    if vbs.is_file():
        return f'wscript.exe "{vbs}"'
    pythonw = Path(sys.executable)
    if pythonw.name.lower() == "python.exe":
        pythonw = pythonw.with_name("pythonw.exe")
    return f'"{pythonw}" "{APP_DIR / "servidor_app.py"}"'


def _inicio_automatico_ativo() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN) as key:
            winreg.QueryValueEx(key, REG_NAME)
            return True
    except OSError:
        pass
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return (startup / "AppPedidos CLIPP.lnk").is_file()


def _definir_inicio_automatico(ativo: bool) -> None:
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    lnk = startup / "AppPedidos CLIPP.lnk"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        REG_RUN,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if ativo:
            winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, _comando_inicio())
        else:
            try:
                winreg.DeleteValue(key, REG_NAME)
            except OSError:
                pass
    if ativo and not lnk.is_file():
        _criar_atalho(lnk, "Servidor importador Tiao Cards → CLIPP")
    elif not ativo and lnk.is_file():
        try:
            lnk.unlink()
        except OSError:
            pass


def _criar_atalho(destino: Path, descricao: str) -> None:
    bat = APP_DIR / "AppPedidos CLIPP.bat"
    if not bat.is_file():
        return
    ps = (
        f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut("{destino}"); '
        f'$s.TargetPath = "{bat}"; '
        f'$s.WorkingDirectory = "{APP_DIR}"; '
        f'$s.Description = "{descricao}"; '
    )
    if APP_ICON_ICO.is_file():
        ps += f'$s.IconLocation = "{APP_ICON_ICO},0"; '
    ps += "$s.Save()"
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _garantir_atalho_area_trabalho() -> None:
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    if not desktop.is_dir():
        return
    lnk = desktop / "AppPedidos CLIPP.lnk"
    if not lnk.is_file():
        _criar_atalho(lnk, "Servidor importador Tiao Cards → CLIPP")


# ---------------------------------------------------------------------------
# Servidor HTTP — porta, thread em background e hot-reload dos módulos Python
# ---------------------------------------------------------------------------

def _ler_porta_config() -> int:
    try:
        import config as app_config

        cfg = app_config.load_config()
        if cfg.has_section("extensao"):
            return int(cfg["extensao"].get("porta", "8765"))
    except Exception:
        pass
    return 8765


def _recarregar_modulos_python() -> None:
    """Recarrega o código Python sem fechar o aplicativo."""
    for nome in _MODULOS_RECARREGAR:
        mod = importlib.import_module(nome)
        importlib.reload(mod)


def _recarregar_servico() -> str | None:
    """
    Para o HTTP, recarrega módulos (.py) e sobe de novo.
    Retorna mensagem de erro ou None se OK.
    """
    global _porta
    with _recarregar_lock:
        _parar_servidor()
        try:
            _recarregar_modulos_python()
        except Exception as exc:
            logging.exception("Falha ao recarregar módulos Python")
            err = _iniciar_servidor()
            msg = f"Erro ao recarregar o código:\n{exc}"
            if err:
                msg += f"\n\nAo tentar subir de novo:\n{err}"
            return msg

        marca = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        logging.info("-" * 56)
        logging.info("Serviço recarregado — %s", marca)

        err = _iniciar_servidor()
        if err:
            logging.error("Falha ao reiniciar servidor após recarregar: %s", err)
            return err
        try:
            import importar_core

            logging.info(
                "Versão importador após recarregar: %s",
                getattr(importar_core, "VERSAO", "?"),
            )
        except Exception:
            pass
        return None


def _recarregar_servico_ui() -> None:
    if _janela is not None:
        _janela.executar_recarregar_servico()
    else:
        err = _recarregar_servico()
        if err:
            _mensagem_erro_windows(APP_TITLE, err)


def _atualizar_github_ui() -> None:
    """Abre janela de progresso, baixa do GitHub e reinicia o app."""
    if not messagebox.askyesno(
        APP_TITLE,
        "Atualizar do GitHub?\n\n"
        f"Repositório: {GITHUB_REPO} ({GITHUB_BRANCH})\n\n"
        "Uma janela com barra de progresso será aberta.\n"
        "O servidor encerra sozinho, aplica a atualização e reabre ao terminar.\n\n"
        "config.ini e pedidos importados não são alterados.",
        parent=_janela,
    ):
        return

    script = SCRIPT_ATUALIZAR_GIT if SCRIPT_ATUALIZAR_GIT.is_file() else SCRIPT_ATUALIZAR_GIT_PS1
    if not script.is_file():
        messagebox.showerror(
            APP_TITLE,
            "Atualizador não encontrado:\n"
            f"  {SCRIPT_ATUALIZAR_GIT}\n"
            f"  {SCRIPT_ATUALIZAR_GIT_PS1}",
            parent=_janela,
        )
        return

    logging.info(
        "Atualização GitHub iniciada — repo=%s branch=%s destino=%s script=%s",
        GITHUB_REPO,
        GITHUB_BRANCH,
        APP_DIR,
        script.name,
    )

    try:
        if script.suffix.lower() == ".py":
            subprocess.Popen(
                [
                    _pythonw_atualizacao(),
                    str(script),
                    "--destino",
                    str(APP_DIR),
                    "--repositorio",
                    GITHUB_REPO,
                    "--branch",
                    GITHUB_BRANCH,
                ],
                cwd=str(APP_DIR),
            )
        else:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Destino",
                    str(APP_DIR),
                    "-Repositorio",
                    GITHUB_REPO,
                    "-Branch",
                    GITHUB_BRANCH,
                    "-AguardarSegundos",
                    "2",
                ],
                cwd=str(APP_DIR),
                creationflags=flags,
            )
    except OSError as exc:
        logging.error("Falha ao iniciar atualização GitHub: %s", exc)
        messagebox.showerror(APP_TITLE, f"Não foi possível iniciar a atualização:\n{exc}")
        return

    if _janela is not None:
        _janela._lbl_status.config(text="● Abrindo atualização…", fg="#ef6c00")
        _janela._lbl_detalhe.config(
            text="Acompanhe o progresso na janela «Atualizando do GitHub»."
        )
        _janela.update_idletasks()
        _janela.after(600, _sair_app)
    else:
        threading.Timer(0.6, _sair_app).start()


def _limpar_controle_local_ui() -> None:
    from tkinter import simpledialog

    import rpa_tiaocards as rpa

    escolha = messagebox.askyesnocancel(
        APP_TITLE,
        "Limpar controle local de pedidos já importados?\n\n"
        "Use isso para permitir reimportar quando a venda foi apagada ou "
        "cancelada no CLIPP.\n\n"
        "• Sim — limpar TODOS os pedidos do arquivo local\n"
        "• Não — remover só um número de pedido\n"
        "• Cancelar — voltar",
    )
    if escolha is None:
        return

    if escolha:
        n = rpa.limpar_todo_controle_local()
        logging.info("Controle local limpo manualmente (%s pedido(s)).", n)
        messagebox.showinfo(
            APP_TITLE,
            f"Controle local limpo.\n\n{n} pedido(s) removido(s).\n\n"
            f"Arquivo:\n{app_config.pedidos_controle_path()}",
        )
        if _janela is not None:
            _janela._append_log_ui(
                f"\nControle local limpo — {n} pedido(s) removido(s).\n"
            )
        return

    numero = simpledialog.askstring(
        APP_TITLE,
        "Número do pedido a liberar (somente dígitos):",
        parent=_janela,
    )
    if not numero:
        return
    numero = str(numero).strip().lstrip("#")
    if not numero.isdigit():
        messagebox.showwarning(APP_TITLE, "Número inválido.")
        return
    removido = rpa.remover_pedido_controle(numero)
    msg = (
        f"Pedido #{numero} removido do controle local."
        if removido
        else f"Pedido #{numero} não estava no controle local."
    )
    logging.info(msg)
    messagebox.showinfo(APP_TITLE, msg)
    if _janela is not None:
        _janela._append_log_ui(f"\n{msg}\n")


def _iniciar_servidor() -> str | None:
    """Sobe o HTTP local. Retorna mensagem de erro ou None se OK."""
    global _servidor_httpd, _servidor_thread, _porta
    if _servidor_httpd is not None:
        return None

    import importlib
    import sys

    for nome in _MODULOS_RECARREGAR:
        sys.modules.pop(nome, None)
    import importar_servidor

    _porta = _ler_porta_config()
    try:
        _servidor_httpd, _porta = importar_servidor.criar_servidor()
    except OSError as exc:
        logging.error("Falha ao abrir porta %s: %s", _porta, exc)
        _servidor_httpd = None
        return (
            f"Não foi possível usar a porta {_porta}. "
            f"Outro servidor já está aberto?\n\n{exc}"
        )
    except Exception as exc:
        logging.exception("Falha ao iniciar servidor HTTP")
        _servidor_httpd = None
        return str(exc)

    _servidor_thread = threading.Thread(
        target=_servidor_httpd.serve_forever,
        name="servidor-clipp-http",
        daemon=True,
    )
    _servidor_thread.start()
    logging.info("Servidor iniciado em http://127.0.0.1:%s", _porta)
    return None


def _parar_servidor() -> None:
    global _servidor_httpd, _servidor_thread
    if _servidor_httpd is None:
        return
    _servidor_httpd.shutdown()
    _servidor_httpd.server_close()
    _servidor_httpd = None
    if _servidor_thread:
        _servidor_thread.join(timeout=5)
        _servidor_thread = None
    logging.info("Servidor encerrado")


def _abrir_pasta_extensao() -> None:
    if EXTENSAO_DIR.is_dir():
        os.startfile(str(EXTENSAO_DIR))
    else:
        logging.warning("Pasta extensao_chrome não encontrada: %s", EXTENSAO_DIR)


def _abrir_config() -> None:
    cfg = APP_DIR / "config.ini"
    if not cfg.is_file():
        exemplo = APP_DIR / "config.ini.exemplo"
        if exemplo.is_file():
            cfg.write_text(exemplo.read_text(encoding="utf-8"), encoding="utf-8")
    os.startfile(str(cfg))


def _abrir_log() -> None:
    if LOG_FILE.is_file():
        os.startfile(str(LOG_FILE))


def _abrir_ajuda() -> None:
    ajuda = APP_DIR / "LEIA-ME-INSTALACAO.md"
    if ajuda.is_file():
        os.startfile(str(ajuda))
    else:
        webbrowser.open("https://www.tiaocards.com.br/")


def _mostrar_janela() -> None:
    global _janela
    if _janela is not None:
        _janela.deiconify()
        _janela.lift()
        _janela.focus_force()


def _alternar_inicio_automatico(ativo: bool | None = None) -> bool:
    if ativo is None:
        ativo = not _inicio_automatico_ativo()
    _definir_inicio_automatico(ativo)
    logging.info("Iniciar com Windows: %s", ativo)
    return ativo


def _sair_app() -> None:
    if _janela is not None:
        _janela.destroy()
    _parar_servidor()
    if _tray_icon is not None:
        _tray_icon.stop()


# ---------------------------------------------------------------------------
# Janela principal — aba Servidor (status/log) + abas Correios (notebook)
# ---------------------------------------------------------------------------

class JanelaServidor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("940x640")
        self.minsize(720, 480)
        self.configure(bg="#f5f7fa")

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        self._log_pos = 0
        self._var_inicio = tk.BooleanVar(value=_inicio_automatico_ativo())

        if APP_ICON_ICO.is_file():
            try:
                self.iconbitmap(str(APP_ICON_ICO))
            except tk.TclError:
                pass

        self._montar_ui()
        self.protocol("WM_DELETE_WINDOW", self._minimizar_para_bandeja)
        self.after(100, self._trazer_para_frente)
        self.after(500, self._atualizar_log)
        self.after(1500, self._atualizar_status)

    def _trazer_para_frente(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

    def definir_erro_servidor(self, mensagem: str) -> None:
        self._lbl_status.config(text="● Servidor com erro", fg="#c62828")
        self._lbl_detalhe.config(text=mensagem[:500])

    def executar_recarregar_servico(self) -> None:
        self._btn_recarregar.config(state="disabled")
        self._lbl_status.config(text="● Recarregando serviço…", fg="#ef6c00")
        self._lbl_detalhe.config(text="Atualizando código e reiniciando a porta local.")
        self.update_idletasks()

        def tarefa() -> None:
            err = _recarregar_servico()
            self.after(0, lambda: self._apos_recarregar_servico(err))

        threading.Thread(target=tarefa, name="recarregar-servico", daemon=True).start()

    def _apos_recarregar_servico(self, erro: str | None) -> None:
        self._btn_recarregar.config(state="normal")
        if erro:
            messagebox.showerror(APP_TITLE, erro)
            self.definir_erro_servidor(erro)
            return

        self._atualizar_status_imediato()
        if LOG_FILE.is_file():
            self._log_pos = max(0, LOG_FILE.stat().st_size - 4096)
        self._append_log_ui(
            f"\n--- Serviço recarregado {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---\n"
        )
        if _tray_icon is not None:
            try:
                _tray_icon.notify(
                    APP_TITLE,
                    "Serviço recarregado — alterações no código já valendo.",
                )
            except Exception:
                pass

    def _append_log_ui(self, texto: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", texto)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _atualizar_status_imediato(self) -> None:
        if _servidor_httpd is not None:
            self._lbl_status.config(
                text=f"● Servidor ativo — porta {_porta}",
                fg="#2e7d32",
            )
            self._lbl_detalhe.config(
                text=f"Pronto para importar. URL local: http://127.0.0.1:{_porta}/ping"
            )
        else:
            self._lbl_status.config(text="● Servidor parado", fg="#c62828")
            self._lbl_detalhe.config(
                text="Clique em «Recarregar serviço» para tentar de novo."
            )

    def _montar_ui(self) -> None:
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True)

        srv = tk.Frame(self._notebook, bg="#f5f7fa")
        self._notebook.add(srv, text="  Servidor  ")

        topo = tk.Frame(srv, bg="#1565c0", padx=16, pady=14)
        topo.pack(fill="x")

        cab = tk.Frame(topo, bg="#1565c0")
        cab.pack(anchor="w", fill="x")
        if APP_ICON_PNG.is_file():
            try:
                self._img_logo = tk.PhotoImage(file=str(APP_ICON_PNG))
                w = self._img_logo.width()
                if w > 48:
                    self._img_logo = self._img_logo.subsample(max(1, w // 48), max(1, w // 48))
                tk.Label(cab, image=self._img_logo, bg="#1565c0").pack(side="left", padx=(0, 12))
            except tk.TclError:
                pass

        titulos = tk.Frame(cab, bg="#1565c0")
        titulos.pack(side="left", fill="x", expand=True)

        tk.Label(
            titulos,
            text=APP_TITLE,
            font=("Segoe UI", 16, "bold"),
            fg="white",
            bg="#1565c0",
        ).pack(anchor="w")
        tk.Label(
            titulos,
            text="Importador Tiao Cards → CLIPP  •  extensão Chrome + Firebird",
            font=("Segoe UI", 9),
            fg="#bbdefb",
            bg="#1565c0",
        ).pack(anchor="w", pady=(4, 0))

        status = tk.Frame(srv, bg="#f5f7fa", padx=16, pady=12)
        status.pack(fill="x")

        self._lbl_status = tk.Label(
            status,
            text="● Iniciando servidor…",
            font=("Segoe UI", 11, "bold"),
            fg="#2e7d32",
            bg="#f5f7fa",
        )
        self._lbl_status.pack(anchor="w")

        self._lbl_detalhe = tk.Label(
            status,
            text="",
            font=("Segoe UI", 9),
            fg="#546e7a",
            bg="#f5f7fa",
        )
        self._lbl_detalhe.pack(anchor="w", pady=(4, 0))

        botoes = tk.Frame(srv, bg="#f5f7fa", padx=16)
        botoes.pack(fill="x", pady=(0, 8))

        self._btn_recarregar = ttk.Button(
            botoes,
            text="Recarregar serviço",
            command=self.executar_recarregar_servico,
        )
        self._btn_recarregar.pack(side="left", padx=(0, 8))

        ttk.Button(
            botoes,
            text="Atualizar do GitHub",
            command=_atualizar_github_ui,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            botoes,
            text="Limpar controle local",
            command=_limpar_controle_local_ui,
        ).pack(side="left", padx=(0, 8))

        for texto, cmd in (
            ("Configurar banco", _abrir_config),
            ("Pasta extensão", _abrir_pasta_extensao),
            ("Log completo", _abrir_log),
            ("Ajuda", _abrir_ajuda),
        ):
            ttk.Button(botoes, text=texto, command=cmd).pack(side="left", padx=(0, 8))

        opcoes = tk.Frame(srv, bg="#f5f7fa", padx=16)
        opcoes.pack(fill="x", pady=(0, 8))

        ttk.Checkbutton(
            opcoes,
            text="Iniciar com o Windows",
            variable=self._var_inicio,
            command=self._toggle_inicio,
        ).pack(anchor="w")

        tk.Label(
            srv,
            text="Atividade recente (importações e erros):",
            font=("Segoe UI", 9, "bold"),
            bg="#f5f7fa",
            fg="#37474f",
        ).pack(anchor="w", padx=16)

        self._log = scrolledtext.ScrolledText(
            srv,
            height=14,
            font=("Consolas", 9),
            state="disabled",
            wrap="word",
            bg="#ffffff",
            relief="flat",
            padx=8,
            pady=8,
        )
        self._log.pack(fill="both", expand=True, padx=16, pady=(6, 8))
        self._log.configure(state="normal")
        self._log.insert(
            "end",
            f"--- Sessão {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---\n",
        )
        self._log.configure(state="disabled")

        rodape = tk.Frame(srv, bg="#eceff1", padx=16, pady=10)
        rodape.pack(fill="x")

        tk.Label(
            rodape,
            text="Feche a janela para minimizar na bandeja (perto do relógio). Use a extensão no Chrome para importar.",
            font=("Segoe UI", 8),
            fg="#607d8b",
            bg="#eceff1",
            wraplength=560,
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(rodape, text="Sair", command=self._confirmar_sair).pack(side="right")

        # Aba de Gerenciamento de Postagens (Correios)
        try:
            from tela_postagens import (
                ConsultaCorreiosFrame,
                EmbalagensFrame,
                PostagensFrame,
            )
            from tela_financeiro import FinanceiroFrame

            self._aba_postagens = PostagensFrame(self._notebook, com_cabecalho=True)
            self._notebook.add(self._aba_postagens, text="  Postagens (Correios)  ")

            self._aba_financeiro = FinanceiroFrame(self._notebook, com_cabecalho=True)
            self._notebook.add(self._aba_financeiro, text="  Financeiro  ")

            self._aba_consulta = ConsultaCorreiosFrame(self._notebook, com_cabecalho=True)
            self._notebook.add(self._aba_consulta, text="  Consultar Correios  ")

            self._aba_embalagens = EmbalagensFrame(self._notebook, com_cabecalho=True)
            self._notebook.add(self._aba_embalagens, text="  Embalagens  ")
        except Exception as exc:  # noqa: BLE001
            logging.warning("Aba Postagens indisponível: %s", exc)

    def _toggle_inicio(self) -> None:
        ativo = self._var_inicio.get()
        _alternar_inicio_automatico(ativo)
        if ativo:
            _garantir_atalho_area_trabalho()

    def _atualizar_status(self) -> None:
        if _servidor_httpd is not None:
            self._lbl_status.config(
                text=f"● Servidor ativo — porta {_porta}",
                fg="#2e7d32",
            )
            self._lbl_detalhe.config(
                text=f"Pronto para importar. URL local: http://127.0.0.1:{_porta}/ping"
            )
        else:
            self._lbl_status.config(text="● Servidor parado", fg="#c62828")
            self._lbl_detalhe.config(
                text="Clique em «Recarregar serviço» para tentar de novo."
            )
        self.after(5000, self._atualizar_status)

    def _atualizar_log(self) -> None:
        if LOG_FILE.is_file():
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._log_pos)
                    novas = f.read()
                    self._log_pos = f.tell()
                if novas:
                    self._log.configure(state="normal")
                    self._log.insert("end", novas)
                    self._log.see("end")
                    self._log.configure(state="disabled")
            except OSError:
                pass
        self.after(1500, self._atualizar_log)

    def _minimizar_para_bandeja(self) -> None:
        self.withdraw()
        if _tray_icon is not None:
            try:
                _tray_icon.notify(
                    APP_TITLE,
                    "Servidor continua ativo na bandeja do Windows.",
                )
            except Exception:
                pass

    def _confirmar_sair(self) -> None:
        if messagebox.askyesno(
            APP_TITLE,
            "Encerrar o servidor?\n\nA extensão Chrome não conseguirá importar enquanto estiver fechado.",
        ):
            _sair_app()


# ---------------------------------------------------------------------------
# Menu do ícone na bandeja (abrir janela, config, pasta extensão, sair)
# ---------------------------------------------------------------------------

def _montar_menu_tray(icon):
    import pystray

    inicio_item = pystray.MenuItem(
        "Iniciar com o Windows",
        lambda icon, item: _alternar_inicio_automatico(not _inicio_automatico_ativo()),
        checked=lambda _: _inicio_automatico_ativo(),
    )
    return pystray.Menu(
        pystray.MenuItem("Abrir janela", lambda *_: _mostrar_janela()),
        pystray.MenuItem(
            lambda _: f"Servidor ativo (porta {_porta})",
            lambda *_: None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Recarregar serviço", lambda *_: _recarregar_servico_ui()),
        pystray.MenuItem("Atualizar do GitHub", lambda *_: _atualizar_github_ui()),
        pystray.MenuItem("Limpar controle local", lambda *_: _limpar_controle_local_ui()),
        pystray.MenuItem("Configurar banco (config.ini)", lambda *_: _abrir_config()),
        pystray.MenuItem("Pasta da extensão Chrome", lambda *_: _abrir_pasta_extensao()),
        pystray.MenuItem("Ver log completo", lambda *_: _abrir_log()),
        pystray.MenuItem("Ajuda", lambda *_: _abrir_ajuda()),
        pystray.Menu.SEPARATOR,
        inicio_item,
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", lambda *_: _sair_app()),
    )


def _iniciar_bandeja() -> None:
    global _tray_icon

    try:
        import pystray
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pystray", "Pillow"],
        )
        import pystray

    _tray_icon = pystray.Icon(
        "AppPedidosCLIPP",
        _criar_icone_tray(),
        f"{APP_TITLE} — importador Tiao Cards",
        menu=_montar_menu_tray(None),
    )
    _tray_icon.run()


def _mensagem_erro_windows(titulo: str, mensagem: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, mensagem, titulo, 0x10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entrypoint — configura log, sobe HTTP, bandeja e janela Tkinter
# ---------------------------------------------------------------------------

def main() -> int:
    global _janela

    try:
        _configurar_log()
        logging.info("%s — aplicativo iniciado", APP_TITLE)
        logging.info("Executável Python: %s", sys.executable)
        logging.info("Instalação: %s", APP_DIR)

        _garantir_atalho_area_trabalho()

        err_servidor = _iniciar_servidor()
        try:
            import importar_core

            logging.info(
                "Versão importador: %s",
                getattr(importar_core, "VERSAO", "?"),
            )
        except Exception:
            pass
        msg_cfg = app_config.mensagem_config_banco(app_config.load_config())
        if not msg_cfg:
            try:
                import schema_app

                # Agregador: cria/reconcilia TODO o schema do AppPedidos
                # (erros, etiqueta dos Correios e embalagem) e loga no arquivo.
                schema_app.garantir_schema_apppedidos(
                    app_config.get_db_config(app_config.load_config()),
                    on_log=logging.info,
                )
            except Exception as exc:
                logging.warning("Schema AppPedidos: %s", exc)
        logging.info("Controle local: %s", app_config.pedidos_controle_path())
        threading.Thread(target=_iniciar_bandeja, name="tray-icon", daemon=True).start()
        try:
            _janela = JanelaServidor()
            if err_servidor:
                _janela.definir_erro_servidor(err_servidor)
            _janela.mainloop()
        except tk.TclError as exc:
            logging.error("Interface grafica indisponivel: %s", exc)
            _mensagem_erro_windows(
                APP_TITLE,
                f"Interface grafica indisponivel nesta sessao.\n\n"
                f"O servidor HTTP continua na bandeja.\n\n{exc}",
            )
            if _tray_icon:
                _tray_icon.run()
        return 0
    except Exception as exc:
        try:
            _configurar_log()
            logging.exception("Falha fatal ao iniciar aplicativo")
        except Exception:
            pass
        detalhe = traceback.format_exc()
        msg = (
            f"{exc}\n\n"
            f"Log: {LOG_FILE}\n\n"
            "Se estiver no Windows Server, verifique se a sessão tem "
            "interface gráfica (não Server Core).\n\n"
            "Use «Abrir Servidor (diagnóstico).bat» para ver o erro no console."
        )
        _mensagem_erro_windows(APP_TITLE, msg)
        try:
            LOG_FILE.write_text(detalhe, encoding="utf-8")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
