/* ============================================================================
   Cadastro de embalagens dos Correios
   ----------------------------------------------------------------------------
   Objetos:
     - GEN_XX_TB_EMB_CORREIO   generator do ID
     - XX_TB_EMB_CORREIO       cadastro de embalagens (dimensões + tara)
     - XX_TB_EMB_CORREIO_BI    trigger de PK (BEFORE INSERT)

   O usuário cadastra aqui as embalagens (Caixa P, M, G, envelope...) com as
   dimensões em cm e o peso da própria embalagem (tara) em gramas. Antes de
   gerar a etiqueta, escolhe a embalagem e as dimensões já vão para os Correios.

   Observação: este script é a referência manual (isql). O app cria e reconcilia
   tudo na conexão via schema_app.garantir_schema_embalagem_correio
   (cria o que falta, altera o que mudou, e segue se estiver igual).

   Campos:
     - FORMATO: 1 = envelope, 2 = caixa/pacote, 3 = rolo/cilindro
     - ATIVO  : 'S'/'N' (some da lista quando 'N')
     - PADRAO : 'S' marca a embalagem sugerida por padrão
   ============================================================================ */

CREATE GENERATOR GEN_XX_TB_EMB_CORREIO;

CREATE TABLE XX_TB_EMB_CORREIO (
    ID_EMB         INTEGER          NOT NULL,
    NOME           VARCHAR(60)      NOT NULL,
    CODIGO         VARCHAR(20),
    COMPRIMENTO    DOUBLE PRECISION,                 -- cm
    LARGURA        DOUBLE PRECISION,                 -- cm
    ALTURA         DOUBLE PRECISION,                 -- cm
    PESO_TARA      DOUBLE PRECISION,                 -- gramas (peso da embalagem)
    FORMATO        VARCHAR(1)  DEFAULT '2' NOT NULL, -- 1 envelope / 2 caixa / 3 rolo
    ATIVO          CHAR(1)     DEFAULT 'S' NOT NULL,
    PADRAO         CHAR(1)     DEFAULT 'N' NOT NULL,
    ORDEM          INTEGER     DEFAULT 0   NOT NULL,
    DT_INCLUSAO    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP NOT NULL,
    DT_ATUALIZACAO TIMESTAMP,
    CONSTRAINT PK_XX_TB_EMB_CORREIO PRIMARY KEY (ID_EMB)
);

SET TERM ^ ;

CREATE OR ALTER TRIGGER XX_TB_EMB_CORREIO_BI FOR XX_TB_EMB_CORREIO
ACTIVE BEFORE INSERT POSITION 0
AS
/* emb_schema v1 */
BEGIN
    IF (NEW.ID_EMB IS NULL) THEN
        NEW.ID_EMB = GEN_ID(GEN_XX_TB_EMB_CORREIO, 1);
    IF (NEW.DT_INCLUSAO IS NULL) THEN
        NEW.DT_INCLUSAO = CURRENT_TIMESTAMP;
END^

SET TERM ; ^
