/* ============================================================================
   Fila de etiquetas dos Correios
   ----------------------------------------------------------------------------
   Objetos:
     - GEN_XX_TB_ETIQUETA_CORREIO        generator do ID
     - XX_TB_ETQ_STATUS                  domínio de status (+ flag de bloqueio)
     - XX_TB_ETIQUETA_CORREIO            fila de etiquetas
     - EX_ETQ_BLOQUEIA_EXCLUSAO          exception da proteção de exclusão
     - XX_TB_ETIQUETA_CORREIO_BI         trigger de PK (BEFORE INSERT)
     - XX_TB_ETIQUETA_CORREIO_BD         proteção de exclusão (BEFORE DELETE)
     - TB_NFE_ETIQUETA_AIU              enfileira na autorização da NF-e
       (exceto TIPO_FRETE = '9' — sem recorrência de transporte)

   Observação: este script é a referência manual (isql). O servidor já cria e
   reconcilia tudo na conexão via schema_app.garantir_schema_etiqueta_correio
   (cria o que falta, altera o que mudou, e segue se estiver igual).

   Mapeamento confirmado no banco:
     - chave de acesso  = TB_NFE.ID_NFE        (VARCHAR 44)
     - status NF-e      = TB_NFE.STATUS        (VARCHAR 5; '100' = autorizada)
     - vínculo venda    = TB_NFE.ID_NFVENDA
     - modelo/numero/cliente = TB_NFVENDA (NF_MODELO, NF_NUMERO, NF_SERIE, ID_CLIENTE)
   ============================================================================ */

CREATE GENERATOR GEN_XX_TB_ETIQUETA_CORREIO;

CREATE TABLE XX_TB_ETQ_STATUS (
    CODIGO            VARCHAR(20) NOT NULL,
    DESCRICAO         VARCHAR(60) DEFAULT '' NOT NULL,
    BLOQUEIA_EXCLUSAO CHAR(1) DEFAULT 'N' NOT NULL,   -- 'S' = não pode excluir
    ORDEM             INTEGER DEFAULT 0 NOT NULL,
    CONSTRAINT PK_XX_TB_ETQ_STATUS PRIMARY KEY (CODIGO)
);

INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('PENDENTE',    'Pendente de impressao',  'N', 10);
INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('PROCESSANDO', 'Processando',            'N', 20);
INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('GERADA',      'Etiqueta gerada',        'N', 30);
INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('IMPRESSO',    'Impresso',               'S', 40);
INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('POSTADO',     'Postado nos Correios',   'S', 50);
INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('ERRO',        'Erro ao gerar',          'N', 60);
INSERT INTO XX_TB_ETQ_STATUS (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) VALUES ('CANCELADA',   'Cancelada',              'N', 70);

CREATE TABLE XX_TB_ETIQUETA_CORREIO (
    ID_ETIQUETA      INTEGER      NOT NULL,
    ID_NFVENDA       INTEGER      NOT NULL,
    ID_CLIENTE       INTEGER,
    CHAVE_ACESSO     VARCHAR(44),
    NF_NUMERO        INTEGER,
    NF_SERIE         VARCHAR(5),
    STATUS           VARCHAR(20)  DEFAULT 'PENDENTE' NOT NULL,
    TENTATIVAS       INTEGER      DEFAULT 0 NOT NULL,
    MENSAGEM_ERRO    VARCHAR(500),
    COD_SERVICO      VARCHAR(10),                               -- 03298 PAC / 03220 SEDEX
    ID_PREPOSTAGEM   VARCHAR(40),
    COD_RASTREIO     VARCHAR(40),
    ARQUIVO_ETIQUETA VARCHAR(260),
    PESO             DOUBLE PRECISION,                          -- definir unidade (gramas)
    ALTURA           DOUBLE PRECISION,
    LARGURA          DOUBLE PRECISION,
    COMPRIMENTO      DOUBLE PRECISION,
    DT_INCLUSAO      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL,
    DT_GERACAO       TIMESTAMP,
    DT_IMPRESSAO     TIMESTAMP,
    DT_ATUALIZACAO   TIMESTAMP,
    CONSTRAINT PK_XX_TB_ETIQUETA_CORREIO PRIMARY KEY (ID_ETIQUETA),
    CONSTRAINT UQ_XX_ETIQUETA_NFVENDA UNIQUE (ID_NFVENDA),
    CONSTRAINT FK_ETIQUETA_STATUS FOREIGN KEY (STATUS) REFERENCES XX_TB_ETQ_STATUS (CODIGO)
);

