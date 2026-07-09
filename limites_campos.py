"""Tamanhos máximos dos campos do CLIPP (Metadatas.txt / TB_CLIENTE).

Usado por parser_pedido, rpa_tiaocards e db ao truncar/normalizar dados
antes de INSERT/UPDATE — evita estouro de VARCHAR no Firebird.
"""

# TB_CLIENTE e endereço de entrega
NOME = 60
END_CEP = 9
END_TIPO = 15
END_LOGRAD = 40
END_NUMERO = 5
END_BAIRRO = 35
END_COMPLE = 29
DDD_CELUL = 2
FONE_CELUL = 13
OBSERVACAO = 1000
ID_PAIS = "1058"
ID_CIDADE = 7
