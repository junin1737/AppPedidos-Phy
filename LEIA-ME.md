# Importador de Pedidos — PDF → CLIPP (Firebird)

Aplicação desktop em Python para importar um ou mais pedidos em PDF (LigaSegura / Tiao Cards) para `TB_NFVENDA_2` e `TB_NFV_ITEM_2`, com status **Pendente** para conferência manual.

## Pré-requisitos

1. **Python 3.10+**
2. **Tesseract OCR** (idioma português): https://github.com/UB-Mannheim/tesseract/wiki
3. **Poppler** (PDF → imagem): http://blog.alivate.com.au/poppler-windows/
4. Bibliotecas Python:

```bash
pip install -r requirements.txt
```

## Primeira execução

1. Execute `Rodar Aplicacao.bat` ou `python aplicacao_vendas.py`
2. Se o banco não estiver configurado, abrirá a tela de configuração
3. Informe o caminho do `.FDB`, usuário e senha — salvo em **`config.ini`**
4. Configure o caminho do **Tesseract** (e Poppler, se não estiver no PATH)
5. Se o Python for 64-bit e o Firebird do PC for 32-bit: copie o **fbclient.dll 64-bit** para uma pasta do projeto (ex.: `lib\firebird64\`) e informe o caminho em **fbclient.dll** — **não precisa instalar** o Firebird 64 no Windows

## Como usar

1. **Adicionar PDF(s)...** — selecione um ou vários pedidos
2. **Ler PDFs selecionados** — OCR + extração (cliente, itens, nº pedido, pagamento)
3. Confira o **preview** à direita (referências convertidas `[PT]` → `-PT`)
4. **Importar para o banco** — grava venda pendente e itens

## Regras na importação

| Campo | Valor |
|---|---|
| `FIM` | `Pendente` |
| `XX_ID_CAMP` | `2` |
| `ID_FMAPGTO` / `ID_PARCELA` | `1` (altere ao finalizar) |
| `OBS` | Nº pedido + forma de pagamento do PDF |
| `ID_NATOPE` | `0` |
| `TIPO_FRETE` | `0` |
| `ENDERECO_ENTREGA` | `S` |
| `STATUS` | `A` |

**Cliente:** busca por nome exato + telefone; se PDF não tiver telefone, usa CPF/CNPJ. Cadastro incompleto é completado quando possível.

**Cartas:** referência do PDF (`BLZD-EN003`) + idioma (`[PT]`) → busca `BLZD-PT003` no estoque.

## Arquivos

| Arquivo | Função |
|---|---|
| `aplicacao_vendas.py` | Interface principal |
| `parser_pedido.py` | OCR e parser do PDF |
| `db.py` | Cliente, venda e itens no Firebird |
| `config.py` / `config.ini` | Configuração persistente |
| `extrator_ocr.py` | Teste de leitura via terminal |

## Teste rápido no terminal

```bash
python extrator_ocr.py "C:\caminho\pedido.pdf"
```
