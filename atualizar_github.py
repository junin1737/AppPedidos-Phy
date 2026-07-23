"""Atualização do AppPedidos CLIPP via GitHub com janela de progresso.

Processo separado: o servidor inicia este script e encerra; esta janela
permanece aberta mostrando o andamento até reiniciar o aplicativo.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import ttk

GITHUB_REPO_PADRAO = "junin1737/AppPedidos-Phy"
GITHUB_BRANCH_PADRAO = "main"

EXCLUIR_PY = frozenset({
    "gerar_pdf_estudo.py", "_reparar_visibilidade.py", "aplicacao_vendas.py",
    "importar_site.py", "comparar_vendas_clipp.py", "extrator_ocr.py",
    "teste_prepostagem.py",
})
PASTAS_COPIAR = ("extensao_chrome",)
ARQUIVOS_EXTRA = ("atualizar_github.ps1", "atualizar_github.py")

LOG_FILE = Path(os.environ.get("TEMP", ".")) / "AppPedidosCLIPP-atualizar.log"


def _log(msg: str) -> None:
    linha = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def _resolver_destino(informado: str) -> Path:
    if informado:
        return Path(informado).resolve()
    here = Path(__file__).resolve().parent
    if (here / "servidor_app.py").is_file():
        return here
    candidatos = [
        Path(os.environ.get("ProgramFiles", "")) / "AppPedidos CLIPP",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "AppPedidos CLIPP",
        Path(os.environ.get("LOCALAPPDATA", "")) / "AppPedidos CLIPP",
        Path("C:/AppPedidos CLIPP"),
        Path("D:/AppPedidos CLIPP"),
    ]
    for c in candidatos:
        if c.is_dir() and (c / "servidor_app.py").is_file():
            return c.resolve()
    return here


def _precisa_admin(destino: Path) -> bool:
    prog = os.environ.get("ProgramFiles", "C:\\Program Files")
    try:
        return str(destino).lower().startswith(prog.lower())
    except Exception:
        return False


def _eh_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _reexecutar_como_admin(argv: list[str]) -> None:
    import ctypes
    params = subprocess.list2cmdline(argv)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )


def _comando_envolve_app(cmd: str, pastas: list[Path]) -> bool:
    """True se o processo é o app (servidor), não o atualizador."""
    if not cmd:
        return False
    cmd_l = cmd.lower()
    # Nunca encerrar o próprio atualizador (python ou powershell).
    if "atualizar_github.py" in cmd_l or "atualizar_github.ps1" in cmd_l:
        return False
    if "apppedidosclipp-git-" in cmd_l:
        return False
    if "servidor_app.py" in cmd_l or "importar_servidor.py" in cmd_l:
        return True
    for pasta in pastas:
        p = str(pasta).lower()
        if not p:
            continue
        # Só considera se o executável/script está NA pasta do app
        # (evita matar o atualizador só porque --destino aponta para lá).
        if p in cmd_l and (
            "pythonw.exe" in cmd_l
            or "python.exe" in cmd_l
            or "servidor_app.py" in cmd_l
            or "apppedidos clipp.bat" in cmd_l
        ):
            # Ainda assim exclui se for o script de atualização
            if "atualizar_github" in cmd_l:
                return False
            return True
    return False


def encerrar_processos_app(destino: Path, aguardar_inicial: float = 3.0) -> None:
    """Aguarda o app principal fechar e encerra processos restantes."""
    pastas = [destino.resolve()]
    meu_pid = os.getpid()
    if aguardar_inicial > 0:
        time.sleep(aguardar_inicial)

    def _listar_via_cim() -> list[tuple[int, str]]:
        """Lista (pid, commandline) sem depender do wmic (removido em Windows novos)."""
        try:
            out = subprocess.check_output(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Select-Object ProcessId, CommandLine | "
                        "ConvertTo-Csv -NoTypeInformation"
                    ),
                ],
                text=True,
                errors="replace",
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return []
        import csv
        from io import StringIO

        rows: list[tuple[int, str]] = []
        reader = csv.DictReader(StringIO(out))
        for row in reader:
            try:
                pid = int(row.get("ProcessId") or 0)
            except ValueError:
                continue
            cmd = (row.get("CommandLine") or "").strip()
            if pid and cmd:
                rows.append((pid, cmd))
        return rows

    for tentativa in range(18):
        rodando = False
        processos = _listar_via_cim()
        if not processos:
            # Fallback legado
            try:
                out = subprocess.check_output(
                    ["wmic", "process", "get", "ProcessId,CommandLine"],
                    text=True,
                    errors="replace",
                    timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for linha in out.splitlines():
                    linha = linha.strip()
                    if not linha or linha.startswith("CommandLine"):
                        continue
                    partes = linha.rsplit(None, 1)
                    if len(partes) != 2 or not partes[1].isdigit():
                        continue
                    processos.append((int(partes[1]), partes[0]))
            except (subprocess.SubprocessError, FileNotFoundError):
                break

        for pid, cmd in processos:
            if pid == meu_pid:
                continue
            if not _comando_envolve_app(cmd, pastas):
                continue
            rodando = True
            if tentativa >= 2:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True,
                        timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    _log(f"Encerrado PID {pid}")
                except subprocess.SubprocessError:
                    pass
        if not rodando:
            break
        time.sleep(1)

    for nome in ("pythonw.exe", "python.exe"):
        exe = destino / "python" / nome
        if not exe.is_file():
            continue
        try:
            out = subprocess.check_output(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    (
                        f"Get-CimInstance Win32_Process -Filter \"Name='{nome}'\" | "
                        "Select-Object ProcessId, ExecutablePath | "
                        "ConvertTo-Csv -NoTypeInformation"
                    ),
                ],
                text=True,
                errors="replace",
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            import csv
            from io import StringIO

            for row in csv.DictReader(StringIO(out)):
                path = (row.get("ExecutablePath") or "").strip()
                if str(exe).lower() not in path.lower():
                    continue
                pid_s = (row.get("ProcessId") or "").strip()
                if pid_s.isdigit() and int(pid_s) != meu_pid:
                    subprocess.run(
                        ["taskkill", "/PID", pid_s, "/F"],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    time.sleep(1.5)


def baixar_zip(url: str, destino: Path, on_progresso) -> None:
    """Baixa o ZIP com callback (bytes_lidos, total ou None)."""
    try:
        import requests
    except ImportError:
        requests = None  # type: ignore

    if requests is not None:
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            lidos = 0
            with open(destino, "wb") as f:
                for bloco in resp.iter_content(65536):
                    if not bloco:
                        continue
                    f.write(bloco)
                    lidos += len(bloco)
                    on_progresso(lidos, total if total > 0 else None)
        return

    # Fallback sem requests (Python embutido mínimo)
    import urllib.request

    with urllib.request.urlopen(url, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        lidos = 0
        with open(destino, "wb") as f:
            while True:
                bloco = resp.read(65536)
                if not bloco:
                    break
                f.write(bloco)
                lidos += len(bloco)
                on_progresso(lidos, total if total > 0 else None)


def extrair_zip(zip_path: Path, pasta: Path) -> Path:
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(pasta)
    subdirs = [p for p in pasta.iterdir() if p.is_dir()]
    if not subdirs:
        raise RuntimeError("ZIP vazio após extração.")
    for d in subdirs:
        if (d / "servidor_app.py").is_file():
            return d
    return subdirs[0]


def copiar_atualizacao(origem: Path, destino: Path, on_arquivo) -> int:
    n = 0
    for py in sorted(origem.glob("*.py")):
        if py.name in EXCLUIR_PY or py.name.startswith("_tmp_"):
            continue
        shutil.copy2(py, destino / py.name)
        on_arquivo(py.name)
        n += 1
    for pasta in PASTAS_COPIAR:
        src = origem / pasta
        if src.is_dir():
            dst = destino / pasta
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
            on_arquivo(f"{pasta}\\")
            n += 1
    for nome in ARQUIVOS_EXTRA:
        src = origem / nome
        if src.is_file():
            shutil.copy2(src, destino / nome)
    pycache = destino / "__pycache__"
    if pycache.is_dir():
        shutil.rmtree(pycache, ignore_errors=True)
    return n


def reiniciar_app(destino: Path) -> None:
    bat = destino / "AppPedidos CLIPP.bat"
    if bat.is_file():
        subprocess.Popen(
            [str(bat)],
            cwd=str(destino),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return
    pyw = destino / "python" / "pythonw.exe"
    if pyw.is_file():
        subprocess.Popen(
            [str(pyw), str(destino / "servidor_app.py")],
            cwd=str(destino),
        )


class JanelaAtualizacao(tk.Tk):
    def __init__(
        self,
        destino: Path,
        repo: str,
        branch: str,
    ) -> None:
        super().__init__()
        self._destino = destino
        self._repo = repo
        self._branch = branch
        self._erro: str | None = None
        self._fechar_apos = 3500

        self.title("AppPedidos CLIPP — Atualização")
        self.geometry("520x220")
        self.minsize(480, 200)
        self.resizable(False, False)
        self.configure(bg="#f4f6fa")
        self.protocol("WM_DELETE_WINDOW", self._ignorar_fechar)

        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass

        cab = tk.Frame(self, bg="#0b3d91", padx=16, pady=12)
        cab.pack(fill=tk.X)
        tk.Label(
            cab, text="Atualizando do GitHub",
            font=("Segoe UI", 13, "bold"), fg="white", bg="#0b3d91",
        ).pack(anchor=tk.W)
        tk.Label(
            cab, text=f"{repo}  •  branch {branch}",
            font=("Segoe UI", 9), fg="#bbdefb", bg="#0b3d91",
        ).pack(anchor=tk.W, pady=(4, 0))

        corpo = tk.Frame(self, bg="#f4f6fa", padx=20, pady=16)
        corpo.pack(fill=tk.BOTH, expand=True)

        self._lbl_status = tk.Label(
            corpo, text="Iniciando…",
            font=("Segoe UI Semibold", 10), fg="#33415c", bg="#f4f6fa", anchor=tk.W,
        )
        self._lbl_status.pack(fill=tk.X)

        self._lbl_detalhe = tk.Label(
            corpo, text="Aguarde — não feche esta janela.",
            font=("Segoe UI", 9), fg="#5a6b85", bg="#f4f6fa", anchor=tk.W,
            wraplength=480, justify=tk.LEFT,
        )
        self._lbl_detalhe.pack(fill=tk.X, pady=(6, 12))

        self._progresso = ttk.Progressbar(corpo, mode="determinate", maximum=100)
        self._progresso.pack(fill=tk.X)
        self._progresso["value"] = 0

        self._lbl_pct = tk.Label(
            corpo, text="0%",
            font=("Segoe UI", 9), fg="#5a6b85", bg="#f4f6fa", anchor=tk.E,
        )
        self._lbl_pct.pack(fill=tk.X, pady=(4, 0))

        self._btn_fechar = ttk.Button(
            corpo, text="Fechar", command=self.destroy, state="disabled",
        )
        self._btn_fechar.pack(pady=(12, 0))

    def _ignorar_fechar(self) -> None:
        if str(self._btn_fechar["state"]) != "disabled":
            self.destroy()

    def _set_progresso(self, valor: float, status: str, detalhe: str = "") -> None:
        v = max(0.0, min(100.0, valor))
        self._progresso["value"] = v
        self._lbl_pct.config(text=f"{int(v)}%")
        self._lbl_status.config(text=status)
        if detalhe:
            self._lbl_detalhe.config(text=detalhe)
        self.update_idletasks()

    def _rodar_atualizacao(self) -> None:
        try:
            self._executar()
        except Exception as exc:  # noqa: BLE001
            self._erro = str(exc)
            _log(f"ERRO: {exc}")
            self.after(0, lambda: self._falhou(exc))

    def _executar(self) -> None:
        repo_nome = self._repo.split("/")[-1]
        zip_url = f"https://github.com/{self._repo}/archive/refs/heads/{self._branch}.zip"

        self.after(0, lambda: self._set_progresso(
            2, "Encerrando o aplicativo…",
            "Fechando o servidor para liberar os arquivos.",
        ))
        _log("Encerrando processos…")
        encerrar_processos_app(self._destino, aguardar_inicial=2.5)

        self.after(0, lambda: self._set_progresso(
            8, "Baixando atualização…", zip_url,
        ))

        temp_base = Path(tempfile.mkdtemp(prefix="AppPedidosCLIPP-git-"))
        zip_path = temp_base / "repo.zip"
        extract_root = temp_base / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        try:
            def prog_download(lidos: int, total: int | None) -> None:
                if total:
                    pct = 8 + (lidos / total) * 52
                    mb = lidos / (1024 * 1024)
                    tot_mb = total / (1024 * 1024)
                    det = f"{mb:.1f} / {tot_mb:.1f} MB"
                else:
                    pct = 8 + min(50, lidos / (1024 * 1024))
                    det = f"{lidos / (1024 * 1024):.1f} MB baixados"
                self.after(0, lambda: self._set_progresso(pct, "Baixando atualização…", det))

            _log(f"Baixando {zip_url}")
            baixar_zip(zip_url, zip_path, prog_download)

            self.after(0, lambda: self._set_progresso(
                65, "Extraindo arquivos…", "Preparando cópia para a instalação.",
            ))
            _log("Extraindo ZIP…")
            origem = extrair_zip(zip_path, extract_root)
            if not (origem / "servidor_app.py").is_file():
                raise RuntimeError("Pacote inválido: servidor_app.py não encontrado.")

            copiados = {"n": 0}

            def on_arquivo(nome: str) -> None:
                copiados["n"] += 1
                pct = 68 + min(22, copiados["n"] * 2)
                self.after(0, lambda: self._set_progresso(
                    pct, "Copiando arquivos…", nome,
                ))

            _log(f"Copiando de {origem} para {self._destino}")
            copiar_atualizacao(origem, self._destino, on_arquivo)

            self.after(0, lambda: self._set_progresso(
                95, "Finalizando…", "Limpando arquivos temporários.",
            ))
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

        self.after(0, lambda: self._set_progresso(
            100, "Atualização concluída!",
            "Reabrindo o AppPedidos CLIPP em instantes…",
        ))
        _log("Reiniciando aplicativo…")
        time.sleep(1.2)
        reiniciar_app(self._destino)
        _log("Concluído.")
        self.after(0, self._concluido)

    def _concluido(self) -> None:
        self._lbl_detalhe.config(
            text="O servidor foi reiniciado. Recarregue a extensão Chrome se necessário.\n"
                 f"Log: {LOG_FILE}",
        )
        self._btn_fechar.config(state="normal")
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass
        self.after(self._fechar_apos, self.destroy)

    def _falhou(self, exc: BaseException) -> None:
        self._set_progresso(
            float(self._progresso["value"] or 0),
            "Falha na atualização",
            f"{exc}\n\nLog: {LOG_FILE}",
        )
        self._btn_fechar.config(state="normal")
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def iniciar(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        threading.Thread(target=self._rodar_atualizacao, daemon=True).start()
        self.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Atualizar AppPedidos CLIPP via GitHub")
    parser.add_argument("--destino", default="", help="Pasta de instalação")
    parser.add_argument("--repositorio", default=GITHUB_REPO_PADRAO)
    parser.add_argument("--branch", default=GITHUB_BRANCH_PADRAO)
    args = parser.parse_args()

    destino = _resolver_destino(args.destino)
    if not (destino / "servidor_app.py").is_file():
        print(f"Instalação não encontrada: {destino}", file=sys.stderr)
        return 1

    if _precisa_admin(destino) and not _eh_admin():
        _reexecutar_como_admin(sys.argv)
        return 0

    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"=== Atualização {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(f"Destino: {destino}\n")
    except OSError:
        pass

    JanelaAtualizacao(destino, args.repositorio, args.branch).iniciar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
