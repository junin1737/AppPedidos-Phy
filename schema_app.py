"""Tabelas auxiliares do AppPedidos e migração automática do schema Firebird.

Cria/atualiza: erros de importação, fila de etiquetas Correios, embalagens
e procedure XX_INC_PDV_PEDV. Chamado uma vez por sessão em db.conectar().
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import fdb

if TYPE_CHECKING:
    from db import ItemFaltante

# ---------------------------------------------------------------------------
# Erros de importação — TB_APPPEDIDOS_IMPORT_ERRO
# ---------------------------------------------------------------------------

TABELA_ERRO = "TB_APPPEDIDOS_IMPORT_ERRO"
GENERATOR_ERRO = "GEN_TB_APPPEDIDOS_IMPORT_ERRO"

_DDL_TABELA = f"""
CREATE TABLE {TABELA_ERRO} (
    ID_ERRO INTEGER NOT NULL,
    DT_REGISTRO TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    NUMERO_PEDIDO VARCHAR(24),
    ID_VENDA INTEGER,
    NF_NUMERO INTEGER,
    TIPO CHAR(1) NOT NULL,
    REFERENCIA VARCHAR(80),
    REFERENCIA_SITE VARCHAR(80),
    QTD_ITEM INTEGER,
    VLR_UNIT DOUBLE PRECISION,
    VLR_TOTAL DOUBLE PRECISION,
    DESCRICAO VARCHAR(120),
    IDIOMA VARCHAR(4),
    RARIDADE VARCHAR(40),
    MENSAGEM VARCHAR(250),
    PRIMARY KEY (ID_ERRO)
)
"""

_DDL_GENERATOR = f"CREATE GENERATOR {GENERATOR_ERRO}"


# --- Fila de etiquetas dos Correios (gerada na emissão da NF-e modelo 55) ---
TABELA_ETIQUETA = "XX_TB_ETIQUETA_CORREIO"
TABELA_STATUS = "XX_TB_ETQ_STATUS"
GENERATOR_ETIQUETA = "GEN_XX_TB_ETIQUETA_CORREIO"
TRIGGER_ETIQUETA_BI = "XX_TB_ETIQUETA_CORREIO_BI"
TRIGGER_ETIQUETA_BD = "XX_TB_ETIQUETA_CORREIO_BD"
TRIGGER_NFE_ETIQUETA = "TB_NFE_ETIQUETA_AIU"
EXCECAO_BD = "EX_ETQ_BLOQUEIA_EXCLUSAO"
FK_ETIQUETA_STATUS = "FK_ETIQUETA_STATUS"

# Marca de versão embutida no corpo das triggers: serve para "comparar e alterar
# caso necessário" sem diffs frágeis. Mude ao alterar qualquer corpo de trigger.
_MARCA_VERSAO = "/* etq_schema v2 */"
_MSG_EXCECAO_BD = "Etiqueta impressa/postada nao pode ser excluida."

_DDL_GENERATOR_ETIQUETA = f"CREATE GENERATOR {GENERATOR_ETIQUETA}"

_DDL_TABELA_ETIQUETA = f"""
CREATE TABLE {TABELA_ETIQUETA} (
    ID_ETIQUETA      INTEGER      NOT NULL,
    ID_NFVENDA       INTEGER      NOT NULL,
    ID_CLIENTE       INTEGER,
    CHAVE_ACESSO     VARCHAR(44),
    NF_NUMERO        INTEGER,
    NF_SERIE         VARCHAR(5),
    STATUS           VARCHAR(20)  DEFAULT 'PENDENTE' NOT NULL,
    TENTATIVAS       INTEGER      DEFAULT 0 NOT NULL,
    MENSAGEM_ERRO    VARCHAR(500),
    COD_SERVICO      VARCHAR(10),
    ID_PREPOSTAGEM   VARCHAR(40),
    COD_RASTREIO     VARCHAR(40),
    ARQUIVO_ETIQUETA VARCHAR(260),
    ID_EMB           INTEGER,
    PESO             DOUBLE PRECISION,
    ALTURA           DOUBLE PRECISION,
    LARGURA          DOUBLE PRECISION,
    COMPRIMENTO      DOUBLE PRECISION,
    DT_INCLUSAO      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL,
    DT_GERACAO       TIMESTAMP,
    DT_IMPRESSAO     TIMESTAMP,
    DT_POSTAGEM      TIMESTAMP,
    DT_PREVISTA      TIMESTAMP,
    DT_ENTREGA       TIMESTAMP,
    VL_POSTAGEM      DOUBLE PRECISION,
    DT_ATUALIZACAO   TIMESTAMP,
    CONSTRAINT PK_{TABELA_ETIQUETA} PRIMARY KEY (ID_ETIQUETA),
    CONSTRAINT UQ_XX_ETIQUETA_NFVENDA UNIQUE (ID_NFVENDA)
)
"""

_DDL_TABELA_STATUS = f"""
CREATE TABLE {TABELA_STATUS} (
    CODIGO            VARCHAR(20) NOT NULL,
    DESCRICAO         VARCHAR(60) DEFAULT '' NOT NULL,
    BLOQUEIA_EXCLUSAO CHAR(1) DEFAULT 'N' NOT NULL,
    ORDEM             INTEGER DEFAULT 0 NOT NULL,
    CONSTRAINT PK_{TABELA_STATUS} PRIMARY KEY (CODIGO)
)
"""

# (codigo, descricao, bloqueia_exclusao, ordem)
_STATUS_SEED = [
    ("PENDENTE", "Pendente de impressao", "N", 10),
    ("PROCESSANDO", "Processando", "N", 20),
    ("GERADA", "Etiqueta gerada", "N", 30),
    ("IMPRESSO", "Impresso", "S", 40),
    ("POSTADO", "Postado nos Correios", "S", 50),
    ("ENTREGUE", "Objeto entregue", "S", 55),
    ("ERRO", "Erro ao gerar", "N", 60),
    ("CANCELADA", "Cancelada", "N", 70),
]

# Colunas esperadas (para ALTER TABLE ADD caso falte numa base já existente).
# A PK (ID_ETIQUETA / CODIGO) não entra aqui.
_COLS_ETIQUETA = {
    "ID_NFVENDA": "INTEGER",
    "ID_CLIENTE": "INTEGER",
    "CHAVE_ACESSO": "VARCHAR(44)",
    "NF_NUMERO": "INTEGER",
    "NF_SERIE": "VARCHAR(5)",
    "STATUS": "VARCHAR(20) DEFAULT 'PENDENTE' NOT NULL",
    "TENTATIVAS": "INTEGER DEFAULT 0 NOT NULL",
    "MENSAGEM_ERRO": "VARCHAR(500)",
    "COD_SERVICO": "VARCHAR(10)",
    "ID_PREPOSTAGEM": "VARCHAR(40)",
    "COD_RASTREIO": "VARCHAR(40)",
    "ARQUIVO_ETIQUETA": "VARCHAR(260)",
    "ID_EMB": "INTEGER",
    "PESO": "DOUBLE PRECISION",
    "ALTURA": "DOUBLE PRECISION",
    "LARGURA": "DOUBLE PRECISION",
    "COMPRIMENTO": "DOUBLE PRECISION",
    "DT_INCLUSAO": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL",
    "DT_GERACAO": "TIMESTAMP",
    "DT_IMPRESSAO": "TIMESTAMP",
    "DT_POSTAGEM": "TIMESTAMP",
    "DT_PREVISTA": "TIMESTAMP",
    "DT_ENTREGA": "TIMESTAMP",
    "VL_POSTAGEM": "DOUBLE PRECISION",
    "DT_ATUALIZACAO": "TIMESTAMP",
}
_COLS_STATUS = {
    "DESCRICAO": "VARCHAR(60) DEFAULT '' NOT NULL",
    "BLOQUEIA_EXCLUSAO": "CHAR(1) DEFAULT 'N' NOT NULL",
    "ORDEM": "INTEGER DEFAULT 0 NOT NULL",
}

_DDL_TRIGGER_ETIQUETA_BI = f"""
CREATE OR ALTER TRIGGER {TRIGGER_ETIQUETA_BI} FOR {TABELA_ETIQUETA}
ACTIVE BEFORE INSERT POSITION 0
AS
{_MARCA_VERSAO}
BEGIN
    IF (NEW.ID_ETIQUETA IS NULL) THEN
        NEW.ID_ETIQUETA = GEN_ID({GENERATOR_ETIQUETA}, 1);
    IF (NEW.DT_INCLUSAO IS NULL) THEN
        NEW.DT_INCLUSAO = CURRENT_TIMESTAMP;
