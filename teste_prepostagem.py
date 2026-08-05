"""Teste manual da criação de pré-postagem nos Correios.

Uso:
    py teste_prepostagem.py

Usa o remetente do config.ini [correios] e um destinatário fictício.
NÃO posta de fato (pré-postagem é só o registro prévio), mas consome o
contrato real — cancele depois se quiser (cancelar_prepostagem(id)).

Cole aqui o retorno (sucesso OU o erro) para ajustarmos o corpo do JSON.
"""

from __future__ import annotations

import json

from correios_api import SERVICOS, CorreiosClient, CorreiosError

# Destinatário fictício (troque por uma nota real quando quiser testar de verdade).
DESTINATARIO = {
    "nome": "Cliente Teste",
    "cpfCnpj": "11144477735",  # CPF de teste (válido em dígito verificador)
    "cep": "01001000",          # Praça da Sé, São Paulo/SP
    "logradouro": "Praça da Sé",
    "numero": "100",
    "complemento": "lado ímpar",
    "bairro": "Sé",
    "cidade": "São Paulo",
    "uf": "SP",
    "celular": "11999990000",
    "email": "cliente@example.com",
}

# Objeto típico (peso em gramas, dimensões em cm). Mini Envios como exemplo.
PARAMS = {
    "codigo_servico": SERVICOS["PAC"],
    "peso_g": 300,
    "formato": "2",          # 1=envelope, 2=pacote/caixa, 3=rolo
    "altura_cm": 5,
    "largura_cm": 15,
    "comprimento_cm": 20,
    "observacao": "Pedido site - teste integração",
    "nota_fiscal": None,
}


def main() -> int:
    cli = CorreiosClient()
    print(f"Ambiente: {cli.ambiente} | base: {cli.base_url}")

    # Mostra o corpo exato que será enviado (sem chamar a API ainda).
    body_preview = {
        "remetente": cli._remetente_payload(),
        "destinatario": cli.montar_destinatario(DESTINATARIO),
        **{
            "codigoServico": PARAMS["codigo_servico"],
            "pesoInformado": str(PARAMS["peso_g"]),
            "codigoFormatoObjetoInformado": PARAMS["formato"],
        },
    }
    print("\n--- Corpo (prévia) ---")
    print(json.dumps(body_preview, ensure_ascii=False, indent=2))

    resp = input("\nEnviar para os Correios agora? (s/N) ").strip().lower()
    if resp != "s":
        print("Cancelado (nada foi enviado).")
        return 0

    try:
        resultado = cli.criar_prepostagem(destinatario=DESTINATARIO, **PARAMS)
    except CorreiosError as erro:
        print("\n=== ERRO DA API ===")
        print(f"status: {erro.status}")
        print(f"mensagem: {erro}")
        print(f"corpo: {erro.corpo}")
        return 1

    print("\n=== SUCESSO ===")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
