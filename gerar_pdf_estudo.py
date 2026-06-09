"""
Script para gerar o PDF de estudo do projeto AppPedidos Phy.
Execute uma vez para produzir o arquivo: EstudoProjetoPedidos.pdf
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted,
    HRFlowable, PageBreak, Table, TableStyle
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

OUTPUT = r"c:\Work\Pessoal\Projetos\AppPedidos Phy\EstudoProjetoPedidos.pdf"

# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------
base = getSampleStyleSheet()

titulo_doc = ParagraphStyle("TituloDoc", parent=base["Title"],
    fontSize=22, textColor=colors.HexColor("#1a237e"),
    spaceAfter=6, alignment=TA_CENTER)

subtitulo = ParagraphStyle("Subtitulo", parent=base["Normal"],
    fontSize=12, textColor=colors.HexColor("#555555"),
    spaceAfter=20, alignment=TA_CENTER)

h1 = ParagraphStyle("H1", parent=base["Heading1"],
    fontSize=16, textColor=colors.HexColor("#283593"),
    spaceBefore=18, spaceAfter=8,
    borderPad=4, leading=20)

h2 = ParagraphStyle("H2", parent=base["Heading2"],
    fontSize=13, textColor=colors.HexColor("#1565c0"),
    spaceBefore=14, spaceAfter=6)

h3 = ParagraphStyle("H3", parent=base["Heading3"],
    fontSize=11, textColor=colors.HexColor("#0277bd"),
    spaceBefore=10, spaceAfter=4)

corpo = ParagraphStyle("Corpo", parent=base["Normal"],
    fontSize=10, leading=15, spaceAfter=6,
    alignment=TA_JUSTIFY)

destaque = ParagraphStyle("Destaque", parent=base["Normal"],
    fontSize=10, leading=14, spaceAfter=6,
    backColor=colors.HexColor("#e8f5e9"),
    borderColor=colors.HexColor("#388e3c"),
    borderWidth=1, borderPad=6,
    leftIndent=8)

aviso = ParagraphStyle("Aviso", parent=base["Normal"],
    fontSize=10, leading=14, spaceAfter=6,
    backColor=colors.HexColor("#fff3e0"),
    borderColor=colors.HexColor("#e65100"),
    borderWidth=1, borderPad=6,
    leftIndent=8)

codigo_style = ParagraphStyle("Codigo", parent=base["Code"],
    fontName="Courier", fontSize=8, leading=12,
    backColor=colors.HexColor("#f5f5f5"),
    borderColor=colors.HexColor("#bdbdbd"),
    borderWidth=1, borderPad=6,
    spaceAfter=8, leftIndent=0)

bullet = ParagraphStyle("Bullet", parent=base["Normal"],
    fontSize=10, leading=14, spaceAfter=4, leftIndent=20,
    bulletIndent=10)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def code(text):
    return Preformatted(text, codigo_style)

def p(text, style=corpo):
    return Paragraph(text, style)

def h(text, level=1):
    styles = {1: h1, 2: h2, 3: h3}
    return Paragraph(text, styles[level])

def hr():
    return HRFlowable(width="100%", thickness=1,
                      color=colors.HexColor("#bbdefb"), spaceAfter=6)

def sp(n=1):
    return Spacer(1, 0.3 * n * cm)

# ---------------------------------------------------------------------------
# Conteúdo
# ---------------------------------------------------------------------------

def build_content():
    story = []

    # ── Capa ────────────────────────────────────────────────────────────────
    story += [
        sp(4),
        p("Importador de Pedidos", titulo_doc),
        p("Tiao Cards  ·  Estudo completo do projeto", subtitulo),
        hr(),
        sp(1),
        p("Este documento explica, bloco por bloco, cada arquivo do projeto "
          "<b>AppPedidos Phy</b>. O objetivo é ajudá-lo a entender como o "
          "código funciona e aprender os conceitos de programação Python "
          "usados na prática.", corpo),
        sp(1),
        p("<b>Arquivos cobertos:</b>", corpo),
        p("• <b>LEIA-ME.md</b> — Documentação de instalação e uso", bullet),
        p("• <b>extrator_ocr.py</b> — Motor de leitura OCR e extração de dados", bullet),
        p("• <b>aplicacao_vendas.py</b> — Interface gráfica e integração com banco Firebird", bullet),
        PageBreak(),
    ]

    # ── Índice ───────────────────────────────────────────────────────────────
    story += [
        h("Sumário"),
        hr(),
        p("1. Visão Geral do Projeto"),
        p("2. Conceitos Fundamentais Usados"),
        p("3. Arquivo: LEIA-ME.md  (documentação)"),
        p("4. Arquivo: extrator_ocr.py"),
        p("   4.1 Importações"),
        p("   4.2 Função extrair_texto_ocr"),
        p("   4.3 Função parsear_dados — bloco Cliente"),
        p("   4.4 Função parsear_dados — bloco Itens"),
        p("   4.5 Função parsear_dados — bloco Totais"),
        p("   4.6 Bloco principal __main__"),
        p("5. Arquivo: aplicacao_vendas.py"),
        p("   5.1 Importações e configuração do banco"),
        p("   5.2 Classe VendasApp — __init__"),
        p("   5.3 Método conectar_banco"),
        p("   5.4 Método extrair_dados_pdf"),
        p("   5.5 Método processar_pedido — Cliente"),
        p("   5.6 Método processar_pedido — Inserção da Venda"),
        p("   5.7 Método processar_pedido — Inserção dos Itens"),
        p("6. Fluxo Completo (passo a passo)"),
        p("7. Pontos de Atenção e Melhorias Sugeridas"),
        PageBreak(),
    ]

    # ── 1. Visão Geral ───────────────────────────────────────────────────────
    story += [
        h("1. Visão Geral do Projeto"),
        hr(),
        p("O <b>Importador de Pedidos</b> é uma aplicação desktop feita em Python "
          "que resolve um problema prático: quando um pedido chega pelo site "
          "<i>tiaocards.com.br</i> em formato PDF, alguém precisaria digitar "
          "manualmente cada item no sistema de gestão (Firebird/CLIPP). Essa "
          "aplicação automatiza esse processo inteiro."),
        sp(),
        p("<b>O que ela faz, em linguagem simples:</b>", h3),
        p("1. O usuário clica em um botão e escolhe o PDF do pedido.", bullet),
        p("2. O programa 'lê' o PDF usando OCR (reconhecimento de texto em imagens).", bullet),
        p("3. O código identifica o cliente, os produtos e os preços no texto extraído.", bullet),
        p("4. Conecta ao banco de dados Firebird e insere tudo automaticamente.", bullet),
        p("5. Exibe uma mensagem de sucesso com o número do pedido gerado.", bullet),
        sp(),
        p("<b>Tecnologias utilizadas:</b>", h3),
    ]

    tabela_tec = [
        ["Biblioteca / Ferramenta", "Para que serve"],
        ["tkinter", "Interface gráfica (janelas, botões, labels)"],
        ["pdf2image + Poppler", "Converte cada página do PDF em uma imagem"],
        ["pytesseract + Tesseract", "Lê o texto dentro das imagens (OCR)"],
        ["re (regex)", "Encontra padrões de texto (CPF, referências, preços)"],
        ["fdb", "Conecta e executa comandos no banco de dados Firebird"],
        ["json", "Formata dados estruturados (usado no modo debug)"],
    ]
    t = Table(tabela_tec, colWidths=[5.5 * cm, 11 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565c0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f5f5"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdbdbd")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [t, sp(), PageBreak()]

    # ── 2. Conceitos Fundamentais ────────────────────────────────────────────
    story += [
        h("2. Conceitos Fundamentais Usados"),
        hr(),
        h("Classes e Objetos", 2),
        p("Em Python, uma <b>classe</b> é como um molde. Ela define atributos (dados) "
          "e métodos (ações). No projeto, <code>VendasApp</code> é uma classe que "
          "agrupa toda a lógica da janela e das ações do botão."),
        code("class VendasApp:\n    def __init__(self, root):   # método construtor\n        self.root = root           # atributo: guarda a janela\n        self.label = tk.Label(...) # atributo: widget de texto"),
        h("Expressões Regulares (regex)", 2),
        p("O módulo <code>re</code> permite encontrar padrões dentro de textos. "
          "É muito usado aqui para extrair CPF/CNPJ, referências de produto e preços "
          "do texto confuso que o OCR produz."),
        code("# Encontra padrões como 'BLZD-EN090', 'CARD-PT012'...\nmatch = re.search(r'([A-Z]{4}-[A-Z]{2}\\d{3})', linha)\n#                    ^^^^ 4 letras maiúsculas\n#                         ^^^ traço literal\n#                           ^^ 2 letras maiúsculas\n#                              ^^^^ 3 dígitos"),
        h("Contexto 'with' e Conexão ao Banco", 2),
        p("O banco Firebird é acessado via <code>fdb.connect()</code>. Após executar "
          "os comandos, é obrigatório chamar <code>commit()</code> para salvar ou "
          "<code>rollback()</code> para cancelar em caso de erro. O bloco "
          "<code>try/except/finally</code> garante que a conexão sempre seja fechada."),
        code("try:\n    cur.execute(\"INSERT INTO TB_...\")\n    con.commit()         # salva no banco\nexcept Exception as e:\n    con.rollback()       # cancela tudo se der erro\nfinally:\n    con.close()          # fecha sempre"),
        h("OCR — Reconhecimento Óptico de Caracteres", 2),
        p("O PDF de pedido não é texto puro — é uma imagem. Por isso precisamos "
          "de dois passos: (1) <b>pdf2image</b> converte cada página em imagem PNG, "
          "e (2) <b>pytesseract</b> passa essa imagem pelo Tesseract, que reconhece "
          "os caracteres e devolve uma string de texto."),
        PageBreak(),
    ]

    # ── 3. LEIA-ME.md ───────────────────────────────────────────────────────
    story += [
        h("3. Arquivo: LEIA-ME.md"),
        hr(),
        p("O arquivo LEIA-ME.md (README) documenta como instalar e usar o projeto. "
          "É o primeiro arquivo que qualquer pessoa deve ler antes de rodar a aplicação."),
        sp(),
        h("Conteúdo do arquivo", 2),
        code(
"""# Importador de Pedidos - Tiao Cards