END
"""

# Impede excluir etiqueta cujo STATUS tem BLOQUEIA_EXCLUSAO='S' (ex.: IMPRESSO/POSTADO).
_DDL_TRIGGER_ETIQUETA_BD = f"""
CREATE OR ALTER TRIGGER {TRIGGER_ETIQUETA_BD} FOR {TABELA_ETIQUETA}
ACTIVE BEFORE DELETE POSITION 0
AS
{_MARCA_VERSAO}
    DECLARE VARIABLE V_BLOQ CHAR(1);
BEGIN
    V_BLOQ = 'N';
    SELECT s.BLOQUEIA_EXCLUSAO FROM {TABELA_STATUS} s
        WHERE s.CODIGO = OLD.STATUS INTO :V_BLOQ;
    IF (V_BLOQ = 'S') THEN
        EXCEPTION {EXCECAO_BD};
END
"""

# Dispara na autorização da NF-e (TB_NFE.STATUS = '100'). Modelo/numero/cliente
# vêm da TB_NFVENDA pelo ID_NFVENDA. Só modelo 55 (NF-e) entra; NFC-e (65) não.
# TIPO_FRETE = '9' (sem recorrência de transporte) não entra na fila de etiquetas.
# Idempotente: só insere se o ID_NFVENDA ainda não está na fila.
_DDL_TRIGGER_NFE_ETIQUETA = f"""
CREATE OR ALTER TRIGGER {TRIGGER_NFE_ETIQUETA} FOR TB_NFE
ACTIVE AFTER INSERT OR UPDATE POSITION 0
AS
{_MARCA_VERSAO}
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
            AND NOT EXISTS (SELECT 1 FROM {TABELA_ETIQUETA} E
                            WHERE E.ID_NFVENDA = NEW.ID_NFVENDA)) THEN
            INSERT INTO {TABELA_ETIQUETA}
                (ID_NFVENDA, ID_CLIENTE, CHAVE_ACESSO, NF_NUMERO, NF_SERIE, STATUS)
            VALUES
                (NEW.ID_NFVENDA, :V_CLIENTE, NEW.ID_NFE, :V_NUMERO, :V_SERIE, 'PENDENTE');
    END
