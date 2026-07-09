"""Tela de Gerenciamento de Postagens (Correios).

Lê a fila XX_TB_ETIQUETA_CORREIO (alimentada por trigger na autorização da
NF-e modelo 55) e lista as etiquetas com cliente/destino e status.

O conteúdo vive em `PostagensFrame`, que pode ser embutido como aba (no
servidor da bandeja) ou aberto em janela própria (`TelaPostagens`).

"Gerar Etiqueta" abre o diálogo de pré-postagem (serviço + peso/dimensões por
nota) e cria a pré-postagem nos Correios. "Imprimir" (rótulo PDF) ainda será
ligado em seguida.

Pré-visualização isolada:  py tela_postagens.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import unicodedata
from datetime import datetime
from tkinter import messagebox, ttk

import config as app_config
import correios_api
import db as firebird_db

# Formato do objeto (rótulo legível -> código Correios)
FORMATOS_OBJETO = {
    "Envelope": correios_api.FORMATO_ENVELOPE,
    "Pacote / Caixa": correios_api.FORMATO_PACOTE,
    "Rolo / Cilindro": correios_api.FORMATO_ROLO,
}

# Paleta (estilo Correios)
COR_HEADER = "#0b3d91"
COR_HEADER_TXT = "#ffffff"
COR_ACENTO = "#ffd500"
COR_FUNDO = "#f4f6fa"
COR_LINHA_ALT = "#eef2f9"
COR_BOTAO = "#1a4fd1"
COR_BOTAO_TXT = "#ffffff"

# Cores por código de status (XX_TB_ETQ_STATUS)
CORES_STATUS = {
    "PENDENTE": "#e08600",
    "PROCESSANDO": "#1565c0",
    "GERADA": "#6a1b9a",
    "IMPRESSO": "#00897b",
    "POSTADO": "#2e7d32",
    "ENTREGUE": "#1b5e20",
    "ERRO": "#c62828",
    "CANCELADA": "#757575",
}

# Intervalo da rotina automática de atualização de status (10 minutos) e o
# atraso da 1ª verificação após abrir a aba (logo no início, sem esperar 10 min).
INTERVALO_SYNC_MS = 10 * 60 * 1000
INTERVALO_SYNC_INICIAL_MS = 60 * 1000

# Geração de rótulo: espera curta em 1º plano (caso normal = segundos); se os
# Correios demorarem, o download segue em 2º plano reaproveitando o MESMO recibo.
ROTULO_FG_TENTATIVAS = 8        # ~24s aguardando na tela
ROTULO_FG_INTERVALO = 3.0
INTERVALO_ROTULO_BG_MS = 10 * 1000   # ciclo do download em segundo plano
ROTULO_BG_MAX_S = 30 * 60            # desiste de um recibo após 30 min


# ---------------------------------------------------------------------------
# Rastreio — interpretação de eventos Correios e classificação de status local
# ---------------------------------------------------------------------------

def _parse_dt_correios(valor):
    """Converte data dos Correios (ISO/variações) em datetime; None se falhar."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor
    s = str(valor).strip().replace("Z", "")
    s = s.split("+")[0].split(".")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s[:19])
    except ValueError:
        return None


def _ev_entregue(ev: dict) -> bool:
    desc = (ev.get("descricao") or "").strip().lower()
    cod = (ev.get("codigo") or ev.get("tipo") or "").strip().upper()
    return (
        ("entregue" in desc and "nao entregue" not in desc
         and "não entregue" not in desc)
        or cod in ("BDE", "BDI", "BDR")
    )


def _ev_postagem(ev: dict) -> bool:
    """Evento que representa a POSTAGEM física do objeto nos Correios.

    Importante: 'FC / Etiqueta emitida' (objeto apenas pré-postado) NÃO conta —
    era o que fazia o status virar 'Postado' sozinho sem ter sido postado.
    """
    cod = (ev.get("codigo") or "").strip().upper()
    desc = (ev.get("descricao") or "").strip().lower()
    if cod in ("PO", "RO", "PMT"):
        return True
    return "postado" in desc  # "Objeto postado" / "Postado nos Correios"


def _classificar_situacao(item_prep: dict | None, obj_rastreio: dict | None) -> dict:
    """Status + datas combinando a pré-postagem (statusAtual) e o rastreio.

    - ENTREGUE: evento de entrega no rastreio.
    - POSTADO: **somente** com evento real de postagem no rastreamento
      (PO/RO/PMT ou "objeto postado"). O `statusAtual` da pré-postagem NÃO marca
      Postado — quem confirma a postagem é o rastreio (rodado a cada 10 min).
    - None: apenas pré-postado / etiqueta emitida -> NÃO altera o status local.
    Datas: postagem (evento real de postagem) e previsão (pré-postagem/rastreio).
    """
    out = {"status": None, "dt_postagem": None, "dt_prevista": None,
           "dt_entrega": None}
    item_prep = item_prep or {}
    obj_rastreio = obj_rastreio or {}
    eventos = obj_rastreio.get("eventos") or []

    out["dt_prevista"] = _parse_dt_correios(
        item_prep.get("dtPrevista") or obj_rastreio.get("dtPrevista")
    )

    # Entrega (prioridade máxima)
    for ev in eventos:
        if _ev_entregue(ev):
            out["status"] = "ENTREGUE"
            out["dt_entrega"] = _parse_dt_correios(ev.get("dtHrCriado"))
            break

    # Postagem real: pega o evento de postagem mais antigo (fim da lista)
    postagem_ev = None
    for ev in eventos:
        if _ev_postagem(ev):
            postagem_ev = ev
    if postagem_ev:
        out["dt_postagem"] = _parse_dt_correios(postagem_ev.get("dtHrCriado"))

    if out["status"] is None and postagem_ev:
        # Só o rastreamento confirma a postagem (não o statusAtual da pré-postagem).
        out["status"] = "POSTADO"
        # senão: continua None -> pré-postado/etiqueta emitida não muda o status
    return out


def _dados_rastreio(obj: dict) -> dict:
    """Compat.: classifica somente pelo rastreio (sem a consulta da pré-postagem)."""
    return _classificar_situacao(None, obj)


def _status_por_rastreio(obj: dict) -> str | None:
    """Status local a partir do objeto rastreado (compat.)."""
    return _classificar_situacao(None, obj)["status"]


def _fmt_data_curta(valor) -> str:
    """Data curta dd/mm/aa para a grade ('' se vazio)."""
    if not valor:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%y")
    dt = _parse_dt_correios(valor)
    return dt.strftime("%d/%m/%y") if dt else ""


def _is_atrasado(r: dict) -> bool:
    """True se passou da previsão e ainda não foi entregue/cancelada."""
    prev = r.get("dt_prevista")
    if not isinstance(prev, datetime):
        prev = _parse_dt_correios(prev)
    if not prev:
        return False
    if (r.get("status") or "").upper() in ("ENTREGUE", "CANCELADA"):
        return False
    if r.get("dt_entrega"):
        return False
    return datetime.now().date() > prev.date()


_FORMATO_NOME = {"1": "Envelope", "2": "Pacote / Caixa", "3": "Rolo / Cilindro"}
_FORMATO_COD = {v: k for k, v in _FORMATO_NOME.items()}


# ---------------------------------------------------------------------------
# Embalagens — combo compartilhado e aba de cadastro (XX_TB_EMB_CORREIO)
# ---------------------------------------------------------------------------

def carregar_embalagens_combo(db_cfg: dict) -> list[dict]:
    """Helper: embalagens ativas para popular combos (com cache leve no chamador)."""
    try:
        return firebird_db.listar_embalagens(db_cfg, somente_ativas=True)
    except Exception:  # noqa: BLE001
        return []


