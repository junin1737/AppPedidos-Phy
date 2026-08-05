"""Geração local da etiqueta Correios + identificação fiscal da NF-e.

O bloco postal segue o Guia de Endereçamento de Encomendas e reproduz o
DataMatrix observado no rótulo oficial atual da PPN. O bloco inferior reúne a
chave, número e protocolo da NF-e para validação piloto na agência.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import zxingcpp
from PIL import Image
from reportlab.graphics.barcode import code128
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


LARGURA = 100 * mm
ALTURA = 150 * mm


def _digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _fixo_num(valor, tamanho: int) -> str:
    n = _digitos(valor)
    return n[-tamanho:].zfill(tamanho)


def _limitar(texto, tamanho: int) -> str:
    return str(texto or "").strip()[:tamanho]


def _validador_cep(cep: str) -> str:
    soma = sum(int(c) for c in _fixo_num(cep, 8))
    return str((10 - (soma % 10)) % 10)


def conteudo_datamatrix(item: dict) -> str:
    """Monta os 147 caracteres usados atualmente pela PPN.

    O formato foi conferido decodificando um rótulo oficial criado no portal:
    CEP/número destino, CEP/número origem, validador, IDV 59, rastreio,
    id da pré-postagem, adicionais, cartão, serviço, número/complemento e
    reserva terminada por ``|``.
    """
    dest = item.get("destinatario") or {}
    rem = item.get("remetente") or {}
    end_dest = dest.get("endereco") or {}
    end_rem = rem.get("endereco") or {}
    adicionais = item.get("listaServicoAdicional") or []
    cod_adicionais = "".join(
        _fixo_num(a.get("codigoServicoAdicional"), 3)
        for a in adicionais
        if isinstance(a, dict) and a.get("codigoServicoAdicional")
    )[:18].ljust(18)
    complemento = _limitar(end_dest.get("complemento"), 20).ljust(20)
    resultado = (
        _fixo_num(end_dest.get("cep"), 8)
        + _fixo_num(end_dest.get("numero"), 5)
        + _fixo_num(end_rem.get("cep"), 8)
        + _fixo_num(end_rem.get("numero"), 5)
        + _validador_cep(end_dest.get("cep"))
        + "59"
        + _limitar(item.get("codigoObjeto"), 13).ljust(13)
        + _limitar(item.get("id") or item.get("idPrePostagem"), 24).ljust(24)
        + cod_adicionais
        + _fixo_num(item.get("numeroCartaoPostagem"), 10)
        + _fixo_num(item.get("codigoServico"), 5)
        + _limitar(end_dest.get("numero"), 5).ljust(5)
        + complemento
        + (" " * 22)
        + "|"
    )
    if len(resultado) != 147:
        raise ValueError(
            f"Conteúdo DataMatrix inválido: esperado 147, obtido {len(resultado)}."
        )
    return resultado


def _imagem_datamatrix(texto: str) -> Image.Image:
    codigo = zxingcpp.create_barcode(
        texto, zxingcpp.BarcodeFormat.DataMatrix, forceSquare=True
    )
    bitmap = zxingcpp.write_barcode_to_image(
        codigo, scale=5, add_hrt=False, add_quiet_zones=True
    )
    return Image.frombuffer(
        "L",
        (bitmap.shape[1], bitmap.shape[0]),
        bitmap,
        "raw",
        "L",
        0,
        1,
    ).copy()


CORES_SERVICO = {
    "MINI": (0.33, 0.75, 0.29),
    "SEDEX": (0.98, 0.70, 0.09),
    "PAC": (0.02, 0.46, 0.76),
}


def _cor_servico(servico: str) -> tuple[float, float, float]:
    """Cor do símbolo por serviço: Mini Envios verde, SEDEX amarelo, PAC azul."""
    nome = str(servico or "").upper()
    for chave, cor in CORES_SERVICO.items():
        if chave in nome:
            return cor
    return CORES_SERVICO["PAC"]


def _simbolo_servico(c: canvas.Canvas, cx, cy, raio, cor) -> None:
    """Desenha o símbolo dos Correios (domo com recorte + domo menor)."""
    c.setFillColorRGB(*cor)
    c.rect(cx - raio, cy - raio * 0.35, raio * 2, raio * 0.35, fill=1, stroke=0)
    c.wedge(cx - raio, cy - raio, cx + raio, cy + raio, 0, 180, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.wedge(
        cx - raio * 0.86,
        cy - raio * 1.15,
        cx + raio * 0.86,
        cy - raio * 0.15,
        0,
        180,
        fill=1,
        stroke=0,
    )
    c.setFillColorRGB(*cor)
    c.wedge(
        cx - raio * 0.42,
        cy - raio * 1.32,
        cx + raio * 0.42,
        cy - raio * 0.48,
        0,
        180,
        fill=1,
        stroke=0,
    )
    c.setFillColorRGB(0, 0, 0)


def _texto(c: canvas.Canvas, x, y, texto, tamanho=7, negrito=False, largura=None) -> None:
    fonte = "Helvetica-Bold" if negrito else "Helvetica"
    valor = str(texto or "")
    if largura:
        valor = _cortar_largura(c, valor, fonte, tamanho, largura)
    c.setFont(fonte, tamanho)
    c.drawString(x, y, valor)


def _paragrafo(
    c: canvas.Canvas,
    x,
    y,
    texto,
    largura,
    tamanho=7,
    negrito=False,
    max_linhas=2,
    entrelinha=None,
) -> float:
    """Quebra o texto em até ``max_linhas`` e devolve o y da próxima linha livre."""
    fonte = "Helvetica-Bold" if negrito else "Helvetica"
    entrelinha = entrelinha or (tamanho * 1.35)
    palavras = str(texto or "").split()
    linhas: list[str] = []
    atual = ""
    for palavra in palavras:
        teste = f"{atual} {palavra}".strip()
        if atual and c.stringWidth(teste, fonte, tamanho) > largura:
            linhas.append(atual)
            atual = palavra
            if len(linhas) == max_linhas:
                break
        else:
            atual = teste
    if atual and len(linhas) < max_linhas:
        linhas.append(atual)
    if not linhas:
        return y
    if len(linhas) == max_linhas and atual not in linhas[-1:]:
        linhas[-1] = _cortar_largura(c, f"{linhas[-1]} {atual}", fonte, tamanho, largura)
    c.setFont(fonte, tamanho)
    for linha in linhas:
        c.drawString(x, y, linha)
        y -= entrelinha
    return y


def _cortar_largura(c: canvas.Canvas, texto: str, fonte: str, tamanho, largura) -> str:
    """Trunca o texto para caber na largura, evitando invadir códigos de barras."""
    if c.stringWidth(texto, fonte, tamanho) <= largura:
        return texto
    while texto and c.stringWidth(texto + "…", fonte, tamanho) > largura:
        texto = texto[:-1]
    return texto + "…"


def _linha_endereco(end: dict) -> str:
    logr = _limitar(end.get("logradouro"), 46)
    numero = _limitar(end.get("numero"), 8)
    comp = _limitar(end.get("complemento"), 20)
    return ", ".join(x for x in (logr, numero, comp) if x)


def _referencia_pedido(item: dict, fiscal: dict) -> str:
    """Identificação do pedido do site, como o rótulo do portal imprime.

    Usa a observação enviada na pré-postagem; sem ela, remonta a partir do
    número do pedido e da forma de pagamento gravados na observação da venda.
    """
    obs = _limitar(item.get("observacao"), 60)
    if obs:
        return obs
    numero = str(fiscal.get("numero_pedido") or "").strip().lstrip("#")
    if not numero:
        return ""
    pagamento = _limitar(fiscal.get("pagamento"), 20)
    return f"Pedido #{numero}" + (f" · {pagamento}" if pagamento else "")


def _valor_reais(valor) -> str:
    try:
        numero = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return ""
    if numero <= 0:
        return ""
    return f"{numero:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _rastreio_formatado(codigo: str) -> str:
    cod = re.sub(r"\s+", "", str(codigo or "").upper())
    return f"{cod[:2]} {cod[2:5]} {cod[5:8]} {cod[8:11]} {cod[11:]}"


def _barcode(
    c: canvas.Canvas,
    valor: str,
    x,
    y,
    largura_max,
    altura,
    *,
    bar_width=0.28 * mm,
) -> None:
    bc = code128.Code128(str(valor), barWidth=bar_width, barHeight=altura)
    escala = min(1.0, largura_max / bc.width)
    c.saveState()
    c.translate(x, y)
    c.scale(escala, 1)
    bc.drawOn(c, 0, 0)
    c.restoreState()


def _desenhar_pagina(
    c: canvas.Canvas,
    item: dict,
    *,
    contrato: str = "",
    fiscal: dict | None = None,
) -> None:
    """Desenha uma etiqueta 100 × 150 mm na página atual do canvas."""
    fiscal = fiscal or {}
    chave = _digitos(item.get("chaveNFe") or fiscal.get("chave"))
    rastreio = _limitar(item.get("codigoObjeto"), 13).upper()
    if len(chave) != 44:
        raise ValueError("A pré-postagem não possui uma chave NF-e válida de 44 dígitos.")
    if len(rastreio) != 13:
        raise ValueError("A pré-postagem não possui código de rastreio válido.")
    esq = 4 * mm
    dir_ = 96 * mm
    util = dir_ - esq
    nota = item.get("numeroNotaFiscal") or fiscal.get("numero") or ""

    # Cabeçalho: marca, contrato, serviço e DataMatrix.
    servico = _limitar(item.get("servico"), 30)
    cor = _cor_servico(servico)
    c.setFillColorRGB(0.12, 0.22, 0.34)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(esq, 142 * mm, "Correios")
    c.setFillColorRGB(0, 0, 0)
    _texto(c, esq, 137 * mm, f"Contrato: {contrato}", 6.5, largura=33 * mm)
    y_serv = _paragrafo(c, esq, 132.5 * mm, servico, 33 * mm, 8.5, True, entrelinha=3.6 * mm)
    siglas = " ".join(
        _limitar(a.get("siglaServicoAdicional"), 3)
        for a in (item.get("listaServicoAdicional") or [])
        if isinstance(a, dict)
    )
    _texto(c, esq, y_serv - 0.6 * mm, siglas, 7, True, largura=33 * mm)

    dm = _imagem_datamatrix(conteudo_datamatrix(item))
    c.drawImage(
        ImageReader(dm),
        39 * mm,
        124.5 * mm,
        24 * mm,
        24 * mm,
        preserveAspectRatio=True,
        mask="auto",
    )
    _simbolo_servico(c, 85 * mm, 137 * mm, 7 * mm, cor)

    # Rastreio em barra larga.
    _barcode(c, rastreio, esq, 110 * mm, util, 12.5 * mm, bar_width=0.42 * mm)
    _texto(c, esq, 105 * mm, _rastreio_formatado(rastreio), 12, True)
    _texto(c, 70 * mm, 105 * mm, f"NF: {nota}", 8, True)
    c.setLineWidth(0.5)
    c.line(esq, 102.5 * mm, dir_, 102.5 * mm)

    # Recebedor / assinatura.
    _texto(c, esq, 98.5 * mm, "Recebedor:", 6.5)
    c.line(19 * mm, 98 * mm, dir_, 98 * mm)
    _texto(c, esq, 93 * mm, "Assinatura:", 6.5)
    c.line(19 * mm, 92.5 * mm, 52 * mm, 92.5 * mm)
    _texto(c, 55 * mm, 93 * mm, "Documento:", 6.5)
    c.line(72 * mm, 92.5 * mm, dir_, 92.5 * mm)

    # Destinatário.
    dest = item.get("destinatario") or {}
    end_dest = dest.get("endereco") or {}
    cep_dest = _fixo_num(end_dest.get("cep"), 8)
    c.rect(esq, 84.5 * mm, 27 * mm, 4.6 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    _texto(c, esq + 1.5 * mm, 86 * mm, "DESTINATÁRIO", 7, True)
    c.setFillColorRGB(0, 0, 0)
    col = 62 * mm
    _paragrafo(c, esq, 80.5 * mm, dest.get("nome"), col, 8.5, True, max_linhas=1)
    _paragrafo(c, esq, 76.8 * mm, _linha_endereco(end_dest), col, 7.5, entrelinha=3.4 * mm)
    _paragrafo(c, esq, 69.8 * mm, end_dest.get("bairro"), col, 7.5, max_linhas=1)
    _texto(
        c,
        esq,
        65.8 * mm,
        f"{cep_dest[:5]}-{cep_dest[5:]}  "
        f"{_limitar(end_dest.get('cidade'), 32)}/{_limitar(end_dest.get('uf'), 2)}",
        9.5,
        True,
        largura=util,
    )
    _barcode(c, cep_dest, 70 * mm, 72 * mm, 26 * mm, 9.5 * mm, bar_width=0.30 * mm)
    referencia = _referencia_pedido(item, fiscal)
    if referencia:
        _texto(c, esq, 62 * mm, referencia, 7.5, True, largura=util)

    # Remetente.
    c.setDash(1, 2)
    c.line(esq, 59.5 * mm, dir_, 59.5 * mm)
    c.setDash()
    rem = item.get("remetente") or {}
    end_rem = rem.get("endereco") or {}
    cep_rem = _fixo_num(end_rem.get("cep"), 8)
    _texto(c, esq, 56 * mm, f"REMETENTE: {_limitar(rem.get('nome'), 44)}", 7, True, largura=util)
    _texto(c, esq, 52.5 * mm, _linha_endereco(end_rem), 6.5, largura=util)
    _texto(
        c,
        esq,
        49 * mm,
        f"{_limitar(end_rem.get('bairro'), 28)} · {cep_rem[:5]}-{cep_rem[5:]} "
        f"{_limitar(end_rem.get('cidade'), 28)}/{_limitar(end_rem.get('uf'), 2)}",
        6.5,
        largura=util,
    )

    # Bloco fiscal.
    c.setLineWidth(1)
    c.line(esq, 46 * mm, dir_, 46 * mm)
    _texto(c, esq, 42 * mm, "NF-e · DANFE SIMPLIFICADO", 8, True)
    _texto(c, 58 * mm, 42 * mm, f"Nº {nota}", 9, True)
    serie = fiscal.get("serie") or ""
    if serie:
        _texto(c, 78 * mm, 42 * mm, f"Série {serie}", 7)
    emissao = fiscal.get("emissao") or ""
    valor = _valor_reais(fiscal.get("valor") or fiscal.get("total"))
    if emissao:
        _texto(c, esq, 38 * mm, f"Emissão: {emissao}", 6.5)
    if valor:
        _texto(c, 40 * mm, 38 * mm, f"Valor total: R$ {valor}", 6.5)
    protocolo = fiscal.get("protocolo") or ""
    if protocolo:
        _texto(c, esq, 34.3 * mm, f"Protocolo de autorização: {protocolo}", 6.5, largura=util)
    _texto(c, esq, 31.5 * mm, "CHAVE DE ACESSO", 6.5, True)
    chave_fmt = " ".join(chave[i:i + 4] for i in range(0, len(chave), 4))
    _texto(c, esq, 27.5 * mm, chave_fmt, 7, largura=util)
    _barcode(c, chave, esq, 11.5 * mm, util, 14.5 * mm, bar_width=0.28 * mm)
    _texto(
        c,
        esq,
        7.5 * mm,
        "Consulta de autenticidade em www.nfe.fazenda.gov.br/portal",
        5.5,
    )
    _texto(c, esq, 4.5 * mm, "Documento auxiliar — não substitui o DANFE completo.", 5.5)


def gerar_pdf(
    item: dict,
    caminho: str | Path,
    *,
    contrato: str = "",
    fiscal: dict | None = None,
) -> str:
    """Gera o PDF de uma etiqueta e retorna o caminho."""
    return gerar_pdf_lote(
        [{"item": item, "contrato": contrato, "fiscal": fiscal}], caminho
    )


def gerar_pdf_lote(etiquetas: list[dict], caminho: str | Path) -> str:
    """Gera um PDF com uma etiqueta por página.

    Cada elemento de ``etiquetas`` é um dict com ``item`` (pré-postagem da API),
    ``contrato`` e ``fiscal`` (dados da NF-e).
    """
    if not etiquetas:
        raise ValueError("Nenhuma etiqueta informada para gerar o PDF.")
    caminho = str(Path(caminho))
    c = canvas.Canvas(caminho, pagesize=(LARGURA, ALTURA), pageCompression=1)
    for etq in etiquetas:
        _desenhar_pagina(
            c,
            etq["item"],
            contrato=etq.get("contrato") or "",
            fiscal=etq.get("fiscal"),
        )
        c.showPage()
    c.save()
    return caminho