Esta aplicação automatiza a entrada de pedidos do site
`tiaocards.com.br` no seu sistema Firebird.

## Pré-requisitos
1. Python 3.x
2. Tesseract OCR  (com dados de idioma "Portuguese")
3. pip install fdb pytesseract pdf2image
4. Poppler (necessário para converter PDF em imagem)

## Configuração
Altere a linha 11 de aplicacao_vendas.py:
  'database': 'C:/Caminho/Para/Seu/CLIPP.FDB'

## Como usar
1. Dê um duplo clique em aplicacao_vendas.py
2. Clique em "Selecionar PDF e Processar"
3. Escolha o PDF do pedido
4. Aguarde a mensagem de sucesso!

## Lógica de Conversão
Detecta se a carta é em português (bandeira do Brasil)
e troca -EN por -PT no código de referência."""
        ),
        h("Explicação dos pré-requisitos", 2),
        p("<b>Python 3.x</b>: A linguagem de programação usada. Sem ela, nenhum "
          "script .py funciona.", bullet),
        p("<b>Tesseract OCR</b>: Programa externo (não é biblioteca Python) que "
          "realiza o reconhecimento de texto nas imagens. Precisa estar instalado "
          "no sistema operacional.", bullet),
        p("<b>fdb</b>: Driver Python para Firebird. Permite executar SQL diretamente "
          "do código.", bullet),
        p("<b>pdf2image</b>: Biblioteca que usa o Poppler por baixo para converter "
          "PDFs em imagens.", bullet),
        p("<b>Poppler</b>: Ferramenta open-source de renderização de PDF. "
          "Precisa estar no PATH do Windows.", bullet),
        PageBreak(),
    ]

    # ── 4. extrator_ocr.py ──────────────────────────────────────────────────
    story += [
        h("4. Arquivo: extrator_ocr.py"),
        hr(),
        p("Este arquivo contém as funções de 'extração pura': ler o PDF e transformar "
          "o texto num dicionário Python organizado. Ele pode ser rodado sozinho para "
          "testar a leitura sem abrir a interface gráfica."),
        sp(),
    ]

    # 4.1
    story += [
        h("4.1 Importações", 2),
        code(
"""from pdf2image import convert_from_path
import pytesseract
import json
import re"""
        ),
        p("<b>from pdf2image import convert_from_path</b>: importa apenas a função "
          "necessária do pacote, em vez de importar o pacote inteiro."),
        p("<b>pytesseract</b>: wrapper Python do Tesseract. Recebe uma imagem e "
          "devolve o texto reconhecido."),
        p("<b>json</b>: usado apenas na função <code>__main__</code> para exibir o "
          "resultado formatado no terminal (debug)."),
        p("<b>re</b>: expressões regulares — o coração da extração de dados."),
        sp(),
    ]

    # 4.2
    story += [
        h("4.2 Função extrair_texto_ocr", 2),
        code(
"""def extrair_texto_ocr(caminho_pdf):
    paginas = convert_from_path(caminho_pdf)
    texto_completo = ""
    for i, pagina in enumerate(paginas):
        texto = pytesseract.image_to_string(pagina, lang='por')
        texto_completo += texto + "\\n"
    return texto_completo"""),
        p("<b>convert_from_path(caminho_pdf)</b>: lê o arquivo PDF e retorna uma "
          "lista de objetos de imagem PIL, um por página. Se o PDF tiver 3 páginas, "
          "a lista terá 3 itens."),
        p("<b>enumerate(paginas)</b>: percorre a lista dando também o índice. "
          "O <code>i</code> não é usado aqui, mas é boa prática quando você pode "
          "precisar do número da página futuramente."),
        p("<b>pytesseract.image_to_string(pagina, lang='por')</b>: passa a imagem "
          "para o Tesseract. O parâmetro <code>lang='por'</code> usa o modelo de "
          "idioma português, melhorando a precisão em palavras com acentos."),
        p("<b>texto_completo += texto + \"\\n\"</b>: concatena o texto de cada página "
          "com uma quebra de linha entre elas."),
        p("A função retorna uma única string gigante com todo o texto do PDF.", destaque),
        sp(),
    ]

    # 4.3
    story += [
        h("4.3 Função parsear_dados — bloco Cliente", 2),
        code(
"""def parsear_dados(texto):
    dados = {
        'cliente': {},
        'itens': [],
        'resumo': {}
    }

    linhas = [l.strip() for l in texto.split('\\n') if l.strip()]

    for i, linha in enumerate(linhas):
        # ── bloco CLIENTE ──────────────────────
        if "Destinatário" in linha:
            match_doc = re.search(r"CPF/CNPJ:\\s*(\\d+)", linha)
            if match_doc:
                dados['cliente']['documento'] = match_doc.group(1)
            nome_part = linha.replace("Destinatário", "").split(",")[0].strip()
            dados['cliente']['nome'] = nome_part"""),
        p("<b>dados = {'cliente': {}, 'itens': [], 'resumo': {}}</b>: cria o "
          "dicionário que será preenchido. Note os tipos: dict para cliente e resumo, "
          "list para itens (pode haver vários produtos)."),
        p("<b>linhas = [l.strip() for l in texto.split('\\n') if l.strip()]</b>: "
          "list comprehension que: (1) quebra o texto em linhas, (2) remove espaços "
          "nas extremidades com strip(), (3) ignora linhas em branco."),
        p("<b>if \"Destinatário\" in linha</b>: o OCR geralmente lê a linha do "
          "destinatário como uma linha longa contendo essa palavra-chave."),
        p("<b>re.search(r\"CPF/CNPJ:\\\\s*(\\\\d+)\", linha)</b>: procura o padrão "
          "\"CPF/CNPJ:\" seguido de zero ou mais espaços (\\\\s*) e captura os "
          "dígitos (\\\\d+). O grupo 1 captura só os números."),
        p("<b>.split(\",\")[0]</b>: o nome do cliente pode vir como "
          "'João Silva, CPF: 123...' — pegar o índice 0 pega só o nome."),
        sp(),
    ]

    # 4.4
    story += [
        h("4.4 Função parsear_dados — bloco Itens", 2),
        code(
"""        match_ref = re.search(r"([A-Z]{4}-[A-Z]{2}\\d{3})", linha)
        if match_ref:
            ref_original = match_ref.group(1)

            # Quantidade: procura 'Nx' nas últimas 3 linhas
            qtd = 1
            for k in range(i, max(-1, i-3), -1):
                match_qtd = re.search(r"(\\d+)x", linhas[k])
                if match_qtd:
                    qtd = int(match_qtd.group(1))
                    break

            # Conversão EN → PT se contexto indicar idioma PT
            ref_final = ref_original
            contexto = linha + (linhas[i-1] if i > 0 else "")
            if "[PT]" in contexto or "PT]" in contexto:
                if "-EN" in ref_original:
                    ref_final = ref_original.replace("-EN", "-PT")

            # Preço unitário nas próximas 4 linhas
            preco_unit = 0.0
            for j in range(i, min(i+4, len(linhas))):
                precos = re.findall(r"R\\$\\s*([\\d,.]+)", linhas[j])
                if len(precos) >= 2:
                    preco_unit = float(precos[-2].replace('.','').replace(',','.'))
                    break

            if not any(item['referencia_original'] == ref_original
                       for item in dados['itens']):
                dados['itens'].append({
                    'quantidade': qtd,
                    'referencia_original': ref_original,
                    'referencia_final': ref_final,
                    'preco_unitario': preco_unit,
                    ...
                })"""),
        p("<b>r\"([A-Z]{4}-[A-Z]{2}\\\\d{3})\"</b>: regex para capturar referências "
          "do tipo BLZD-EN090 — exatamente 4 letras, traço, 2 letras, 3 dígitos."),
        p("<b>range(i, max(-1, i-3), -1)</b>: loop de trás para frente, partindo da "
          "linha atual (i) até 3 linhas acima. Isso porque a quantidade (ex: '2x') "
          "pode estar numa linha antes do código do produto."),
        p("<b>Lógica EN→PT</b>: alguns produtos têm versão em inglês e em português "
          "com referências diferentes (ex: BLZD-EN090 vs BLZD-PT090). Se o OCR leu "
          "'[PT]' perto do produto, troca automaticamente o sufixo."),
        p("<b>re.findall</b>: diferente de re.search, retorna TODOS os matches. "
          "precos[-2] pega o penúltimo valor encontrado (preço unitário), pois o "
          "último costuma ser o total da linha."),
        p("<b>not any(...)</b>: evita duplicatas. O OCR às vezes lê o mesmo produto "
          "duas vezes (ex: em linhas adjacentes). O any() verifica se a referência "
          "já está na lista antes de adicionar.", aviso),
        sp(),
    ]

    # 4.5
    story += [
        h("4.5 Função parsear_dados — bloco Totais", 2),
        code(
"""        if "Valor dos Itens:" in linha:
            v = re.search(r"R\\$\\s*([\\d,.]+)", linha)
            if v: dados['resumo']['valor_itens'] = float(
                      v.group(1).replace('.', '').replace(',', '.'))
        if "Frete:" in linha:
            v = re.search(r"R\\$\\s*([\\d,.]+)", linha)
            if v: dados['resumo']['valor_frete'] = float(...)
        if "Valor Total:" in linha:
            v = re.search(r"R\\$\\s*([\\d,.]+)", linha)
            if v: dados['resumo']['valor_total'] = float(...)"""),
        p("Cada <code>if</code> procura uma linha específica no texto do OCR."),
        p("<b>.replace('.', '').replace(',', '.')</b>: converte o formato brasileiro "
          "(R$ 1.234,56) para o formato que o Python entende como float (1234.56). "
          "Primeiro remove os pontos de milhar, depois troca a vírgula decimal por ponto."),
        sp(),
    ]

    # 4.6
    story += [
        h("4.6 Bloco principal __main__", 2),
        code(
"""if __name__ == "__main__":
    caminho = "/home/ubuntu/projeto_firebird/pdf pedido.pdf"
    texto = extrair_texto_ocr(caminho)
    resultado = parsear_dados(texto)
    print(json.dumps(resultado, indent=2, ensure_ascii=False))"""),
        p("<b>if __name__ == \"__main__\"</b>: este bloco só roda quando você "
          "executa o arquivo diretamente (python extrator_ocr.py). Se outro arquivo "
          "importar este módulo, o bloco é ignorado. É o ponto de entrada para testes."),
        p("<b>json.dumps(..., indent=2, ensure_ascii=False)</b>: formata o dicionário "
          "como JSON bonito com indentação de 2 espaços. "
          "<code>ensure_ascii=False</code> garante que acentos sejam impressos "
          "corretamente em vez de códigos \\\\uXXXX."),
        PageBreak(),
    ]

    # ── 5. aplicacao_vendas.py ───────────────────────────────────────────────
    story += [
        h("5. Arquivo: aplicacao_vendas.py"),
        hr(),
        p("Este é o arquivo principal. Ele combina a extração OCR do arquivo anterior "
          "com a interface gráfica (tkinter) e a integração com o banco Firebird."),
        sp(),
    ]

    # 5.1
    story += [
        h("5.1 Importações e Configuração do Banco", 2),
        code(
"""import os
import re
import json
import fdb
import pytesseract
from pdf2image import convert_from_path
import tkinter as tk
from tkinter import filedialog, messagebox

