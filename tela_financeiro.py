"""Aba Financeiro — acompanhamento do valor tarifado no contrato Correios.

O contrato fecha no último dia útil de cada mês. Os valores vêm da API
GET /prepostagem/v1/prepostagens/postada (campo valorAtendimento), gravados
em XX_TB_ETIQUETA_CORREIO.VL_POSTAGEM.
"""

from __future__ import annotations

import calendar
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

import config as app_config
import correios_api
import db as firebird_db

from tela_postagens import (
    COR_ACENTO,
    COR_FUNDO,
    COR_HEADER,
    COR_HEADER_TXT,
    COR_LINHA_ALT,
)

_MESES = (
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
)


# ---------------------------------------------------------------------------
# Utilitários de formatação — moeda, data e nome do serviço Correios
# ---------------------------------------------------------------------------

def ultimo_dia_util_mes(ano: int, mes: int) -> date:
    """Último dia útil (seg–sex) do mês."""
    d = date(int(ano), int(mes), calendar.monthrange(int(ano), int(mes))[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _moeda(valor: float | None) -> str:
    if valor is None:
        return "—"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_data(valor) -> str:
    if not valor:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor)[:10]


def _rotulo_servico(cod: str) -> str:
    cod = (cod or "").strip()
    if cod:
        return correios_api.SERVICOS_POR_CODIGO.get(cod, cod)
    return ""


# ---------------------------------------------------------------------------
# FinanceiroFrame — navegação por mês, totais e busca de VL_POSTAGEM na API
# ---------------------------------------------------------------------------

class FinanceiroFrame(tk.Frame):
    """Painel de acompanhamento financeiro do contrato Correios."""

    def __init__(self, master, *, com_cabecalho: bool = True):
        super().__init__(master, bg=COR_FUNDO)
        hoje = date.today()
        self._ano = hoje.year
        self._mes = hoje.month
        self._registros: list[dict] = []
        self._carregando = False
        self._sincronizando = False
        self._primeira_carga = True
        self._ajustou_mes = False

        if com_cabecalho:
            self._montar_cabecalho()
        self._montar_resumo()
        self._montar_filtros()
        self._montar_tabela()
        self._montar_rodape()
        self.after(150, self.recarregar)

    # --- Montagem da UI (cabeçalho, cards, filtros, grade, rodapé) ---

    def _mudar_mes(self, ano: int, mes: int) -> None:
        self._ano, self._mes = int(ano), int(mes)
        self.recarregar()

    def _cfg(self) -> dict:
        return app_config.get_db_config()

    def _cliente_correios(self) -> correios_api.CorreiosClient:
        return correios_api.CorreiosClient()

    def _montar_cabecalho(self) -> None:
        cab = tk.Frame(self, bg=COR_HEADER, height=52)
        cab.pack(fill=tk.X)
        cab.pack_propagate(False)
        tk.Label(
            cab, text="💰  Financeiro — Contrato Correios",
            bg=COR_HEADER, fg=COR_HEADER_TXT,
            font=("Segoe UI", 14, "bold"),
        ).pack(side=tk.LEFT, padx=16, pady=10)
        tk.Frame(self, bg=COR_ACENTO, height=3).pack(fill=tk.X)

    def _montar_resumo(self) -> None:
        box = tk.Frame(self, bg="#ffffff", relief="solid", bd=1, padx=16, pady=12)
        box.pack(fill=tk.X, padx=14, pady=(12, 8))

        self.lbl_periodo = tk.Label(
            box, text="", bg="#ffffff", fg="#33415c",
            font=("Segoe UI Semibold", 10), anchor=tk.W,
        )
        self.lbl_periodo.pack(fill=tk.X)

        linha = tk.Frame(box, bg="#ffffff")
        linha.pack(fill=tk.X, pady=(10, 0))

        def card(pai, titulo, cor_valor="#0b3d91"):
            f = tk.Frame(pai, bg="#f4f6fa", padx=14, pady=8)
            f.pack(side=tk.LEFT, padx=(0, 12))
            tk.Label(f, text=titulo, bg="#f4f6fa", fg="#5a6b85",
                     font=("Segoe UI", 9)).pack(anchor=tk.W)
            lbl = tk.Label(f, text="—", bg="#f4f6fa", fg=cor_valor,
                           font=("Segoe UI Semibold", 16))
            lbl.pack(anchor=tk.W)
            return lbl

        self.lbl_total = card(linha, "Total tarifado no mês")
        self.lbl_qtd = card(linha, "Postagens", "#2e7d32")
        self.lbl_pendente = card(linha, "Sem valor na API", "#e65100")
        self.lbl_fechamento = card(linha, "Fechamento do contrato", "#5a6b85")

        self.lbl_dica = tk.Label(
            box,
            text="Valores obtidos da API dos Correios (valor tarifado após postagem). "
                 "Clique em «Buscar valores» para atualizar postagens ainda sem valor.",
            bg="#ffffff", fg="#8392ab", font=("Segoe UI", 8), wraplength=900,
            justify=tk.LEFT, anchor=tk.W,
        )
        self.lbl_dica.pack(fill=tk.X, pady=(10, 0))

    def _montar_filtros(self) -> None:
        bar = tk.Frame(self, bg=COR_FUNDO, padx=14, pady=4)
        bar.pack(fill=tk.X)

        tk.Button(
            bar, text="◀", command=self._mes_anterior,
            bg="#e9edf5", fg="#33415c", font=("Segoe UI", 10),
            relief="flat", padx=8, cursor="hand2",
        ).pack(side=tk.LEFT)

        self.lbl_mes = tk.Label(
            bar, text="", bg=COR_FUNDO, fg="#33415c",
            font=("Segoe UI Semibold", 11), width=22, anchor=tk.CENTER,
        )
        self.lbl_mes.pack(side=tk.LEFT, padx=4)

        tk.Button(
            bar, text="▶", command=self._mes_proximo,
            bg="#e9edf5", fg="#33415c", font=("Segoe UI", 10),
            relief="flat", padx=8, cursor="hand2",
        ).pack(side=tk.LEFT)

        tk.Button(
            bar, text="Mês atual", command=self._ir_mes_atual,
            bg="#e9edf5", fg="#33415c", font=("Segoe UI Semibold", 9),
            relief="flat", padx=12, pady=4, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(12, 0))

        tk.Button(
            bar, text="🔄  Buscar valores", command=self._acao_buscar_valores,
            bg="#0b3d91", fg="#ffffff", font=("Segoe UI Semibold", 9),
            relief="flat", padx=14, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT)

        tk.Button(
            bar, text="Atualizar", command=self.recarregar,
            bg="#e9edf5", fg="#33415c", font=("Segoe UI Semibold", 9),
            relief="flat", padx=12, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 8))

    def _montar_tabela(self) -> None:
        wrap = tk.Frame(self, bg=COR_FUNDO, padx=14)
        wrap.pack(fill=tk.BOTH, expand=True)

        cols = ("nf", "cliente", "destino", "envio", "postagem", "valor", "rastreio")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for cid, txt, w, anc in (
            ("nf", "NF", 64, tk.W),
            ("cliente", "Cliente", 180, tk.W),
            ("destino", "Destino", 140, tk.W),
            ("envio", "Envio", 96, tk.W),
            ("postagem", "Postagem", 88, tk.CENTER),
            ("valor", "Valor", 96, tk.E),
            ("rastreio", "Rastreio", 120, tk.W),
        ):
            self.tree.heading(cid, text=txt, anchor=anc)
            self.tree.column(cid, width=w, anchor=anc, stretch=(cid == "cliente"))

        scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree.tag_configure("par", background=COR_LINHA_ALT)
        self.tree.tag_configure("sem_valor", foreground="#e65100")

    def _montar_rodape(self) -> None:
        rod = tk.Frame(self, bg=COR_FUNDO, padx=14, pady=8)
        rod.pack(fill=tk.X)
        self.lbl_status = tk.Label(
            rod, text="", bg=COR_FUNDO, fg="#5a6b85", font=("Segoe UI", 9),
        )
        self.lbl_status.pack(side=tk.LEFT)

    def _atualizar_titulo_periodo(self) -> None:
        ini = date(self._ano, self._mes, 1)
        fim = date(self._ano, self._mes, calendar.monthrange(self._ano, self._mes)[1])
        fech = ultimo_dia_util_mes(self._ano, self._mes)
        self.lbl_mes.configure(text=f"{_MESES[self._mes - 1]} / {self._ano}")
        self.lbl_periodo.configure(
            text=(
                f"Período: {ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}  ·  "
                f"Fechamento do contrato: {fech.strftime('%d/%m/%Y')} (último dia útil)"
            )
        )
        self.lbl_fechamento.configure(text=fech.strftime("%d/%m/%Y"))

    def _mes_anterior(self) -> None:
        if self._mes == 1:
            self._mes, self._ano = 12, self._ano - 1
        else:
            self._mes -= 1
        self.recarregar()

    def _mes_proximo(self) -> None:
        if self._mes == 12:
            self._mes, self._ano = 1, self._ano + 1
        else:
            self._mes += 1
        self.recarregar()

    def _ir_mes_atual(self) -> None:
        hoje = date.today()
        self._ano, self._mes = hoje.year, hoje.month
        self.recarregar()

    # --- Carga de dados — lista do mês, ajuste automático e sync de valores ---

    def recarregar(self) -> None:
        if self._carregando:
            return
        self._carregando = True
        self._atualizar_titulo_periodo()
        self.lbl_status.configure(text="Carregando...", fg="#1565c0")
        ano, mes = self._ano, self._mes

        def trabalho():
            try:
                regs = firebird_db.listar_financeiro_correio(
                    self._cfg(), ano=ano, mes=mes)
                erro = None
            except Exception as exc:  # noqa: BLE001
                regs, erro = [], str(exc)
            # Na 1ª abertura: se o mês atual está vazio, vai pro mês da última postagem.
            if (not erro and not regs and self._primeira_carga
                    and not self._ajustou_mes):
                try:
                    ult = firebird_db.obter_mes_ultima_postagem(self._cfg())
                except Exception:  # noqa: BLE001
                    ult = None
                if ult and ult != (ano, mes):
                    self._ajustou_mes = True
                    a, m = ult
                    self.after(0, lambda: self._mudar_mes(a, m))
                    return
            self.after(0, lambda: self._aplicar_registros(regs, erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _aplicar_registros(self, registros: list[dict], erro: str | None) -> None:
        self._carregando = False
        if erro:
            self.lbl_status.configure(
                text=f"Erro ao carregar: {erro[:120]}", fg="#c62828")
            self._primeira_carga = False
            return

        self._registros = registros
        self.tree.delete(*self.tree.get_children())

        total = 0.0
        sem_valor = 0
        for i, r in enumerate(registros):
            vl = r.get("vl_postagem")
            tags = ["sem_valor"] if vl in (None, 0) else []
            if i % 2:
                tags.append("par")
            if vl:
                total += float(vl)
            else:
                sem_valor += 1
            self.tree.insert(
                "", tk.END,
                values=(
                    r.get("nf_numero") or "",
                    r.get("cliente_nome", ""),
                    r.get("destino", ""),
                    _rotulo_servico(r.get("cod_servico")),
                    _fmt_data(r.get("dt_postagem")),
                    _moeda(vl) if vl else "pendente",
                    r.get("cod_rastreio", ""),
                ),
                tags=tuple(tags),
            )

        self.lbl_total.configure(text=_moeda(total))
        self.lbl_qtd.configure(text=str(len(registros)))
        self.lbl_pendente.configure(text=str(sem_valor))
        if not registros:
            self.lbl_status.configure(
                text="Nenhuma postagem neste mês — use ◀ para ver meses anteriores",
                fg="#5a6b85",
            )
        else:
            self.lbl_status.configure(
                text=f"{len(registros)} postagem(ns) no período", fg="#5a6b85")

        # 1ª abertura: busca valores automaticamente se faltar algum.
        if self._primeira_carga:
            self._primeira_carga = False
            if sem_valor > 0 or self._contar_pendentes_globais() > 0:
                self.after(300, lambda: self._acao_buscar_valores(silencioso=True))

    def _contar_pendentes_globais(self) -> int:
        try:
            return len(firebird_db.listar_etiquetas_sem_valor_postagem(self._cfg()))
        except Exception:  # noqa: BLE001
            return 0

    def _acao_buscar_valores(self, silencioso: bool = False) -> None:
        if self._sincronizando:
            return
        self._sincronizando = True
        self.lbl_status.configure(text="Consultando valores na API...", fg="#1565c0")
        cliente = self._cliente_correios()
        db_cfg = self._cfg()

        def trabalho():
            import correios_api as _api

            try:
                pendentes = firebird_db.listar_etiquetas_sem_valor_postagem(db_cfg)
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._fim_sync(0, 0, str(exc), silencioso))
                return
            ok, falhas = 0, 0
            total = len(pendentes)
            erro_api: str | None = None
            for i, r in enumerate(pendentes, 1):
                cod = (r.get("cod_rastreio") or "").strip()
                id_pp = (r.get("id_prepostagem") or "").strip()
                if not cod and not id_pp:
                    continue
                self.after(0, lambda i=i, t=total: self.lbl_status.configure(
                    text=f"Buscando valor {i}/{t}...", fg="#1565c0"))
                try:
                    v = firebird_db.buscar_e_gravar_valor_postagem(
                        db_cfg, r["id_etiqueta"], cod,
                        cliente=cliente,
                        dt_postagem_fallback=r.get("dt_postagem"),
                        id_prepostagem=id_pp or None,
                    )
                except _api.CorreiosError as exc:
                    if erro_api is None:
                        erro_api = str(exc)
                    falhas += 1
                    break
                if v is not None:
                    ok += 1
                else:
                    falhas += 1
            self.after(0, lambda: self._fim_sync(ok, falhas, erro_api, silencioso))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_sync(self, ok: int, falhas: int, erro: str | None,
                  silencioso: bool = False) -> None:
        self._sincronizando = False
        if erro:
            msg = erro[:200]
            self.lbl_status.configure(
                text=f"Erro na API dos Correios: {msg[:80]}…" if len(msg) > 80 else f"Erro na API: {msg}",
                fg="#c62828",
            )
            if not silencioso:
                messagebox.showerror("Buscar valores", erro, parent=self)
        elif ok == 0 and falhas > 0:
            self.lbl_status.configure(
                text=(
                    f"Nenhum valor obtido ({falhas} postagem(ns)). "
                    "Confira credenciais [correios] no config.ini e clique em Buscar valores."
                ),
                fg="#c62828",
            )
            if not silencioso:
                messagebox.showwarning(
                    "Buscar valores",
                    f"Nenhuma das {falhas} consultas retornou valor.\n\n"
                    "Possíveis causas:\n"
                    "• Credenciais [correios] incorretas no config.ini\n"
                    "• API dos Correios indisponível (timeout)\n"
                    "• Objeto ainda sem valor tarifado na API",
                    parent=self,
                )
        elif not silencioso:
            if ok or falhas:
                msg = f"{ok} valor(es) atualizado(s)."
                if falhas:
                    msg += (
                        f"\n{falhas} não retornaram valor "
                        "(ainda não postado ou indisponível)."
                    )
                messagebox.showinfo("Buscar valores", msg, parent=self)
            else:
                messagebox.showinfo(
                    "Buscar valores",
                    "Nenhuma postagem pendente de valor para buscar.",
                    parent=self,
                )
        elif ok > 0:
            self.lbl_status.configure(
                text=f"{ok} valor(es) atualizado(s) na API dos Correios", fg="#2e7d32",
            )
        self.recarregar()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Financeiro Correios — preview")
    root.geometry("1000x620")
    FinanceiroFrame(root).pack(fill=tk.BOTH, expand=True)
    root.mainloop()
