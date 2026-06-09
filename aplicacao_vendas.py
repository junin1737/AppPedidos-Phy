"""
Importador de pedidos PDF → Firebird (TB_NFVENDA_2 / TB_NFV_ITEM_2).
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config as app_config
import db as firebird_db
from parser_pedido import PedidoExtraido, extrair_pedido_pdf


class ConfigDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent)
        self.title("Configurações")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.on_save = on_save
        self.cfg = cfg

        root_frame = ttk.Frame(self, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(root_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        tab_banco = ttk.Frame(notebook, padding=8)
        tab_nf = ttk.Frame(notebook, padding=8)
        notebook.add(tab_banco, text="Banco e OCR")
        notebook.add(tab_nf, text="Numeração NF")

        frame = tab_banco

        ttk.Label(frame, text="Caminho do banco (.FDB):").grid(row=0, column=0, sticky=tk.W)
        self.var_database = tk.StringVar(value=cfg["firebird"].get("database", ""))
        entry_db = ttk.Entry(frame, textvariable=self.var_database, width=55)
        entry_db.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))
        ttk.Button(frame, text="Procurar...", command=self._browse_db).grid(row=1, column=2, padx=(6, 0))

        ttk.Label(frame, text="Usuário:").grid(row=2, column=0, sticky=tk.W)
        self.var_user = tk.StringVar(value=cfg["firebird"].get("user", "SYSDBA"))
        ttk.Entry(frame, textvariable=self.var_user, width=20).grid(row=3, column=0, sticky=tk.W)

        ttk.Label(frame, text="Senha:").grid(row=2, column=1, sticky=tk.W, padx=(12, 0))
        self.var_password = tk.StringVar(value=cfg["firebird"].get("password", "masterkey"))
        ttk.Entry(frame, textvariable=self.var_password, width=20, show="*").grid(row=3, column=1, sticky=tk.W, padx=(12, 0))

        self.var_use_server = tk.BooleanVar(
            value=cfg["firebird"].get("use_server", "true").lower() in ("1", "true", "yes", "sim")
        )
        ttk.Checkbutton(
            frame,
            text="Conectar via servidor Firebird (IBExpert/sistema abertos — recomendado)",
            variable=self.var_use_server,
        ).grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

        ttk.Label(frame, text="Host / Porta:").grid(row=5, column=0, sticky=tk.W, pady=(4, 0))
        self.var_host = tk.StringVar(value=cfg["firebird"].get("host", "localhost"))
        ttk.Entry(frame, textvariable=self.var_host, width=18).grid(row=6, column=0, sticky=tk.W)
        self.var_port = tk.StringVar(value=cfg["firebird"].get("port", "3050"))
        ttk.Entry(frame, textvariable=self.var_port, width=8).grid(row=6, column=1, sticky=tk.W, padx=(8, 0))

        ttk.Label(frame, text="fbclient.dll (Firebird 64-bit, pasta local):").grid(
            row=7, column=0, columnspan=3, sticky=tk.W, pady=(8, 0)
        )
        self.var_fbclient = tk.StringVar(value=cfg["firebird"].get("fbclient_path", ""))
        ttk.Entry(frame, textvariable=self.var_fbclient, width=55).grid(row=8, column=0, columnspan=2, sticky=tk.EW)
        ttk.Button(frame, text="Procurar...", command=self._browse_fbclient).grid(row=8, column=2, padx=(6, 0))

        ttk.Label(frame, text="Tesseract (tesseract.exe):").grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        self.var_tesseract = tk.StringVar(value=cfg["ocr"].get("tesseract_cmd", ""))
        ttk.Entry(frame, textvariable=self.var_tesseract, width=55).grid(row=10, column=0, columnspan=2, sticky=tk.EW)

        ttk.Label(frame, text="Poppler (pasta bin, opcional):").grid(row=11, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        self.var_poppler = tk.StringVar(value=cfg["ocr"].get("poppler_path", ""))
        ttk.Entry(frame, textvariable=self.var_poppler, width=55).grid(row=12, column=0, columnspan=2, sticky=tk.EW)

        frame.columnconfigure(0, weight=1)

        nf = tab_nf
        ttk.Label(
            nf,
            text="Série em TB_NFVENDA_GEN_ID (NF_PROXIMA → NF_NUMERO na venda):",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W)

        ttk.Label(nf, text="ID_SERMOD:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.var_id_sermod = tk.StringVar(value=cfg["nfvenda"].get("id_sermod", "99"))
        ttk.Entry(nf, textvariable=self.var_id_sermod, width=10).grid(row=2, column=0, sticky=tk.W)

        ttk.Label(nf, text="NF_SERIE:").grid(row=1, column=1, sticky=tk.W, padx=(12, 0), pady=(8, 0))
        self.var_nf_serie = tk.StringVar(value=cfg["nfvenda"].get("nf_serie", "2"))
        ttk.Entry(nf, textvariable=self.var_nf_serie, width=8).grid(row=2, column=1, sticky=tk.W, padx=(12, 0))

        ttk.Label(nf, text="NF_MODELO:").grid(row=1, column=2, sticky=tk.W, padx=(12, 0), pady=(8, 0))
        self.var_nf_modelo = tk.StringVar(value=cfg["nfvenda"].get("nf_modelo", "99"))
        ttk.Entry(nf, textvariable=self.var_nf_modelo, width=8).grid(row=2, column=2, sticky=tk.W, padx=(12, 0))

        ttk.Label(nf, text="NF_TIPO:").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        self.var_nf_tipo = tk.StringVar(value=cfg["nfvenda"].get("nf_tipo", "S"))
        ttk.Entry(nf, textvariable=self.var_nf_tipo, width=6).grid(row=4, column=0, sticky=tk.W)

        self.lbl_proximo_nf = ttk.Label(nf, text="Próximo NF_NUMERO: —", foreground="#1565c0")
        self.lbl_proximo_nf.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(12, 0))
        ttk.Button(nf, text="Consultar próximo número", command=self._consultar_proximo_nf).grid(
            row=6, column=0, sticky=tk.W, pady=(6, 0)
        )

        btns = ttk.Frame(root_frame)
        btns.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(btns, text="Testar conexão", command=self._testar_conexao).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text="Salvar", command=self._salvar).pack(side=tk.RIGHT)

    def _testar_conexao(self):
        db_path = self.var_database.get().strip()
        if not db_path or not os.path.isfile(db_path):
            messagebox.showwarning("Teste", "Informe um caminho válido do .FDB.", parent=self)
            return
        cfg = app_config.get_db_config()
        cfg["database"] = db_path
        cfg["user"] = self.var_user.get().strip()
        cfg["password"] = self.var_password.get()
        cfg["fbclient_path"] = self.var_fbclient.get().strip()
        cfg["host"] = self.var_host.get().strip()
        cfg["port"] = int(self.var_port.get().strip() or "3050")
        cfg["use_server"] = self.var_use_server.get()
        try:
            con = firebird_db.conectar(cfg)
            firebird_db.encerrar_conexao(con, commit=True)
            messagebox.showinfo("Teste", "Conexão com o banco OK.", parent=self)
        except Exception as exc:
            messagebox.showerror("Teste", f"Falha na conexão:\n\n{exc}", parent=self)

    def _browse_db(self):
        path = filedialog.askopenfilename(
            title="Selecione o arquivo CLIPP.FDB",
            filetypes=[("Firebird", "*.fdb"), ("Todos", "*.*")],
        )
        if path:
            self.var_database.set(path)

    def _nfvenda_cfg_dialogo(self) -> dict:
        return {
            "id_sermod": int(self.var_id_sermod.get().strip() or "99"),
            "nf_serie": self.var_nf_serie.get().strip(),
            "nf_modelo": self.var_nf_modelo.get().strip(),
            "nf_tipo": self.var_nf_tipo.get().strip() or "S",
        }

    def _consultar_proximo_nf(self):
        db_path = self.var_database.get().strip()
        if not db_path or not os.path.isfile(db_path):
            messagebox.showwarning("NF", "Configure o banco na aba Banco e OCR.", parent=self)
            return
        cfg = app_config.get_db_config()
        cfg["database"] = db_path
        cfg["user"] = self.var_user.get().strip()
        cfg["password"] = self.var_password.get()
        cfg["fbclient_path"] = self.var_fbclient.get().strip()
        cfg["host"] = self.var_host.get().strip()
        cfg["port"] = int(self.var_port.get().strip() or "3050")
        cfg["use_server"] = self.var_use_server.get()
        try:
            con = firebird_db.conectar(cfg)
            cur = con.cursor()
            nf_numero, serie, modelo = firebird_db.consultar_nf_proximo(
                cur, self._nfvenda_cfg_dialogo()
            )
            firebird_db.encerrar_conexao(con, commit=True)
            self.lbl_proximo_nf.config(
                text=f"Próximo NF_NUMERO: {nf_numero} (série {serie}, modelo {modelo}) — consulta sem gravar"
            )
        except Exception as exc:
            messagebox.showerror("NF", f"Não foi possível consultar:\n\n{exc}", parent=self)

    def _browse_fbclient(self):
        path = filedialog.askopenfilename(
            title="Selecione fbclient.dll (64-bit)",
            filetypes=[("Firebird client", "fbclient.dll"), ("Todos", "*.*")],
        )
        if path:
            self.var_fbclient.set(path)

    def _salvar(self):
        db_path = self.var_database.get().strip()
        if not db_path:
            messagebox.showwarning("Configuração", "Informe o caminho do banco.", parent=self)
            return
        if not os.path.isfile(db_path):
            messagebox.showwarning("Configuração", "Arquivo do banco não encontrado.", parent=self)
            return
        self.on_save(
            {
                "database": db_path,
                "user": self.var_user.get().strip(),
                "password": self.var_password.get(),
                "fbclient_path": self.var_fbclient.get().strip(),
                "host": self.var_host.get().strip(),
                "port": self.var_port.get().strip(),
                "use_server": "true" if self.var_use_server.get() else "false",
                "tesseract_cmd": self.var_tesseract.get().strip(),
                "poppler_path": self.var_poppler.get().strip(),
                "id_sermod": self.var_id_sermod.get().strip(),
                "nf_serie": self.var_nf_serie.get().strip(),
                "nf_modelo": self.var_nf_modelo.get().strip(),
                "nf_tipo": self.var_nf_tipo.get().strip(),
            }
        )
        self.destroy()


class ImportadorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Importador de Pedidos — PDF → CLIPP")
        self.root.geometry("960x620")
        self.root.minsize(800, 500)

        self.cfg = app_config.load_config()
        self.pedidos: list[PedidoExtraido] = []
        self._importando = False
        self.registros_desfazer: dict[str, firebird_db.RegistroDesfazer] = {}
        self._ultima_desfazer: firebird_db.RegistroDesfazer | None = None
        self._build_ui()
        self._atualizar_status_banco()

        if not app_config.is_configured(self.cfg):
            self.root.after(200, self._abrir_config)

    def _build_ui(self):
        menubar = tk.Menu(self.root)
        menu_arq = tk.Menu(menubar, tearoff=0)
        menu_arq.add_command(label="Configurações...", command=self._abrir_config)
        menu_arq.add_command(
            label="Desfazer importação...",
            command=self._desfazer_importacao,
        )
        menu_arq.add_separator()
        menu_arq.add_command(label="Sair", command=self.root.quit)
        menubar.add_cascade(label="Arquivo", menu=menu_arq)
        self.root.config(menu=menubar)

        topo = ttk.Frame(self.root, padding=10)
        topo.pack(fill=tk.X)

        ttk.Button(topo, text="Adicionar PDF(s)...", command=self._adicionar_pdfs).pack(side=tk.LEFT)
        ttk.Button(topo, text="Ler PDFs selecionados", command=self._ler_selecionados).pack(side=tk.LEFT, padx=(8, 0))
        self.btn_importar = ttk.Button(
            topo, text="Importar para o banco", command=self._importar_selecionados
        )
        self.btn_importar.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_desfazer = ttk.Button(
            topo,
            text="Desfazer importação",
            command=self._desfazer_importacao,
            state="disabled",
        )
        self.btn_desfazer.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(topo, text="Limpar lista", command=self._limpar_lista).pack(side=tk.LEFT, padx=(8, 0))

        self.lbl_banco = ttk.Label(topo, text="", foreground="#1565c0")
        self.lbl_banco.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        frame_lista = ttk.LabelFrame(paned, text="Pedidos na fila", padding=6)
        paned.add(frame_lista, weight=1)

        cols = ("arquivo", "pedido", "cliente", "itens", "status")
        self.tree = ttk.Treeview(frame_lista, columns=cols, show="headings", height=20)
        self.tree.heading("arquivo", text="Arquivo")
        self.tree.heading("pedido", text="Nº pedido")
        self.tree.heading("cliente", text="Cliente")
        self.tree.heading("itens", text="Itens")
        self.tree.heading("status", text="Status")
        self.tree.column("arquivo", width=200)
        self.tree.column("pedido", width=90)
        self.tree.column("cliente", width=180)
        self.tree.column("itens", width=50, anchor=tk.CENTER)
        self.tree.column("status", width=120)
        scroll = ttk.Scrollbar(frame_lista, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._mostrar_detalhe)

        frame_det = ttk.LabelFrame(paned, text="Detalhes / preview", padding=6)
        paned.add(frame_det, weight=2)

        self.txt_detalhe = tk.Text(frame_det, wrap=tk.WORD, font=("Consolas", 10))
        det_scroll = ttk.Scrollbar(frame_det, command=self.txt_detalhe.yview)
        self.txt_detalhe.configure(yscrollcommand=det_scroll.set)
        self.txt_detalhe.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        det_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        rodape = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        rodape.pack(fill=tk.X)
        self.lbl_status = ttk.Label(rodape, text="Aguardando...", foreground="#333")
        self.lbl_status.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(rodape, mode="indeterminate", length=200)
        self.progress.pack(side=tk.RIGHT)

        self._import_progress_max = 1
        self._import_progress_val = 0

        self.overlay_backdrop = tk.Frame(self.root, bg="#808080", cursor="watch")
        painel = ttk.LabelFrame(
            self.overlay_backdrop,
            text="Importação em andamento",
            padding=14,
        )
        painel.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self.lbl_import_arquivo = ttk.Label(painel, text="", font=("", 10, "bold"))
        self.lbl_import_arquivo.pack(anchor=tk.W)

        self.progress_import = ttk.Progressbar(painel, mode="determinate", length=420)
        self.progress_import.pack(fill=tk.X, pady=(8, 8))

        frame_log = ttk.Frame(painel)
        frame_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log_import = tk.Text(
            frame_log,
            height=14,
            width=58,
            wrap=tk.WORD,
            font=("Consolas", 9),
            state=tk.DISABLED,
        )
        log_scroll = ttk.Scrollbar(frame_log, command=self.txt_log_import.yview)
        self.txt_log_import.configure(yscrollcommand=log_scroll.set)
        self.txt_log_import.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _mostrar_overlay_importacao(self, total_pedidos: int):
        self._import_progress_max = max(1, 1 + total_pedidos)
        self._import_progress_val = 0
        self.progress_import.configure(maximum=self._import_progress_max, value=0)
        self._limpar_log_importacao()
        self.overlay_backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        self.overlay_backdrop.lift()
        self.root.update_idletasks()

    def _ocultar_overlay_importacao(self):
        self.overlay_backdrop.place_forget()

    def _titulo_overlay(self, titulo: str):
        for child in self.overlay_backdrop.winfo_children():
            if isinstance(child, ttk.LabelFrame):
                child.configure(text=titulo)
                break

    def _limpar_log_importacao(self):
        self.txt_log_import.configure(state=tk.NORMAL)
        self.txt_log_import.delete("1.0", tk.END)
        self.txt_log_import.configure(state=tk.DISABLED)

    def _log_importacao_ui(self, texto: str):
        self.txt_log_import.configure(state=tk.NORMAL)
        self.txt_log_import.insert(tk.END, texto + "\n")
        self.txt_log_import.see(tk.END)
        self.txt_log_import.configure(state=tk.DISABLED)

    def _log_importacao_threadsafe(self, texto: str):
        self.root.after(0, lambda t=texto: self._log_importacao_ui(t))

    def _etapa_importacao_threadsafe(self):
        self._import_progress_val = min(
            self._import_progress_val + 1, self._import_progress_max
        )
        val = self._import_progress_val

        def ui():
            self.progress_import.configure(value=val)

        self.root.after(0, ui)

    def _definir_arquivo_importacao(self, nome: str, indice: int, total: int):
        def ui():
            self.lbl_import_arquivo.config(
                text=f"Pedido {indice}/{total}: {nome}"
            )

        self.root.after(0, ui)

    def _atualizar_status_banco(self):
        db = self.cfg["firebird"].get("database", "")
        nome = os.path.basename(db) if db else "(não configurado)"
        ok = app_config.is_configured(self.cfg)
        self.lbl_banco.config(
            text=f"Banco: {nome}" + (" (arquivo OK)" if ok else " — configure em Arquivo"),
            foreground="#2e7d32" if ok else "#c62828",
        )

    def _set_status(self, texto: str):
        self.lbl_status.config(text=texto)
        self.root.update_idletasks()

    def _abrir_config(self):
        def on_save(valores):
            self.cfg["firebird"]["database"] = valores["database"]
            self.cfg["firebird"]["user"] = valores["user"]
            self.cfg["firebird"]["password"] = valores["password"]
            self.cfg["firebird"]["fbclient_path"] = valores.get("fbclient_path", "")
            self.cfg["firebird"]["host"] = valores.get("host", "localhost")
            self.cfg["firebird"]["port"] = valores.get("port", "3050")
            self.cfg["firebird"]["use_server"] = valores.get("use_server", "true")
            if "nfvenda" not in self.cfg:
                self.cfg["nfvenda"] = {}
            self.cfg["nfvenda"]["id_sermod"] = valores.get("id_sermod", "99")
            self.cfg["nfvenda"]["nf_serie"] = valores.get("nf_serie", "2")
            self.cfg["nfvenda"]["nf_modelo"] = valores.get("nf_modelo", "99")
            self.cfg["nfvenda"]["nf_tipo"] = valores.get("nf_tipo", "S")
            self.cfg["ocr"]["tesseract_cmd"] = valores["tesseract_cmd"]
            self.cfg["ocr"]["poppler_path"] = valores["poppler_path"]
            app_config.save_config(self.cfg)
            self._atualizar_status_banco()
            messagebox.showinfo("Configuração", "Configurações salvas em config.ini.")

        ConfigDialog(self.root, self.cfg, on_save)

    def _indice_selecionado(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.index(sel[0])

    def _adicionar_pdfs(self):
        paths = filedialog.askopenfilenames(
            title="Selecione um ou mais PDFs de pedido",
            filetypes=[("PDF", "*.pdf")],
        )
        if not paths:
            return

        for path in paths:
            if any(p.arquivo == path for p in self.pedidos):
                continue
            pedido = PedidoExtraido(arquivo=path)
            self.pedidos.append(pedido)
            self.tree.insert(
                "",
                tk.END,
                values=(
                    os.path.basename(path),
                    "",
                    "",
                    "",
                    "Na fila",
                ),
            )
        self._set_status(f"{len(paths)} PDF(s) adicionado(s). Clique em 'Ler PDFs selecionados'.")

    def _atualizar_linha(self, idx: int, pedido: PedidoExtraido, status: str):
        item_id = self.tree.get_children()[idx]
        nome_cli = pedido.cliente.get("nome", "")[:40]
        self.tree.item(
            item_id,
            values=(
                os.path.basename(pedido.arquivo),
                pedido.numero_pedido or "—",
                nome_cli or "—",
                len(pedido.itens),
                status,
            ),
        )

    def _mostrar_detalhe(self, _event=None):
        idx = self._indice_selecionado()
        self.txt_detalhe.delete("1.0", tk.END)
        if idx is None or idx >= len(self.pedidos):
            return
        p = self.pedidos[idx]
        venda_cfg = app_config.get_venda_config(self.cfg)
        if p.cliente.get("fone_celul"):
            tel_txt = f"({p.cliente.get('ddd_celul', '')}) {p.cliente.get('fone_celul')}"
        else:
            tel_txt = p.cliente.get("telefone_aviso", "—")
        linhas = [
            f"Arquivo: {p.arquivo}",
            f"Pedido: #{p.numero_pedido}" if p.numero_pedido else "Pedido: —",
            f"Data: {p.data_pedido or '—'}",
            f"Pagamento (vai em OBS): {p.pagamento or '—'}",
            f"Envio: {p.envio or '—'}",
            "",
            "Cliente:",
            f"  Nome: {p.cliente.get('nome', '—')}",
            f"  CPF/CNPJ: {p.cliente.get('documento', '—')}",
            f"  Telefone (DDD_CELUL / FONE_CELUL): {tel_txt}",
            f"  END_LOGRAD: {p.cliente.get('end_lograd', '—')}",
            f"  END_NUMERO: {p.cliente.get('end_numero', '—')}",
            f"  END_COMPLE: {p.cliente.get('end_comple', '—')}",
            f"  END_BAIRRO: {p.cliente.get('end_bairro', '—')}",
            f"  END_CEP: {p.cliente.get('end_cep', '—')}",
            f"  Cidade/UF (PDF): {(p.cliente.get('cidade') or '—')} / {(p.cliente.get('uf') or '—')}",
            f"  ID_CIDADE (TB_CIDADE_SIS): {p.cliente.get('id_cidade', '— (busca na importação)')}",
            f"  ID_CLIENTE (TB_CLIENTE): {p.cliente.get('id_cliente', '— (gravado na importação)')}",
            f"  ID_PAIS: 1058",
            f"  Observação: {p.cliente.get('observacao', '—')}",
            "",
            "Venda (valores na importação):",
            f"  ID_VENDEDOR: {venda_cfg['id_vendedor']}",
            f"  XX_VENDEDOR: {venda_cfg['xx_vendedor']}",
            f"  ID_PLANOCONTA: {venda_cfg['id_planoconta']}",
            f"  CC_CUSTO: {venda_cfg['cc_custo']}",
            f"  VLR_BC_FRETE: R$ {float(p.resumo.get('valor_frete') or 0):.2f}",
            "",
            f"Itens ({len(p.itens)}):",
        ]
        for it in p.itens:
            id_est = it.id_identificador
            id_txt = str(id_est) if id_est else "— (não encontrado no estoque)"
            desc = it.descricao or "— (vincule ao estoque na leitura)"
            linhas.append(
                f"  {it.quantidade}x {it.referencia} "
                f"(PDF: {it.referencia_original}) — ID_IDENTIFICADOR: {id_txt}\n"
                f"      {desc} — R$ {it.preco_unitario:.2f} = R$ {it.preco_total:.2f}"
            )
        total = p.resumo.get("valor_total")
        if total:
            linhas.extend(["", f"Total: R$ {total:.2f}"])
        if p.erros:
            linhas.extend(["", "Avisos:", *[f"  - {e}" for e in p.erros]])
        self.txt_detalhe.insert("1.0", "\n".join(linhas))

    def _ler_selecionados(self):
        sel = self.tree.selection()
        indices = [self.tree.index(i) for i in sel] if sel else list(range(len(self.pedidos)))
        if not indices:
            messagebox.showinfo("Leitura", "Adicione ou selecione PDFs na lista.")
            return

        tess = self.cfg["ocr"].get("tesseract_cmd", "").strip()
        if not tess or not os.path.isfile(tess):
            messagebox.showwarning(
                "OCR",
                "Configure o caminho do Tesseract em Arquivo → Configurar banco / OCR.",
            )
            return

        poppler = self.cfg["ocr"].get("poppler_path", "")
        lang = self.cfg["ocr"].get("lang", "por")

        self.progress.start(10)
        try:
            for idx in indices:
                pedido = self.pedidos[idx]
                self._set_status(f"Lendo {os.path.basename(pedido.arquivo)}...")
                self._atualizar_linha(idx, pedido, "Lendo OCR...")

                def on_prog(msg):
                    self._set_status(msg)

                try:
                    novo = extrair_pedido_pdf(
                        pedido.arquivo,
                        tesseract_cmd=tess,
                        poppler_path=poppler,
                        lang=lang,
                        on_progress=on_prog,
                    )
                    if app_config.is_configured(self.cfg):
                        db_cfg = app_config.get_db_config(self.cfg)
                        try:
                            if novo.cliente.get("nome"):
                                self._set_status(
                                    f"Cliente — {os.path.basename(novo.arquivo)}..."
                                )
                                id_cli, cliente_novo = (
                                    firebird_db.preparar_cliente_no_banco(db_cfg, novo)
                                )
                                if cliente_novo:
                                    novo.erros.append(
                                        f"Cliente novo cadastrado: ID_CLIENTE={id_cli}"
                                    )
                                else:
                                    novo.erros.append(
                                        f"Cliente existente vinculado: ID_CLIENTE={id_cli}"
                                    )
                            if novo.itens:
                                self._set_status(
                                    f"Estoque — {os.path.basename(novo.arquivo)}..."
                                )
                                con_est = firebird_db.conectar(db_cfg)
                                try:
                                    sem_estoque = firebird_db.vincular_produtos_pedido(
                                        con_est, novo
                                    )
                                finally:
                                    firebird_db.encerrar_conexao(con_est)
                                for ref in sem_estoque:
                                    novo.erros.append(
                                        f"Referência sem estoque: {ref} "
                                        "(TB_EST_PRODUTO_2)"
                                    )
                        except Exception as exc_db:
                            novo.erros.append(f"Banco na leitura: {exc_db}")
                    self.pedidos[idx] = novo
                    n_lin = len(novo.itens)
                    n_un = sum(it.quantidade for it in novo.itens)
                    st = f"Lido OK — {n_lin} linha(s)" if novo.itens else "Sem itens"
                    if novo.itens and n_un != n_lin:
                        st += f" ({n_un} un.)"
                    if novo.erros:
                        st = "Lido c/ avisos"
                    self._atualizar_linha(idx, novo, st)
                except Exception as exc:
                    pedido.erros.append(str(exc))
                    self.pedidos[idx] = pedido
                    self._atualizar_linha(idx, pedido, "Erro leitura")
                    messagebox.showerror("OCR", f"Erro em {pedido.arquivo}:\n{exc}")
        finally:
            self.progress.stop()
            self._set_status("Leitura concluída.")
            self._mostrar_detalhe()

    def _limpar_lista(self):
        self.pedidos.clear()
        self.registros_desfazer.clear()
        self._ultima_desfazer = None
        self._atualizar_btn_desfazer()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.txt_detalhe.delete("1.0", tk.END)
        self._set_status("Lista limpa.")

    def _atualizar_btn_desfazer(self):
        tem = bool(self.registros_desfazer)
        self.btn_desfazer.config(state="normal" if tem else "disabled")

    def _registro_desfazer_para_ui(self) -> firebird_db.RegistroDesfazer | None:
        idx = self._indice_selecionado()
        if idx is not None:
            arq = self.pedidos[idx].arquivo
            if arq in self.registros_desfazer:
                return self.registros_desfazer[arq]
        return self._ultima_desfazer

    def _registrar_desfazer(self, resultado: firebird_db.ResultadoImportacao):
        if resultado.desfazer and resultado.desfazer.pode_desfazer:
            self.registros_desfazer[resultado.arquivo] = resultado.desfazer
            self._ultima_desfazer = resultado.desfazer
            self._atualizar_btn_desfazer()

    def _desfazer_importacao(self):
        if self._importando:
            return

        reg = self._registro_desfazer_para_ui()
        if not reg:
            messagebox.showinfo(
                "Desfazer",
                "Nenhuma importação pendente de desfazer.\n\n"
                "Selecione um pedido na lista que já foi importado (ou falhou após gravar no banco).",
            )
            return

        if not app_config.is_configured(self.cfg):
            messagebox.showwarning("Banco", "Configure o banco antes de desfazer.")
            return

        nome = os.path.basename(reg.arquivo)
        if not messagebox.askyesno(
            "Desfazer importação",
            f"Desfazer o que foi gravado para «{nome}»?\n\n"
            f"Será removido/revertido: {reg.resumo()}.\n\n"
            "Esta ação não pode ser desfeita pelo aplicativo.",
            icon="warning",
        ):
            return

        self._importando = True
        self.btn_importar.config(state="disabled")
        self.btn_desfazer.config(state="disabled")
        self._mostrar_overlay_importacao(1)
        self._titulo_overlay("Desfazendo importação")
        self._limpar_log_importacao()
        self._log_importacao_ui(f"Desfazendo: {nome}")
        self._set_status(f"Desfazendo {nome}...")

        db_cfg = app_config.get_db_config(self.cfg)
        arquivo_reg = reg.arquivo

        def worker():
            erro: Exception | None = None
            res_desf: firebird_db.ResultadoDesfazer | None = None
            con = None
            try:
                con = firebird_db.conectar(db_cfg)
                res_desf = firebird_db.desfazer_importacao(
                    con,
                    reg,
                    on_log=self._log_importacao_threadsafe,
                )
            except Exception as exc:
                erro = exc
            finally:
                firebird_db.encerrar_conexao(con, commit=erro is None)

            self.root.after(
                0,
                lambda: self._desfazer_finalizado(erro, res_desf, arquivo_reg),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _desfazer_finalizado(
        self,
        erro: Exception | None,
        resultado: firebird_db.ResultadoDesfazer | None,
        arquivo: str,
    ):
        self._titulo_overlay("Importação em andamento")
        self._ocultar_overlay_importacao()
        self._importando = False
        self.btn_importar.config(state="normal")

        if erro is not None:
            self._set_status("Falha ao desfazer.")
            messagebox.showerror("Desfazer", f"Erro:\n\n{erro}")
            self._atualizar_btn_desfazer()
            return

        if resultado and resultado.sucesso:
            self.registros_desfazer.pop(arquivo, None)
            if self._ultima_desfazer and self._ultima_desfazer.arquivo == arquivo:
                self._ultima_desfazer = None
                if self.registros_desfazer:
                    self._ultima_desfazer = next(
                        iter(self.registros_desfazer.values())
                    )
            for i, p in enumerate(self.pedidos):
                if p.arquivo == arquivo:
                    self._atualizar_linha(i, p, "Desfeito")
                    break
            self._set_status("Importação desfeita.")
            messagebox.showinfo("Desfazer", resultado.mensagem)
        else:
            self._set_status("Não foi possível desfazer.")
            messagebox.showerror(
                "Desfazer",
                resultado.mensagem if resultado else "Falha desconhecida.",
            )

        self._atualizar_btn_desfazer()

    def _importar_selecionados(self):
        if self._importando:
            return

        if not app_config.is_configured(self.cfg):
            messagebox.showwarning("Banco", "Configure o banco antes de importar.")
            self._abrir_config()
            return

        sel = self.tree.selection()
        indices = [self.tree.index(i) for i in sel] if sel else list(range(len(self.pedidos)))
        if not indices:
            messagebox.showinfo("Importar", "Nenhum pedido na fila.")
            return

        sem_itens = [i for i in indices if not self.pedidos[i].itens]
        if sem_itens and len(sem_itens) == len(indices):
            messagebox.showwarning("Importar", "Leia o PDF antes de importar (nenhum item extraído).")
            return

        pedidos_importar = [
            (idx, self.pedidos[idx])
            for idx in indices
            if self.pedidos[idx].itens
        ]
        sem_cliente = [
            (idx, self.pedidos[idx])
            for idx, _ in pedidos_importar
            if not (self.pedidos[idx].cliente.get("nome") or "").strip()
        ]
        if sem_cliente:
            nomes = ", ".join(os.path.basename(p.arquivo) for _, p in sem_cliente[:3])
            extra = f" (+{len(sem_cliente) - 3})" if len(sem_cliente) > 3 else ""
            messagebox.showwarning(
                "Importar",
                "Leia o PDF antes de importar — nome do cliente não foi extraído.\n\n"
                f"Pedido(s): {nomes}{extra}",
            )
            return

        pulados = [
            (idx, "Leia o PDF antes")
            for idx in indices
            if not self.pedidos[idx].itens
        ]

        self._importando = True
        self.btn_importar.config(state="disabled")
        self._set_status("Importando pedidos...")
        self._mostrar_overlay_importacao(len(pedidos_importar) or 1)
        self._log_importacao_ui("Conectando ao Firebird...")

        db_cfg = app_config.get_db_config(self.cfg)
        nfvenda_cfg = app_config.get_nfvenda_config(self.cfg)
        venda_cfg = app_config.get_venda_config(self.cfg)

        def worker():
            erro: Exception | None = None
            resultados: list = []
            status_linhas: list[tuple[int, str]] = list(pulados)

            try:
                firebird_db.conectar(db_cfg).close()
                self._log_importacao_threadsafe("Conexão com o banco OK.")
                self._etapa_importacao_threadsafe()
                total = len(pedidos_importar)
                for n, (idx, pedido) in enumerate(pedidos_importar, start=1):
                    nome = os.path.basename(pedido.arquivo)
                    self._definir_arquivo_importacao(nome, n, total)
                    self._log_importacao_threadsafe(f"——— {nome} ———")
                    res = firebird_db.importar_pedido(
                        db_cfg,
                        pedido,
                        nfvenda_cfg,
                        venda_cfg,
                        on_log=self._log_importacao_threadsafe,
                        on_etapa=self._etapa_importacao_threadsafe,
                    )
                    resultados.append((idx, res))
                    status_linhas.append(
                        (idx, "Importado" if res.sucesso else "Falhou")
                    )
                    self._log_importacao_threadsafe(
                        "Concluído." if res.sucesso else f"Falhou: {res.mensagem}"
                    )
            except Exception as exc:
                erro = exc

            self.root.after(
                0, lambda: self._importar_finalizado(erro, resultados, status_linhas)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _importar_finalizado(self, erro, resultados, status_linhas):
        self._ocultar_overlay_importacao()
        self._importando = False
        self.btn_importar.config(state="normal")

        for idx, status in status_linhas:
            self._atualizar_linha(idx, self.pedidos[idx], status)

        if erro is not None:
            self._set_status("Falha na importação.")
            messagebox.showerror(
                "Importação",
                f"Não foi possível conectar ou gravar no banco:\n\n{erro}\n\n"
                "Verifique se o Firebird está em execução e se o caminho do .FDB está correto.",
            )
            return

        if not resultados:
            self._set_status("Nenhum pedido importado.")
            messagebox.showwarning(
                "Importação",
                "Nenhum pedido foi importado. Confira se o PDF foi lido e possui itens.",
            )
            return

        for _idx, r in resultados:
            self._registrar_desfazer(r)

        ok = sum(1 for _i, r in resultados if r.sucesso)
        falha = len(resultados) - ok
        linhas = [f"Importação: {ok} sucesso(s), {falha} falha(s)."]
        if ok:
            linhas.append("")
            linhas.append(
                "Visibilidade CLIPP: UPDATE + commit automático (padrão IBExpert)."
            )
            linhas.append(
                "Se a saída ainda abrir sem nome/CPF, salve qualquer cadastro "
                "no CLIPP ou reabra Saídas/Mercadorias."
            )
        if self.registros_desfazer:
            linhas.append(
                "Pedidos com gravação no banco podem ser revertidos com «Desfazer importação»."
            )
        for _idx, r in resultados:
            nome = os.path.basename(r.arquivo)
            linhas.append(f"• {nome}: {r.mensagem}")
            if r.desfazer and r.desfazer.pode_desfazer and not r.sucesso:
                linhas.append("  → Disponível para desfazer")
            if r.itens_nao_encontrados:
                for faltante in r.itens_nao_encontrados[:8]:
                    linhas.append(
                        f"  • qtd={faltante.quantidade} {faltante.referencia} "
                        f"| R$ {faltante.preco_unitario:.2f} un."
                        + (
                            f" | {faltante.descricao}"
                            if faltante.descricao
                            else ""
                        )
                    )
                if len(r.itens_nao_encontrados) > 8:
                    linhas.append(
                        f"  … +{len(r.itens_nao_encontrados) - 8} linha(s)"
                    )
                linhas.append(
                    f"  Subtotal pendente: R$ "
                    f"{sum(i.preco_total for i in r.itens_nao_encontrados):.2f}"
                )

        messagebox.showinfo("Importação", "\n".join(linhas))
        self._set_status(f"Importação finalizada: {ok} OK, {falha} falha(s).")
        self._atualizar_btn_desfazer()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    ImportadorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