DB_CONFIG = {
    'database': 'C:\\\\Users\\\\junin\\\\...\\\\CLIPP.FDB',
    'user': 'SYSDBA',
    'password': 'masterkey',
    'charset': 'WIN1252'
}"""),
        p("<b>fdb</b>: driver Python para o banco de dados Firebird."),
        p("<b>tkinter</b>: biblioteca padrão do Python para criar interfaces gráficas. "
          "Não precisa instalar — já vem com o Python."),
        p("<b>filedialog</b>: módulo do tkinter para abrir a janela 'Abrir Arquivo'."),
        p("<b>messagebox</b>: módulo do tkinter para exibir caixas de diálogo "
          "(erro, aviso, informação)."),
        p("<b>DB_CONFIG</b>: dicionário com as configurações de conexão. Usar um "
          "dicionário facilita passar tudo de uma vez com <code>**DB_CONFIG</code>. "
          "O <code>charset='WIN1252'</code> é essencial para que acentos do português "
          "sejam lidos corretamente do banco.", aviso),
        sp(),
    ]

    # 5.2
    story += [
        h("5.2 Classe VendasApp — __init__", 2),
        code(
"""class VendasApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Importador de Pedidos - Tiao Cards")
        self.root.geometry("500x300")

        self.label = tk.Label(root,
            text="Selecione o PDF do pedido para importar", pady=20)
        self.label.pack()

        self.btn_select = tk.Button(root,
            text="Selecionar PDF e Processar",
            command=self.processar_pedido,
            height=2, width=30)
        self.btn_select.pack(pady=10)

        self.status = tk.Label(root, text="Aguardando...", fg="blue")
        self.status.pack(pady=20)"""),
        p("<b>__init__(self, root)</b>: construtor da classe. É chamado uma vez "
          "quando criamos <code>VendasApp(root)</code>. O parâmetro "
          "<code>root</code> é a janela principal do tkinter."),
        p("<b>self.root.geometry(\"500x300\")</b>: define o tamanho inicial da "
          "janela em pixels (largura x altura)."),
        p("<b>tk.Label</b>: widget de texto estático (não editável pelo usuário)."),
        p("<b>tk.Button(..., command=self.processar_pedido)</b>: o parâmetro "
          "<code>command</code> define qual função será chamada ao clicar. "
          "Note que passa <code>self.processar_pedido</code> sem parênteses — "
          "estamos passando a referência da função, não chamando ela."),
        p("<b>.pack()</b>: posiciona o widget na janela de cima para baixo. "
          "É o gerenciador de layout mais simples do tkinter."),
        p("<b>self.status</b>: label especial que será atualizado durante o "
          "processamento para informar o progresso ao usuário.", destaque),
        sp(),
    ]

    # 5.3
    story += [
        h("5.3 Método conectar_banco", 2),
        code(
"""    def conectar_banco(self):
        try:
            return fdb.connect(**DB_CONFIG)
        except Exception as e:
            messagebox.showerror("Erro de Conexão",
                f"Não foi possível conectar ao Firebird:\\n{e}")
            return None"""),
        p("<b>fdb.connect(**DB_CONFIG)</b>: o operador <code>**</code> desempacota "
          "o dicionário como argumentos nomeados. Equivale a escrever: "
          "<code>fdb.connect(database='...', user='SYSDBA', ...)</code>"),
        p("<b>return None</b>: se a conexão falhar, retorna None. O método "
          "<code>processar_pedido</code> verifica isso com <code>if not con: return</code>."),
        sp(),
    ]

    # 5.4
    story += [
        h("5.4 Método extrair_dados_pdf", 2),
        code(
"""    def extrair_dados_pdf(self, caminho_pdf):
        self.status.config(text="Lendo PDF (OCR)...", fg="orange")
        self.root.update()

        paginas = convert_from_path(caminho_pdf)
        texto_completo = ""
        for pagina in paginas:
            texto_completo += pytesseract.image_to_string(
                pagina, lang='por') + "\\n"

        dados = {'cliente': {}, 'itens': [], 'resumo': {}}
        linhas = [l.strip() for l in texto_completo.split('\\n') if l.strip()]
        # ... (mesma lógica do extrator_ocr.py)
        return dados"""),
        p("<b>self.status.config(text=..., fg=\"orange\")</b>: atualiza o texto e "
          "a cor do label de status. <code>fg</code> é a cor da fonte "
          "(foreground = frente)."),
        p("<b>self.root.update()</b>: força o tkinter a redesenhar a janela "
          "<i>imediatamente</i>. Sem isso, a interface 'trava' durante o OCR "
          "(que pode demorar alguns segundos) e o usuário não vê a mensagem "
          "'Lendo PDF...'.", aviso),
        p("A lógica de extração é idêntica ao <code>extrator_ocr.py</code> — "
          "o código foi duplicado aqui para que o arquivo seja autocontido."),
        sp(),
    ]

    # 5.5
    story += [
        h("5.5 Método processar_pedido — Cliente", 2),
        code(
"""    def processar_pedido(self):
        caminho_pdf = filedialog.askopenfilename(
            filetypes=[("Arquivos PDF", "*.pdf")])
        if not caminho_pdf: return

        dados = self.extrair_dados_pdf(caminho_pdf)
        if not dados['itens']:
            messagebox.showwarning("Aviso",
                "Nenhum item encontrado no PDF.")
            return

        con = self.conectar_banco()
        if not con: return

        try:
            cur = con.cursor()
            doc = dados['cliente'].get('documento')
            id_cliente = None

            # Busca em PF (pessoa física)
            cur.execute(
                "SELECT ID_CLIENTE FROM TB_CLI_PF WHERE CPF = ?", (doc,))
            row = cur.fetchone()
            if row:
                id_cliente = row[0]
            else:
                # Busca em PJ (pessoa jurídica)
                cur.execute(
                    "SELECT ID_CLIENTE FROM TB_CLI_PJ WHERE CNPJ = ?", (doc,))
                row = cur.fetchone()
                if row: id_cliente = row[0]

            if not id_cliente:
                cur.execute(
                    "INSERT INTO TB_CLIENTE (NOME, ID_PAIS) "
                    "VALUES (?, '0105') RETURNING ID_CLIENTE",
                    (dados['cliente']['nome'],))
                id_cliente = cur.fetchone()[0]
                if len(doc) <= 11:
                    cur.execute(
                        "INSERT INTO TB_CLI_PF (ID_CLIENTE, CPF) VALUES (?, ?)",
                        (id_cliente, doc))
                else:
                    cur.execute(
                        "INSERT INTO TB_CLI_PJ (ID_CLIENTE, CNPJ) VALUES (?, ?)",
                        (id_cliente, doc))"""),
        p("<b>filedialog.askopenfilename(filetypes=[...])</b>: abre a janela nativa "
          "do sistema operacional para escolher um arquivo. O <code>filetypes</code> "
          "filtra para mostrar só PDFs."),
        p("<b>cur = con.cursor()</b>: cria um cursor, que é o objeto usado para "
          "executar comandos SQL."),
        p("<b>cur.execute(\"SELECT ... WHERE CPF = ?\", (doc,))</b>: o "
          "<code>?</code> é um placeholder — o fdb substitui pelo valor de "
          "<code>(doc,)</code> de forma segura, prevenindo SQL injection. "
          "Note a vírgula: <code>(doc,)</code> cria uma tupla com um elemento; "
          "<code>(doc)</code> seria só parênteses."),
        p("<b>RETURNING ID_CLIENTE</b>: cláusula Firebird que retorna o valor "
          "gerado pelo banco logo após o INSERT, sem precisar fazer um SELECT adicional."),
        p("<b>len(doc) <= 11</b>: CPF tem 11 dígitos, CNPJ tem 14. Isso determina "
          "em qual tabela salvar o documento do cliente.", destaque),
        sp(),
    ]

    # 5.6
    story += [
        h("5.6 Método processar_pedido — Inserção da Venda", 2),
        code(
"""            cur.execute(\"\"\"
                INSERT INTO TB_NFVENDA_2
                    (ID_CLIENTE, DT_EMISSAO, TOTALNOTA, TOTALPRODUTOS,
                     ID_NATOPE, ID_FMAPGTO, ID_PARCELA, STATUS,
                     ENT_SAI, NF_NUMERO, NF_SERIE, NF_MODELO)
                VALUES
                    (?, CURRENT_DATE, ?, ?, 1, 1, 1, 'A', 'S', 0, '1', '01')
                RETURNING ID_NFVENDA
            \"\"\", (id_cliente,
                   dados['resumo'].get('total', 0),
                   dados['resumo'].get('total', 0)))
            id_venda = cur.fetchone()[0]"""),
        p("<b>\"\"\"...(SQL)...\"\"\"</b>: string multilinha (triple-quoted). "
          "Permite escrever o SQL em múltiplas linhas para melhor leitura."),
        p("<b>CURRENT_DATE</b>: função do Firebird que insere a data de hoje "
          "automaticamente — sem precisar passar no Python."),
        p("<b>STATUS = 'A'</b>: provavelmente 'Aberto' ou 'Ativo', conforme "
          "convenção do sistema CLIPP."),
        p("<b>ENT_SAI = 'S'</b>: indica Saída (venda), diferente de 'E' (Entrada)."),
        p("<b>.get('total', 0)</b>: acessa o dicionário com valor padrão 0 se a chave "
          "não existir — mais seguro que <code>dados['resumo']['total']</code>, que "
          "lançaria KeyError se o OCR não encontrou o total.", aviso),
        sp(),
    ]

    # 5.7
    story += [
        h("5.7 Método processar_pedido — Inserção dos Itens", 2),
        code(
"""            for item in dados['itens']:
                cur.execute(
                    "SELECT ID_IDENTIFICADOR FROM TB_EST_PRODUTO_2 "
                    "WHERE REFERENCIA = ?",
                    (item['referencia'],))
                prod_row = cur.fetchone()

                if prod_row:
                    id_prod = prod_row[0]
                    cur.execute(\"\"\"
                        INSERT INTO TB_NFV_ITEM_2
                            (ID_NFVENDA, ID_IDENTIFICADOR, QTD_ITEM,
                             VLR_UNIT, VLR_TOTAL, CFOP, NUM_ITEM,
                             VLR_FRETE, INCLUIR_FATURA)
                        VALUES (?, ?, ?, ?, ?, '5102', 1, 0, 'S')
                    \"\"\", (id_venda, id_prod,
                            item['quantidade'],
                            item['preco_unitario'],
                            item['quantidade'] * item['preco_unitario']))
                else:
                    print(f"Produto não encontrado: {item['referencia']}")

            con.commit()
            self.status.config(text="Pedido importado com sucesso!", fg="green")
            messagebox.showinfo("Sucesso",
                f"Pedido importado com ID: {id_venda}")

        except Exception as e:
            con.rollback()
            messagebox.showerror("Erro", f"Erro no banco:\\n{e}")
        finally:
            con.close()"""),
        p("<b>for item in dados['itens']</b>: itera sobre a lista de itens extraídos "
          "do PDF. Para cada produto, primeiro busca o ID no cadastro."),
        p("<b>SELECT ID_IDENTIFICADOR</b>: busca o produto pela referência lida no "
          "PDF. Se não encontrar, registra no terminal mas não interrompe o processo."),
        p("<b>CFOP = '5102'</b>: código fiscal de operação para venda de "
          "mercadoria adquirida ou recebida de terceiros — padrão no Brasil "
          "para vendas dentro do estado."),
        p("<b>item['quantidade'] * item['preco_unitario']</b>: calcula o total "
          "do item diretamente no Python antes de inserir."),
        p("<b>con.commit()</b>: só após inserir TODOS os itens com sucesso. "
          "Isso garante que, se um item der erro, nada é salvo parcialmente — "
          "ou entra tudo ou não entra nada (atomicidade).", destaque),
        PageBreak(),
    ]

    # ── 6. Fluxo Completo ────────────────────────────────────────────────────
    story += [
        h("6. Fluxo Completo (passo a passo)"),
        hr(),
        p("Veja o que acontece desde o clique do usuário até a confirmação:"),
        sp(),
        code(
"""Usuário clica em "Selecionar PDF e Processar"
    │
    ▼
