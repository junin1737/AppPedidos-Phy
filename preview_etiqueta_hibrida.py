"""Preview local da etiqueta híbrida (uso interno de desenvolvimento)."""

import sys

import pymupdf

import etiqueta_hibrida as eh

ITEM = {
    "codigoObjeto": "AD761308797BR",
    "observacao": "Pedido Liga #19191919",
    "id": "6812fd1c9b7e4a0001d3aa11",
    "chaveNFe": "31260440918528000169550010000000501877959332",
    "servico": "SEDEX CONTRATO AG",
    "codigoServico": "03298",
    "numeroCartaoPostagem": "0079089874",
    "numeroNotaFiscal": "50",
    "listaServicoAdicional": [
        {"codigoServicoAdicional": "025", "siglaServicoAdicional": "AR"},
        {"codigoServicoAdicional": "019", "siglaServicoAdicional": "VD"},
    ],
    "destinatario": {
        "nome": "MARIA APARECIDA DE OLIVEIRA SOUZA",
        "endereco": {
            "logradouro": "RUA PROFESSOR ANTONIO ALEIXO DA SILVA",
            "numero": "1250",
            "complemento": "APTO 402 BLOCO B",
            "bairro": "SANTA EFIGENIA",
            "cidade": "BELO HORIZONTE",
            "uf": "MG",
            "cep": "30110000",
        },
    },
    "remetente": {
        "nome": "PHY GAMES COMERCIO DE CARTAS LTDA",
        "endereco": {
            "logradouro": "AVENIDA JOAO CESAR DE OLIVEIRA",
            "numero": "1420",
            "complemento": "SALA 08",
            "bairro": "ELDORADO",
            "cidade": "CONTAGEM",
            "uf": "MG",
            "cep": "32315000",
        },
    },
}

FISCAL = {
    "chave": ITEM["chaveNFe"],
    "numero": "50",
    "serie": "1",
    "emissao": "05/08/2026",
    "protocolo": "131260012345678",
    "valor": "349,90",
}

def gerar(saida: str, item: dict | None = None, fiscal: dict | None = None) -> str:
    pdf = eh.gerar_pdf(
        item or ITEM,
        f"{saida}.pdf",
        contrato="9912578757",
        fiscal=fiscal or FISCAL,
    )
    pymupdf.open(pdf)[0].get_pixmap(dpi=200).save(f"{saida}.png")
    return f"{saida}.png"


if __name__ == "__main__":
    print(gerar(sys.argv[1] if len(sys.argv) > 1 else "preview_etiqueta_hibrida"))