class EmbalagensFrame(tk.Frame):
    """Cadastro de embalagens (XX_TB_EMB_CORREIO): dimensões + tara."""

    def __init__(self, parent: tk.Misc, db_cfg: dict | None = None, *,
                 com_cabecalho: bool = True):
        super().__init__(parent, bg=COR_FUNDO)
        self._db_cfg = db_cfg
        self._registros: list[dict] = []
        self._sel_id: int | None = None

        if com_cabecalho:
            self._montar_cabecalho()
        self._montar_corpo()
        self.after(100, self.recarregar)

    def _cfg(self) -> dict:
        if self._db_cfg is None:
            self._db_cfg = app_config.get_db_config()
        return self._db_cfg

    def _montar_cabecalho(self) -> None:
        header = tk.Frame(self, bg=COR_HEADER, height=58)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        tk.Frame(self, bg=COR_ACENTO, height=3).pack(fill=tk.X, side=tk.TOP)
        tk.Label(header, text="📦  Correios", bg=COR_HEADER, fg=COR_HEADER_TXT,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=16)
        tk.Label(header, text="Cadastro de Embalagens", bg=COR_HEADER,
                 fg=COR_HEADER_TXT, font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=4)

    def _montar_corpo(self) -> None:
        corpo = tk.Frame(self, bg=COR_FUNDO, padx=14, pady=12)
        corpo.pack(fill=tk.BOTH, expand=True)

        # Lista (esquerda)
        esq = tk.Frame(corpo, bg=COR_FUNDO)
        esq.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cols = ("nome", "codigo", "dim", "tara", "formato", "flags")
        self.tree = ttk.Treeview(esq, columns=cols, show="headings",
                                 style="Postagens.Treeview", selectmode="browse")
        for cid, txt, w, anc in (
            ("nome", "Nome", 150, tk.W),
            ("codigo", "Código", 80, tk.W),
            ("dim", "C×L×A (cm)", 110, tk.CENTER),
            ("tara", "Tara (g)", 70, tk.CENTER),
            ("formato", "Formato", 110, tk.W),
            ("flags", "Situação", 110, tk.W),
        ):
            self.tree.heading(cid, text=txt)
            self.tree.column(cid, width=w, anchor=anc, stretch=(cid == "nome"))
        scroll = ttk.Scrollbar(esq, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("inativa", foreground="#9aa6b8")
        self.tree.bind("<<TreeviewSelect>>", self._ao_selecionar)

        # Formulário (direita)
        dir_ = tk.Frame(corpo, bg="#ffffff", relief="solid", bd=1, padx=14, pady=12)
        dir_.pack(side=tk.RIGHT, fill=tk.Y, padx=(14, 0))
        tk.Label(dir_, text="Embalagem", bg="#ffffff", fg="#33415c",
                 font=("Segoe UI Semibold", 11)).grid(row=0, column=0, columnspan=2,
                                                       sticky=tk.W, pady=(0, 8))

        self.var_nome = tk.StringVar()
        self.var_codigo = tk.StringVar()
        self.var_compr = tk.StringVar()
        self.var_larg = tk.StringVar()
        self.var_alt = tk.StringVar()
        self.var_tara = tk.StringVar()
        self.var_formato = tk.StringVar(value="Pacote / Caixa")
        self.var_ordem = tk.StringVar(value="0")
        self.var_ativo = tk.BooleanVar(value=True)
        self.var_padrao = tk.BooleanVar(value=False)

        def linha(r, rotulo, var, width=24):
            tk.Label(dir_, text=rotulo, bg="#ffffff", fg="#33415c",
                     font=("Segoe UI Semibold", 9)).grid(row=r, column=0, sticky=tk.W, pady=4)
            ent = ttk.Entry(dir_, textvariable=var, width=width)
            ent.grid(row=r, column=1, sticky=tk.W, pady=4)
            return ent

        linha(1, "Nome:", self.var_nome)
        linha(2, "Código:", self.var_codigo, 14)
        # Dimensões
        tk.Label(dir_, text="Dimensões (cm):", bg="#ffffff", fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=3, column=0, sticky=tk.W, pady=4)
        fdim = tk.Frame(dir_, bg="#ffffff")
        fdim.grid(row=3, column=1, sticky=tk.W, pady=4)
        for i, (lbl, var) in enumerate((("C", self.var_compr), ("L", self.var_larg),
                                        ("A", self.var_alt))):
            tk.Label(fdim, text=lbl, bg="#ffffff", fg="#5a6b85",
                     font=("Segoe UI", 9)).grid(row=0, column=i * 2, padx=(0 if i == 0 else 6, 2))
            ttk.Entry(fdim, textvariable=var, width=5).grid(row=0, column=i * 2 + 1)
        linha(4, "Tara (g):", self.var_tara, 10)
        tk.Label(dir_, text="Formato:", bg="#ffffff", fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=5, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(dir_, textvariable=self.var_formato, state="readonly",
                     values=list(_FORMATO_COD.keys()), width=22).grid(
            row=5, column=1, sticky=tk.W, pady=4)
        linha(6, "Ordem:", self.var_ordem, 6)
        tk.Checkbutton(dir_, text="Ativa", variable=self.var_ativo, bg="#ffffff",
                       font=("Segoe UI", 9)).grid(row=7, column=1, sticky=tk.W, pady=2)
        tk.Checkbutton(dir_, text="Padrão (sugerida)", variable=self.var_padrao,
                       bg="#ffffff", font=("Segoe UI", 9)).grid(row=8, column=1,
                                                                sticky=tk.W, pady=2)

        botoes = tk.Frame(dir_, bg="#ffffff")
        botoes.grid(row=9, column=0, columnspan=2, sticky="we", pady=(12, 0))
        tk.Button(botoes, text="Novo", command=self._novo, bg="#e9edf5", fg="#33415c",
                  font=("Segoe UI Semibold", 9), relief="flat", padx=12, pady=5,
                  cursor="hand2").pack(side=tk.LEFT)
        self.btn_excluir = tk.Button(botoes, text="Excluir", command=self._excluir,
                                     bg="#c62828", fg="#fff", font=("Segoe UI Semibold", 9),
                                     relief="flat", padx=12, pady=5, cursor="hand2",
                                     state=tk.DISABLED)
        self.btn_excluir.pack(side=tk.LEFT, padx=6)
        tk.Button(botoes, text="Salvar", command=self._salvar, bg=COR_BOTAO,
                  fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9), relief="flat",
                  padx=16, pady=5, cursor="hand2", activebackground="#1540ad").pack(
            side=tk.RIGHT)

    # ------------------------------------------------------------- ações
    def recarregar(self) -> None:
        try:
            self._registros = firebird_db.listar_embalagens(self._cfg())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Embalagens", f"Erro ao carregar:\n{exc}", parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        for e in self._registros:
            dim = "×".join(_fmt_num(e[k]) for k in ("comprimento", "largura", "altura"))
            sit = []
            if e["padrao"] == "S":
                sit.append("Padrão")
            sit.append("Ativa" if e["ativo"] == "S" else "Inativa")
            tags = () if e["ativo"] == "S" else ("inativa",)
            self.tree.insert("", tk.END, iid=str(e["id_emb"]), tags=tags, values=(
                e["nome"], e["codigo"], dim, _fmt_num(e["peso_tara"]),
                _FORMATO_NOME.get(e["formato"], e["formato"]), " · ".join(sit),
            ))

    def _ao_selecionar(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        e = next((x for x in self._registros if str(x["id_emb"]) == sel[0]), None)
        if not e:
            return
        self._sel_id = e["id_emb"]
        self.var_nome.set(e["nome"])
        self.var_codigo.set(e["codigo"])
        self.var_compr.set(_fmt_num(e["comprimento"]))
        self.var_larg.set(_fmt_num(e["largura"]))
        self.var_alt.set(_fmt_num(e["altura"]))
        self.var_tara.set(_fmt_num(e["peso_tara"]))
        self.var_formato.set(_FORMATO_NOME.get(e["formato"], "Pacote / Caixa"))
        self.var_ordem.set(str(e["ordem"]))
        self.var_ativo.set(e["ativo"] == "S")
        self.var_padrao.set(e["padrao"] == "S")
        self.btn_excluir.configure(state=tk.NORMAL)

    def _novo(self) -> None:
        self._sel_id = None
        for v in (self.var_nome, self.var_codigo, self.var_compr, self.var_larg,
                  self.var_alt, self.var_tara):
            v.set("")
        self.var_formato.set("Pacote / Caixa")
        self.var_ordem.set("0")
        self.var_ativo.set(True)
        self.var_padrao.set(False)
        if self.tree.selection():
            self.tree.selection_remove(self.tree.selection())
        self.btn_excluir.configure(state=tk.DISABLED)

    def _salvar(self) -> None:
        dados = {
            "nome": self.var_nome.get(),
            "codigo": self.var_codigo.get(),
            "comprimento": self.var_compr.get(),
            "largura": self.var_larg.get(),
            "altura": self.var_alt.get(),
            "peso_tara": self.var_tara.get(),
            "formato": _FORMATO_COD.get(self.var_formato.get(), "2"),
            "ordem": self.var_ordem.get(),
            "ativo": "S" if self.var_ativo.get() else "N",
            "padrao": "S" if self.var_padrao.get() else "N",
        }
        try:
            novo_id = firebird_db.salvar_embalagem(self._cfg(), dados, self._sel_id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Embalagens", f"Erro ao salvar:\n{exc}", parent=self)
            return
        self._sel_id = novo_id
        self.recarregar()
        if self.tree.exists(str(novo_id)):
            self.tree.selection_set(str(novo_id))
        messagebox.showinfo("Embalagens", "Embalagem salva.", parent=self)

    def _excluir(self) -> None:
        if not self._sel_id:
            return
        if not messagebox.askyesno("Excluir", "Excluir esta embalagem?", parent=self):
            return
        try:
            firebird_db.excluir_embalagem(self._cfg(), self._sel_id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Embalagens", f"Erro ao excluir:\n{exc}", parent=self)
            return
        self._novo()
        self.recarregar()


def _fmt_num(v) -> str:
    """Número curto para exibição (sem .0 desnecessário)."""
    if v in (None, ""):
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


# ---------------------------------------------------------------------------
# Postagens — grade principal: fila, sync rastreio, geração e impressão de rótulos
# ---------------------------------------------------------------------------

class PostagensFrame(tk.Frame):
    """Painel de gerenciamento de postagens (embutível como aba ou janela)."""

    def __init__(
        self,
        parent: tk.Misc,
        db_cfg: dict | None = None,
        *,
        com_cabecalho: bool = True,
    ):
        super().__init__(parent, bg=COR_FUNDO)
        self._db_cfg = db_cfg
        self._registros: list[dict] = []
        self._status_map: dict[str, str] = {}  # descrição -> código
        self._carregando = False
        self._correios: correios_api.CorreiosClient | None = None
        self._marcados: set[str] = set()
        self._grupos: dict | None = None
        self._gerando_lote = False
        # Embalagens (seletor por linha)
        self._embalagens: list[dict] = []
        self._emb_by_id: dict[int, dict] = {}
        self._emb_padrao_id: int | None = None
        self._emb_minipac_id: int | None = None
        self._emb_por_etq: dict[str, int] = {}  # id_etiqueta -> id_emb
        self._combo_emb: ttk.Combobox | None = None
        self._combo_iid: str | None = None
        self._entry_peso: ttk.Entry | None = None
        self._entry_iid: str | None = None
        self._atrasados_alertados: set[str] = set()
        self._qtd_atrasados = 0
        self._filtro_atraso_on = False
        self._ultima_sync_auto: datetime | None = None
        self._sync_after_id = None
        # download de rótulo em segundo plano (quando os Correios demoram)
        self._rotulos_pendentes: list[dict] = []
        self._rotulo_bg_after_id = None
        # pré-download: ao GERAR a etiqueta já baixa o rótulo e guarda em cache,
        # para a impressão ser instantânea depois (sem esperar os Correios).
        self._rotulo_cache: dict[str, str] = {}   # id_prepostagem -> caminho PDF
        self._prefetch_pend: list[dict] = []      # fila de pré-download
        self._prefetch_after_id = None

        self._configurar_estilo()
        if com_cabecalho:
            self._montar_cabecalho()
        self._montar_filtros()
        self._montar_tabela()
        self._montar_rodape()

        self._carregar_status()
        self.after(100, self.recarregar)
        self._agendar_sync_auto(INTERVALO_SYNC_INICIAL_MS)

    # --------------------------------------------------------------- infra
    def _cfg(self) -> dict:
        if self._db_cfg is None:
            self._db_cfg = app_config.get_db_config()
        return self._db_cfg

    def _configurar_estilo(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Postagens.Treeview",
            background="#ffffff", fieldbackground="#ffffff", foreground="#222222",
            rowheight=32, borderwidth=1, relief="solid", bordercolor="#cdd6e4",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Postagens.Treeview.Heading",
            background="#dbe3f0", foreground="#22324d",
            font=("Segoe UI Semibold", 10), relief="raised", borderwidth=1,
            padding=(8, 6),
        )
        style.map(
            "Postagens.Treeview",
            background=[("selected", "#cfe0ff")], foreground=[("selected", "#0b2a66")],
        )
        style.configure("Filtro.TLabel", background=COR_FUNDO, foreground="#33415c",
                        font=("Segoe UI Semibold", 9))

    # ----------------------------------------------------------- cabeçalho
    def _montar_cabecalho(self) -> None:
        header = tk.Frame(self, bg=COR_HEADER, height=58)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        tk.Frame(self, bg=COR_ACENTO, height=3).pack(fill=tk.X, side=tk.TOP)

        tk.Label(header, text="📦  Correios", bg=COR_HEADER, fg=COR_HEADER_TXT,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=16)
        tk.Label(header, text="Gerenciamento de Postagens", bg=COR_HEADER,
                 fg=COR_HEADER_TXT, font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=4)

    # ------------------------------------------------------------- filtros
    def _montar_filtros(self) -> None:
        barra = tk.Frame(self, bg=COR_FUNDO, pady=10, padx=14)
        barra.pack(fill=tk.X)

        ttk.Label(barra, text="Status:", style="Filtro.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.var_status = tk.StringVar(value="Todos")
        self.cb_status = ttk.Combobox(
            barra, textvariable=self.var_status, values=["Todos"],
            state="readonly", width=20,
        )
        self.cb_status.grid(row=0, column=1, padx=(0, 16))
        self.cb_status.bind("<<ComboboxSelected>>", lambda _e: self.recarregar())

        ttk.Label(barra, text="Período (inclusão):", style="Filtro.TLabel").grid(row=0, column=2, padx=(0, 6))
        self.var_data_ini = tk.StringVar()
        self.var_data_fim = tk.StringVar()
        ttk.Entry(barra, textvariable=self.var_data_ini, width=11).grid(row=0, column=3)
        ttk.Label(barra, text="–", style="Filtro.TLabel").grid(row=0, column=4, padx=4)
        ttk.Entry(barra, textvariable=self.var_data_fim, width=11).grid(row=0, column=5, padx=(0, 16))

        tk.Button(barra, text="Filtrar", command=self.recarregar, bg=COR_BOTAO,
                  fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9), relief="flat",
                  padx=16, pady=4, cursor="hand2", activebackground="#1540ad").grid(row=0, column=6)
        tk.Button(barra, text="Atualizar", command=self.recarregar, bg="#e9edf5",
                  fg="#33415c", font=("Segoe UI Semibold", 9), relief="flat",
                  padx=12, pady=4, cursor="hand2").grid(row=0, column=7, padx=(8, 0))

        ttk.Label(barra, text="Buscar:", style="Filtro.TLabel").grid(row=0, column=9, padx=(16, 6), sticky=tk.E)
        self.var_busca = tk.StringVar()
        busca = ttk.Entry(barra, textvariable=self.var_busca, width=24)
        busca.grid(row=0, column=10, sticky=tk.E)
        busca.bind("<Return>", lambda _e: self.recarregar())
        barra.columnconfigure(8, weight=1)

    # -------------------------------------------------------------- tabela
    def _montar_tabela(self) -> None:
        wrap = tk.Frame(self, bg=COR_FUNDO, padx=14)
        wrap.pack(fill=tk.BOTH, expand=True)

        colunas = ("sel", "numero", "cliente", "destino", "envio", "peso", "status",
                   "geracao", "postagem", "previsao", "entrega",
                   "embalagem", "imprimir", "etiqueta", "rastreio")
        self.tree = ttk.Treeview(
            wrap, columns=colunas, show="headings",
            style="Postagens.Treeview", selectmode="browse",
        )
        self.tree.heading("sel", text="☐", anchor=tk.CENTER, command=self._alternar_todas)
        self.tree.heading("numero", text="NF", anchor=tk.W)
        self.tree.heading("cliente", text="Cliente", anchor=tk.W)
        self.tree.heading("destino", text="Destino", anchor=tk.W)
        self.tree.heading("envio", text="Envio", anchor=tk.W)
        self.tree.heading("peso", text="Peso (g)", anchor=tk.CENTER)
        self.tree.heading("status", text="Status", anchor=tk.W)
        self.tree.heading("geracao", text="Geração", anchor=tk.CENTER)
        self.tree.heading("postagem", text="Postagem", anchor=tk.CENTER)
        self.tree.heading("previsao", text="Previsão", anchor=tk.CENTER)
        self.tree.heading("entrega", text="Entrega", anchor=tk.CENTER)
        self.tree.heading("embalagem", text="Embalagem", anchor=tk.W)
        self.tree.heading("imprimir", text="", anchor=tk.CENTER)
        self.tree.heading("etiqueta", text="", anchor=tk.CENTER)
        self.tree.heading("rastreio", text="", anchor=tk.CENTER)
        self.tree.column("sel", width=36, anchor=tk.CENTER, stretch=False)
        self.tree.column("numero", width=64, anchor=tk.W, stretch=False)
        self.tree.column("cliente", width=170, anchor=tk.W)
        self.tree.column("destino", width=140, anchor=tk.W)
        self.tree.column("envio", width=96, anchor=tk.W, stretch=False)
        self.tree.column("peso", width=72, anchor=tk.CENTER, stretch=False)
        self.tree.column("status", width=100, anchor=tk.W, stretch=False)
        self.tree.column("geracao", width=78, anchor=tk.CENTER, stretch=False)
        self.tree.column("postagem", width=78, anchor=tk.CENTER, stretch=False)
        self.tree.column("previsao", width=78, anchor=tk.CENTER, stretch=False)
        self.tree.column("entrega", width=78, anchor=tk.CENTER, stretch=False)
        self.tree.column("embalagem", width=148, anchor=tk.W, stretch=False)
        self.tree.column("imprimir", width=92, anchor=tk.CENTER, stretch=False)
        self.tree.column("etiqueta", width=118, anchor=tk.CENTER, stretch=False)
        self.tree.column("rastreio", width=92, anchor=tk.CENTER, stretch=False)

        scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(0, 4))

        for codigo, cor in CORES_STATUS.items():
            self.tree.tag_configure(f"st_{codigo}", foreground=cor)
        self.tree.tag_configure("par", background=COR_LINHA_ALT)
        self.tree.tag_configure("atrasado", foreground="#c62828",
                                font=("Segoe UI Semibold", 10))

        self.tree.bind("<Button-1>", self._ao_clicar)

    def _montar_rodape(self) -> None:
        rodape = tk.Frame(self, bg=COR_FUNDO, padx=14, pady=8)
        rodape.pack(fill=tk.X)
        self.lbl_total = tk.Label(rodape, text="", bg=COR_FUNDO, fg="#5a6b85",
                                  font=("Segoe UI", 9))
        self.lbl_total.pack(side=tk.LEFT)
        self.lbl_alerta = tk.Label(rodape, text="", bg=COR_FUNDO, fg="#c62828",
                                   font=("Segoe UI Semibold", 9), cursor="hand2")
        self.lbl_alerta.pack(side=tk.LEFT, padx=(12, 0))
        self.lbl_alerta.bind("<Button-1>", lambda _e: self._filtrar_atrasados())
        tk.Button(
            rodape, text="🔄  Atualizar status", command=self._acao_sincronizar_status,
            bg="#e9edf5", fg="#33415c", font=("Segoe UI Semibold", 9),
            relief="flat", padx=12, pady=5, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.btn_lote = tk.Button(
            rodape, text="🏷  Gerar selecionadas", command=self._acao_gerar_selecionadas,
            bg=COR_BOTAO, fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9),
            relief="flat", padx=16, pady=5, cursor="hand2",
            activebackground="#1540ad", state=tk.DISABLED,
        )
        self.btn_lote.pack(side=tk.RIGHT)
        self.btn_imprimir_lote = tk.Button(
            rodape, text="🖨  Imprimir selecionadas", command=self._acao_imprimir_selecionadas,
            bg="#00897b", fg="#ffffff", font=("Segoe UI Semibold", 9),
            relief="flat", padx=16, pady=5, cursor="hand2",
            activebackground="#00695c", state=tk.DISABLED,
        )
        self.btn_imprimir_lote.pack(side=tk.RIGHT, padx=(0, 8))
        self.btn_cancelar_lote = tk.Button(
            rodape, text="✖  Cancelar pré-postagem", command=self._acao_cancelar_selecionadas,
            bg="#c62828", fg="#ffffff", font=("Segoe UI Semibold", 9),
            relief="flat", padx=16, pady=5, cursor="hand2",
            activebackground="#a31f1f", state=tk.DISABLED,
        )
        self.btn_cancelar_lote.pack(side=tk.RIGHT, padx=(0, 8))
        tk.Button(
            rodape, text="Limpar seleção", command=self._limpar_marcacoes,
            bg="#e9edf5", fg="#33415c", font=("Segoe UI Semibold", 9),
            relief="flat", padx=12, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------ carregar
    def _carregar_status(self) -> None:
        try:
            status = firebird_db.listar_status_etiqueta(self._cfg())
        except Exception:
            status = [{"codigo": c, "descricao": d}
                      for c, d in firebird_db.ETIQUETA_STATUS_PADRAO]
        self._status_map = {s["descricao"]: s["codigo"] for s in status}
        self.cb_status.configure(values=["Todos"] + [s["descricao"] for s in status])

    def _status_codigo_selecionado(self) -> str | None:
        desc = self.var_status.get()
        if desc == "Todos":
            return None
        return self._status_map.get(desc, desc)

    def recarregar(self) -> None:
        if self._carregando:
            return
        self._carregando = True
        self.lbl_total.configure(text="Carregando...", fg="#5a6b85")
        status = self._status_codigo_selecionado()
        busca = self.var_busca.get().strip()

        def trabalho():
            embalagens: list[dict] = []
            try:
                registros = firebird_db.listar_etiquetas_correio(
                    self._cfg(), status=status, busca=busca
                )
                erro = None
            except Exception as exc:  # noqa: BLE001
                registros, erro = [], str(exc)
            try:
                embalagens = firebird_db.listar_embalagens(
                    self._cfg(), somente_ativas=True
                )
            except Exception:  # noqa: BLE001
                embalagens = []
            self.after(0, lambda: self._aplicar_registros(registros, erro, embalagens))

        threading.Thread(target=trabalho, daemon=True).start()

    def _aplicar_registros(self, registros: list[dict], erro: str | None,
                           embalagens: list[dict] | None = None) -> None:
        self._carregando = False
        if erro:
            self.lbl_total.configure(
                text=f"Erro ao ler etiquetas: {erro[:120]}", fg="#c62828"
            )
            return
        self._fechar_combo()
        if embalagens is not None:
            self._embalagens = embalagens
            self._emb_by_id = {e["id_emb"]: e for e in embalagens}
            self._emb_padrao_id = next(
                (e["id_emb"] for e in embalagens if e.get("padrao") == "S"), None
            )
            # Embalagem usada automaticamente quando o envio é "Mini Envios".
            self._emb_minipac_id = next(
                (
                    e["id_emb"]
                    for e in embalagens
                    if "minipac"
                    in (e.get("nome") or "").lower().replace(" ", "").replace("-", "")
                ),
                None,
            )
        registros = self._filtrar_data(registros)
        self._registros = registros
        self._marcados.clear()
        self.tree.heading("sel", text="☐")
        self.tree.delete(*self.tree.get_children())
        atrasados = 0
        for i, r in enumerate(registros):
            iid = str(r["id_etiqueta"])
            atrasado = _is_atrasado(r)
            if atrasado:
                atrasados += 1
            tags = ["atrasado"] if atrasado else [f"st_{r['status']}"]
            if i % 2:
                tags.append("par")
            if iid not in self._emb_por_etq:
                if r.get("id_emb") is not None:
                    self._emb_por_etq[iid] = int(r["id_emb"])
                # Mini Envios → traz MiniPac automaticamente; senão, a padrão.
                elif "mini" in self._rotulo_envio(r).lower() and self._emb_minipac_id:
                    self._emb_por_etq[iid] = self._emb_minipac_id
                elif self._emb_padrao_id is not None:
                    self._emb_por_etq[iid] = self._emb_padrao_id
            self.tree.insert(
                "", tk.END, iid=iid,
                values=(
                    "☐",
                    r["nf_numero"] if r["nf_numero"] is not None else "",
                    r.get("cliente_nome", ""),
                    r["destino"],
                    self._texto_envio(r),
                    self._texto_peso(r),
                    r["status_desc"],
                    _fmt_data_curta(r.get("dt_geracao")),
                    _fmt_data_curta(r.get("dt_postagem")),
                    _fmt_data_curta(r.get("dt_prevista")),
                    _fmt_data_curta(r.get("dt_entrega")),
                    self._texto_emb(iid),
                    "🖨  Imprimir",
                    "🏷  Gerar Etiqueta",
                    "🚚  Rastrear" if r.get("cod_rastreio") else "",
                ),
                tags=tuple(tags),
            )
        self._atualizar_botao_lote()
        self.lbl_total.configure(text=self._texto_rodape(len(registros)), fg="#5a6b85")
        self._atualizar_alerta_atraso(registros, atrasados)

    # ----------------------------------------------------- alerta de atraso
    def _atualizar_alerta_atraso(self, registros: list[dict], atrasados: int) -> None:
        self._filtro_atraso_on = False
        self._qtd_atrasados = atrasados
        self.lbl_alerta.configure(
            text=(f"⚠ {atrasados} atrasada(s) — clique para filtrar" if atrasados else "")
        )
        atuais = {str(r["id_etiqueta"]) for r in registros if _is_atrasado(r)}
        novos = [r for r in registros
                 if _is_atrasado(r)
                 and str(r["id_etiqueta"]) not in self._atrasados_alertados]
        self._atrasados_alertados &= atuais
        if not novos:
            return
        for r in novos:
            self._atrasados_alertados.add(str(r["id_etiqueta"]))
        linhas = "\n".join(
            f"• NF {r.get('nf_numero')} — {r.get('cliente_nome', '')} "
            f"(previsão {_fmt_data_curta(r.get('dt_prevista'))})"
            for r in novos[:15]
        )
        extra = f"\n... e mais {len(novos) - 15}." if len(novos) > 15 else ""
        messagebox.showwarning(
            "Entregas atrasadas",
            f"{len(novos)} objeto(s) passaram da previsão de entrega e ainda não "
            "constam como entregues:\n\n" + linhas + extra
            + "\n\nVerifique o rastreio para contestar / abrir chamado nos Correios.",
            parent=self,
        )

    def _filtrar_atrasados(self) -> None:
        if not self._qtd_atrasados:
            return
        if self._filtro_atraso_on:
            for r in self._registros:
                iid = str(r["id_etiqueta"])
                if self.tree.exists(iid):
                    self.tree.reattach(iid, "", "end")
            self._filtro_atraso_on = False
            self.lbl_alerta.configure(
                text=f"⚠ {self._qtd_atrasados} atrasada(s) — clique para filtrar")
        else:
            for r in self._registros:
                iid = str(r["id_etiqueta"])
                if self.tree.exists(iid) and not _is_atrasado(r):
                    self.tree.detach(iid)
            self._filtro_atraso_on = True
            self.lbl_alerta.configure(
                text="⚠ mostrando só atrasadas — clique para ver todas")

    # --------------------------------------------------------- parâmetros
    _BLOQUEIA_EDIT_PARAMS = frozenset({"IMPRESSO", "POSTADO", "ENTREGUE", "CANCELADA"})

    def _pode_editar_parametros(self, reg: dict | None) -> bool:
        if not reg:
            return False
        return (reg.get("status") or "").upper() not in self._BLOQUEIA_EDIT_PARAMS

    def _rotulo_envio(self, r: dict) -> str:
        cod = (r.get("cod_servico") or "").strip()
        if cod:
            return correios_api.SERVICOS_POR_CODIGO.get(cod, cod)
        env = (r.get("envio_obs") or "").strip()
        if not env:
            return ""
        u = env.upper()
        if "MINI" in u:
            return "Mini Envios"
        if "SEDEX" in u:
            return "SEDEX"
        if "PAC" in u:
            return "PAC"
        return env

    def _texto_envio(self, r: dict) -> str:
        rot = self._rotulo_envio(r)
        return rot if rot else "▾ escolher"

    def _texto_peso(self, r: dict) -> str:
        p = r.get("peso")
        if p in (None, "", 0):
            return "▾ informar"
        try:
            v = float(p)
            return str(int(v)) if v == int(v) else str(v)
        except (TypeError, ValueError):
            return "▾ informar"

    def _texto_emb(self, iid: str) -> str:
        eid = self._emb_por_etq.get(iid)
        e = self._emb_by_id.get(eid) if eid is not None else None
        return f"▾ {e['nome']}" if e else "▾ escolher"

    def _embalagem_da_linha(self, iid: str) -> dict | None:
        eid = self._emb_por_etq.get(iid)
        return self._emb_by_id.get(eid) if eid is not None else None

    def _abrir_combo_envio(self, iid: str) -> None:
        self._fechar_overlays()
        reg = next((r for r in self._registros if str(r["id_etiqueta"]) == iid), None)
        if not self._pode_editar_parametros(reg):
            messagebox.showinfo(
                "Envio",
                "Etiqueta já impressa/postada — não é possível alterar o serviço.",
                parent=self,
            )
            return
        bbox = self.tree.bbox(iid, "envio")
        if not bbox:
            return
        x, y, w, h = bbox
        nomes = list(correios_api.SERVICOS.keys())
        cb = ttk.Combobox(self.tree, values=nomes, state="readonly")
        atual = self._rotulo_envio(reg) if reg else ""
        cb.set(atual if atual in nomes else nomes[0])
        cb.place(x=x, y=y, width=w, height=max(h, 24))
        cb.focus_set()
        self._combo_emb = cb
        self._combo_iid = iid
        self._combo_tipo = "envio"
        cb.bind("<<ComboboxSelected>>", self._on_combo_envio)
        cb.bind("<FocusOut>", lambda _e: self._fechar_overlays())
        cb.bind("<Escape>", lambda _e: self._fechar_overlays())

    def _on_combo_envio(self, _e=None) -> None:
        cb, iid = self._combo_emb, self._combo_iid
        if not cb or not iid:
            return
        nome = cb.get().strip()
        cod = correios_api.SERVICOS.get(nome, "")
        reg = next((r for r in self._registros if str(r["id_etiqueta"]) == iid), None)
        self._fechar_overlays()
        if not reg or not cod:
            return
        try:
            firebird_db.atualizar_parametros_etiqueta(
                self._cfg(), reg["id_etiqueta"], cod_servico=cod)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Envio", f"Não foi possível salvar:\n{exc}", parent=self)
            return
        reg["cod_servico"] = cod
        if self.tree.exists(iid):
            self.tree.set(iid, "envio", self._texto_envio(reg))
        # Mini Envios → sugere embalagem MiniPac automaticamente.
        if "mini" in nome.lower() and self._emb_minipac_id:
            self._emb_por_etq[iid] = self._emb_minipac_id
            try:
                firebird_db.atualizar_parametros_etiqueta(
                    self._cfg(), reg["id_etiqueta"], id_emb=self._emb_minipac_id)
                reg["id_emb"] = self._emb_minipac_id
            except Exception:  # noqa: BLE001
                pass
            if self.tree.exists(iid):
                self.tree.set(iid, "embalagem", self._texto_emb(iid))

    def _abrir_entry_peso(self, iid: str) -> None:
        self._fechar_overlays()
        reg = next((r for r in self._registros if str(r["id_etiqueta"]) == iid), None)
        if not self._pode_editar_parametros(reg):
            messagebox.showinfo(
                "Peso",
                "Etiqueta já impressa/postada — não é possível alterar o peso.",
                parent=self,
            )
            return
        bbox = self.tree.bbox(iid, "peso")
        if not bbox:
            return
        x, y, w, h = bbox
        var = tk.StringVar(value="" if not reg or not reg.get("peso") else self._texto_peso(reg))
        ent = ttk.Entry(self.tree, textvariable=var, width=8, justify=tk.CENTER)
        ent.place(x=x, y=y, width=w, height=max(h, 24))
        ent.focus_set()
        ent.select_range(0, tk.END)
        self._entry_peso = ent
        self._entry_iid = iid
        self._entry_var = var

        def salvar(_e=None):
            self._salvar_peso_linha(iid, var.get())

        ent.bind("<Return>", salvar)
        ent.bind("<FocusOut>", salvar)
        ent.bind("<Escape>", lambda _e: self._fechar_overlays())

    def _salvar_peso_linha(self, iid: str, texto: str) -> None:
        if getattr(self, "_entry_iid", None) != iid:
            return
        reg = next((r for r in self._registros if str(r["id_etiqueta"]) == iid), None)
        self._fechar_overlays()
        if not reg:
            return
        t = (texto or "").strip().replace(",", ".")
        if not t or t == "▾ informar":
            return
        try:
            peso = float(t)
            if peso <= 0:
                raise ValueError("peso inválido")
        except ValueError:
            messagebox.showwarning("Peso", "Informe o peso em gramas (número positivo).",
                                   parent=self)
            return
        try:
            firebird_db.atualizar_parametros_etiqueta(
                self._cfg(), reg["id_etiqueta"], peso=peso)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Peso", f"Não foi possível salvar:\n{exc}", parent=self)
            return
        reg["peso"] = peso
        if self.tree.exists(iid):
            self.tree.set(iid, "peso", self._texto_peso(reg))

    def _abrir_combo_emb(self, iid: str) -> None:
        self._fechar_overlays()
        reg = next((r for r in self._registros if str(r["id_etiqueta"]) == iid), None)
        if not self._pode_editar_parametros(reg):
            messagebox.showinfo(
                "Embalagem",
                "Etiqueta já impressa/postada — não é possível alterar a embalagem.",
                parent=self,
            )
            return
        if not self._embalagens:
            messagebox.showinfo(
                "Embalagens",
                "Nenhuma embalagem ativa. Cadastre na aba 'Embalagens' primeiro.",
                parent=self,
            )
            return
        bbox = self.tree.bbox(iid, "embalagem")
        if not bbox:
            return
        x, y, w, h = bbox
        nomes = ["(nenhuma)"] + [e["nome"] for e in self._embalagens]
        cb = ttk.Combobox(self.tree, values=nomes, state="readonly")
        atual = self._embalagem_da_linha(iid)
        cb.set(atual["nome"] if atual else "(nenhuma)")
        cb.place(x=x, y=y, width=w, height=max(h, 24))
        cb.focus_set()
        self._combo_emb = cb
        self._combo_iid = iid
        cb.bind("<<ComboboxSelected>>", self._on_combo_emb)
        cb.bind("<FocusOut>", lambda _e: self._fechar_overlays())
        cb.bind("<Escape>", lambda _e: self._fechar_overlays())

    def _on_combo_emb(self, _e=None) -> None:
        cb, iid = self._combo_emb, self._combo_iid
        if not cb or not iid:
            return
        nome = cb.get()
        reg = next((r for r in self._registros if str(r["id_etiqueta"]) == iid), None)
        id_emb = None
        limpar = False
        if nome == "(nenhuma)":
            self._emb_por_etq.pop(iid, None)
            limpar = True
        else:
            e = next((x for x in self._embalagens if x["nome"] == nome), None)
            if e:
                self._emb_por_etq[iid] = e["id_emb"]
                id_emb = e["id_emb"]
        self._fechar_overlays()
        if reg:
            try:
                if limpar:
                    firebird_db.atualizar_parametros_etiqueta(
                        self._cfg(), reg["id_etiqueta"], limpar_emb=True)
                    reg["id_emb"] = None
                elif id_emb is not None:
                    firebird_db.atualizar_parametros_etiqueta(
                        self._cfg(), reg["id_etiqueta"], id_emb=id_emb)
                    reg["id_emb"] = id_emb
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Embalagem", f"Não foi possível salvar:\n{exc}",
                                     parent=self)
        if self.tree.exists(iid):
            self.tree.set(iid, "embalagem", self._texto_emb(iid))

    def _fechar_overlays(self) -> None:
        cb = getattr(self, "_combo_emb", None)
        if cb is not None:
            try:
                cb.destroy()
            except tk.TclError:
                pass
        self._combo_emb = None
        self._combo_iid = None
        ent = getattr(self, "_entry_peso", None)
        if ent is not None:
            try:
                ent.destroy()
            except tk.TclError:
                pass
        self._entry_peso = None
        self._entry_iid = None

    def _fechar_combo(self) -> None:
        self._fechar_overlays()

    def _filtrar_data(self, registros: list[dict]) -> list[dict]:
        ini = _parse_data(self.var_data_ini.get())
        fim = _parse_data(self.var_data_fim.get())
        if not ini and not fim:
            return registros
        out = []
        for r in registros:
            dt = r.get("dt_inclusao")
            data = dt.date() if isinstance(dt, datetime) else None
            if data is None:
                continue
            if ini and data < ini:
                continue
            if fim and data > fim:
                continue
            out.append(r)
        return out

    # --------------------------------------------------------------- ações
    def _ao_clicar(self, event: tk.Event) -> None:
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        coluna = self.tree.identify_column(event.x)
        linha = self.tree.identify_row(event.y)
        if not linha:
            return
        if coluna == "#1":
            self._toggle_marcado(linha)
            return
        if coluna == "#5":
            self._abrir_combo_envio(linha)
            return
        if coluna == "#6":
            self._abrir_entry_peso(linha)
            return
        if coluna == "#12":
            self._abrir_combo_emb(linha)
            return
        registro = next((r for r in self._registros if str(r["id_etiqueta"]) == linha), None)
        if not registro:
            return
        if coluna == "#13":
            self._acao_imprimir(registro)
        elif coluna == "#14":
            self._acao_gerar_etiqueta(registro, linha)
        elif coluna == "#15":
            self._acao_rastrear(registro)

    # ----------------------------------------------------------- seleção
    def _toggle_marcado(self, iid: str) -> None:
        if iid in self._marcados:
            self._marcados.discard(iid)
            self.tree.set(iid, "sel", "☐")
        else:
            self._marcados.add(iid)
            self.tree.set(iid, "sel", "☑")
        self._atualizar_botao_lote()

    def _alternar_todas(self) -> None:
        todos = self.tree.get_children()
        if not todos:
            return
        marcar = len(self._marcados) < len(todos)
        self._marcados = set(todos) if marcar else set()
        simbolo = "☑" if marcar else "☐"
        for iid in todos:
            self.tree.set(iid, "sel", simbolo)
        self.tree.heading("sel", text="☑" if marcar else "☐")
        self._atualizar_botao_lote()

    def _limpar_marcacoes(self) -> None:
        for iid in list(self._marcados):
            self.tree.set(iid, "sel", "☐")
        self._marcados.clear()
        self.tree.heading("sel", text="☐")
        self._atualizar_botao_lote()

    def _atualizar_botao_lote(self) -> None:
        n = len(self._marcados)
        ocupado = self._gerando_lote
        # cancelar só faz sentido para marcadas que têm pré-postagem
        n_canc = sum(
            1 for r in self._registros
            if str(r["id_etiqueta"]) in self._marcados and (r.get("id_prepostagem") or "").strip()
        )
        if n and not ocupado:
            self.btn_lote.configure(state=tk.NORMAL, text=f"🏷  Gerar selecionadas ({n})")
            self.btn_imprimir_lote.configure(
                state=tk.NORMAL, text=f"🖨  Imprimir selecionadas ({n})")
        else:
            self.btn_lote.configure(
                state=tk.DISABLED,
                text="🏷  Gerar selecionadas" if not ocupado else "Gerando...",
            )
            self.btn_imprimir_lote.configure(
                state=tk.DISABLED,
                text="🖨  Imprimir selecionadas" if not ocupado else "Aguarde...",
            )
        if n_canc and not ocupado:
            self.btn_cancelar_lote.configure(
                state=tk.NORMAL, text=f"✖  Cancelar pré-postagem ({n_canc})")
        else:
            self.btn_cancelar_lote.configure(
                state=tk.DISABLED, text="✖  Cancelar pré-postagem")

    def _layout_rotulo(self) -> str:
        try:
            fmt = app_config.get_correios_config().get("rotulo_formato", "100x150")
        except Exception:  # noqa: BLE001
            fmt = "100x150"
        return correios_api.layout_rotulo(fmt)

    def _grupos_cfg(self) -> dict:
        if self._grupos is None:
            try:
                self._grupos = app_config.get_clipp_config()
            except Exception:  # noqa: BLE001
                self._grupos = {"id_grupo_yugioh": 2, "id_grupo_pokemon": 27}
        return self._grupos

    def _acao_imprimir(self, registro: dict) -> None:
        if self._gerando_lote:
            return
        id_prep = (registro.get("id_prepostagem") or "").strip()
        if not id_prep:
            messagebox.showinfo(
                "Imprimir",
                "Esta etiqueta ainda não tem pré-postagem.\n"
                "Use 'Gerar Etiqueta' antes de imprimir.",
                parent=self,
            )
            return
        # rótulo já pré-baixado ao gerar -> impressão instantânea
        cache = self._rotulo_em_cache(id_prep)
        if cache:
            self._imprimir_cache_single(registro, cache)
            return
        self._gerando_lote = True
        self._atualizar_botao_lote()
        self.lbl_total.configure(text="Baixando rótulo dos Correios...", fg="#1565c0")
        cliente = self._cliente_correios()
        layout = self._layout_rotulo()
        nf = registro.get("nf_numero")

        cod_rastreio = (registro.get("cod_rastreio") or "").strip()

        def progresso(msg: str) -> None:
            self.after(0, lambda m=msg: self.lbl_total.configure(text=m, fg="#1565c0"))

        def trabalho():
            try:
                if cod_rastreio:
                    cliente.aguardar_prepostado(cod_rastreio, on_progress=progresso)
                pdf, recibo = self._rotulo_primeiro_plano(
                    cliente, [id_prep], layout, progresso)
                if pdf is not None:
                    rast = registro.get("cod_rastreio") or registro["id_etiqueta"]
                    nome = _nome_pdf_etiqueta(nf, registro.get("cliente_nome"), rast)
                    caminho = os.path.join(_pasta_etiquetas(), nome)
                    with open(caminho, "wb") as fp:
                        fp.write(pdf)
                    resultado = (registro, caminho, None, None)
                else:
                    pend = {"recibo": recibo, "ids": [id_prep], "layout": layout,
                            "modo": "single", "registros": [registro],
                            "ts": time.time()}
                    resultado = (registro, None, None, pend)
            except Exception as exc:  # noqa: BLE001
                resultado = (registro, None, str(exc), None)
            self.after(0, lambda: self._concluir_impressao([resultado]))

        threading.Thread(target=trabalho, daemon=True).start()

    def _acao_imprimir_selecionadas(self) -> None:
        if self._gerando_lote:
            return
        selecionados = [r for r in self._registros
                        if str(r["id_etiqueta"]) in self._marcados]
        com_prep = [r for r in selecionados if (r.get("id_prepostagem") or "").strip()]
        sem_prep = [r for r in selecionados if not (r.get("id_prepostagem") or "").strip()]
        if not com_prep:
            messagebox.showinfo(
                "Imprimir selecionadas",
                "Nenhuma das selecionadas tem pré-postagem gerada.",
                parent=self,
            )
            return
        msg = f"Imprimir {len(com_prep)} etiqueta(s) em um único PDF?"
        if sem_prep:
            msg += f"\n\n{len(sem_prep)} sem pré-postagem serão ignoradas."
        if not messagebox.askyesno("Imprimir selecionadas", msg, parent=self):
            return

        ids = [(r.get("id_prepostagem") or "").strip() for r in com_prep]
        # se todos já foram pré-baixados ao gerar -> impressão instantânea
        if all(self._rotulo_em_cache(i) for i in ids):
            self._imprimir_cache_lote(com_prep, ids)
            return

        self._gerando_lote = True
        self._atualizar_botao_lote()
        self.lbl_total.configure(text="Gerando rótulos dos Correios...", fg="#1565c0")
        cliente = self._cliente_correios()
        layout = self._layout_rotulo()
        codigos = [(r.get("cod_rastreio") or "").strip() for r in com_prep]

        def progresso(msg: str) -> None:
            self.after(0, lambda m=msg: self.lbl_total.configure(text=m, fg="#1565c0"))

        def trabalho():
            try:
                for cod in codigos:
                    if cod:
                        cliente.aguardar_prepostado(cod, on_progress=progresso)
                pdf, recibo = self._rotulo_primeiro_plano(
                    cliente, ids, layout, progresso)
                if pdf is not None:
                    nome = f"etiquetas_{datetime.now():%d%m%Y_%H%M}.pdf"
                    caminho = os.path.join(_pasta_etiquetas(), nome)
                    with open(caminho, "wb") as fp:
                        fp.write(pdf)
                    resultados = [(r, caminho, None, None) for r in com_prep]
                else:
                    pend = {"recibo": recibo, "ids": ids, "layout": layout,
                            "modo": "lote", "registros": list(com_prep),
                            "ts": time.time()}
                    resultados = [(r, None, None, pend) for r in com_prep]
            except Exception as exc:  # noqa: BLE001
                resultados = [(r, None, str(exc), None) for r in com_prep]
            self.after(0, lambda: self._concluir_impressao(resultados))

        threading.Thread(target=trabalho, daemon=True).start()

    # --------------------------------------------- geração/baixa do rótulo
    def _rotulo_primeiro_plano(self, cliente, ids: list, layout, on_progress):
        """Solicita UM recibo e aguarda por pouco tempo na tela.

        Retorna (pdf_bytes, recibo). Se pdf for None, o rótulo ficou pendente
        (Correios demorando) e deve ser baixado em segundo plano com o MESMO
        recibo — sem rejogar a fila a cada timeout.
        """
        recibo = cliente.solicitar_rotulo(ids, layout=layout)
        try:
            pdf = cliente.baixar_rotulo(
                recibo, tentativas=ROTULO_FG_TENTATIVAS,
                intervalo=ROTULO_FG_INTERVALO, on_progress=on_progress)
            return pdf, recibo
        except correios_api.RotuloRefazerError as exc:
            # PPN-295 (falha real): recibo morto -> faz um novo p/ o background.
            if exc.por_falha:
                try:
                    recibo = cliente.solicitar_rotulo(ids, layout=layout)
                except Exception:  # noqa: BLE001
                    pass
            return None, recibo

    def _concluir_impressao(self, resultados: list) -> None:
        self._gerando_lote = False
        db_cfg = self._cfg()
        sucesso: list = []
        falhas: list = []
        arquivos: list[str] = []
        pendentes: list[dict] = []
        recibos_pend: set[str] = set()
        for registro, caminho, erro, pend in resultados:
            if pend is not None:
                rec = pend.get("recibo")
                if rec and rec not in recibos_pend:
                    recibos_pend.add(rec)
                    pendentes.append(pend)
                continue
            if erro or not caminho:
                falhas.append((registro.get("nf_numero"), erro or "sem arquivo"))
                continue
            try:
                firebird_db.atualizar_etiqueta_prepostagem(
                    db_cfg, registro["id_etiqueta"], status="IMPRESSO",
                    arquivo_etiqueta=caminho[:255], mensagem_erro="",
                    marcar_impressao=True,
                )
            except Exception:  # noqa: BLE001
                pass
            sucesso.append(registro.get("nf_numero"))
            if caminho not in arquivos:
                arquivos.append(caminho)

        for caminho in arquivos:
            _abrir_arquivo(caminho)

        for pend in pendentes:
            self._rotulos_pendentes.append(pend)
        if pendentes:
            self._iniciar_bg_rotulos()

        self._marcados.clear()
        self._atualizar_botao_lote()
        if pendentes and not sucesso and not falhas:
            qtd = sum(len(p["registros"]) for p in pendentes)
            messagebox.showinfo(
                "Rótulo em geração",
                f"Os Correios estão demorando para gerar o rótulo de "
                f"{qtd} etiqueta(s).\n\nO download continua em segundo plano: a "
                "etiqueta será salva e marcada como Impressa automaticamente "
                "assim que ficar pronta. Pode continuar usando o sistema.",
                parent=self,
            )
            self.recarregar()
            return
        if falhas and not sucesso:
            # Quando a mesma falha vale para todas as NFs (ex.: lote num único PDF),
            # mostra a mensagem uma vez + a lista de NFs, sem repetir o texto longo.
            msgs_unicas = {str(e).strip() for _, e in falhas}
            nfs = ", ".join(str(nf) for nf, _ in falhas)
            if len(msgs_unicas) == 1:
                texto = f"Falha ao gerar rótulo das NFs {nfs}:\n\n{next(iter(msgs_unicas))}"
            else:
                texto = "Falha ao gerar rótulo:\n" + "\n".join(
                    f"• NF {nf}: {str(e)[:300]}" for nf, e in falhas[:10])
            messagebox.showerror("Impressão", texto, parent=self)
        else:
            partes = [f"{len(sucesso)} etiqueta(s) gerada(s)."]
            if arquivos:
                partes.append("\nArquivo(s):")
                partes += [f"  • {os.path.basename(a)}" for a in arquivos]
            if falhas:
                partes.append(f"\n{len(falhas)} com erro:")
                partes += [f"  • NF {nf}: {str(e)[:120]}" for nf, e in falhas[:10]]
            messagebox.showinfo("Impressão", "\n".join(partes), parent=self)
        self.recarregar()

    # ----------------------------------- download do rótulo em segundo plano
    def _iniciar_bg_rotulos(self) -> None:
        if self._rotulo_bg_after_id is None and self._rotulos_pendentes:
            try:
                self._rotulo_bg_after_id = self.after(
                    INTERVALO_ROTULO_BG_MS, self._tick_bg_rotulos)
            except tk.TclError:
                self._rotulo_bg_after_id = None

    def _tick_bg_rotulos(self) -> None:
        self._rotulo_bg_after_id = None
        if not self.winfo_exists() or not self._rotulos_pendentes:
            return
        cliente = self._cliente_correios()
        pend = list(self._rotulos_pendentes)

        def trabalho():
            prontos: list[tuple[dict, bytes]] = []
            expirados: list[dict] = []
            for p in pend:
                if time.time() - p.get("ts", 0) > ROTULO_BG_MAX_S:
                    expirados.append(p)
                    continue
                try:
                    pdf = cliente.baixar_rotulo(p["recibo"], tentativas=1, intervalo=0)
                    prontos.append((p, pdf))
                except correios_api.RotuloRefazerError as exc:
                    # PPN-295: recibo morreu -> pede outro p/ o mesmo lote.
                    if exc.por_falha:
                        try:
                            p["recibo"] = cliente.solicitar_rotulo(
                                p["ids"], layout=p.get("layout"))
                        except Exception:  # noqa: BLE001
                            pass
                    # timeout (ainda gerando): mantém o recibo p/ o próximo ciclo
                except Exception:  # noqa: BLE001
                    pass
            self.after(0, lambda: self._fim_bg_rotulos(prontos, expirados))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_bg_rotulos(self, prontos: list, expirados: list) -> None:
        db_cfg = self._cfg()
        salvos: list[str] = []
        for p, pdf in prontos:
            try:
                if p.get("modo") == "lote":
                    nome = f"etiquetas_{datetime.now():%d%m%Y_%H%M}.pdf"
                    caminho = os.path.join(_pasta_etiquetas(), nome)
                    with open(caminho, "wb") as fp:
                        fp.write(pdf)
                    for r in p["registros"]:
                        try:
                            firebird_db.atualizar_etiqueta_prepostagem(
                                db_cfg, r["id_etiqueta"], status="IMPRESSO",
                                arquivo_etiqueta=caminho[:255], mensagem_erro="",
                                marcar_impressao=True)
                        except Exception:  # noqa: BLE001
                            pass
                    salvos.append(caminho)
                else:
                    r = p["registros"][0]
                    rast = r.get("cod_rastreio") or r["id_etiqueta"]
                    nome = _nome_pdf_etiqueta(r.get("nf_numero"),
                                              r.get("cliente_nome"), rast)
                    caminho = os.path.join(_pasta_etiquetas(), nome)
                    with open(caminho, "wb") as fp:
                        fp.write(pdf)
                    try:
                        firebird_db.atualizar_etiqueta_prepostagem(
                            db_cfg, r["id_etiqueta"], status="IMPRESSO",
                            arquivo_etiqueta=caminho[:255], mensagem_erro="",
                            marcar_impressao=True)
                    except Exception:  # noqa: BLE001
                        pass
                    salvos.append(caminho)
            except Exception:  # noqa: BLE001
                pass
            if p in self._rotulos_pendentes:
                self._rotulos_pendentes.remove(p)

        for p in expirados:
            if p in self._rotulos_pendentes:
                self._rotulos_pendentes.remove(p)

        for caminho in salvos:
            _abrir_arquivo(caminho)
        if salvos:
            messagebox.showinfo(
                "Rótulo pronto",
                f"{len(salvos)} rótulo(s) baixado(s) em segundo plano e "
                "marcado(s) como Impresso.",
                parent=self,
            )
            if not self._carregando:
                self.recarregar()
        if expirados and not salvos:
            messagebox.showwarning(
                "Rótulo",
                f"{len(expirados)} rótulo(s) não ficaram prontos a tempo "
                "(instabilidade nos Correios). Tente Imprimir novamente mais tarde.",
                parent=self,
            )
        if self._rotulos_pendentes:
            self._iniciar_bg_rotulos()
        self._atualizar_rodape_pendentes()

    def _atualizar_rodape_pendentes(self) -> None:
        if self._carregando or self._gerando_lote:
            return
        try:
            self.lbl_total.configure(
                text=self._texto_rodape(len(self._registros)), fg="#5a6b85")
        except tk.TclError:
            pass

    # ----------------------------- pré-download do rótulo (logo após gerar)
    def _prefetch_rotulo(self, id_prep: str, layout: str) -> None:
        """Enfileira o download do rótulo assim que a pré-postagem é gerada.

        O PDF é só guardado em cache (não marca Impresso nem abre nada). Quando
        o usuário clicar em Imprimir, a etiqueta já estará pronta -> instantâneo.
        """
        idp = (id_prep or "").strip()
        if not idp or idp in self._rotulo_cache:
            return
        if any(p.get("id_prep") == idp for p in self._prefetch_pend):
            return
        self._prefetch_pend.append(
            {"id_prep": idp, "recibo": None, "layout": layout, "ts": time.time()})
        self._iniciar_prefetch()
        self._atualizar_rodape_pendentes()

    def _iniciar_prefetch(self) -> None:
        if self._prefetch_after_id is None and self._prefetch_pend:
            try:
                self._prefetch_after_id = self.after(
                    INTERVALO_ROTULO_BG_MS, self._tick_prefetch)
            except tk.TclError:
                self._prefetch_after_id = None

    def _tick_prefetch(self) -> None:
        self._prefetch_after_id = None
        if not self.winfo_exists() or not self._prefetch_pend:
            return
        cliente = self._cliente_correios()
        pend = list(self._prefetch_pend)

        def trabalho():
            prontos: list[tuple[str, bytes]] = []
            expirados: list[dict] = []
            for p in pend:
                if time.time() - p.get("ts", 0) > ROTULO_BG_MAX_S:
                    expirados.append(p)
                    continue
                try:
                    if not p.get("recibo"):
                        p["recibo"] = cliente.solicitar_rotulo(
                            [p["id_prep"]], layout=p.get("layout"))
                    pdf = cliente.baixar_rotulo(
                        p["recibo"], tentativas=1, intervalo=0)
                    prontos.append((p["id_prep"], pdf))
                except correios_api.RotuloRefazerError as exc:
                    # PPN-295: recibo morreu -> pede outro no próximo ciclo.
                    if exc.por_falha:
                        p["recibo"] = None
                    # timeout (ainda gerando): mantém o recibo
                except Exception:  # noqa: BLE001
                    # pré-postagem ainda 'Pendente'/instável: tenta de novo depois
                    pass
            self.after(0, lambda: self._fim_prefetch(prontos, expirados))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_prefetch(self, prontos: list, expirados: list) -> None:
        for id_prep, pdf in prontos:
            try:
                caminho = os.path.join(
                    _pasta_cache_rotulos(), f"{_nome_arquivo_seguro(id_prep)}.pdf")
                with open(caminho, "wb") as fp:
                    fp.write(pdf)
                self._rotulo_cache[id_prep] = caminho
            except Exception:  # noqa: BLE001
                pass
        concluidos = {idp for idp, _ in prontos}
        self._prefetch_pend = [
            p for p in self._prefetch_pend
            if p.get("id_prep") not in concluidos and p not in expirados
        ]
        if self._prefetch_pend:
            self._iniciar_prefetch()
        self._atualizar_rodape_pendentes()

    def _rotulo_em_cache(self, id_prep: str) -> str | None:
        """Caminho do rótulo já pré-baixado, se existir e o arquivo estiver lá."""
        caminho = self._rotulo_cache.get((id_prep or "").strip())
        if caminho and os.path.exists(caminho):
            return caminho
        return None

    def _imprimir_cache_single(self, registro: dict, cache: str) -> None:
        """Imprime usando o rótulo pré-baixado (sem esperar os Correios)."""
        rast = registro.get("cod_rastreio") or registro["id_etiqueta"]
        nome = _nome_pdf_etiqueta(registro.get("nf_numero"),
                                  registro.get("cliente_nome"), rast)
        destino = os.path.join(_pasta_etiquetas(), nome)
        try:
            with open(cache, "rb") as fp:
                dados = fp.read()
            with open(destino, "wb") as fp:
                fp.write(dados)
        except Exception:  # noqa: BLE001
            destino = cache
        try:
            firebird_db.atualizar_etiqueta_prepostagem(
                self._cfg(), registro["id_etiqueta"], status="IMPRESSO",
                arquivo_etiqueta=destino[:255], mensagem_erro="",
                marcar_impressao=True)
        except Exception:  # noqa: BLE001
            pass
        _abrir_arquivo(destino)
        self.recarregar()

    def _imprimir_cache_lote(self, com_prep: list, ids: list) -> None:
        """Junta os rótulos pré-baixados em um único PDF e imprime."""
        caminhos = [self._rotulo_cache[i] for i in ids]
        nome = f"etiquetas_{datetime.now():%d%m%Y_%H%M}.pdf"
        destino = os.path.join(_pasta_etiquetas(), nome)
        try:
            _combinar_pdfs(caminhos, destino)
        except Exception:  # noqa: BLE001
            destino = None
            for c in caminhos:
                _abrir_arquivo(c)
        db_cfg = self._cfg()
        for r in com_prep:
            try:
                firebird_db.atualizar_etiqueta_prepostagem(
                    db_cfg, r["id_etiqueta"], status="IMPRESSO",
                    arquivo_etiqueta=(destino or "")[:255], mensagem_erro="",
                    marcar_impressao=True)
            except Exception:  # noqa: BLE001
                pass
        if destino:
            _abrir_arquivo(destino)
        self._marcados.clear()
        self._atualizar_botao_lote()
        self.recarregar()

    def _acao_cancelar_selecionadas(self) -> None:
        if self._gerando_lote:
            return
        alvos = [r for r in self._registros
                 if str(r["id_etiqueta"]) in self._marcados
                 and (r.get("id_prepostagem") or "").strip()]
        if not alvos:
            messagebox.showinfo(
                "Cancelar pré-postagem",
                "Nenhuma das selecionadas tem pré-postagem para cancelar.",
                parent=self,
            )
            return
        msg = (f"Cancelar {len(alvos)} pré-postagem(ns) nos Correios?\n\n"
               "Isso invalida o código de rastreio. Use apenas se NÃO for postar "
               "o objeto. (Pré-postagens já postadas não podem ser canceladas.)")
        if not messagebox.askyesno("Cancelar pré-postagem", msg, parent=self):
            return

        self._gerando_lote = True
        self._atualizar_botao_lote()
        self.lbl_total.configure(text="Cancelando pré-postagens...", fg="#1565c0")
        cliente = self._cliente_correios()

        def trabalho():
            resultados = []
            for r in alvos:
                idp = (r.get("id_prepostagem") or "").strip()
                try:
                    cliente.cancelar_prepostagem(idp)
                    resultados.append((r, None))
                except Exception as exc:  # noqa: BLE001
                    resultados.append((r, str(exc)))
            self.after(0, lambda: self._concluir_cancelamento(resultados))

        threading.Thread(target=trabalho, daemon=True).start()

    def _concluir_cancelamento(self, resultados: list) -> None:
        self._gerando_lote = False
        db_cfg = self._cfg()
        ok, falhas = [], []
        for registro, erro in resultados:
            if erro:
                falhas.append((registro.get("nf_numero"), erro))
                continue
            try:
                firebird_db.atualizar_etiqueta_prepostagem(
                    db_cfg, registro["id_etiqueta"], status="CANCELADA",
                    mensagem_erro="Pré-postagem cancelada pelo usuário",
                )
            except Exception:  # noqa: BLE001
                pass
            ok.append(registro.get("nf_numero"))

        self._marcados.clear()
        self._atualizar_botao_lote()
        if falhas and not ok:
            messagebox.showerror(
                "Cancelar pré-postagem",
                "Falha ao cancelar:\n"
                + "\n".join(f"• NF {nf}: {str(e)[:160]}" for nf, e in falhas[:10]),
                parent=self,
            )
        else:
            partes = [f"{len(ok)} pré-postagem(ns) cancelada(s)."]
            if falhas:
                partes.append(f"\n{len(falhas)} com erro:")
                partes += [f"  • NF {nf}: {str(e)[:120]}" for nf, e in falhas[:10]]
            messagebox.showinfo("Cancelar pré-postagem", "\n".join(partes), parent=self)
        self.recarregar()

    def _cliente_correios(self) -> correios_api.CorreiosClient:
        if self._correios is None:
            self._correios = correios_api.CorreiosClient()
        return self._correios

    def _acao_rastrear(self, registro: dict) -> None:
        cod = (registro.get("cod_rastreio") or "").strip()
        if not cod:
            messagebox.showinfo(
                "Rastrear",
                "Esta etiqueta ainda não possui código de rastreio.\n"
                "Gere a etiqueta antes de rastrear.",
                parent=self,
            )
            return
        if getattr(self, "_rastreando", False):
            return
        self._rastreando = True
        self._rastreio_registro = registro
        self.lbl_total.configure(text=f"Rastreando {cod}...", fg="#1565c0")
        cliente = self._cliente_correios()

        def trabalho():
            try:
                obj = cliente.rastrear(cod)
                erro = None
            except Exception as exc:  # noqa: BLE001
                obj, erro = None, str(exc)
            self.after(0, lambda: self._fim_rastreio(obj, erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_rastreio(self, obj, erro) -> None:
        self._rastreando = False
        if erro or not obj:
            self.lbl_total.configure(text="Falha ao rastrear.", fg="#c62828")
            messagebox.showerror("Rastrear", erro or "Sem dados de rastreio.", parent=self)
            return
        self.lbl_total.configure(text="", fg="#5a6b85")
        # Objeto com eventos = já postado/em movimento -> reflete no status local.
        self._marcar_postado_se_preciso(getattr(self, "_rastreio_registro", None), obj)
        DialogoRastreio(self, obj)

    # status que ainda podem evoluir (Postado -> Entregue) e valem consulta
    _STATUS_SINCRONIZAVEIS = ("GERADA", "IMPRESSO", "POSTADO")

    def _acao_sincronizar_status(self) -> None:
        """Botão manual: consulta os Correios e atualiza Postado/Entregue."""
        self._executar_sincronizacao(silencioso=False, do_banco=False)

    def _executar_sincronizacao(self, *, silencioso: bool, do_banco: bool) -> None:
        if getattr(self, "_sincronizando", False):
            return
        if self._gerando_lote:
            return
        self._sincronizando = True
        cliente = self._cliente_correios()
        db_cfg = self._cfg()

        def trabalho():
            if do_banco:
                try:
                    regs = firebird_db.listar_etiquetas_correio(db_cfg, status=None)
                except Exception:  # noqa: BLE001
                    regs = list(self._registros)
            else:
                regs = list(self._registros)
            alvos = [
                r for r in regs
                if (r.get("cod_rastreio") or "").strip()
                and (r.get("status") or "").upper() in self._STATUS_SINCRONIZAVEIS
            ]
            if not alvos and not silencioso:
                self.after(0, lambda: self._fim_sincronizar(0, silencioso, vazio=True))
                return
            atualizados = 0
            total = len(alvos)
            for i, r in enumerate(alvos, 1):
                if not silencioso:
                    self.after(0, lambda i=i: self.lbl_total.configure(
                        text=f"Verificando {i}/{total}...", fg="#1565c0"))
                cod = (r.get("cod_rastreio") or "").strip()
                dados = self._situacao_objeto(cliente, cod, (r.get("status") or "").upper())
                if dados is None:
                    continue
                if self._persistir_situacao(db_cfg, r, dados):
                    atualizados += 1
            self.after(0, lambda: self._fim_sincronizar(atualizados, silencioso))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_sincronizar(self, atualizados: int, silencioso: bool = False,
                         vazio: bool = False) -> None:
        self._sincronizando = False
        if silencioso:
            self._ultima_sync_auto = datetime.now()
        if not silencioso:
            if vazio:
                messagebox.showinfo(
                    "Atualizar status",
                    "Nenhuma etiqueta com rastreio para verificar.",
                    parent=self,
                )
            else:
                messagebox.showinfo(
                    "Atualizar status",
                    f"{atualizados} etiqueta(s) atualizada(s) (Postado/Entregue).",
                    parent=self,
                )
        if atualizados and not self._carregando:
            self.recarregar()
        else:
            self.lbl_total.configure(text=self._texto_rodape(len(self._registros)),
                                     fg="#5a6b85")

    def _texto_rodape(self, n: int) -> str:
        base = f"{n} etiqueta(s)"
        if self._rotulos_pendentes:
            qtd = sum(len(p.get("registros", [])) for p in self._rotulos_pendentes)
            base += f"  ·  baixando {qtd} rótulo(s)…"
        if self._prefetch_pend:
            base += f"  ·  pré-baixando {len(self._prefetch_pend)} rótulo(s)…"
        if self._ultima_sync_auto:
            base += f"  ·  auto {self._ultima_sync_auto.strftime('%H:%M')}"
        return base

    # ---------------------------------------------- rotina automática (10 min)
    def _agendar_sync_auto(self, intervalo: int = INTERVALO_SYNC_MS) -> None:
        try:
            if self._sync_after_id is not None:
                self.after_cancel(self._sync_after_id)
        except (tk.TclError, ValueError):
            pass
        try:
            self._sync_after_id = self.after(intervalo, self._tick_sync_auto)
        except tk.TclError:
            self._sync_after_id = None

    def _tick_sync_auto(self) -> None:
        if not self.winfo_exists():
            return
        self._executar_sincronizacao(silencioso=True, do_banco=True)
        # reagenda sempre o ciclo padrão (10 min), independente do resultado
        self._agendar_sync_auto(INTERVALO_SYNC_MS)

    def _situacao_objeto(self, cliente, cod: str, atual: str,
                         obj: dict | None = None) -> dict | None:
        """Classifica status/datas de um objeto (rastreio + pré-postagem).

        O status POSTADO/ENTREGUE vem SOMENTE do rastreamento. Quando o rastreio
        é inconclusivo e o objeto ainda está GERADA/IMPRESSO, consulta a
        pré-postagem apenas para enriquecer a data prevista (não muda o status).
        Retorna None se nem rastrear funcionou (sem mexer em nada).
        """
        if obj is None:
            try:
                obj = cliente.rastrear(cod)
            except Exception:  # noqa: BLE001
                obj = None
        dados = _classificar_situacao(None, obj)
        # Se rastreio não definiu postagem/entrega e ainda está como pré-postado
        # localmente, confirma na pré-postagem (pode ter postado sem evento ainda).
        if dados["status"] is None and atual in ("GERADA", "IMPRESSO"):
            try:
                item = cliente.consultar_prepostagem(codigo_objeto=cod)
            except Exception:  # noqa: BLE001
                item = None
            if item is not None:
                dados = _classificar_situacao(item, obj)
        if obj is None and dados["status"] is None and not dados["dt_prevista"]:
            return None
        return dados

    def _persistir_situacao(self, db_cfg: dict, r: dict, dados: dict) -> bool:
        """Persiste status + datas quando houver novidade. -> houve update."""
        atual = (r.get("status") or "").upper()
        muda_status = bool(dados["status"] and dados["status"] != atual)
        nova_post = dados["dt_postagem"] if (dados["dt_postagem"] and not r.get("dt_postagem")) else None
        nova_prev = dados["dt_prevista"] if (dados["dt_prevista"] and not r.get("dt_prevista")) else None
        nova_entr = dados["dt_entrega"] if (dados["dt_entrega"] and not r.get("dt_entrega")) else None
        if not (muda_status or nova_post or nova_prev or nova_entr):
            return False
        try:
            firebird_db.atualizar_etiqueta_prepostagem(
                db_cfg, r["id_etiqueta"],
                status=(dados["status"] if muda_status else (atual or "GERADA")),
                dt_postagem=nova_post, dt_prevista=nova_prev, dt_entrega=nova_entr,
            )
        except Exception:  # noqa: BLE001
            return False
        if muda_status:
            r["status"] = dados["status"]
        if nova_post:
            r["dt_postagem"] = nova_post
        if nova_prev:
            r["dt_prevista"] = nova_prev
        if nova_entr:
            r["dt_entrega"] = nova_entr
        novo_status = (dados["status"] or atual).upper()
        if muda_status and novo_status in ("POSTADO", "ENTREGUE"):
            cod = (r.get("cod_rastreio") or "").strip()
            if cod:
                self._buscar_valor_postagem_bg(
                    r["id_etiqueta"], cod, nova_post or r.get("dt_postagem"))
        return True

    def _buscar_valor_postagem_bg(self, id_etiqueta: int, cod: str, dt_fallback=None) -> None:
        """Busca valorAtendimento na API e grava VL_POSTAGEM (não bloqueia a UI)."""
        db_cfg = self._cfg()

        def trabalho():
            try:
                firebird_db.buscar_e_gravar_valor_postagem(
                    db_cfg, id_etiqueta, cod,
                    cliente=self._cliente_correios(),
                    dt_postagem_fallback=dt_fallback,
                )
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=trabalho, daemon=True).start()

    def _marcar_postado_se_preciso(self, registro: dict | None, obj: dict) -> None:
        if not registro:
            return
        cod = (registro.get("cod_rastreio") or "").strip()
        atual = (registro.get("status") or "").upper()
        dados = self._situacao_objeto(self._cliente_correios(), cod, atual, obj=obj)
        if dados and self._persistir_situacao(self._cfg(), registro, dados):
            self.recarregar()

    def _acao_gerar_selecionadas(self) -> None:
        if self._gerando_lote:
            return
        selecionados = [r for r in self._registros
                        if str(r["id_etiqueta"]) in self._marcados]
        if not selecionados:
            return
        ja_geradas = [r for r in selecionados
                      if (r.get("status") or "").upper() in ("GERADA", "IMPRESSO", "POSTADO")]
        msg = f"Gerar pré-postagem de {len(selecionados)} etiqueta(s)?"
        if ja_geradas:
            msg += (f"\n\nAtenção: {len(ja_geradas)} já possuem pré-postagem e "
                    "serão geradas novamente.")
        if not messagebox.askyesno("Gerar selecionadas", msg, parent=self):
            return

        self._gerando_lote = True
        self._atualizar_botao_lote()
        db_cfg = self._cfg()
        grupos = self._grupos_cfg()
        cliente = self._cliente_correios()

        def trabalho():
            sucesso, falhas, gerados = [], [], []
            total = len(selecionados)
            for i, registro in enumerate(selecionados, 1):
                nf = registro.get("nf_numero")
                self.after(0, lambda i=i: self.lbl_total.configure(
                    text=f"Gerando {i}/{total}...", fg="#1565c0"))
                emb = self._embalagem_da_linha(str(registro["id_etiqueta"]))
                kwargs, erros = preparar_envio_etiqueta(db_cfg, registro, grupos, emb)
                if erros:
                    self._persistir_erro(db_cfg, registro, "; ".join(erros))
                    falhas.append((nf, "; ".join(erros)))
                    continue
                try:
                    resp = cliente.criar_prepostagem(**kwargs)
                    id_prep = self._persistir_sucesso(db_cfg, registro, resp, kwargs)
                    sucesso.append(nf)
                    if id_prep:
                        gerados.append(id_prep)
                except Exception as exc:  # noqa: BLE001
                    self._persistir_erro(db_cfg, registro, str(exc)[:480])
                    falhas.append((nf, str(exc)))
            self.after(0, lambda: self._finalizar_lote(sucesso, falhas, gerados))

        threading.Thread(target=trabalho, daemon=True).start()

    def _persistir_sucesso(self, db_cfg, registro, resp, kwargs) -> str:
        id_prep = str(resp.get("id") or resp.get("idPrePostagem") or "").strip()
        rastreio = str(resp.get("codigoObjeto") or resp.get("codigoRastreio")
                       or resp.get("numeroObjeto") or "").strip()
        try:
            firebird_db.atualizar_etiqueta_prepostagem(
                db_cfg, registro["id_etiqueta"], status="GERADA",
                id_prepostagem=id_prep or None, cod_rastreio=rastreio or None,
                cod_servico=kwargs.get("codigo_servico"),
                peso=kwargs.get("peso_g"), altura=kwargs.get("altura_cm"),
                largura=kwargs.get("largura_cm"), comprimento=kwargs.get("comprimento_cm"),
                mensagem_erro="", marcar_geracao=True,
            )
        except Exception:  # noqa: BLE001
            pass
        return id_prep

    def _persistir_erro(self, db_cfg, registro, mensagem) -> None:
        try:
            firebird_db.atualizar_etiqueta_prepostagem(
                db_cfg, registro["id_etiqueta"], status="ERRO", mensagem_erro=mensagem,
            )
        except Exception:  # noqa: BLE001
            pass

    def _finalizar_lote(self, sucesso: list, falhas: list,
                        gerados: list | None = None) -> None:
        self._gerando_lote = False
        self._marcados.clear()
        self._atualizar_botao_lote()
        # já dispara o download do rótulo em segundo plano (impressão instantânea depois)
        layout = self._layout_rotulo()
        for id_prep in (gerados or []):
            self._prefetch_rotulo(id_prep, layout)
        partes = [f"{len(sucesso)} gerada(s) com sucesso."]
        if falhas:
            partes.append(f"\n{len(falhas)} com erro:")
            for nf, erro in falhas[:12]:
                partes.append(f"  • NF {nf}: {erro[:120]}")
            if len(falhas) > 12:
                partes.append(f"  • ... e mais {len(falhas) - 12}.")
        titulo = "Geração concluída" if not falhas else "Geração concluída com avisos"
        (messagebox.showinfo if not falhas else messagebox.showwarning)(
            titulo, "\n".join(partes), parent=self)
        self.recarregar()

    def _acao_gerar_etiqueta(self, registro: dict, iid: str | None = None) -> None:
        status = (registro.get("status") or "").upper()
        if status in ("GERADA", "IMPRESSO", "POSTADO"):
            if not messagebox.askyesno(
                "Gerar Etiqueta",
                f"A NF {registro['nf_numero']} já tem pré-postagem ({registro.get('status_desc')}).\n"
                "Gerar uma nova mesmo assim?",
                parent=self,
            ):
                return
        emb = self._embalagem_da_linha(iid) if iid else None
        DialogoGerarEtiqueta(self, registro, embalagem=emb)


# ---------------------------------------------------------------------------
# Diálogo — confirma serviço, peso e dimensões antes de criar pré-postagem
# ---------------------------------------------------------------------------

class DialogoGerarEtiqueta(tk.Toplevel):
    """Coleta serviço + peso/dimensões (por nota) e cria a pré-postagem."""

    def __init__(self, painel: "PostagensFrame", registro: dict,
                 embalagem: dict | None = None):
        super().__init__(painel)
        self._painel = painel
        self._registro = registro
        self._emb = embalagem
        self._dest: dict | None = None
        self._enviando = False

        self.title(f"Gerar Etiqueta — NF {registro.get('nf_numero')}")
        self.geometry("540x660")
        self.minsize(500, 600)
        self.configure(bg=COR_FUNDO)
        self.transient(painel.winfo_toplevel())
        self.grab_set()

        self._montar()
        self.after(50, self._carregar_destinatario)

    # ------------------------------------------------------------------ UI
    def _montar(self) -> None:
        tk.Frame(self, bg=COR_HEADER, height=46).pack(fill=tk.X)
        cab = self.winfo_children()[-1]
        cab.pack_propagate(False)
        tk.Label(cab, text="🏷  Nova pré-postagem", bg=COR_HEADER, fg=COR_HEADER_TXT,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, padx=14)
        tk.Frame(self, bg=COR_ACENTO, height=3).pack(fill=tk.X)

        corpo = tk.Frame(self, bg=COR_FUNDO, padx=16, pady=12)
        corpo.pack(fill=tk.BOTH, expand=True)

        # Destinatário (somente leitura)
        tk.Label(corpo, text="Destinatário", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 10)).grid(row=0, column=0, columnspan=4, sticky=tk.W)
        self.lbl_dest = tk.Label(corpo, text="Carregando...", bg="#ffffff", fg="#222222",
                                 justify=tk.LEFT, anchor="nw", relief="solid", bd=1,
                                 font=("Segoe UI", 9), padx=8, pady=6, wraplength=470)
        self.lbl_dest.grid(row=1, column=0, columnspan=4, sticky="we", pady=(2, 12))

        # Serviço
        tk.Label(corpo, text="Serviço:", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=2, column=0, sticky=tk.W, pady=4)
        self.var_servico = tk.StringVar()
        nomes = list(correios_api.SERVICOS.keys())
        atual = correios_api.SERVICOS_POR_CODIGO.get((self._registro.get("cod_servico") or "").strip())
        self.var_servico.set(atual or nomes[0])
        ttk.Combobox(corpo, textvariable=self.var_servico, values=nomes,
                     state="readonly", width=22).grid(row=2, column=1, sticky=tk.W, pady=4)

        # Formato
        tk.Label(corpo, text="Formato:", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=2, column=2, sticky=tk.E, padx=(12, 6), pady=4)
        self.var_formato = tk.StringVar(value="Pacote / Caixa")
        ttk.Combobox(corpo, textvariable=self.var_formato, values=list(FORMATOS_OBJETO.keys()),
                     state="readonly", width=16).grid(row=2, column=3, sticky=tk.W, pady=4)

        # Peso
        tk.Label(corpo, text="Peso (g):", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=3, column=0, sticky=tk.W, pady=4)
        self.var_peso = tk.StringVar(value=_num_ou(self._registro.get("peso"), "300"))
        ttk.Entry(corpo, textvariable=self.var_peso, width=12).grid(row=3, column=1, sticky=tk.W, pady=4)

        # Dimensões
        tk.Label(corpo, text="Dimensões (cm)", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 10)).grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=(10, 2))
        dim = tk.Frame(corpo, bg=COR_FUNDO)
        dim.grid(row=5, column=0, columnspan=4, sticky=tk.W)
        self.var_compr = tk.StringVar(value=_num_ou(self._registro.get("comprimento"), "20"))
        self.var_larg = tk.StringVar(value=_num_ou(self._registro.get("largura"), "15"))
        self.var_alt = tk.StringVar(value=_num_ou(self._registro.get("altura"), "5"))
        # Embalagem escolhida na grade: dimensões já entram preenchidas.
        if self._emb:
            if self._emb.get("comprimento"):
                self.var_compr.set(_fmt_num(self._emb["comprimento"]))
            if self._emb.get("largura"):
                self.var_larg.set(_fmt_num(self._emb["largura"]))
            if self._emb.get("altura"):
                self.var_alt.set(_fmt_num(self._emb["altura"]))
        for i, (rotulo, var) in enumerate(
            (("Compr.", self.var_compr), ("Larg.", self.var_larg), ("Alt.", self.var_alt))
        ):
            tk.Label(dim, text=rotulo, bg=COR_FUNDO, fg="#5a6b85",
                     font=("Segoe UI", 9)).grid(row=0, column=i * 2, padx=(0 if i == 0 else 10, 4))
            ttk.Entry(dim, textvariable=var, width=7).grid(row=0, column=i * 2 + 1)

        tk.Label(corpo, text="Peso e dimensões vêm da nota; ajuste se necessário.",
                 bg=COR_FUNDO, fg="#8392ab", font=("Segoe UI", 8)).grid(
            row=6, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))

        # Declaração de conteúdo (obrigatória pelos Correios)
        tk.Label(corpo, text="Declaração de conteúdo", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 10)).grid(
            row=7, column=0, columnspan=4, sticky=tk.W, pady=(12, 2))
        tk.Label(corpo, text="Conteúdo:", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=8, column=0, sticky=tk.W, pady=4)
        self.var_conteudo = tk.StringVar(value="Carregando...")
        ttk.Entry(corpo, textvariable=self.var_conteudo).grid(
            row=8, column=1, columnspan=3, sticky="we", pady=4)
        tk.Label(corpo, text="Valor declarado (R$):", bg=COR_FUNDO, fg="#33415c",
                 font=("Segoe UI Semibold", 9)).grid(row=9, column=0, sticky=tk.W, pady=4)
        self.var_valor = tk.StringVar(value="")
        ttk.Entry(corpo, textvariable=self.var_valor, width=14).grid(
            row=9, column=1, sticky=tk.W, pady=4)

        corpo.columnconfigure(3, weight=1)

        # Rodapé / status
        self.lbl_status = tk.Label(self, text="", bg=COR_FUNDO, fg="#5a6b85",
                                   font=("Segoe UI", 9), anchor="w")
        self.lbl_status.pack(fill=tk.X, padx=16)

        rod = tk.Frame(self, bg=COR_FUNDO, padx=16, pady=12)
        rod.pack(fill=tk.X)
        tk.Button(rod, text="Cancelar", command=self.destroy, bg="#e9edf5", fg="#33415c",
                  font=("Segoe UI Semibold", 9), relief="flat", padx=14, pady=5,
                  cursor="hand2").pack(side=tk.RIGHT)
        self.btn_ok = tk.Button(rod, text="Gerar pré-postagem", command=self._enviar,
                                bg=COR_BOTAO, fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9),
                                relief="flat", padx=16, pady=5, cursor="hand2",
                                activebackground="#1540ad", state=tk.DISABLED)
        self.btn_ok.pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------- dados
    def _carregar_destinatario(self) -> None:
        self._set_status("Buscando destinatário...", "#5a6b85")
        id_etq = self._registro["id_etiqueta"]
        id_venda = self._registro.get("id_nfvenda")

        def trabalho():
            try:
                dest = firebird_db.obter_destinatario_etiqueta(
                    self._painel._cfg(), id_etq
                )
                erro = None
            except Exception as exc:  # noqa: BLE001
                dest, erro = None, str(exc)
            decl = None
            if id_venda:
                try:
                    grupos = app_config.get_clipp_config()
                    decl = firebird_db.obter_dados_envio_etiqueta(
                        self._painel._cfg(), id_venda,
                        id_grupo_yugioh=grupos.get("id_grupo_yugioh", 2),
                        id_grupo_pokemon=grupos.get("id_grupo_pokemon", 27),
                    )
                except Exception:  # noqa: BLE001
                    decl = None
            self.after(0, lambda: self._aplicar_destinatario(dest, erro, decl))

        threading.Thread(target=trabalho, daemon=True).start()

    def _aplicar_destinatario(self, dest: dict | None, erro: str | None,
                              decl: dict | None = None) -> None:
        # Preenche declaração, dimensões (ESPECIE) e peso (kg×1000) da venda
        self._aviso_envio = ""
        self._dados_envio = decl or {}
        if decl:
            self.var_conteudo.set(decl.get("descricao") or "")
            self.var_valor.set(f"{float(decl.get('valor') or 0):.2f}")
            # Dimensões só quando ESPECIE veio estruturada (>= comprimento e largura)
            if decl.get("comprimento") is not None and decl.get("largura") is not None:
                self.var_compr.set(str(decl["comprimento"]))
                self.var_larg.set(str(decl["largura"]))
                if decl.get("altura") is not None:
                    self.var_alt.set(str(decl["altura"]))
            elif (decl.get("especie") or "").strip():
                self._aviso_envio = (
                    f"ESPECIE '{decl['especie']}' fora do padrão (ex.: CX20X10X10); "
                    "confira as dimensões."
                )
            if decl.get("peso_g"):
                self.var_peso.set(str(decl["peso_g"]))
            else:
                self._aviso_envio = (self._aviso_envio + " Peso não informado na nota.").strip()
            tipo = (decl.get("tipo") or "").upper()
            if tipo.startswith("EN"):
                self.var_formato.set("Envelope")
            elif tipo.startswith(("RL", "RO", "CIL")):
                self.var_formato.set("Rolo / Cilindro")
            elif tipo:
                self.var_formato.set("Pacote / Caixa")
            # Serviço pela observação da nota (Envio: SEDEX/PAC/Mini Pac)
            envio = decl.get("envio") or ""
            servico = correios_api.servico_por_texto(envio)
            if servico:
                self.var_servico.set(servico[0])
            elif envio:
                self._aviso_envio = (
                    self._aviso_envio
                    + f" Envio '{envio}' não reconhecido; confira o serviço."
                ).strip()
            elif decl.get("observacao"):
                self._aviso_envio = (
                    self._aviso_envio + " Serviço não encontrado na observação da nota."
                ).strip()
        else:
            if self.var_conteudo.get() == "Carregando...":
                self.var_conteudo.set("")
        # Parâmetros informados na grade têm prioridade sobre a nota.
        cod = (self._registro.get("cod_servico") or "").strip()
        if cod:
            nome = correios_api.SERVICOS_POR_CODIGO.get(cod)
            if nome:
                self.var_servico.set(nome)
        if self._registro.get("peso"):
            self.var_peso.set(_num_ou(self._registro.get("peso"), self.var_peso.get()))
        # Embalagem escolhida na grade tem prioridade: dimensões + tara no peso.
        self._aplicar_embalagem_override()
        if erro:
            self.lbl_dest.configure(text=f"Erro ao buscar destinatário: {erro[:200]}")
            self._set_status("Não foi possível carregar o destinatário.", "#c62828")
            return
        if not dest:
            self.lbl_dest.configure(text="Destinatário não encontrado para esta etiqueta.")
            self._set_status("Sem cliente vinculado.", "#c62828")
            return
        self._dest = dest
        compl = f" - {dest['complemento']}" if dest.get("complemento") else ""
        texto = (
            f"{dest.get('nome') or '(sem nome)'}   {dest.get('cpfCnpj') or ''}\n"
            f"{dest.get('logradouro') or ''}, {dest.get('numero') or 's/n'}{compl}\n"
            f"{dest.get('bairro') or ''} — {dest.get('cidade') or ''}/{dest.get('uf') or ''}   "
            f"CEP {dest.get('cep') or ''}\n"
            f"Cel: {dest.get('celular') or '—'}"
        )
        self.lbl_dest.configure(text=texto)
        faltando = self._campos_destino_faltando(dest)
        if faltando:
            self._set_status("Faltam dados do destinatário: " + ", ".join(faltando), "#c62828")
        elif getattr(self, "_aviso_envio", ""):
            self._set_status(self._aviso_envio, "#e08600")
        else:
            self._set_status("Pronto para gerar.", "#2e7d32")
        self.btn_ok.configure(state=tk.NORMAL)

    def _aplicar_embalagem_override(self) -> None:
        """Aplica dimensões da embalagem e soma a tara ao peso da nota."""
        e = self._emb
        if not e:
            return
        if e.get("comprimento"):
            self.var_compr.set(_fmt_num(e["comprimento"]))
        if e.get("largura"):
            self.var_larg.set(_fmt_num(e["largura"]))
        if e.get("altura"):
            self.var_alt.set(_fmt_num(e["altura"]))
        tara = e.get("peso_tara")
        if tara:
            try:
                base = float((self.var_peso.get() or "0").replace(",", "."))
            except ValueError:
                base = 0.0
            self.var_peso.set(str(int(round(base + float(tara)))))

    @staticmethod
    def _campos_destino_faltando(dest: dict) -> list[str]:
        rotulos = {
            "nome": "nome", "cep": "CEP", "logradouro": "logradouro",
            "numero": "número", "bairro": "bairro", "cidade": "cidade", "uf": "UF",
        }
        return [r for c, r in rotulos.items() if not (dest.get(c) or "").strip()]

    # ------------------------------------------------------------- envio
    def _enviar(self) -> None:
        if self._enviando or not self._dest:
            return
        faltando = self._campos_destino_faltando(self._dest)
        if faltando:
            messagebox.showwarning(
                "Dados incompletos",
                "Complete o cadastro do cliente antes de gerar:\n• " + "\n• ".join(faltando),
                parent=self,
            )
            return
        try:
            peso = int(float(self.var_peso.get().replace(",", ".")))
            compr = float(self.var_compr.get().replace(",", "."))
            larg = float(self.var_larg.get().replace(",", "."))
            alt = float(self.var_alt.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Valores inválidos",
                                   "Peso e dimensões devem ser numéricos.", parent=self)
            return

        conteudo = self.var_conteudo.get().strip()
        if not conteudo:
            messagebox.showwarning(
                "Declaração de conteúdo",
                "Informe a descrição do conteúdo (obrigatório pelos Correios).",
                parent=self,
            )
            return
        try:
            valor_decl = float(self.var_valor.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Valor inválido",
                                   "O valor declarado deve ser numérico.", parent=self)
            return
        declaracao = [{"conteudo": conteudo, "quantidade": "1", "valor": valor_decl}]

        servico_nome = self.var_servico.get()
        cod_servico = correios_api.SERVICOS.get(servico_nome, servico_nome)
        formato = FORMATOS_OBJETO.get(self.var_formato.get(), correios_api.FORMATO_PACOTE)
        nf = self._registro.get("nf_numero")
        pedido = (getattr(self, "_dados_envio", {}) or {}).get("numero_pedido", "")
        partes = ["Pedido site"]
        if pedido:
            partes.append(f"#{pedido}")
        if nf:
            partes.append(f"- NF {nf}")
        observacao = " ".join(partes)

        self._enviando = True
        self.btn_ok.configure(state=tk.DISABLED)
        self._set_status("Enviando aos Correios...", "#1565c0")

        def trabalho():
            try:
                resp = self._painel._cliente_correios().criar_prepostagem(
                    destinatario=self._dest,
                    codigo_servico=cod_servico,
                    peso_g=peso,
                    formato=formato,
                    altura_cm=alt,
                    largura_cm=larg,
                    comprimento_cm=compr,
                    observacao=observacao,
                    nota_fiscal=str(nf) if nf else None,
                    chave_nfe=(self._registro.get("chave_acesso") or "").strip() or None,
                    serie_nota=(self._registro.get("nf_serie") or "").strip() or None,
                    declaracao_conteudo=declaracao,
                )
                erro = None
            except Exception as exc:  # noqa: BLE001
                resp, erro = None, str(exc)
            self.after(0, lambda: self._concluir(
                resp, erro, cod_servico, peso, alt, larg, compr))

        threading.Thread(target=trabalho, daemon=True).start()

    def _concluir(self, resp, erro, cod_servico, peso, alt, larg, compr) -> None:
        self._enviando = False
        id_etq = self._registro["id_etiqueta"]
        if erro:
            try:
                firebird_db.atualizar_etiqueta_prepostagem(
                    self._painel._cfg(), id_etq, status="ERRO",
                    cod_servico=cod_servico, peso=peso, altura=alt,
                    largura=larg, comprimento=compr, mensagem_erro=erro[:480],
                )
            except Exception:
                pass
            self._set_status("Falha ao gerar.", "#c62828")
            self.btn_ok.configure(state=tk.NORMAL)
            messagebox.showerror("Erro ao gerar pré-postagem", erro, parent=self)
            self._painel.recarregar()
            return

        id_prep = str(resp.get("id") or resp.get("idPrePostagem") or "").strip()
        rastreio = str(
            resp.get("codigoObjeto") or resp.get("codigoRastreio")
            or resp.get("numeroObjeto") or ""
        ).strip()
        try:
            firebird_db.atualizar_etiqueta_prepostagem(
                self._painel._cfg(), id_etq, status="GERADA",
                id_prepostagem=id_prep or None, cod_rastreio=rastreio or None,
                cod_servico=cod_servico, peso=peso, altura=alt, largura=larg,
                comprimento=compr, mensagem_erro="", marcar_geracao=True,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(
                "Pré-postagem criada, mas...",
                f"A pré-postagem foi criada (objeto {rastreio or id_prep}), "
                f"mas falhou ao gravar no banco:\n{exc}",
                parent=self,
            )
            self._painel.recarregar()
            self.destroy()
            return

        if id_prep:
            self._painel._prefetch_rotulo(id_prep, self._painel._layout_rotulo())
        messagebox.showinfo(
            "Pré-postagem criada",
            f"NF {self._registro.get('nf_numero')} — pré-postagem gerada.\n\n"
            f"Objeto/rastreio: {rastreio or '(pendente)'}\n"
            f"ID pré-postagem: {id_prep or '—'}\n\n"
            "O rótulo já está sendo baixado em segundo plano — a impressão "
            "será instantânea.",
            parent=self,
        )
        self._painel.recarregar()
        self.destroy()

    def _set_status(self, texto: str, cor: str) -> None:
        self.lbl_status.configure(text=texto, fg=cor)


def _pasta_etiquetas() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    pasta = os.path.join(base, "etiquetas")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _pasta_cache_rotulos() -> str:
    """Cache dos rótulos pré-baixados (1 PDF por id de pré-postagem)."""
    pasta = os.path.join(_pasta_etiquetas(), "_cache_rotulos")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _combinar_pdfs(caminhos: list[str], destino: str) -> str:
    """Junta vários PDFs (um rótulo cada) em um único arquivo. Requer pypdf."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for caminho in caminhos:
        leitor = PdfReader(caminho)
        for pagina in leitor.pages:
            writer.add_page(pagina)
    with open(destino, "wb") as fp:
        writer.write(fp)
    return destino


def _nome_arquivo_seguro(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (texto or "").strip()) or "etiqueta"


def _nome_pdf_etiqueta(nf, cliente_nome, rastreio) -> str:
    """Nome do PDF: NF{nf}_{2 primeiros nomes do cliente}_{cod_rastreio}.pdf"""
    nome = unicodedata.normalize("NFKD", str(cliente_nome or ""))
    nome = nome.encode("ascii", "ignore").decode("ascii")
    partes = [p for p in nome.split() if p]
    nome2 = "_".join(partes[:2]) if partes else "cliente"
    nf_txt = nf if nf not in (None, "") else "S-N"
    rast = (str(rastreio).strip() or "SEMRASTREIO")
    return _nome_arquivo_seguro(f"NF{nf_txt}_{nome2}_{rast}.pdf")


def _abrir_arquivo(caminho: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(caminho)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", caminho])
        else:
            subprocess.Popen(["xdg-open", caminho])
    except Exception:  # noqa: BLE001
        pass


def _formato_por_tipo(tipo: str) -> str:
    t = (tipo or "").upper()
    if t.startswith("EN"):
        return correios_api.FORMATO_ENVELOPE
    if t.startswith(("RL", "RO", "CIL")):
        return correios_api.FORMATO_ROLO
    return correios_api.FORMATO_PACOTE


# ---------------------------------------------------------------------------
# Montagem do payload de envio — destinatário, serviço e dimensões para a API
# ---------------------------------------------------------------------------

def preparar_envio_etiqueta(db_cfg: dict, registro: dict, grupos: dict,
                            embalagem: dict | None = None) -> tuple[dict | None, list[str]]:
    """Monta os kwargs de criar_prepostagem a partir do banco (uso em lote).

    Quando uma `embalagem` é informada, suas dimensões substituem as da ESPECIE
    e a tara (peso da embalagem, em g) é somada ao peso da nota.

    Retorna (kwargs, erros). Se houver erros, kwargs vem None.
    """
    erros: list[str] = []
    dest = firebird_db.obter_destinatario_etiqueta(db_cfg, registro["id_etiqueta"])
    if not dest:
        return None, ["destinatário não encontrado"]
    rotulos = {"nome": "nome", "cep": "CEP", "logradouro": "logradouro",
               "numero": "número", "bairro": "bairro", "cidade": "cidade", "uf": "UF"}
    for campo, rot in rotulos.items():
        if not (dest.get(campo) or "").strip():
            erros.append(f"destinatário sem {rot}")

    id_venda = registro.get("id_nfvenda")
    dados = {}
    if id_venda:
        try:
            dados = firebird_db.obter_dados_envio_etiqueta(
                db_cfg, id_venda,
                id_grupo_yugioh=grupos.get("id_grupo_yugioh", 2),
                id_grupo_pokemon=grupos.get("id_grupo_pokemon", 27),
            )
        except Exception as exc:  # noqa: BLE001
            return None, [f"falha ao ler dados da venda: {exc}"]

    servico = None
    cod = (registro.get("cod_servico") or "").strip()
    if cod:
        nome = correios_api.SERVICOS_POR_CODIGO.get(cod, cod)
        servico = (nome, cod)
    if not servico:
        servico = correios_api.servico_por_texto(dados.get("envio", ""))
    if not servico:
        envio = dados.get("envio") or "(vazio)"
        if not cod:
            erros.append(f"serviço não identificado (Envio: {envio})")

    comp, larg, alt = dados.get("comprimento"), dados.get("largura"), dados.get("altura")
    peso = dados.get("peso_g") or 0
    if registro.get("peso"):
        try:
            peso = int(round(float(registro["peso"])))
        except (TypeError, ValueError):
            pass
    if embalagem:
        if embalagem.get("comprimento"):
            comp = float(embalagem["comprimento"])
        if embalagem.get("largura"):
            larg = float(embalagem["largura"])
        if embalagem.get("altura"):
            alt = float(embalagem["altura"])
        if embalagem.get("peso_tara"):
            peso = int(round(float(peso or 0) + float(embalagem["peso_tara"])))
    if not (comp and larg and alt):
        erros.append(f"dimensões incompletas (ESPECIE '{dados.get('especie') or ''}')")
    if peso <= 0:
        erros.append("peso não informado na nota")

    conteudo = dados.get("descricao") or ""
    valor = float(dados.get("valor") or 0)
    if not conteudo:
        erros.append("declaração de conteúdo vazia")

    if erros:
        return None, erros

    nf = registro.get("nf_numero")
    pedido = dados.get("numero_pedido") or ""
    partes = ["Pedido site"]
    if pedido:
        partes.append(f"#{pedido}")
    if nf:
        partes.append(f"- NF {nf}")

    kwargs = dict(
        destinatario=dest,
        codigo_servico=servico[1],
        peso_g=peso,
        formato=_formato_por_tipo(dados.get("tipo", "")),
        altura_cm=alt,
        largura_cm=larg,
        comprimento_cm=comp,
        observacao=" ".join(partes),
        nota_fiscal=str(nf) if nf else None,
        chave_nfe=(registro.get("chave_acesso") or "").strip() or None,
        serie_nota=(registro.get("nf_serie") or "").strip() or None,
        declaracao_conteudo=[{"conteudo": conteudo, "quantidade": "1", "valor": valor}],
    )
    return kwargs, []


def _num_ou(valor, padrao: str) -> str:
    """Formata número do banco para exibição (inteiro quando possível)."""
    if valor in (None, ""):
        return padrao
    try:
        f = float(valor)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return padrao


def _fmt_dt(valor: str) -> str:
    if not valor:
        return ""
    try:
        return datetime.strptime(str(valor)[:19], "%Y-%m-%dT%H:%M:%S").strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return str(valor)


def _fmt_endereco(end: dict | None) -> str:
    if not end:
        return ""
    p1 = ", ".join(x for x in [end.get("logradouro"), end.get("numero")] if x)
    extras = ", ".join(
        x for x in [end.get("complemento"), end.get("bairro")] if x)
    cidade = " - ".join(x for x in [end.get("cidade"), end.get("uf")] if x)
    cep = end.get("cep") or ""
    linhas = [p1, extras, " ".join(x for x in [cidade, cep] if x)]
    return "\n".join(l for l in linhas if l)


def _normalizar_item_consulta(item: dict) -> dict:
    dest = item.get("destinatario") or {}
    end = dest.get("endereco") or {}
    cod = item.get("codigoServico") or ""
    cidade = f"{end.get('cidade', '')}/{end.get('uf', '')}".strip("/")
    return {
        "id": item.get("id") or "",
        "codigo": item.get("codigoObjeto") or "",
        "status": item.get("descStatusAtual") or str(item.get("statusAtual") or ""),
        "servico": item.get("servico") or correios_api.SERVICOS_POR_CODIGO.get(cod, cod),
        "nf": item.get("numeroNotaFiscal") or "",
        "destinatario": dest.get("nome") or "",
        "cidade": cidade,
        "data": (item.get("dataHoraStatusAtual") or "")[:10],
    }


def _extrair_codigos(texto: str) -> list[str]:
    """Extrai ids de pré-postagem (PR...) e códigos de objeto (AV 085 512 060 BR)."""
    texto = texto or ""
    vistos: list[str] = []
    # ids de pré-postagem: PR + 22 alfanuméricos (preserva maiúsc./minúsc.)
    for m in re.findall(r"PR[A-Za-z0-9]{22}", texto):
        if m not in vistos:
            vistos.append(m)
    # códigos de objeto: 2 letras + 9 dígitos (com ou sem espaços) + 2 letras
    for m in re.findall(r"[A-Za-z]{2}\s*\d{3}\s*\d{3}\s*\d{3}\s*[A-Za-z]{2}", texto):
        cod = re.sub(r"\s+", "", m).upper()
        if cod not in vistos:
            vistos.append(cod)
    return vistos


# ---------------------------------------------------------------------------
# Consulta avulsa — pré-postagem e rastreio fora da fila de etiquetas
# ---------------------------------------------------------------------------

class ConsultaCorreiosFrame(tk.Frame):
    """Consulta pré-postagens por código do objeto (inclui as feitas no portal)."""

    def __init__(self, parent: tk.Misc, *, com_cabecalho: bool = True):
        super().__init__(parent, bg=COR_FUNDO)
        self._correios: correios_api.CorreiosClient | None = None
        self._itens: list[dict] = []
        self._ocupado = False

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        if com_cabecalho:
            header = tk.Frame(self, bg=COR_HEADER, height=58)
            header.pack(fill=tk.X, side=tk.TOP)
            header.pack_propagate(False)
            tk.Frame(self, bg=COR_ACENTO, height=3).pack(fill=tk.X, side=tk.TOP)
            tk.Label(header, text="🔎  Consultar Correios", bg=COR_HEADER,
                     fg=COR_HEADER_TXT, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=16)
            tk.Label(header, text="Pré-postagens por código do objeto", bg=COR_HEADER,
                     fg=COR_HEADER_TXT, font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=4)

        entrada = tk.Frame(self, bg=COR_FUNDO, padx=14, pady=10)
        entrada.pack(fill=tk.X)
        tk.Label(entrada, text="Códigos de objeto (um por linha ou separados por vírgula):",
                 bg=COR_FUNDO, fg="#33415c", font=("Segoe UI Semibold", 9)).pack(anchor=tk.W)
        linha = tk.Frame(entrada, bg=COR_FUNDO)
        linha.pack(fill=tk.X, pady=(4, 0))
        self.txt_codigos = tk.Text(linha, height=3, width=50, font=("Consolas", 10),
                                   relief="solid", bd=1)
        self.txt_codigos.pack(side=tk.LEFT, fill=tk.X, expand=True)
        botoes = tk.Frame(linha, bg=COR_FUNDO)
        botoes.pack(side=tk.LEFT, padx=(8, 0), fill=tk.Y)
        self.btn_consultar = tk.Button(
            botoes, text="Consultar", command=self._consultar, bg=COR_BOTAO,
            fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9), relief="flat",
            padx=16, pady=6, cursor="hand2", activebackground="#1540ad")
        self.btn_consultar.pack(fill=tk.X)
        tk.Button(botoes, text="Limpar", command=self._limpar, bg="#e9edf5",
                  fg="#33415c", font=("Segoe UI Semibold", 9), relief="flat",
                  padx=16, pady=4, cursor="hand2").pack(fill=tk.X, pady=(6, 0))

        wrap = tk.Frame(self, bg=COR_FUNDO, padx=14)
        wrap.pack(fill=tk.BOTH, expand=True)
        cols = ("codigo", "status", "servico", "nf", "destinatario", "cidade", "data")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 style="Postagens.Treeview", selectmode="browse")
        titulos = {
            "codigo": "Código", "status": "Status", "servico": "Serviço", "nf": "NF",
            "destinatario": "Destinatário", "cidade": "Cidade/UF", "data": "Data",
        }
        larguras = {
            "codigo": 130, "status": 120, "servico": 180, "nf": 60,
            "destinatario": 210, "cidade": 150, "data": 95,
        }
        for c in cols:
            self.tree.heading(c, text=titulos[c])
            self.tree.column(c, width=larguras[c],
                             anchor=(tk.CENTER if c in ("nf", "data") else tk.W),
                             stretch=(c == "destinatario"))
        scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(0, 4))
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("par", background=COR_LINHA_ALT)
        self.tree.bind("<Double-1>", lambda e: self._ver_dados())

        rod = tk.Frame(self, bg=COR_FUNDO, padx=14, pady=8)
        rod.pack(fill=tk.X)
        self.btn_dados = tk.Button(
            rod, text="📋  Ver dados", command=self._ver_dados, bg="#e9edf5",
            fg="#33415c", font=("Segoe UI Semibold", 9), relief="flat",
            padx=14, pady=6, cursor="hand2")
        self.btn_dados.pack(side=tk.LEFT)
        self.btn_rastreio = tk.Button(
            rod, text="🚚  Rastrear", command=self._rastrear, bg="#0b7d4b",
            fg="#ffffff", font=("Segoe UI Semibold", 9), relief="flat",
            padx=14, pady=6, cursor="hand2", activebackground="#0a6a40")
        self.btn_rastreio.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_pdf = tk.Button(
            rod, text="🖨  Baixar PDF", command=self._baixar_pdf, bg=COR_BOTAO,
            fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9), relief="flat",
            padx=14, pady=6, cursor="hand2", activebackground="#1540ad")
        self.btn_pdf.pack(side=tk.LEFT, padx=(8, 0))
        self.lbl_total = tk.Label(rod, text="", bg=COR_FUNDO, fg="#5a6b85",
                                  font=("Segoe UI", 9))
        self.lbl_total.pack(side=tk.RIGHT)

    def _cliente(self) -> correios_api.CorreiosClient:
        if self._correios is None:
            self._correios = correios_api.CorreiosClient()
        return self._correios

    def _limpar(self) -> None:
        self.txt_codigos.delete("1.0", tk.END)
        self._itens = []
        self.tree.delete(*self.tree.get_children())
        self.lbl_total.configure(text="")

    def _selecionado(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Correios", "Selecione uma linha na lista.", parent=self)
            return None
        return next((x for x in self._itens
                     if (x.get("codigoObjeto") or x.get("id")) == sel[0]), None)

    def _consultar(self) -> None:
        if self._ocupado:
            return
        codigos = _extrair_codigos(self.txt_codigos.get("1.0", tk.END))
        if not codigos:
            messagebox.showinfo("Consultar", "Informe ao menos um código de objeto.",
                                parent=self)
            return
        self._ocupado = True
        self.btn_consultar.configure(state=tk.DISABLED, text="Consultando...")
        cliente = self._cliente()

        def trabalho():
            itens, falhas = [], []
            total = len(codigos)
            for i, cod in enumerate(codigos, 1):
                self.after(0, lambda i=i: self.lbl_total.configure(
                    text=f"Consultando {i}/{total}...", fg="#1565c0"))
                try:
                    if cod.startswith("PR") and len(cod) == 24:
                        item = cliente.consultar_prepostagem(id_prepostagem=cod)
                    else:
                        item = cliente.consultar_prepostagem(codigo_objeto=cod)
                    if item:
                        itens.append(item)
                    else:
                        falhas.append((cod, "não encontrado"))
                except Exception as exc:  # noqa: BLE001
                    falhas.append((cod, str(exc)))
            self.after(0, lambda: self._mostrar(itens, falhas))

        threading.Thread(target=trabalho, daemon=True).start()

    def _mostrar(self, itens: list, falhas: list) -> None:
        self._ocupado = False
        self.btn_consultar.configure(state=tk.NORMAL, text="Consultar")
        self._itens = itens
        self.tree.delete(*self.tree.get_children())
        for i, raw in enumerate(itens):
            it = _normalizar_item_consulta(raw)
            iid = raw.get("codigoObjeto") or raw.get("id") or str(i)
            tags = ("par",) if i % 2 else ()
            self.tree.insert(
                "", tk.END, iid=iid,
                values=(it["codigo"], it["status"], it["servico"], it["nf"],
                        it["destinatario"], it["cidade"], it["data"]),
                tags=tags,
            )
        txt = f"{len(itens)} encontrada(s)"
        if falhas:
            txt += f" · {len(falhas)} não encontrada(s)/erro"
        self.lbl_total.configure(text=txt, fg="#5a6b85")
        if falhas:
            messagebox.showwarning(
                "Consulta",
                "Não encontradas / erro:\n"
                + "\n".join(f"• {c}: {str(e)[:120]}" for c, e in falhas[:12]),
                parent=self,
            )

    # ---- Ações sobre a linha selecionada -------------------------------
    def _ver_dados(self) -> None:
        item = self._selecionado()
        if item:
            DialogoDadosPostagem(self, item)

    def _rastrear(self) -> None:
        if self._ocupado:
            return
        item = self._selecionado()
        if not item:
            return
        cod = (item.get("codigoObjeto") or "").strip()
        if not cod:
            messagebox.showinfo("Rastrear", "Esta pré-postagem ainda não tem código de objeto.",
                                parent=self)
            return
        self._ocupado = True
        self.lbl_total.configure(text=f"Rastreando {cod}...", fg="#1565c0")
        cliente = self._cliente()

        def trabalho():
            try:
                obj = cliente.rastrear(cod)
                erro = None
            except Exception as exc:  # noqa: BLE001
                obj, erro = None, str(exc)
            self.after(0, lambda: self._fim_rastreio(obj, erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_rastreio(self, obj, erro) -> None:
        self._ocupado = False
        if erro or not obj:
            self.lbl_total.configure(text="Falha ao rastrear.", fg="#c62828")
            messagebox.showerror("Rastrear", erro or "Sem dados de rastreio.", parent=self)
            return
        self.lbl_total.configure(text="", fg="#5a6b85")
        DialogoRastreio(self, obj)

    def _baixar_pdf(self) -> None:
        if self._ocupado:
            return
        item = self._selecionado()
        if not item:
            return
        id_prep = (item.get("id") or "").strip()
        if not id_prep:
            messagebox.showinfo("Baixar PDF", "Sem id de pré-postagem para esta etiqueta.",
                                parent=self)
            return
        nf = item.get("numeroNotaFiscal") or ""
        cod = item.get("codigoObjeto") or id_prep
        self._ocupado = True
        self.lbl_total.configure(text="Baixando rótulo...", fg="#1565c0")
        cliente = self._cliente()
        try:
            layout = correios_api.layout_rotulo(
                app_config.get_correios_config().get("rotulo_formato", "100x150"))
        except Exception:  # noqa: BLE001
            layout = "LINEAR_100_150"

        cod_objeto = (item.get("codigoObjeto") or "").strip()

        def trabalho():
            try:
                if cod_objeto:
                    cliente.aguardar_prepostado(cod_objeto)
                pdf = cliente.gerar_rotulo_pdf([id_prep], layout=layout)
                nome_cli = (item.get("destinatario") or {}).get("nome") or ""
                nome = _nome_pdf_etiqueta(nf, nome_cli, cod)
                caminho = os.path.join(_pasta_etiquetas(), nome)
                with open(caminho, "wb") as fp:
                    fp.write(pdf)
                erro = None
            except Exception as exc:  # noqa: BLE001
                caminho, erro = None, str(exc)
            self.after(0, lambda: self._fim_pdf(caminho, erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_pdf(self, caminho, erro) -> None:
        self._ocupado = False
        if erro or not caminho:
            self.lbl_total.configure(text="Rótulo indisponível.", fg="#c62828")
            msg = erro or "Falha ao gerar PDF."
            if erro and ("postado" in erro.lower() or "expirad" in erro.lower()):
                msg = ("O rótulo não pode ser reemitido porque o objeto já foi postado "
                       "(ou expirou).\nUse \"Ver dados\" e \"Rastrear\" para acompanhar.\n\n"
                       f"Detalhe: {erro}")
            messagebox.showinfo("Baixar PDF", msg, parent=self)
            return
        self.lbl_total.configure(text=f"Salvo: {os.path.basename(caminho)}", fg="#2e7d32")
        _abrir_arquivo(caminho)


class DialogoDadosPostagem(tk.Toplevel):
    """Exibe os dados completos de uma pré-postagem (somente leitura)."""

    def __init__(self, parent: tk.Misc, item: dict):
        super().__init__(parent)
        self.title("Dados da postagem")
        self.geometry("560x600")
        self.configure(bg=COR_FUNDO)
        self.transient(parent.winfo_toplevel())

        norm = _normalizar_item_consulta(item)
        header = tk.Frame(self, bg=COR_HEADER, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text=f"📦  {norm['codigo'] or norm['id']}", bg=COR_HEADER,
                 fg=COR_HEADER_TXT, font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        tk.Label(header, text=f"{norm['servico']}  ·  {norm['status']}", bg=COR_HEADER,
                 fg="#cfe0ff", font=("Segoe UI", 10)).pack(anchor=tk.W)

        txt = tk.Text(self, wrap="word", font=("Segoe UI", 10), bg="#ffffff",
                      relief="flat", padx=14, pady=12)
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        rem = item.get("remetente") or {}
        dest = item.get("destinatario") or {}
        dim = (f"{item.get('comprimentoInformado', '?')} x "
               f"{item.get('larguraInformada', '?')} x "
               f"{item.get('alturaInformada', '?')} cm")
        peso = item.get("pesoInformado")
        partes = [
            ("Serviço", f"{norm['servico']} ({item.get('codigoServico', '')})"),
            ("Nota fiscal", norm["nf"] or "—"),
            ("Status atual", f"{norm['status']}  ({_fmt_dt(item.get('dataHoraStatusAtual'))})"),
            ("Dimensões", dim),
            ("Peso informado", f"{peso} g" if peso else "—"),
            ("", ""),
            ("REMETENTE", ""),
            ("Nome", rem.get("nome", "")),
            ("CPF/CNPJ", rem.get("cpfCnpj", "")),
            ("Endereço", _fmt_endereco(rem.get("endereco"))),
            ("", ""),
            ("DESTINATÁRIO", ""),
            ("Nome", dest.get("nome", "")),
            ("CPF/CNPJ", dest.get("cpfCnpj", "")),
            ("Telefone", " ".join(x for x in [dest.get("dddCelular"), dest.get("celular")] if x)),
            ("Endereço", _fmt_endereco(dest.get("endereco"))),
        ]
        for rotulo, valor in partes:
            if rotulo in ("REMETENTE", "DESTINATÁRIO"):
                txt.insert(tk.END, f"{rotulo}\n", "secao")
            elif rotulo == "" and valor == "":
                txt.insert(tk.END, "\n")
            else:
                txt.insert(tk.END, f"{rotulo}: ", "rotulo")
                txt.insert(tk.END, f"{valor}\n")

        decl = item.get("itensDeclaracaoConteudo") or []
        if decl:
            txt.insert(tk.END, "\nDECLARAÇÃO DE CONTEÚDO\n", "secao")
            for d in decl:
                txt.insert(tk.END,
                           f"  • {d.get('conteudo', '')} "
                           f"(qtd {d.get('quantidade', '')}, R$ {d.get('valor', '')})\n")

        txt.tag_configure("secao", font=("Segoe UI", 10, "bold"), foreground=COR_HEADER)
        txt.tag_configure("rotulo", font=("Segoe UI Semibold", 10), foreground="#33415c")
        txt.configure(state=tk.DISABLED)

        tk.Button(self, text="Fechar", command=self.destroy, bg=COR_BOTAO,
                  fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9), relief="flat",
                  padx=20, pady=6, cursor="hand2").pack(pady=(0, 12))


class DialogoRastreio(tk.Toplevel):
    """Linha do tempo de rastreamento de um objeto (API Rastro)."""

    def __init__(self, parent: tk.Misc, obj: dict):
        super().__init__(parent)
        self.title("Rastreamento do objeto")
        self.geometry("720x520")
        self.configure(bg=COR_FUNDO)
        self.transient(parent.winfo_toplevel())

        eventos = obj.get("eventos") or []
        tipo = obj.get("tipoPostal") or {}
        ultimo = eventos[0].get("descricao") if eventos else "Sem eventos"

        header = tk.Frame(self, bg=COR_HEADER, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text=f"🚚  {obj.get('codObjeto', '')}", bg=COR_HEADER,
                 fg=COR_HEADER_TXT, font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        sub = tipo.get("categoria") or tipo.get("descricao") or ""
        prev = _fmt_dt(obj.get("dtPrevista"))
        info = sub + (f"  ·  Previsão: {prev}" if prev else "")
        tk.Label(header, text=info, bg=COR_HEADER, fg="#cfe0ff",
                 font=("Segoe UI", 10)).pack(anchor=tk.W)
        tk.Label(header, text=f"Situação atual: {ultimo}", bg=COR_HEADER,
                 fg="#ffd500", font=("Segoe UI Semibold", 10)).pack(anchor=tk.W, pady=(4, 0))

        wrap = tk.Frame(self, bg=COR_FUNDO, padx=12, pady=12)
        wrap.pack(fill=tk.BOTH, expand=True)
        cols = ("data", "evento", "local")
        tree = ttk.Treeview(wrap, columns=cols, show="headings", style="Postagens.Treeview")
        tree.heading("data", text="Data/Hora")
        tree.heading("evento", text="Evento")
        tree.heading("local", text="Local")
        tree.column("data", width=130, anchor=tk.W, stretch=False)
        tree.column("evento", width=330, anchor=tk.W)
        tree.column("local", width=200, anchor=tk.W)
        scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("par", background=COR_LINHA_ALT)

        for i, ev in enumerate(eventos):
            uni = ev.get("unidade") or {}
            end = uni.get("endereco") or {}
            local = " - ".join(x for x in [end.get("cidade"), end.get("uf")] if x)
            dest = ev.get("unidadeDestino") or {}
            dend = dest.get("endereco") or {}
            dloc = " - ".join(x for x in [dend.get("cidade"), dend.get("uf")] if x)
            if dloc:
                local = f"{local} → {dloc}"
            desc = ev.get("descricao") or ev.get("codigo") or ""
            tags = ("par",) if i % 2 else ()
            tree.insert("", tk.END,
                        values=(_fmt_dt(ev.get("dtHrCriado")), desc, local), tags=tags)

        if not eventos:
            tk.Label(wrap, text="Nenhum evento de rastreamento encontrado.",
                     bg=COR_FUNDO, fg="#5a6b85", font=("Segoe UI", 10)).pack(pady=20)

        tk.Button(self, text="Fechar", command=self.destroy, bg=COR_BOTAO,
                  fg=COR_BOTAO_TXT, font=("Segoe UI Semibold", 9), relief="flat",
                  padx=20, pady=6, cursor="hand2").pack(pady=(0, 12))


# ---------------------------------------------------------------------------
# Janela standalone — abre Postagens fora do servidor (py tela_postagens.py)
# ---------------------------------------------------------------------------

class TelaPostagens(tk.Toplevel):
    """Janela própria que embute o PostagensFrame (uso isolado)."""

    def __init__(self, parent: tk.Misc | None = None, db_cfg: dict | None = None):
        super().__init__(parent)
        self.title("Gerenciamento de Postagens — Correios")
        self.geometry("1040x620")
        self.minsize(900, 480)
        self.configure(bg=COR_FUNDO)
        self.painel = PostagensFrame(self, db_cfg=db_cfg)
        self.painel.pack(fill=tk.BOTH, expand=True)


def _parse_data(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def abrir_tela_postagens(parent: tk.Misc | None = None, db_cfg: dict | None = None) -> TelaPostagens:
    tela = TelaPostagens(parent, db_cfg=db_cfg)
    if parent:
        tela.transient(parent)
    tela.focus_set()
    return tela


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    tela = TelaPostagens(root)
    tela.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
