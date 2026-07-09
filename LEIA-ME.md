# AppPedidos CLIPP

Integração de pedidos do **Tiao Cards / LigaSegura** com o ERP **CLIPP** (Firebird), incluindo servidor na bandeja, extensão Chrome e postagens **Correios**.

> Documentação completa de todos os módulos e blocos: **[DOCUMENTACAO.md](DOCUMENTACAO.md)**  
> Comentários de bloco no código: procure por `# ---------------------------------------------------------------------------` nos `.py`.

---

## O que o projeto faz

| Módulo | Descrição |
|--------|-----------|
| **Servidor (bandeja)** | `servidor_app.py` — HTTP local, importação via extensão, abas Correios |
| **Extensão Chrome** | Importa o pedido aberto no painel (sem RPA separado) |
| **Importador PDF** | `aplicacao_vendas.py` — OCR de PDFs (fluxo legado) |
| **Postagens Correios** | Fila de etiquetas, geração de rótulo, rastreio |
| **Financeiro** | Valores tarifados do contrato por mês |

---

## Início rápido (produção)

1. Execute **`AppPedidos CLIPP.bat`** (ou instale via `instalador/` — ver `LEIA-ME-INSTALACAO.md`).
2. Configure **`config.ini`** (banco Firebird + seção `[correios]` se usar etiquetas).
3. Instale a extensão: `chrome://extensions` → pasta `extensao_chrome` (ver `LEIA-ME-EXTENSAO.md`).
4. Abra o pedido no site → clique na extensão → **Importar esta página**.
5. Confira a venda **Pendente** no gerencial CLIPP e finalize.

---

## Início rápido (importador PDF)

1. Python 3.10+, Tesseract, Poppler — `pip install -r requirements.txt`
2. `python aplicacao_vendas.py`
3. Adicione PDFs → **Ler PDFs** → **Importar para o banco**

---

## Arquivos principais

| Arquivo | Função |
|---------|--------|
| `servidor_app.py` | Aplicação na bandeja + abas |
| `importar_servidor.py` | API HTTP para a extensão |
| `importar_core.py` | Orquestra importação → Firebird |
| `rpa_tiaocards.py` | Parser HTML do painel |
| `parser_pedido.py` | OCR e parser de PDF |
| `db.py` | Acesso ao Firebird |
| `correios_api.py` | API Correios |
| `tela_postagens.py` / `tela_financeiro.py` | Abas Correios |
| `schema_app.py` | Migração automática do banco |
| `config.py` | `config.ini` |

---

## Regras na importação

| Campo | Valor |
|-------|-------|
| `FIM` | `Pendente` |
| `XX_ID_CAMP` | `2` |
| `ID_FMAPGTO` / `ID_PARCELA` | `1` |
| `OBS` | Nº pedido + forma de pagamento |

Detalhes: [DOCUMENTACAO.md §12](DOCUMENTACAO.md#12-regras-de-negócio-na-importação) e `.cursor/rules/importacao-pedidos.mdc`.

---

## Atualizar em produção

Gere `instalador\dist\AppPedidosCLIPP-Update.zip` com `instalador\gerar_atualizacao.ps1`, extraia na máquina e execute `ATUALIZAR.bat`.