END
"""


# --- Cadastro de embalagens dos Correios (dimensões + tara) ---
TABELA_EMB = "XX_TB_EMB_CORREIO"
GENERATOR_EMB = "GEN_XX_TB_EMB_CORREIO"
TRIGGER_EMB_BI = "XX_TB_EMB_CORREIO_BI"
_MARCA_VERSAO_EMB = "/* emb_schema v1 */"

_DDL_GENERATOR_EMB = f"CREATE GENERATOR {GENERATOR_EMB}"

_DDL_TABELA_EMB = f"""
CREATE TABLE {TABELA_EMB} (
    ID_EMB         INTEGER          NOT NULL,
    NOME           VARCHAR(60)      NOT NULL,
    CODIGO         VARCHAR(20),
    COMPRIMENTO    DOUBLE PRECISION,
    LARGURA        DOUBLE PRECISION,
    ALTURA         DOUBLE PRECISION,
    PESO_TARA      DOUBLE PRECISION,
    FORMATO        VARCHAR(1)  DEFAULT '2' NOT NULL,
    ATIVO          CHAR(1)     DEFAULT 'S' NOT NULL,
    PADRAO         CHAR(1)     DEFAULT 'N' NOT NULL,
    ORDEM          INTEGER     DEFAULT 0   NOT NULL,
    DT_INCLUSAO    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP NOT NULL,
    DT_ATUALIZACAO TIMESTAMP,
    CONSTRAINT PK_{TABELA_EMB} PRIMARY KEY (ID_EMB)
)
"""

# Colunas esperadas (ALTER TABLE ADD numa base já existente). A PK não entra.
_COLS_EMB = {
    "NOME": "VARCHAR(60) NOT NULL",
    "CODIGO": "VARCHAR(20)",
    "COMPRIMENTO": "DOUBLE PRECISION",
    "LARGURA": "DOUBLE PRECISION",
    "ALTURA": "DOUBLE PRECISION",
    "PESO_TARA": "DOUBLE PRECISION",
    "FORMATO": "VARCHAR(1) DEFAULT '2' NOT NULL",
    "ATIVO": "CHAR(1) DEFAULT 'S' NOT NULL",
    "PADRAO": "CHAR(1) DEFAULT 'N' NOT NULL",
    "ORDEM": "INTEGER DEFAULT 0 NOT NULL",
    "DT_INCLUSAO": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL",
    "DT_ATUALIZACAO": "TIMESTAMP",
}

_DDL_TRIGGER_EMB_BI = f"""
CREATE OR ALTER TRIGGER {TRIGGER_EMB_BI} FOR {TABELA_EMB}
ACTIVE BEFORE INSERT POSITION 0
AS
{_MARCA_VERSAO_EMB}
BEGIN
    IF (NEW.ID_EMB IS NULL) THEN
        NEW.ID_EMB = GEN_ID({GENERATOR_EMB}, 1);
    IF (NEW.DT_INCLUSAO IS NULL) THEN
        NEW.DT_INCLUSAO = CURRENT_TIMESTAMP;