filedialog.askopenfilename()  →  Janela nativa do Windows para escolher o PDF
    │
    ▼
extrair_dados_pdf(caminho_pdf)
    ├── convert_from_path(pdf)     → lista de imagens PIL
    ├── pytesseract.image_to_string(img, lang='por')  → texto bruto
    └── regex sobre texto          → dict{cliente, itens, resumo}
    │
    ▼
conectar_banco()
    └── fdb.connect(**DB_CONFIG)   → objeto de conexão
    │
    ▼
Buscar ou Criar Cliente
    ├── SELECT em TB_CLI_PF        (CPF)
    ├── SELECT em TB_CLI_PJ        (CNPJ)
    └── INSERT TB_CLIENTE + PF/PJ  (se não existir)
    │
    ▼
INSERT TB_NFVENDA_2               → id_venda = novo ID gerado
    │
    ▼
Para cada item:
    ├── SELECT TB_EST_PRODUTO_2    (busca ID pelo código de referência)
    └── INSERT TB_NFV_ITEM_2       (quantidade, preço unitário, total)
    │
    ▼
con.commit()                      → tudo salvo no banco
    │
    ▼
messagebox.showinfo("Sucesso")    → exibe ID do pedido gerado"""),
        PageBreak(),
    ]

    # ── 7. Pontos de Atenção ─────────────────────────────────────────────────
    story += [
        h("7. Pontos de Atenção e Melhorias Sugeridas"),
        hr(),
        h("Pontos que precisam de atenção", 2),
        p("1. <b>Senha hardcoded</b>: a senha <code>masterkey</code> está diretamente "
          "no código. Em produção, use variáveis de ambiente ou um arquivo de "
          "configuração não versionado.", aviso),
        p("2. <b>Campos fixos no INSERT de venda</b>: <code>ID_NATOPE=1</code>, "
          "<code>ID_FMAPGTO=1</code>, <code>ID_PARCELA=1</code> são valores "
          "hardcoded — certifique-se que esses IDs existem no seu banco."),
        p("3. <b>self.root.update() bloqueia</b>: o OCR roda na thread principal. "
          "Para PDFs grandes, a janela pode não responder durante o processamento. "
          "A solução avançada seria usar threading."),
        p("4. <b>referencia_original não existe no dict do aplicacao_vendas.py</b>: "
          "o check de duplicata usa <code>item['referencia_original']</code> mas "
          "o dict criado não tem essa chave — pode gerar um KeyError.", aviso),
        h("Melhorias sugeridas", 2),
        p("• Adicionar uma barra de progresso (ttk.Progressbar) durante o OCR.", bullet),
        p("• Exibir os itens encontrados numa tabela antes de confirmar o import.", bullet),
        p("• Mover as credenciais do banco para um arquivo <code>config.ini</code>.", bullet),
        p("• Implementar log de erros em arquivo para facilitar debugging.", bullet),
        p("• Usar threading.Thread para não bloquear a interface durante o OCR.", bullet),
        sp(2),
        hr(),
        p("<b>Fim do documento.</b>  Bons estudos!", destaque),
    ]

    return story

# ---------------------------------------------------------------------------
# Geração do PDF
# ---------------------------------------------------------------------------

def main():
    doc = SimpleDocTemplate(
        OUTPUT,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="Estudo do Projeto AppPedidos Phy",
        author="AppPedidos Phy",
    )
    story = build_content()
    doc.build(story)
    print(f"PDF gerado: {OUTPUT}")

if __name__ == "__main__":
    main()