CREATE EXCEPTION EX_ETQ_BLOQUEIA_EXCLUSAO 'Etiqueta impressa/postada nao pode ser excluida.';

SET TERM ^ ;

CREATE OR ALTER TRIGGER XX_TB_ETIQUETA_CORREIO_BI FOR XX_TB_ETIQUETA_CORREIO
ACTIVE BEFORE INSERT POSITION 0
AS
/* etq_schema v1 */
BEGIN
    IF (NEW.ID_ETIQUETA IS NULL) THEN
        NEW.ID_ETIQUETA = GEN_ID(GEN_XX_TB_ETIQUETA_CORREIO, 1);
    IF (NEW.DT_INCLUSAO IS NULL) THEN
        NEW.DT_INCLUSAO = CURRENT_TIMESTAMP;
END^

CREATE OR ALTER TRIGGER XX_TB_ETIQUETA_CORREIO_BD FOR XX_TB_ETIQUETA_CORREIO
ACTIVE BEFORE DELETE POSITION 0
AS
/* etq_schema v1 */
    DECLARE VARIABLE V_BLOQ CHAR(1);
BEGIN
    V_BLOQ = 'N';
    SELECT s.BLOQUEIA_EXCLUSAO FROM XX_TB_ETQ_STATUS s
        WHERE s.CODIGO = OLD.STATUS INTO :V_BLOQ;
    IF (V_BLOQ = 'S') THEN
        EXCEPTION EX_ETQ_BLOQUEIA_EXCLUSAO;
END^

CREATE OR ALTER TRIGGER TB_NFE_ETIQUETA_AIU FOR TB_NFE
ACTIVE AFTER INSERT OR UPDATE POSITION 0
AS
/* etq_schema v2 */
    DECLARE VARIABLE V_MODELO     VARCHAR(2);
    DECLARE VARIABLE V_NUMERO     INTEGER;
    DECLARE VARIABLE V_SERIE      VARCHAR(5);
    DECLARE VARIABLE V_CLIENTE    INTEGER;
    DECLARE VARIABLE V_TIPO_FRETE VARCHAR(5);
BEGIN
    IF (TRIM(NEW.STATUS) = '100') THEN
    BEGIN
        V_MODELO = NULL;
        SELECT v.NF_MODELO, v.NF_NUMERO, v.NF_SERIE, v.ID_CLIENTE, v.TIPO_FRETE
            FROM TB_NFVENDA v
            WHERE v.ID_NFVENDA = NEW.ID_NFVENDA
            INTO :V_MODELO, :V_NUMERO, :V_SERIE, :V_CLIENTE, :V_TIPO_FRETE;

        IF (V_MODELO = '55'
            AND TRIM(COALESCE(V_TIPO_FRETE, '')) <> '9'
            AND NOT EXISTS (SELECT 1 FROM XX_TB_ETIQUETA_CORREIO E
                            WHERE E.ID_NFVENDA = NEW.ID_NFVENDA)) THEN
            INSERT INTO XX_TB_ETIQUETA_CORREIO
                (ID_NFVENDA, ID_CLIENTE, CHAVE_ACESSO, NF_NUMERO, NF_SERIE, STATUS)
            VALUES
                (NEW.ID_NFVENDA, :V_CLIENTE, NEW.ID_NFE, :V_NUMERO, :V_SERIE, 'PENDENTE');
    END
END^

SET TERM ; ^