END
"""


# --- Procedure CLIPP: gera pedido de venda a partir da saída importada ---
PROCEDURE_INC_PDV_PEDV = "XX_INC_PDV_PEDV"
_MARCA_VERSAO_PROC_INC_PDV = "/* inc_pdv_pedv v3 */"

_DDL_PROCEDURE_INC_PDV_PEDV = f"""
CREATE OR ALTER PROCEDURE {PROCEDURE_INC_PDV_PEDV} (
    ID_NFVENDA INTEGER
)
AS
declare variable idnfv integer;
declare variable idcliente integer;
declare variable idparcela integer;
declare variable idfmap integer;
declare variable idvendedor integer;
declare variable qtditem numeric(18,4);
declare variable idident integer;
declare variable vlr_total numeric(18,4);
declare variable vlr_desc numeric(18,4);
declare variable vlr_unit numeric(18,4);
declare variable vlr_custo numeric(18,4);
declare variable newPedido integer;
declare variable nfNumero integer;
declare variable idpedido integer;
declare variable vvfiscal numeric(18,4);
declare variable vcfiscal numeric(18,4);
declare variable chave varchar(40);
declare variable vlr_frete numeric(18,4);
declare variable obsvenda varchar(5000);
begin
  {_MARCA_VERSAO_PROC_INC_PDV}

  select nf_numero, id_vendedor, id_cliente, id_parcela, id_fmapgto, vlr_bc_frete, obs
  from tb_nfvenda_2
  where tb_nfvenda_2.id_nfvenda = :id_nfvenda
  into :nfnumero, :idvendedor, :idcliente, :idparcela, :idfmap, :vlr_frete, :obsvenda;

  insert into tb_pedido_venda (chave, id_modulo, dt_valida, id_cliente, id_vendedor, id_pedido, dt_pedido, hr_pedido, id_parcela, id_fmapgto,
  id_status, observacao) values ('', 4, current_date, :idcliente, :idvendedor, :nfNumero, current_date, current_time, :idparcela, :idfmap, 1, :obsvenda) returning id_pedido into :idpedido;

  insert into tb_ped_venda_nome( nome, cpf_cnpj, id_pedido) values ((select nome from v_clientes_2 where id_cliente = :idcliente), (select cpf from v_clientes_2 where id_cliente = :idcliente), :idpedido);

  for select i.qtd_item, i.id_identificador, i.vlr_total, i.vlr_desc, coalesce(i.vlr_unit,0.01) as vlr_unit, i.vlr_custo from tb_nfv_item_2 i
  where i.id_nfvenda = :id_nfvenda
    into :qtditem, :idident, :vlr_total, :vlr_desc, :vlr_unit, :vlr_custo
    do
    begin
      update tb_nfvenda_2 set statusdav = 'e' where id_nfvenda = :id_nfvenda;

      select prc_custo, prc_venda from v_estoque where id_identificador = :idident into vcfiscal, vvfiscal;

      vlr_unit = :vvfiscal;
      vlr_custo = :vcfiscal;

      if (:vlr_total <> (:vvfiscal * :qtditem)) then
        vlr_desc = 0;

      vlr_total = :vvfiscal * :qtditem;

      insert into tb_ped_venda_item (dt_lacto, item_cancel, id_itemped, qtd_item, vlr_total, vlr_desc, id_identificador, id_pedido, vlr_unit, prc_custo, observacao)
      values (current_date, 'N', -1, :qtditem, :vlr_total, :vlr_desc, :idident, :nfNumero, coalesce(:vlr_unit,0), :vlr_custo, :obsvenda);
    end

  update tb_pedido_venda_tot set vlr_total = :vlr_total where id_pedido = :nfNumero;

  if (:vlr_frete is null) then
    vlr_frete = 0;

  if (:vlr_frete > 0
      and not exists (select 1 from tb_ped_venda_frete where id_pedido = :idpedido)) then
  begin
    if (upper(:obsvenda) like '%MINI PAC%' or upper(:obsvenda) like '%MINIPAC%') then
      insert into tb_ped_venda_frete (id_pedido, id_fornec, vlr_frete, tipo_frete, pes_bruto, pes_liquid, qtd_volum)
      values (:idpedido, 4, :vlr_frete, '0', 0.3, 0.3, 1);
    else
      insert into tb_ped_venda_frete (id_pedido, id_fornec, vlr_frete, tipo_frete)
      values (:idpedido, 4, :vlr_frete, '0');
  end
