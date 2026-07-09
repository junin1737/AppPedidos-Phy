"""Configuração persistente da aplicação (config.ini).

Centraliza leitura/gravação do INI e expõe helpers (`get_db_config`,
`get_correios_config`, etc.) usados por servidor, importador e abas Correios.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

CONFIG_FILENAME = "config.ini"
DEFAULT_DATABASE = r"C:\Work\MT\Cliente\Clipp\Base\CLIPP.FDB"

# Valores padrão quando config.ini não existe ou falta alguma chave.
DEFAULTS = {
    "firebird": {
        "database": DEFAULT_DATABASE,
        "user": "SYSDBA",
        "password": "masterkey",
        "charset": "WIN1252",
        "fbclient_path": "",
        "connection_timeout": "20",
        "host": "localhost",
        "port": "3050",
        "use_server": "true",
    },
    "ocr": {
        "tesseract_cmd": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        "poppler_path": "",
        "lang": "por",
    },
    "nfvenda": {
        "id_sermod": "99",
        "nf_serie": "2",
        "nf_modelo": "99",
        "nf_tipo": "S",
    },
    "venda": {
        "id_vendedor": "17",
        "xx_vendedor": "17",
        "id_planoconta": "22",
        "cc_custo": "23",
    },
    "rpa": {
        "base_url": "https://www.tiaocards.com.br/",
        "usuario": "",
        "senha": "",
        "browser_channel": "chrome",
        "chrome_debug_url": "",
    },
    "extensao": {
        "porta": "8765",
    },
    "clipp": {
        "id_grupo_yugioh": "2",
        "id_grupo_pokemon": "27",
        "sets_raridade_grupo2": "RA01,RA02,RA03,RA04",
        "sets_legacy_pt_prefix": "LOB:LDB",
    },
    "correios": {
        "ambiente": "producao",
        "usuario": "",
        "codigo_acesso": "",
        "cartao_postagem": "",
        "contrato": "",
        "timeout": "30",
        # Formato do rótulo para impressão (etiqueta térmica)
        "rotulo_formato": "100x150",
        # Remetente (loja) — usado na pré-postagem/etiqueta
        "remetente_nome": "",
        "remetente_cnpj": "",
        "remetente_ie": "",
        "remetente_cep": "",
        "remetente_logradouro": "",
        "remetente_numero": "",
        "remetente_complemento": "",
        "remetente_bairro": "",
        "remetente_cidade": "",
        "remetente_uf": "",
        "remetente_telefone": "",
        "remetente_celular": "",
        "remetente_email": "",
    },
}


# ---------------------------------------------------------------------------
# Caminhos do aplicativo (código, dados graváveis, log, controle de pedidos)
# ---------------------------------------------------------------------------

def app_dir() -> Path:
    return Path(__file__).resolve().parent


def dados_dir() -> Path:
    """
    Pasta gravável para log e controle de pedidos.
    Em Program Files usa %LOCALAPPDATA%\\AppPedidosCLIPP.
    """
    base = app_dir()
    probe = base / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return base
    except OSError:
        dest = Path(os.environ.get("LOCALAPPDATA", "")) / "AppPedidosCLIPP"
        dest.mkdir(parents=True, exist_ok=True)
        return dest


def log_path() -> Path:
    return dados_dir() / "servidor_clipp.log"


def pedidos_controle_path() -> Path:
    return dados_dir() / "pedidos_rpa.json"


def config_path() -> str:
    return str(app_dir() / CONFIG_FILENAME)


# ---------------------------------------------------------------------------
# Leitura e gravação do config.ini
# ---------------------------------------------------------------------------

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULTS)
    path = config_path()
    if os.path.isfile(path):
        cfg.read(path, encoding="utf-8")
    return cfg


def save_config(cfg: configparser.ConfigParser) -> None:
    path = config_path()
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f)


# ---------------------------------------------------------------------------
# Seções do INI → dicts tipados para cada módulo do projeto
# ---------------------------------------------------------------------------

def get_db_config(cfg: configparser.ConfigParser | None = None) -> dict:
    if cfg is None:
        cfg = load_config()
    section = cfg["firebird"]
    return {
        "database": section.get("database", DEFAULT_DATABASE),
        "user": section.get("user", "SYSDBA"),
        "password": section.get("password", "masterkey"),
        "charset": section.get("charset", "WIN1252"),
        "connection_timeout": int(section.get("connection_timeout", "20")),
        "fbclient_path": section.get("fbclient_path", "").strip(),
        "host": section.get("host", "localhost").strip(),
        "port": int(section.get("port", "3050")),
        "use_server": section.get("use_server", "true").strip().lower()
        in ("1", "true", "yes", "sim"),
    }


def get_nfvenda_config(cfg: configparser.ConfigParser | None = None) -> dict:
    if cfg is None:
        cfg = load_config()
    section = cfg["nfvenda"]
    return {
        "id_sermod": int(section.get("id_sermod", "99")),
        "nf_serie": section.get("nf_serie", "2").strip(),
        "nf_modelo": section.get("nf_modelo", "99").strip(),
        "nf_tipo": section.get("nf_tipo", "S").strip(),
    }


def get_venda_config(cfg: configparser.ConfigParser | None = None) -> dict:
    if cfg is None:
        cfg = load_config()
    section = cfg["venda"]
    return {
        "id_vendedor": int(section.get("id_vendedor", "17")),
        "xx_vendedor": int(section.get("xx_vendedor", "17")),
        "id_planoconta": int(section.get("id_planoconta", "22")),
        "cc_custo": int(section.get("cc_custo", "23")),
    }


def get_clipp_config(cfg: configparser.ConfigParser | None = None) -> dict:
    if cfg is None:
        cfg = load_config()
    if not cfg.has_section("clipp"):
        section = DEFAULTS["clipp"]
    else:
        section = cfg["clipp"]
    sets_raw = section.get(
        "sets_raridade_grupo2", DEFAULTS["clipp"]["sets_raridade_grupo2"]
    )
    sets_grupo2 = frozenset(
        s.strip().upper() for s in sets_raw.split(",") if s.strip()
    )
    legacy_raw = section.get(
        "sets_legacy_pt_prefix", DEFAULTS["clipp"]["sets_legacy_pt_prefix"]
    )
    sets_legacy_pt_prefix: dict[str, str] = {}
    for par in legacy_raw.split(","):
        par = par.strip()
        if ":" not in par:
            continue
        en, pt = par.split(":", 1)
        en, pt = en.strip().upper(), pt.strip().upper()
        if en and pt:
            sets_legacy_pt_prefix[en] = pt
    return {
        "id_grupo_yugioh": int(section.get("id_grupo_yugioh", "2")),
        "id_grupo_pokemon": int(section.get("id_grupo_pokemon", "27")),
        "sets_grupo2": sets_grupo2,
        "sets_legacy_pt_prefix": sets_legacy_pt_prefix,
    }


def get_rpa_config(cfg: configparser.ConfigParser | None = None) -> dict:
    if cfg is None:
        cfg = load_config()
    if not cfg.has_section("rpa"):
        return dict(DEFAULTS["rpa"])
    section = cfg["rpa"]
    return {
        "base_url": section.get("base_url", DEFAULTS["rpa"]["base_url"]).strip(),
        "usuario": section.get("usuario", "").strip(),
        "senha": section.get("senha", "").strip(),
        "browser_channel": section.get(
            "browser_channel", DEFAULTS["rpa"]["browser_channel"]
        ).strip()
        or "chrome",
        "chrome_debug_url": section.get("chrome_debug_url", "").strip(),
    }


def get_correios_config(cfg: configparser.ConfigParser | None = None) -> dict:
    """Credenciais e ambiente da integração Correios API (seção [correios])."""
    if cfg is None:
        cfg = load_config()
    if not cfg.has_section("correios"):
        section = DEFAULTS["correios"]
    else:
        section = cfg["correios"]
    ambiente = section.get("ambiente", "producao").strip().lower()
    if ambiente not in ("producao", "homologacao"):
        ambiente = "producao"
    try:
        timeout = int(section.get("timeout", "30"))
    except (TypeError, ValueError):
        timeout = 30
    return {
        "ambiente": ambiente,
        "usuario": section.get("usuario", "").strip(),
        "codigo_acesso": section.get("codigo_acesso", "").strip(),
        # cartão preserva zeros à esquerda (ex.: 0077180607)
        "cartao_postagem": section.get("cartao_postagem", "").strip(),
        "contrato": section.get("contrato", "").strip(),
        "timeout": timeout,
        "rotulo_formato": section.get("rotulo_formato", "100x150").strip(),
        "remetente": {
            "nome": section.get("remetente_nome", "").strip(),
            "cnpj": section.get("remetente_cnpj", "").strip(),
            "ie": section.get("remetente_ie", "").strip(),
            "cep": section.get("remetente_cep", "").strip(),
            "logradouro": section.get("remetente_logradouro", "").strip(),
            "numero": section.get("remetente_numero", "").strip(),
            "complemento": section.get("remetente_complemento", "").strip(),
            "bairro": section.get("remetente_bairro", "").strip(),
            "cidade": section.get("remetente_cidade", "").strip(),
            "uf": section.get("remetente_uf", "").strip().upper(),
            "telefone": section.get("remetente_telefone", "").strip(),
            "celular": section.get("remetente_celular", "").strip(),
            "email": section.get("remetente_email", "").strip(),
        },
    }


# ---------------------------------------------------------------------------
# Validação rápida antes de importar ou conectar
# ---------------------------------------------------------------------------

def is_configured(cfg: configparser.ConfigParser | None = None) -> bool:
    if cfg is None:
        cfg = load_config()
    db = cfg["firebird"].get("database", "").strip()
    return bool(db) and os.path.isfile(db)


def mensagem_config_banco(cfg: configparser.ConfigParser | None = None) -> str:
    if cfg is None:
        cfg = load_config()
    db = cfg["firebird"].get("database", "").strip()
    if not db:
        return "Configure config.ini — seção [firebird], chave database."
    if not os.path.isfile(db):
        return (
            f"Arquivo do banco não encontrado neste PC:\n{db}\n\n"
            "Ajuste o caminho da cópia do CLIPP.FDB em config.ini "
            "(botão Configurar banco no app)."
        )
    return ""
