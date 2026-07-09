"""CLI para testar leitura de PDF sem abrir a interface (parser_pedido.extrair_pedido_pdf)."""

import json
import sys

from config import load_config
from parser_pedido import extrair_pedido_pdf


def pedido_para_dict(pedido):
    return {
        "arquivo": pedido.arquivo,
        "numero_pedido": pedido.numero_pedido,
        "data_pedido": pedido.data_pedido,
        "pagamento": pedido.pagamento,
        "envio": pedido.envio,
        "cliente": pedido.cliente,
        "itens": [
            {
                "quantidade": it.quantidade,
                "referencia_original": it.referencia_original,
                "referencia": it.referencia,
                "preco_unitario": it.preco_unitario,
                "preco_total": it.preco_total,
            }
            for it in pedido.itens
        ],
        "resumo": pedido.resumo,
        "erros": pedido.erros,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python extrator_ocr.py <caminho.pdf>")
        sys.exit(1)

    cfg = load_config()
    ocr = cfg["ocr"]
    pedido = extrair_pedido_pdf(
        sys.argv[1],
        tesseract_cmd=ocr.get("tesseract_cmd", ""),
        poppler_path=ocr.get("poppler_path", ""),
        lang=ocr.get("lang", "por"),
        on_progress=print,
    )
    print(json.dumps(pedido_para_dict(pedido), indent=2, ensure_ascii=False))