end
"""


# ---------------------------------------------------------------------------
# Introspection Firebird — verifica existência de tabela, generator, trigger, etc.
# ---------------------------------------------------------------------------

def _tabela_existe(cur, nome: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM RDB$RELATIONS
        WHERE TRIM(RDB$RELATION_NAME) = ?
          AND RDB$VIEW_BLR IS NULL
        """,
        (nome.upper(),),
    )
    return cur.fetchone() is not None


def _generator_existe(cur, nome: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM RDB$GENERATORS
        WHERE TRIM(RDB$GENERATOR_NAME) = ?
        """,
        (nome.upper(),),
    )
    return cur.fetchone() is not None


def _trigger_existe(cur, nome: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM RDB$TRIGGERS
        WHERE TRIM(RDB$TRIGGER_NAME) = ?
        """,
        (nome.upper(),),
    )
    return cur.fetchone() is not None


def _colunas_tabela(cur, tabela: str) -> set[str]:
    cur.execute(
        """
        SELECT TRIM(RDB$FIELD_NAME)
        FROM RDB$RELATION_FIELDS
        WHERE TRIM(RDB$RELATION_NAME) = ?
        """,
        (tabela.upper(),),
    )
    return {r[0] for r in cur.fetchall()}


def _constraint_existe(cur, nome: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM RDB$RELATION_CONSTRAINTS
        WHERE TRIM(RDB$CONSTRAINT_NAME) = ?
        """,
        (nome.upper(),),
    )
    return cur.fetchone() is not None


def _exception_mensagem(cur, nome: str) -> str | None:
    """Mensagem atual da exception; None se não existir."""
    cur.execute(
        """
        SELECT RDB$MESSAGE
        FROM RDB$EXCEPTIONS
        WHERE TRIM(RDB$EXCEPTION_NAME) = ?
        """,
        (nome.upper(),),
    )
    row = cur.fetchone()
    return None if row is None else (row[0] or "")


def _trigger_atualizado(cur, nome: str, marca: str) -> bool:
    """True se a trigger existe e o corpo contém a marca de versão atual."""
    cur.execute(
        """
        SELECT CAST(RDB$TRIGGER_SOURCE AS VARCHAR(8000))
        FROM RDB$TRIGGERS
        WHERE TRIM(RDB$TRIGGER_NAME) = ?
        """,
        (nome.upper(),),
    )
    row = cur.fetchone()
    if row is None:
        return False
    return marca in (row[0] or "")


def _procedure_atualizado(cur, nome: str, marca: str) -> bool:
    """True se a procedure existe e o corpo contém a marca de versão atual."""
    cur.execute(
        """
        SELECT CAST(RDB$PROCEDURE_SOURCE AS VARCHAR(8000))
        FROM RDB$PROCEDURES
        WHERE TRIM(RDB$PROCEDURE_NAME) = ?
        """,
        (nome.upper(),),
    )
    row = cur.fetchone()
    if row is None:
        return False
    return marca in (row[0] or "")


def _sincronizar_status_seed(cur) -> bool:
    """Upsert das linhas de status (insere as que faltam, atualiza divergentes)."""
    cur.execute(
        f"SELECT TRIM(CODIGO), DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM FROM {TABELA_STATUS}"
    )
    atuais = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    mudou = False
    for codigo, desc, bloq, ordem in _STATUS_SEED:
        if codigo not in atuais:
            cur.execute(
                f"INSERT INTO {TABELA_STATUS} (CODIGO, DESCRICAO, BLOQUEIA_EXCLUSAO, ORDEM) "
                f"VALUES (?, ?, ?, ?)",
                (codigo, desc, bloq, ordem),
            )
            mudou = True
            continue
        d_atual, b_atual, o_atual = atuais[codigo]
        if (
            (d_atual or "").strip() != desc
            or (b_atual or "").strip().upper() != bloq
            or int(o_atual or 0) != ordem
        ):
            cur.execute(
                f"UPDATE {TABELA_STATUS} SET DESCRICAO = ?, BLOQUEIA_EXCLUSAO = ?, ORDEM = ? "
                f"WHERE CODIGO = ?",
                (desc, bloq, ordem, codigo),
            )
            mudou = True
    return mudou


def _proximo_id_erro(cur) -> int:
    cur.execute(f"SELECT GEN_ID({GENERATOR_ERRO}, 1) FROM RDB$DATABASE")
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Schema — erros de importação (TB_APPPEDIDOS_IMPORT_ERRO)
# ---------------------------------------------------------------------------

def garantir_schema_app_pedidos(
    db_config: dict,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """
    Cria TB_APPPEDIDOS_IMPORT_ERRO se ainda não existir.
    Chamado ao iniciar o servidor.
    """
    import db as firebird_db

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = None
    try:
        con = firebird_db._abrir_conexao(db_config)
        cur = con.cursor()
        criou = False
        if not _generator_existe(cur, GENERATOR_ERRO):
            cur.execute(_DDL_GENERATOR)
            criou = True
        if not _tabela_existe(cur, TABELA_ERRO):
            cur.execute(_DDL_TABELA)
            criou = True
        if criou:
            con.commit()
            log(f"Schema AppPedidos: tabela {TABELA_ERRO} pronta.")
        return True
    except fdb.Error as exc:
        log(f"Aviso: não foi possível garantir schema AppPedidos: {exc}")
        if con:
            try:
                con.rollback()
            except fdb.Error:
                pass
        return False
    finally:
        if con:
            firebird_db.encerrar_conexao(con)


# ---------------------------------------------------------------------------
# Schema — fila de etiquetas Correios (XX_TB_ETIQUETA_CORREIO + triggers NF-e)
# ---------------------------------------------------------------------------

def garantir_schema_etiqueta_correio(
    db_config: dict,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Garante o schema de etiquetas na conexão (cria / compara-e-altera / segue).

    Objetos: generator, tabela da fila (XX_TB_ETIQUETA_CORREIO), tabela de status
    (XX_TB_ETQ_STATUS) + seed, exception de bloqueio, FK e as triggers (PK,
    BEFORE DELETE de proteção e a da TB_NFE que enfileira na autorização 100/modelo 55).

    Reconciliação:
    - falta objeto    -> cria;
    - existe diferente -> ALTER (colunas faltantes, mensagem da exception, corpo
      das triggers via marca de versão, linhas de status via upsert);
    - tudo igual      -> não faz nada.
    """
    import db as firebird_db

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = None
    try:
        con = firebird_db._abrir_conexao(db_config)
        cur = con.cursor()
        mudou = False

        # No Firebird, o objeto recém-criado por DDL só fica visível para o que
        # vem depois (DML, triggers, FK) após COMMIT. Por isso commitamos cada
        # etapa de DDL antes de usá-la.
        def ddl(sql: str) -> None:
            nonlocal mudou
            cur.execute(sql)
            con.commit()
            mudou = True

        # 1) Generator
        if not _generator_existe(cur, GENERATOR_ETIQUETA):
            ddl(_DDL_GENERATOR_ETIQUETA)

        # 2) Tabela de status (+ colunas que faltarem)
        if not _tabela_existe(cur, TABELA_STATUS):
            ddl(_DDL_TABELA_STATUS)
        else:
            for nome, col_ddl in _COLS_STATUS.items():
                if nome not in _colunas_tabela(cur, TABELA_STATUS):
                    ddl(f"ALTER TABLE {TABELA_STATUS} ADD {nome} {col_ddl}")

        # 3) Seed/atualização das linhas de status (DML)
        if _sincronizar_status_seed(cur):
            con.commit()
            mudou = True

        # 4) Tabela da fila (+ colunas que faltarem)
        if not _tabela_existe(cur, TABELA_ETIQUETA):
            ddl(_DDL_TABELA_ETIQUETA)
        else:
            for nome, col_ddl in _COLS_ETIQUETA.items():
                if nome not in _colunas_tabela(cur, TABELA_ETIQUETA):
                    ddl(f"ALTER TABLE {TABELA_ETIQUETA} ADD {nome} {col_ddl}")

        # 5) Exception usada pela trigger de proteção de exclusão
        msg_atual = _exception_mensagem(cur, EXCECAO_BD)
        if msg_atual is None:
            ddl(f"CREATE EXCEPTION {EXCECAO_BD} '{_MSG_EXCECAO_BD}'")
        elif (msg_atual or "").strip() != _MSG_EXCECAO_BD:
            ddl(f"ALTER EXCEPTION {EXCECAO_BD} '{_MSG_EXCECAO_BD}'")

        # 6) Triggers (compara pela marca de versão; CREATE OR ALTER se preciso)
        if not _trigger_atualizado(cur, TRIGGER_ETIQUETA_BI, _MARCA_VERSAO):
            ddl(_DDL_TRIGGER_ETIQUETA_BI)
        if not _trigger_atualizado(cur, TRIGGER_ETIQUETA_BD, _MARCA_VERSAO):
            ddl(_DDL_TRIGGER_ETIQUETA_BD)
        # O trigger na TB_NFE só existe se a tabela fiscal existir.
        if _tabela_existe(cur, "TB_NFE") and not _trigger_atualizado(
            cur, TRIGGER_NFE_ETIQUETA, _MARCA_VERSAO
        ):
            ddl(_DDL_TRIGGER_NFE_ETIQUETA)

        # 7) FK STATUS -> tabela de status (depois do seed já existir/commitado)
        if not _constraint_existe(cur, FK_ETIQUETA_STATUS):
            ddl(
                f"ALTER TABLE {TABELA_ETIQUETA} ADD CONSTRAINT {FK_ETIQUETA_STATUS} "
                f"FOREIGN KEY (STATUS) REFERENCES {TABELA_STATUS} (CODIGO)"
            )

        if mudou:
            log(f"Schema etiquetas: {TABELA_ETIQUETA}/{TABELA_STATUS} sincronizados.")
        return True
    except fdb.Error as exc:
        log(f"Aviso: não foi possível garantir schema de etiquetas: {exc}")
        if con:
            try:
                con.rollback()
            except fdb.Error:
                pass
        return False
    finally:
        if con:
            firebird_db.encerrar_conexao(con)


# ---------------------------------------------------------------------------
# Schema — embalagens padrão (XX_TB_EMB_CORREIO)
# ---------------------------------------------------------------------------

def garantir_schema_embalagem_correio(
    db_config: dict,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Garante o cadastro de embalagens (XX_TB_EMB_CORREIO) na conexão.

    Mesmo padrão das demais: cria o que falta, adiciona colunas que faltarem,
    atualiza a trigger de PK pela marca de versão e segue se estiver tudo igual.
    """
    import db as firebird_db

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = None
    try:
        con = firebird_db._abrir_conexao(db_config)
        cur = con.cursor()
        mudou = False

        def ddl(sql: str) -> None:
            nonlocal mudou
            cur.execute(sql)
            con.commit()
            mudou = True

        if not _generator_existe(cur, GENERATOR_EMB):
            ddl(_DDL_GENERATOR_EMB)

        if not _tabela_existe(cur, TABELA_EMB):
            ddl(_DDL_TABELA_EMB)
        else:
            for nome, col_ddl in _COLS_EMB.items():
                if nome not in _colunas_tabela(cur, TABELA_EMB):
                    ddl(f"ALTER TABLE {TABELA_EMB} ADD {nome} {col_ddl}")

        if not _trigger_atualizado(cur, TRIGGER_EMB_BI, _MARCA_VERSAO_EMB):
            ddl(_DDL_TRIGGER_EMB_BI)

        if mudou:
            log(f"Schema embalagens: {TABELA_EMB} sincronizada.")
        return True
    except fdb.Error as exc:
        log(f"Aviso: não foi possível garantir schema de embalagens: {exc}")
        if con:
            try:
                con.rollback()
            except fdb.Error:
                pass
        return False
    finally:
        if con:
            firebird_db.encerrar_conexao(con)


# ---------------------------------------------------------------------------
# Procedure CLIPP — XX_INC_PDV_PEDV (OBS da NF → TB_PEDIDO_VENDA + frete)
# ---------------------------------------------------------------------------

def garantir_procedure_inc_pdv_pedv(
    db_config: dict,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Atualiza XX_INC_PDV_PEDV quando a versão embutida mudou.

    Copia TB_NFVENDA_2.OBS para TB_PEDIDO_VENDA.OBSERVACAO (e itens/frete).
    """
    import db as firebird_db

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    con = None
    try:
        con = firebird_db._abrir_conexao(db_config)
        cur = con.cursor()
        if _procedure_atualizado(
            cur, PROCEDURE_INC_PDV_PEDV, _MARCA_VERSAO_PROC_INC_PDV
        ):
            return True
        cur.execute(_DDL_PROCEDURE_INC_PDV_PEDV)
        con.commit()
        log(
            f"Procedure {PROCEDURE_INC_PDV_PEDV} atualizada "
            f"(OBS da venda -> TB_PEDIDO_VENDA.OBSERVACAO)."
        )
        return True
    except fdb.Error as exc:
        log(f"Aviso: não foi possível atualizar {PROCEDURE_INC_PDV_PEDV}: {exc}")
        if con:
            try:
                con.rollback()
            except fdb.Error:
                pass
        return False
    finally:
        if con:
            firebird_db.encerrar_conexao(con)


# ---------------------------------------------------------------------------
# Orquestrador — reconcilia todo o schema numa conexão ao banco
# ---------------------------------------------------------------------------

def garantir_schema_apppedidos(
    db_config: dict,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Reconcilia TODO o schema do AppPedidos numa só chamada.

    Roda cada bloco de schema de forma independente (um erro num não impede os
    demais). É o ponto único chamado na conexão ao banco.
    """
    ok = True
    for fn in (
        garantir_schema_app_pedidos,
        garantir_schema_etiqueta_correio,
        garantir_schema_embalagem_correio,
        garantir_procedure_inc_pdv_pedv,
    ):
        try:
            if not fn(db_config, on_log):
                ok = False
        except Exception as exc:  # noqa: BLE001
            if on_log:
                on_log(f"Aviso schema ({fn.__name__}): {exc}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Gravação de falhas de importação no banco (auditoria / relatório)
# ---------------------------------------------------------------------------

def gravar_itens_faltantes_import(
    cur,
    faltantes: list[ItemFaltante],
    *,
    numero_pedido: str = "",
    id_venda: int | None = None,
    nf_numero: int | None = None,
) -> int:
    """Persiste itens não encontrados na importação (TIPO='F')."""
    if not faltantes or not _tabela_existe(cur, TABELA_ERRO):
        return 0

    pedido = str(numero_pedido or "").strip().lstrip("#")
    gravados = 0
    for faltante in faltantes:
        id_erro = _proximo_id_erro(cur)
        cur.execute(
            f"""
            INSERT INTO {TABELA_ERRO} (
                ID_ERRO, NUMERO_PEDIDO, ID_VENDA, NF_NUMERO, TIPO,
                REFERENCIA, REFERENCIA_SITE, QTD_ITEM, VLR_UNIT, VLR_TOTAL,
                DESCRICAO, IDIOMA, RARIDADE, MENSAGEM
            ) VALUES (?, ?, ?, ?, 'F', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_erro,
                pedido or None,
                id_venda,
                nf_numero,
                (faltante.referencia or "")[:80] or None,
                (faltante.referencia_site or "")[:80] or None,
                int(faltante.quantidade or 1),
                float(faltante.preco_unitario or 0),
                float(faltante.preco_total),
                (faltante.descricao or "")[:120] or None,
                (faltante.idioma or "")[:4] or None,
                (faltante.raridade or "")[:40] or None,
                "Item não encontrado no estoque CLIPP",
            ),
        )
        gravados += 1
    return gravados


def gravar_erro_import(
    cur,
    mensagem: str,
    *,
    numero_pedido: str = "",
    id_venda: int | None = None,
    nf_numero: int | None = None,
) -> None:
    """Persiste erro geral da importação (TIPO='E')."""
    if not _tabela_existe(cur, TABELA_ERRO):
        return
    pedido = str(numero_pedido or "").strip().lstrip("#")
    id_erro = _proximo_id_erro(cur)
    cur.execute(
        f"""
        INSERT INTO {TABELA_ERRO} (
            ID_ERRO, NUMERO_PEDIDO, ID_VENDA, NF_NUMERO, TIPO, MENSAGEM
        ) VALUES (?, ?, ?, ?, 'E', ?)
        """,
        (
            id_erro,
            pedido or None,
            id_venda,
            nf_numero,
            (mensagem or "")[:250],
        ),
    )


def _diagnostico_cli() -> int:
    """Cria/reconcilia TODO o schema do AppPedidos mostrando o resultado.

    Use na produção para criar as tabelas e VER o erro real (que no modo
    bandeja fica escondido):
        "<instalacao>\\python\\python.exe" schema_app.py
    """
    import config as app_config

    cfg = app_config.load_config()
    msg = app_config.mensagem_config_banco(cfg)
    if msg:
        print(f"[ERRO] Banco nao configurado: {msg}")
        return 2

    db_cfg = app_config.get_db_config(cfg)
    print(f"Banco: {db_cfg.get('database')} (host={db_cfg.get('host')})")
    ok = garantir_schema_apppedidos(db_cfg, on_log=lambda m: print(f"  {m}"))

    import db as firebird_db

    con = firebird_db._abrir_conexao(db_cfg)
    try:
        cur = con.cursor()
        for tab in (TABELA_ERRO, TABELA_STATUS, TABELA_ETIQUETA, TABELA_EMB):
            existe = _tabela_existe(cur, tab)
            print(f"  [{'OK ' if existe else 'FALTA'}] {tab}")
        proc_ok = _procedure_atualizado(
            cur, PROCEDURE_INC_PDV_PEDV, _MARCA_VERSAO_PROC_INC_PDV
        )
        print(f"  [{'OK ' if proc_ok else 'FALTA'}] {PROCEDURE_INC_PDV_PEDV}")
    finally:
        firebird_db.encerrar_conexao(con)

    print("Concluido." if ok else "Concluido com avisos (veja acima).")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(_diagnostico_cli())
