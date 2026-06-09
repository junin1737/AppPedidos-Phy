"""Leitura OCR de PDFs de pedido e extração estruturada dos dados."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import limites_campos as lim

CNPJ_LOJA = "40918528000169"
# Ex.: BLZD-EN020, RA05-EN030-UR, DR1-EN206 (setor 3–4 chars, começa com letra)
REF_SETOR = r"[A-Z][A-Z0-9]{2,3}"
REF_SUFIXOS_OPCIONAIS = r"(?:-[A-Z0-9]{2,12})*"
REF_PADRAO = re.compile(
    rf"{REF_SETOR}-[A-Z]{{2}}\d{{2,3}}{REF_SUFIXOS_OPCIONAIS}",
    re.IGNORECASE,
)
REF_COM_HASH = re.compile(
    rf"#?{REF_SETOR}-[A-Z]{{2}}\d{{2,3}}{REF_SUFIXOS_OPCIONAIS}",
    re.IGNORECASE,
)
REF_OCR_EN = re.compile(
    rf"({REF_SETOR})-EN[O0]?(\d{{2,3}})({REF_SUFIXOS_OPCIONAIS})",
    re.IGNORECASE,
)
REF_OCR_PT = re.compile(
    rf"({REF_SETOR})-PT[O0]?(\d{{2,3}})({REF_SUFIXOS_OPCIONAIS})",
    re.IGNORECASE,
)
REF_OCR_FR = re.compile(
    rf"({REF_SETOR})-FR[O0]?(\d{{2,3}})({REF_SUFIXOS_OPCIONAIS})",
    re.IGNORECASE,
)
REF_LEGADO_NUM = re.compile(
    rf"(?:#)?({REF_SETOR})-(\d{{2,3}}){REF_SUFIXOS_OPCIONAIS}",
    re.IGNORECASE,
)
REF_LEGADO_P = re.compile(
    rf"(?:#)?({REF_SETOR})-P(\d{{2,3}}){REF_SUFIXOS_OPCIONAIS}",
    re.IGNORECASE,
)


@dataclass
class ItemPedido:
    quantidade: int
    referencia_original: str
    referencia: str
    preco_unitario: float
    preco_total: float
    id_identificador: int | None = None
    descricao: str = ""
    idioma: str | None = None
    raridade: str | None = None
    sku: str | None = None


@dataclass
class PedidoExtraido:
    arquivo: str = ""
    numero_pedido: str = ""
    data_pedido: str = ""
    pagamento: str = ""
    envio: str = ""
    cliente: dict = field(default_factory=dict)
    itens: list[ItemPedido] = field(default_factory=list)
    resumo: dict = field(default_factory=dict)
    erros: list[str] = field(default_factory=list)


def _parse_moeda(valor: str) -> float:
    """Converte '20,99' / '1.234,56' em float; retorna 0 se OCR vier inválido."""
    texto = (valor or "").strip()
    if not texto or not re.search(r"\d", texto):
        return 0.0
    normalizado = texto.replace(".", "").replace(",", ".")
    if not normalizado or not re.search(r"\d", normalizado):
        return 0.0
    try:
        return float(normalizado)
    except ValueError:
        return 0.0


def _extrair_valores_moeda(linha: str) -> list[str]:
    """Valores após R$ que contenham ao menos um dígito (evita '.' solto do OCR)."""
    bruto = re.findall(r"R\$?\s*([\d,.]+)", linha, flags=re.IGNORECASE)
    return [v for v in bruto if re.search(r"\d", v)]


def _normalizar_telefone(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def digitos_documento(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def formatar_documento(valor: str) -> str:
    """CPF 000.000.000-00 (14) ou CNPJ 00.000.000/0000-00 (18), conforme TB_CLI_PF / TB_CLI_PJ."""
    digitos = digitos_documento(valor)
    if len(digitos) == 11:
        return f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:11]}"
    if len(digitos) == 14:
        return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:14]}"
    return digitos


def linha_e_apelido_nick(lin: str, apelido: str | None = None) -> bool:
    """
    Apelido do site sem ser endereço: (Raiden1994), Rafael_Kaiba ou linha Raiden1994.
    Não confundir com logradouro «B» ou «Avenida B» (com espaço ou tipo).
    """
    s = (lin or "").strip()
    if not s:
        return False
    if apelido and s.lower() == apelido.lower():
        return True
    if re.match(r"^\([^)]+\)\s*$", s):
        return True
    if " " in s or "," in s:
        return False
    if re.match(r"^\d", s) or re.match(r"^N[uú]mero\b", s, re.I):
        return False
    lower = s.lower()
    if lower.startswith(
        ("rua", "av.", "avenida", "travessa", "alameda", "rod.", "estrada")
    ):
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9_]{2,39}$", s):
        if "_" in s:
            return True
        if re.search(r"\d", s):
            return True
    return False


def _dedup_prefixo_codigo_site(texto: str) -> str:
    """Corrige prefixo duplicado do site: LOBLOB-035 → LOB-035, PSVPSV-099 → PSV-099."""
    if not texto:
        return texto
    return re.sub(
        r"^([A-Z][A-Z0-9]{2,3})\1-",
        r"\1-",
        texto.lstrip("#").strip().upper(),
    )


def _colapsar_duplicata_meio_ref(texto: str) -> str:
    """LEHD-ENALEHD-ENA28 → LEHD-ENA28 (código duplicado no meio)."""
    t = (texto or "").upper().strip().lstrip("#")
    m = re.match(r"^([A-Z][A-Z0-9]{2,3})-(.+)$", t)
    if not m:
        return t
    setor, rest = m.group(1), m.group(2)
    for i in range(2, min(len(rest) + 1, 8)):
        prefix = rest[:i]
        marcador = f"{setor}-{prefix}"
        pos = rest.find(marcador, 1)
        if pos > 0:
            tail = rest[pos + len(marcador):]
            return f"{setor}-{prefix}{tail}"
    return t


def _colapsar_duplicata_edicao_site(texto: str) -> str:
    """ABYR-SEABYR-ENSE2 → ABYR-ENSE2; COTD-EECOTD-ENSE4 → COTD-ENSE4."""
    t = (texto or "").upper().strip().lstrip("#")
    m = re.match(r"^([A-Z][A-Z0-9]{2,3})-(SE|EE)\1-(EN.+)$", t)
    if m:
        return f"{m.group(1)}-{m.group(3)}"
    return t


def _normalizar_codigo_site(texto: str) -> str:
    t = _dedup_prefixo_codigo_site(texto)
    t = _colapsar_duplicata_edicao_site(t)
    return _colapsar_duplicata_meio_ref(t)


REF_EDICAO = re.compile(
    rf"(?:#)?({REF_SETOR})-EN[A-Z]*\d{{1,4}}{REF_SUFIXOS_OPCIONAIS}",
    re.IGNORECASE,
)


def normalizar_referencia_site(texto: str) -> str | None:
    """
    Corrige código duplicado do site: DR1DR1-EN206 → DR1-EN206.
    Evita falso positivo «1DR1-EN206» dentro de DR1DR1-EN206.
    """
    if not texto:
        return None
    t = _normalizar_codigo_site(texto)
    m = REF_PADRAO.search(t)
    if m:
        return m.group(0).upper()
    m = REF_LEGADO_P.search(t)
    if m:
        return f"{m.group(1).upper()}-P{_normalizar_digitos_ref(m.group(2))}"
    m = REF_LEGADO_NUM.search(t)
    if m:
        return f"{m.group(1).upper()}-{_normalizar_digitos_ref(m.group(2))}"
    m = REF_EDICAO.search(t)
    if m:
        return m.group(0).upper()
    m = re.search(
        rf"([A-Z][A-Z0-9]{{2,3}})(?:\1)?-[A-Z]{{2}}\d{{2,3}}{REF_SUFIXOS_OPCIONAIS}",
        t,
    )
    if not m:
        return None
    ref = m.group(0).upper()
    ref = re.sub(
        r"^([A-Z][A-Z0-9]{2,3})\1-",
        r"\1-",
        ref,
    )
    return ref if REF_PADRAO.fullmatch(ref) else REF_PADRAO.search(ref).group(0).upper()


def _eh_linha_loja(linha: str) -> bool:
    lower = linha.lower()
    return (
        CNPJ_LOJA in re.sub(r"\D", "", linha)
        or "tião cards" in lower
        or "tiao cards" in lower
        or "remetente" in lower
    )


def _normalizar_digitos_ref(digitos: str) -> str:
    return digitos.replace("O", "0").replace("o", "0").zfill(3)


def _ref_en(setor: str, digitos: str) -> str:
    return f"{setor.upper()}-EN{_normalizar_digitos_ref(digitos)}"


def _extrair_referencia(linha: str) -> str | None:
    texto = (linha or "").upper()

    for bloco in re.findall(
        r"(?:C[ÓO]DIGO|CODIGO)\s*:\s*([A-Z0-9\-/]+)",
        texto,
        flags=re.IGNORECASE,
    ):
        bloco = _normalizar_codigo_site(bloco)
        for match in REF_PADRAO.finditer(bloco):
            return match.group(0).upper()
        match = REF_EDICAO.search(bloco)
        if match:
            return match.group(0).upper()
        match = REF_LEGADO_P.search(bloco)
        if match:
            return f"{match.group(1).upper()}-P{_normalizar_digitos_ref(match.group(2))}"
        match = REF_LEGADO_NUM.search(bloco)
        if match:
            return f"{match.group(1).upper()}-{_normalizar_digitos_ref(match.group(2))}"

    texto = _normalizar_codigo_site(texto)

    match = REF_PADRAO.search(texto)
    if match:
        return match.group(0).upper()

    match = REF_EDICAO.search(texto)
    if match:
        return match.group(0).upper()

    match = REF_LEGADO_P.search(texto)
    if match:
        return f"{match.group(1).upper()}-P{_normalizar_digitos_ref(match.group(2))}"

    match = REF_LEGADO_NUM.search(texto)
    if match:
        return f"{match.group(1).upper()}-{_normalizar_digitos_ref(match.group(2))}"

    match = REF_OCR_EN.search(texto)
    if match:
        return (_ref_en(match.group(1), match.group(2)) + (match.group(3) or "")).upper()

    match = REF_OCR_PT.search(texto)
    if match:
        base = f"{match.group(1).upper()}-PT{_normalizar_digitos_ref(match.group(2))}"
        return (base + (match.group(3) or "")).upper()

    match = REF_OCR_FR.search(texto)
    if match:
        base = f"{match.group(1).upper()}-FR{_normalizar_digitos_ref(match.group(2))}"
        return (base + (match.group(3) or "")).upper()

    return None


def _linha_ignorar_item(linha: str) -> bool:
    lower = linha.lower()
    return bool(
        re.search(
            r"valor dos itens|valor total|separado por|embalado por|https?://|^\d/\d$",
            lower,
        )
        or re.match(r"^\d{2}/\d{2}/\d{4}", linha.strip())
        or _eh_linha_loja(linha)
    )


def _linha_precificacao(linha: str) -> bool:
    lower = linha.lower()
    if "cod" not in lower:
        return False
    return bool(re.search(r"r\$\s*[\d,.]+\d", linha, re.IGNORECASE))


def _contar_linhas_precificacao(texto: str) -> int:
    return sum(1 for l in texto.splitlines() if _linha_precificacao(l))


def _inferir_quantidade_preco(qtd: int, preco_unit: float, preco_total: float) -> int:
    """Se unitário × qtd ≠ total, usa o total do PDF (ex.: total 0,70 / unit 0,35 → 2)."""
    if preco_unit <= 0 or preco_total <= 0:
        return max(qtd, 1)
    qtd_calc = round(preco_total / preco_unit)
    if qtd_calc >= 1 and abs(preco_total - preco_unit * qtd_calc) < 0.02:
        return qtd_calc
    return max(qtd, 1)


def _extrair_quantidade_cod(linhas: list[str], indice_cod: int) -> int:
    linha = linhas[indice_cod].strip()

    for pattern in (
        r"^(\d+)\s*x\s*Cod",
        r"(\d+)\s*x\s*Cod",
    ):
        match = re.search(pattern, linha, re.IGNORECASE)
        if match:
            return int(match.group(1))

    for j in range(indice_cod - 1, max(indice_cod - 12, -1), -1):
        prev = linhas[j].strip()
        if _linha_ignorar_item(prev):
            continue
        if _linha_precificacao(prev):
            break
        match = re.match(r"^(\d+)\s*x\b", prev, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.match(r"^(\d+)\s*x\s+", prev, re.IGNORECASE)
        if match:
            return int(match.group(1))
        if _extrair_referencia(prev):
            match = re.search(r"\b(\d+)\s*x\b", prev, re.IGNORECASE)
            if match:
                return int(match.group(1))

    return 1


def _extrair_precos_linha(linha: str) -> tuple[float, float]:
    precos = _extrair_valores_moeda(linha)
    if len(precos) >= 2:
        unit = _parse_moeda(precos[-2])
        total = _parse_moeda(precos[-1])
        if unit > 0 or total > 0:
            return unit, total
    if len(precos) == 1:
        valor = _parse_moeda(precos[0])
        if valor > 0:
            return valor, valor
    return 0.0, 0.0


def _extrair_descricao_item(linhas: list[str], indice_cod: int) -> str:
    if indice_cod + 1 >= len(linhas):
        return ""
    prox = linhas[indice_cod + 1].strip()
    if not prox or _linha_ignorar_item(prox) or _linha_precificacao(prox):
        return ""
    if _extrair_referencia(prox):
        return ""
    return prox[:60]


def _contexto_item(linhas: list[str], indice_cod: int) -> tuple[str | None, str | None, int]:
    ref_original: str | None = None
    idioma: str | None = None

    for j in range(indice_cod - 1, max(indice_cod - 12, -1), -1):
        prev = linhas[j]
        if _linha_ignorar_item(prev):
            continue
        if _linha_precificacao(prev):
            break
        if not ref_original:
            ref_original = _extrair_referencia(prev)
        if ref_original:
            break

    trecho = linhas[max(0, indice_cod - 12) : indice_cod + 1]
    idioma = _idioma_sigla_bloco(trecho)
    if not idioma:
        for prev in reversed(trecho):
            if _linha_ignorar_item(prev) or _linha_precificacao(prev):
                continue
            idioma = _idioma_da_linha(prev)
            if idioma:
                break

    if not ref_original:
        bloco = " ".join(linhas[max(0, indice_cod - 4) : indice_cod + 1])
        ref_original = _extrair_referencia(bloco)
        idioma = idioma or _idioma_sigla_bloco(
            linhas[max(0, indice_cod - 12) : indice_cod + 1]
        )
        idioma = idioma or _idioma_da_linha(bloco)

    return ref_original, idioma, indice_cod


def _extrair_itens_pedido(linhas: list[str]) -> list[ItemPedido]:
    """Cada linha Cod: com preço vira um item (referência repetida = linhas distintas)."""
    itens: list[ItemPedido] = []

    for i, linha in enumerate(linhas):
        if not _linha_precificacao(linha):
            continue

        ref_original, idioma, _ = _contexto_item(linhas, i)
        if not ref_original:
            continue

        bloco_ctx_linhas = linhas[max(0, i - 12) : i + 1]
        bloco_ctx = " ".join(bloco_ctx_linhas)
        idioma = idioma or _idioma_sigla_bloco(bloco_ctx_linhas)
        idioma = idioma or _idioma_da_linha(bloco_ctx)
        raridade = _extrair_raridade_bloco(bloco_ctx)

        qtd = _extrair_quantidade_cod(linhas, i)
        preco_unit, preco_total = _extrair_precos_linha(linha)
        if preco_unit > 0 and preco_total <= 0:
            preco_total = preco_unit * qtd
        elif preco_total > 0 and preco_unit <= 0:
            preco_unit = preco_total / qtd if qtd else preco_total

        qtd = _inferir_quantidade_preco(qtd, preco_unit, preco_total)
        if preco_unit > 0:
            preco_total = round(preco_unit * qtd, 2)

        ref_final = montar_referencia_clipp(
            ref_original, idioma, raridade=raridade
        )
        descricao = _extrair_descricao_item(linhas, i)

        itens.append(
            ItemPedido(
                quantidade=qtd,
                referencia_original=ref_original,
                referencia=ref_final,
                preco_unitario=preco_unit,
                preco_total=preco_total or qtd * preco_unit,
                descricao=descricao,
                idioma=idioma,
                raridade=raridade,
            )
        )

    return itens


SETS_RARIDADE_GRUPO2 = frozenset({"RA01", "RA02", "RA03", "RA04"})

RARIDADES_SITE = (
    "Platinum Secret Rare",
    "Prismatic Secret Rare",
    "Quarter Century Secret Rare",
    "Quarter Century",
    "Collectors Rare",
    "Collector's Rare",
    "Platinum Rare",
    "Secret Rare",
    "Super Rare",
    "Ultra Rare",
    "Ultimate Rare",
    "Starlight Rare",
    "Ghost Rare",
    "Gold Rare",
    "Prismatic Rare",
    "Rare",
    "Common",
)


def _preservar_marcadores_html(html: str) -> str:
    """Injeta [PT]/raridade no HTML antes de virar texto (bandeira fica só em img/atributos)."""
    if not html:
        return html
    out = html

    _CAMINHOS_IDIOMA = (
        ("PT", (
            r"/idiomas?/pt", r"/bandeiras?/pt", r"/flags?/pt", r"/lang/pt",
            r"pt[_\-]?br", r"portug", r"brazil", r"brasil", r"/br[\.\\-_/]",
            r"flag[^\"'\s>]*\bbr\b",
        )),
        ("EN", (
            r"/idiomas?/en", r"/flags?/en", r"/lang/en", r"english",
            r"/us[\.\-/]", r"/gb[\.\-/]", r"flag[^\"'\s>]*\ben\b",
        )),
        ("FR", (r"/idiomas?/fr", r"/flags?/fr", r"/lang/fr", r"franc")),
    )
    for lang, pats in _CAMINHOS_IDIOMA:
        for pat in pats:
            out = re.sub(
                rf"<img\b[^>]*{pat}[^>]*>",
                f" [{lang}] ",
                out,
                flags=re.I,
            )

    for lang in ("PT", "EN", "FR"):
        out = re.sub(
            rf'<img\b[^>]*(?:alt|title)\s*=\s*["\']?\s*{lang}\s*["\']?[^>]*>',
            f" [{lang}] ",
            out,
            flags=re.I,
        )
    out = re.sub(
        r"<img\b[^>]*(?:brasil|brazil|/br[\.\-/]|flag[^>]*br|portugues|portugu)[^>]*>",
        " [PT] ",
        out,
        flags=re.I,
    )
    out = re.sub(
        r'data-(?:lang|language|idioma)=["\']?(pt|en|fr)["\']?',
        r" [\1] ",
        out,
        flags=re.I,
    )
    out = re.sub(
        r'class=["\'][^"\']*\b(?:lang-pt|idioma-pt|language-pt)\b[^"\']*["\']',
        " [PT] ",
        out,
        flags=re.I,
    )
    out = out.replace("🇧🇷", " [PT] ").replace("🇺🇸", " [EN] ").replace("🇬🇧", " [EN] ")
    for nome in RARIDADES_SITE:
        out = re.sub(
            rf'<img\b[^>]*(?:alt|title)\s*=\s*["\']?\s*{re.escape(nome)}\s*["\']?[^>]*>',
            f" {nome} ",
            out,
            flags=re.I,
        )
    return out


def _trecho_html_proximo_ref(
    html: str, ref: str, *, antes: int = 1500, depois: int = 2000
) -> str:
    if not html or not ref:
        return ""
    html_u = html.upper()
    ref_u = ref.upper()
    pos = html_u.find(ref_u)
    if pos < 0:
        m = REF_PADRAO.search(ref_u)
        if m:
            pos = html_u.find(m.group(0))
    if pos < 0:
        return ""
    return html[max(0, pos - antes): pos + depois]


def _idioma_do_html(html: str, ref: str | None = None) -> str | None:
    trecho = _trecho_html_proximo_ref(html, ref) if ref else html
    if not trecho:
        return None
    marcado = _preservar_marcadores_html(trecho)
    texto = re.sub(r"<[^>]+>", "\n", marcado)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    idioma = _idioma_sigla_bloco(linhas)
    if idioma:
        return idioma
    return _idioma_da_linha(re.sub(r"\s+", " ", texto))


def _raridade_do_html(html: str, ref: str | None = None) -> str | None:
    trecho = _trecho_html_proximo_ref(html, ref) if ref else html
    if not trecho:
        return None
    texto = re.sub(r"<[^>]+>", " ", _preservar_marcadores_html(trecho))
    return _extrair_raridade_bloco(texto)


def _remover_padroes_referencia(texto: str) -> str:
    """Remove códigos SET-EN### do texto antes de detectar idioma."""
    limpo = REF_PADRAO.sub(" ", texto or "")
    return re.sub(r"\s+", " ", limpo).strip()


_CONDICOES_CARTA = frozenset(
    {
        "NM",
        "LP",
        "MP",
        "HP",
        "DMG",
        "SP",
        "PL",
        "GD",
        "EX",
        "VG",
        "FN",
        "M",
        "P",
        "NR",
    }
)


def _idioma_sigla_bloco(linhas: list[str]) -> str | None:
    """Idioma pela sigla PT/EN/FR em linha isolada ou «PT NM» (texto ao lado da bandeira)."""
    cond = "|".join(sorted(_CONDICOES_CARTA, key=len, reverse=True))
    for i, linha in enumerate(linhas):
        bruta = (linha or "").strip()
        m = re.match(r"^(PT|EN|FR)$", bruta, re.I)
        if not m:
            m = re.match(rf"^(PT|EN|FR)\s+(?:{cond})\b", bruta, re.I)
        if not m:
            continue
        if i + 1 < len(linhas):
            prox = (linhas[i + 1] or "").strip().upper()
            if prox in _CONDICOES_CARTA:
                return m.group(1).upper()
        return m.group(1).upper()
    return None


def _idioma_sigla_html(html: str, ref: str | None = None) -> str | None:
    """Sigla PT/EN/FR no HTML visível perto da referência (não usa src de bandeira)."""
    trecho = _trecho_html_proximo_ref(html, ref) if ref else html
    if not trecho:
        return None
    for m in re.finditer(
        r"(?:^|[>\s])(PT|EN|FR)(?:\s|<|$|(?:\s+(?:NM|LP|MP|HP|DMG|SP|PL|GD)\b))",
        re.sub(r"<[^>]+>", " ", trecho),
        re.I,
    ):
        return m.group(1).upper()
    texto = re.sub(r"<[^>]+>", "\n", trecho)
    return _idioma_sigla_bloco([l.strip() for l in texto.split("\n") if l.strip()])


def _idioma_da_linha(contexto: str) -> str | None:
    match = re.search(r"\[(PT|EN|FR)\]", contexto, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    limpo = _remover_padroes_referencia(contexto)
    limpo = re.sub(rf"-(?:EN|PT|FR)\d{{2,3}}\b", " ", limpo, flags=re.I)
    if re.search(r"\bPT\b|Portugu", limpo, re.I):
        return "PT"
    if re.search(r"\bFR\b|Franc|Espa", limpo, re.I):
        return "FR"
    if re.search(r"\bEN\b|English|Ingl", limpo, re.I):
        return "EN"
    return None


def _prefixo_set(referencia: str) -> str | None:
    m = re.match(r"^([A-Z][A-Z0-9]{2,3})-", (referencia or "").upper())
    return m.group(1) if m else None


def set_usa_grupo2(referencia: str, sets_grupo2: frozenset[str] | None = None) -> bool:
    prefixo = _prefixo_set(referencia)
    if not prefixo:
        return False
    alvo = sets_grupo2 or SETS_RARIDADE_GRUPO2
    return prefixo in alvo


def _normalizar_raridade(texto: str) -> str:
    t = (texto or "").upper()
    t = t.replace("'", "")
    return re.sub(r"\s+", " ", t).strip()


def _extrair_raridade_bloco(contexto: str) -> str | None:
    """Extrai raridade do site (ex.: Ultra Rare) normalizada p/ TB_EST_GRUPO_SUB."""
    if not contexto:
        return None
    for nome in RARIDADES_SITE:
        if re.search(re.escape(nome), contexto, re.I):
            return _normalizar_raridade(nome)
    return None


def _nucleo_referencia(ref: str) -> tuple[str, str, str, str] | None:
    """Separa SET, letras após '-', dígitos e sufixo opcional (-UR etc.)."""
    m = re.match(
        rf"^({REF_SETOR})-([A-Z]*)(\d{{2,3}})({REF_SUFIXOS_OPCIONAIS})$",
        (ref or "").strip().upper(),
    )
    if not m:
        return None
    return m.group(1), m.group(2), _normalizar_digitos_ref(m.group(3)), m.group(4) or ""


def _setor_usa_formato_pt_legado(setor: str, mapa: dict[str, str] | None) -> bool:
    """Só sets mapeados (ex.: LOB→LDB) usam LDB-P### em PT; PSV/SDK mantêm SET-###."""
    return bool(mapa and setor.upper() in mapa)


def _prefixo_pt_setor(setor: str, mapa: dict[str, str] | None) -> str:
    setor = setor.upper()
    if mapa and setor in mapa:
        return mapa[setor].upper()
    return setor


def _prefixo_en_setor(setor_pt: str, mapa: dict[str, str] | None) -> str:
    setor_pt = setor_pt.upper()
    if mapa:
        for en, pt in mapa.items():
            if pt.upper() == setor_pt:
                return en.upper()
    return setor_pt


def _converter_referencia(
    ref_original: str,
    idioma: str | None,
    *,
    sets_legacy_pt_prefix: dict[str, str] | None = None,
) -> str:
    nucleo = _nucleo_referencia(ref_original)
    if not nucleo:
        ref_u = (ref_original or "").upper()
        if idioma and "-EN" in ref_u:
            sufixo = {"PT": "-PT", "FR": "-FR"}.get((idioma or "").upper())
            if sufixo:
                return ref_original.replace("-EN", sufixo, 1)
        return ref_original
    setor, letras, num, tail = nucleo
    idioma_u = (idioma or "").upper()

    n_letras = len(letras)
    if n_letras == 0:
        if idioma_u == "PT" and _setor_usa_formato_pt_legado(
            setor, sets_legacy_pt_prefix
        ):
            pt_set = _prefixo_pt_setor(setor, sets_legacy_pt_prefix)
            return f"{pt_set}-P{num}{tail}"
        return f"{setor}-{num}{tail}"

    if n_letras == 1 and letras == "P":
        if idioma_u == "EN":
            en_set = _prefixo_en_setor(setor, sets_legacy_pt_prefix)
            return f"{en_set}-{num}{tail}"
        return f"{setor}-P{num}{tail}"

    if n_letras == 2:
        if idioma_u == "EN" and letras == "PT":
            return f"{setor}-EN{num}{tail}"
        if idioma_u == "PT" and letras == "EN":
            return f"{setor}-PT{num}{tail}"
        if idioma_u == "FR" and letras == "EN":
            return f"{setor}-FR{num}{tail}"
        if idioma_u == "EN":
            return f"{setor}-EN{num}{tail}"
        if idioma_u == "PT":
            return f"{setor}-PT{num}{tail}"
        if idioma_u == "FR":
            return f"{setor}-FR{num}{tail}"
        return f"{setor}-{letras}{num}{tail}"

    return ref_original


def montar_referencia_clipp(
    ref_original: str,
    idioma: str | None,
    *,
    raridade: str | None = None,
    sets_grupo2: frozenset[str] | None = None,
    sets_legacy_pt_prefix: dict[str, str] | None = None,
) -> str:
    """Converte idioma; RA01–RA04 não ganham sufixo — raridade vai para busca por ID_GRUPO2."""
    if sets_legacy_pt_prefix is None:
        try:
            from config import get_clipp_config

            sets_legacy_pt_prefix = get_clipp_config().get("sets_legacy_pt_prefix")
        except Exception:
            sets_legacy_pt_prefix = None
    ref = _converter_referencia(
        ref_original, idioma, sets_legacy_pt_prefix=sets_legacy_pt_prefix
    )
    if set_usa_grupo2(ref, sets_grupo2):
        return ref
    if raridade and not re.search(r"-[A-Z0-9]{2,12}$", ref):
        sufixo = _sufixo_raridade_ref(raridade)
        if sufixo:
            return f"{ref}{sufixo}"
    return ref


def _sufixo_raridade_ref(raridade: str) -> str | None:
    norm = _normalizar_raridade(raridade)
    mapa = {
        "ULTRA RARE": "-UR",
        "SUPER RARE": "-SR",
        "SECRET RARE": "-ScR",
        "ULTIMATE RARE": "-UtR",
        "COLLECTORS RARE": "-CR",
        "COLLECTOR S RARE": "-CR",
        "PLATINUM RARE": "-PL",
        "QUARTER CENTURY": "-QC",
        "STARLIGHT RARE": "-SLR",
        "GHOST RARE": "-GR",
        "GOLD RARE": "-GLD",
        "PRISMATIC RARE": "-PScR",
        "PLATINUM SECRET RARE": "-PS",
        "PRISMATIC SECRET RARE": "-PScR",
        "RARE": "-R",
        "COMMON": "-C",
    }
    return mapa.get(norm)


def _limpar_nome_de_linha_cpf(linha: str) -> str:
    nome = re.split(r",?\s*CPF/CNPJ", linha, flags=re.IGNORECASE)[0]
    nome = re.sub(r"^\s*Destinat[áa]rio\s*", "", nome, flags=re.IGNORECASE)
    nome = nome.strip(" ,")
    nome = re.sub(r"\s*\([^)]+\)\s*$", "", nome).strip()
    return nome[: lim.NOME]


def _fim_bloco_endereco(linha: str) -> bool:
    if _eh_linha_loja(linha):
        return True
    if re.search(r"método de pagamento|método de envio|itens do pedido", linha, re.IGNORECASE):
        return True
    if re.search(r"CPF/CNPJ", linha, re.IGNORECASE):
        return True
    return False


def _aplicar_complemento(dados: dict, texto: str) -> None:
    """END_COMPLE = até 30 chars (metadata); restante em OBSERVACAO."""
    texto = texto.strip().strip(",")
    if not texto:
        return
    if len(texto) <= lim.END_COMPLE:
        dados["end_comple"] = texto
        return
    dados["end_comple"] = texto[: lim.END_COMPLE]
    obs = dados.get("observacao", "")
    complemento_obs = f"Complemento: {texto}"
    dados["observacao"] = f"{obs}; {complemento_obs}".strip("; ") if obs else complemento_obs


def _separar_tipo_logradouro(texto: str) -> tuple[str | None, str]:
    """Separa END_TIPO (Rua, Av., …) do nome do logradouro — padrão CLIPP."""
    texto = (texto or "").strip()
    if not texto:
        return None, ""
    padroes = (
        (r"^avenida\s+", "Avenida"),
        (r"^av\.?\s+", "Av."),
        (r"^travessa\s+", "Travessa"),
        (r"^alameda\s+", "Alameda"),
        (r"^rod\.?\s+", "Rod."),
        (r"^estrada\s+", "Estrada"),
        (r"^r\.?\s+", "Rua"),
        (r"^rua\s+", "Rua"),
    )
    for pattern, tipo in padroes:
        m = re.match(pattern, texto, re.IGNORECASE)
        if m:
            return tipo[: lim.END_TIPO], texto[m.end() :].strip()[: lim.END_LOGRAD]
    return None, texto[: lim.END_LOGRAD]


def _parse_linha_logradouro(linha: str) -> dict:
    """
    Formato típico: Rua X, complemento, numero
    Ex.: Rua Osvaldo Rodrigues, 1001
    Ex.: Rua X, Apto 12, 1001
    """
    out: dict = {}
    if not re.search(
        r"\b(rua|av\.|avenida|travessa|alameda|rod\.|estrada|r\.)\b",
        linha,
        re.IGNORECASE,
    ):
        return out

    partes = [p.strip() for p in linha.split(",") if p.strip()]
    if not partes:
        return out

    end_tipo, end_lograd = _separar_tipo_logradouro(partes[0])
    if end_tipo:
        out["end_tipo"] = end_tipo
    out["end_lograd"] = end_lograd
    if len(partes) == 1:
        return out

    idx_numero = None
    for i in range(1, len(partes)):
        if re.fullmatch(r"\d+[A-Za-z]?", partes[i]):
            idx_numero = i
            break

    if idx_numero is None:
        for i in range(len(partes) - 1, 0, -1):
            if re.search(r"\d", partes[i]):
                idx_numero = i
                break

    if idx_numero is not None:
        out["end_numero"] = partes[idx_numero][: lim.END_NUMERO]
        complemento_partes = partes[1:idx_numero]
        if complemento_partes:
            _aplicar_complemento(out, ", ".join(complemento_partes))
        if idx_numero < len(partes) - 1 and not out.get("end_comple"):
            _aplicar_complemento(out, ", ".join(partes[idx_numero + 1 :]))
    elif len(partes) == 2:
        if re.search(r"\d", partes[1]):
            out["end_numero"] = partes[1][: lim.END_NUMERO]
        else:
            _aplicar_complemento(out, partes[1])

    return out


def _aplicar_telefone_campos(cliente: dict, telefone: str) -> None:
    tel = _normalizar_telefone(telefone)
    if len(tel) < 10:
        return
    cliente["telefone"] = tel
    cliente["ddd_celul"] = tel[: lim.DDD_CELUL]
    cliente["fone_celul"] = tel[lim.DDD_CELUL : lim.DDD_CELUL + lim.FONE_CELUL]
    cliente["ddd"] = cliente["ddd_celul"]
    cliente["fone"] = cliente["fone_celul"]


def _estruturar_endereco(bloco: list[str]) -> dict:
    """Mapeia endereço do PDF → campos TB_CLIENTE."""
    dados: dict = {}
    if not bloco:
        return dados

    dados["endereco_completo"] = ", ".join(bloco)

    for linha in bloco:
        cep_m = re.search(r"(\d{5})-?(\d{3})", linha)
        if cep_m:
            dados["end_cep"] = f"{cep_m.group(1)}-{cep_m.group(2)}"[: lim.END_CEP]

        cid_m = re.search(r"^(.+?)\s*-\s*([A-Z]{2})\s*\(Brasil\)", linha, re.IGNORECASE)
        if cid_m:
            dados["cidade"] = cid_m.group(1).strip()
            dados["uf"] = cid_m.group(2).upper()

        parsed = _parse_linha_logradouro(linha)
        for chave, valor in parsed.items():
            if valor and not dados.get(chave):
                dados[chave] = valor

    for linha in bloco:
        if re.search(r"\d{5}-?\d{3}", linha):
            continue
        if re.search(r"-\s*[A-Z]{2}\s*\(Brasil\)", linha, re.IGNORECASE):
            continue
        if re.search(
            r"\b(rua|av\.|avenida|travessa|alameda|rod\.|estrada)\b",
            linha,
            re.IGNORECASE,
        ):
            continue
        if len(linha) > 2 and not dados.get("end_bairro"):
            dados["end_bairro"] = linha.strip()[: lim.END_BAIRRO]
            break

    return dados


def _extrair_telefone(linhas: list[str], indice_cliente: int, documento: str = "") -> str:
    doc = digitos_documento(documento)
    inicio = max(0, indice_cliente - 2)
    fim = min(len(linhas), indice_cliente + 12)
    texto = " ".join(linhas[inicio:fim])

    padroes = [
        r"(?:Tel(?:efone)?|Celular|Fone|Whats(?:App)?)\s*:?\s*(\(?\d{2}\)?\s*\d{4,5}[-\s]?\d{4})",
        r"\(?(\d{2})\)?\s*(9\d{4})[-\s]?(\d{4})",
        r"\b(\d{2})(9\d{8})\b",
    ]
    for pattern in padroes:
        match = re.search(pattern, texto, re.IGNORECASE)
        if not match:
            continue
        if match.lastindex and match.lastindex >= 2:
            tel = _normalizar_telefone(f"{match.group(1)}{match.group(2)}")
        else:
            tel = _normalizar_telefone(match.group(1))
        if len(tel) >= 10 and tel != doc and tel != doc[:11]:
            return tel
    return ""


def _extrair_endereco_cliente(linhas: list[str], indice_cpf: int, cliente: dict) -> None:
    bloco = []
    for j in range(indice_cpf + 1, len(linhas)):
        linha = linhas[j]
        if _fim_bloco_endereco(linha):
            break
        bloco.append(linha)

    cliente.update(_estruturar_endereco(bloco))


def _proxima_linha_util(linhas: list[str], indice: int, limite: int = 4) -> str:
    for j in range(indice + 1, min(indice + limite, len(linhas))):
        linha = linhas[j].strip()
        if not linha:
            continue
        if re.search(r"método|metodo|itens do pedido", linha, re.IGNORECASE):
            break
        return linha
    return ""


def _extrair_texto_pdf_nativo(caminho_pdf: str) -> str:
    """Tenta ler texto embutido no PDF (muito mais rápido que OCR)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(caminho_pdf)
        partes = []
        for page in reader.pages:
            partes.append(page.extract_text() or "")
        return "\n".join(partes)
    except Exception:
        return ""


def _dpi_ocr(caminho_pdf: str, poppler_path: str = "", dpi_padrao: int = 300) -> int:
    """Pedidos longos: DPI menor = OCR mais rápido (Poppler)."""
    try:
        from pdf2image import pdfinfo_from_path

        kwargs = {}
        if poppler_path.strip():
            kwargs["poppler_path"] = poppler_path.strip()
        info = pdfinfo_from_path(caminho_pdf, **kwargs)
        paginas = int(info.get("Pages") or info.get("pages") or 1)
    except Exception:
        paginas = 1
    if paginas >= 6:
        return 150
    if paginas >= 3:
        return 200
    return dpi_padrao


def extrair_texto_ocr(
    caminho_pdf: str,
    tesseract_cmd: str,
    poppler_path: str = "",
    lang: str = "por",
    on_progress: Callable[[str], None] | None = None,
    dpi: int | None = None,
) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    kwargs = {}
    if poppler_path.strip():
        kwargs["poppler_path"] = poppler_path.strip()

    if dpi is None:
        dpi = _dpi_ocr(caminho_pdf, poppler_path)

    if on_progress:
        on_progress(f"Convertendo PDF (DPI {dpi})...")

    paginas = convert_from_path(caminho_pdf, dpi=dpi, **kwargs)
    texto_completo = ""

    for i, pagina in enumerate(paginas, start=1):
        if on_progress:
            on_progress(f"OCR página {i}/{len(paginas)}...")
        texto_completo += pytesseract.image_to_string(pagina, lang=lang) + "\n"

    return texto_completo


def parsear_texto(texto: str, arquivo: str = "") -> PedidoExtraido:
    pedido = PedidoExtraido(arquivo=arquivo)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]

    # --- Cabeçalho: pedido, data, pagamento, envio ---
    for i, linha in enumerate(linhas):
        if not pedido.numero_pedido:
            for pattern in (
                r"Pedido:\s*#?(\d+)",
                r"Itens do pedido\s*[#*\s]*(\d+)",
                r"cod=(\d+)",
                r"\b(\d{8})\b",
            ):
                match = re.search(pattern, linha, re.IGNORECASE)
                if match:
                    candidato = match.group(1)
                    if len(candidato) >= 7:
                        pedido.numero_pedido = candidato
                        break

        if not pedido.data_pedido:
            match_data = re.search(r"(\d{2}/\d{2}/\d{4}(?:\s*,\s*\d{2}:\d{2})?)", linha)
            if match_data:
                pedido.data_pedido = match_data.group(1).replace(",", "")

        if re.search(r"método de pagamento|metodo de pagamento", linha, re.IGNORECASE):
            valor = re.sub(r".*pagamento\s*:?\s*", "", linha, flags=re.IGNORECASE).strip()
            if not valor or re.search(r"método|metodo", valor, re.IGNORECASE):
                valor = _proxima_linha_util(linhas, i)
            if valor and not re.search(r"método de envio|metodo de envio", valor, re.IGNORECASE):
                pedido.pagamento = valor

        if re.search(r"método de envio|metodo de envio", linha, re.IGNORECASE):
            valor = re.sub(r".*envio\s*:?\s*", "", linha, flags=re.IGNORECASE).strip()
            if not valor or re.search(r"método|metodo", valor, re.IGNORECASE):
                valor = _proxima_linha_util(linhas, i)
            if valor:
                pedido.envio = valor

    # --- Cliente: linha com CPF/CNPJ que não seja da loja ---
    indice_cliente = -1
    for i, linha in enumerate(linhas):
        if not re.search(r"CPF/CNPJ", linha, re.IGNORECASE):
            continue
        if _eh_linha_loja(linha):
            continue
        match_doc = re.search(r"CPF/CNPJ:\s*([\d./-]+)", linha, re.IGNORECASE)
        if match_doc:
            pedido.cliente["documento"] = formatar_documento(match_doc.group(1))
        nome = _limpar_nome_de_linha_cpf(linha)
        if nome:
            pedido.cliente["nome"] = nome
            indice_cliente = i
            break

    if "Destinat" in texto and indice_cliente < 0:
        for i, linha in enumerate(linhas):
            if "Destinat" in linha and not pedido.cliente.get("nome"):
                match_doc = re.search(r"CPF/CNPJ:\s*([\d./-]+)", linha)
                if match_doc:
                    pedido.cliente["documento"] = formatar_documento(match_doc.group(1))
                nome = _limpar_nome_de_linha_cpf(linha)
                if nome:
                    pedido.cliente["nome"] = nome
                    indice_cliente = i
                    break

    if indice_cliente < 0:
        for i, linha in enumerate(linhas):
            if _eh_linha_loja(linha):
                continue
            match_doc = re.search(
                r"CPF/CNPJ:\s*([\d./-]+)|(?:^|[,\s])(\d{11}|\d{14})(?:\s|$|[,\s])",
                linha,
                re.IGNORECASE,
            )
            if not match_doc:
                continue
            doc = match_doc.group(1) or match_doc.group(2)
            doc_limpo = digitos_documento(doc)
            if doc_limpo == CNPJ_LOJA or len(doc_limpo) not in (11, 14):
                continue
            pedido.cliente["documento"] = formatar_documento(doc_limpo)
            nome = _limpar_nome_de_linha_cpf(linha)
            if not nome:
                nome = re.split(r",?\s*CPF", linha, flags=re.IGNORECASE)[0].strip(" ,")[: lim.NOME]
            if nome:
                pedido.cliente["nome"] = nome
                indice_cliente = i
                break

    if indice_cliente >= 0:
        _extrair_endereco_cliente(linhas, indice_cliente, pedido.cliente)

    # --- Telefone (se existir no PDF) ---
    tel = ""
    if indice_cliente >= 0:
        tel = _extrair_telefone(linhas, indice_cliente, pedido.cliente.get("documento", ""))
    if not tel:
        for linha in linhas:
            match_tel = re.search(
                r"(?:Tel(?:efone)?|Celular|Fone)\s*:?\s*(\(?\d{2}\)?\s*\d{4,5}[-\s]?\d{4})",
                linha,
                re.IGNORECASE,
            )
            if match_tel:
                tel = _normalizar_telefone(match_tel.group(1))
                break
    if tel and len(tel) >= 10:
        _aplicar_telefone_campos(pedido.cliente, tel)
    elif not pedido.cliente.get("telefone"):
        pedido.cliente["telefone_aviso"] = "Não consta no PDF — busca de cliente usará CPF/CNPJ."

    # --- Itens (linhas Cod: com preço) ---
    pedido.itens = _extrair_itens_pedido(linhas)
    for linha in linhas:
        match = re.search(r"(\d+)\s*Itens do pedido", linha, re.IGNORECASE)
        if match:
            esperado = int(match.group(1))
            extraidas = len(pedido.itens)
            unidades = sum(it.quantidade for it in pedido.itens)
            if extraidas != esperado:
                pedido.erros.append(
                    f"PDF indica {esperado} linha(s) de item; "
                    f"extraídas {extraidas} linha(s) ({unidades} unidades). "
                    f"Confira o preview antes de importar."
                )
            break

    # --- Totais ---
    for linha in linhas:
        if "Valor dos Itens" in linha:
            for valor in _extrair_valores_moeda(linha):
                pedido.resumo["valor_itens"] = _parse_moeda(valor)
                break
        if re.match(r"Frete\s*:", linha, re.IGNORECASE):
            for valor in _extrair_valores_moeda(linha):
                pedido.resumo["valor_frete"] = _parse_moeda(valor)
                break
        if re.search(r"Desconto|Cupom|Voucher", linha, re.IGNORECASE):
            for valor in _extrair_valores_moeda(linha):
                pedido.resumo["valor_desconto"] = _parse_moeda(valor)
                break
        if "Valor Total" in linha:
            for valor in _extrair_valores_moeda(linha):
                pedido.resumo["valor_total"] = _parse_moeda(valor)
                break

    if not pedido.resumo.get("valor_total"):
        total_itens = sum(it.preco_total for it in pedido.itens)
        if total_itens:
            pedido.resumo["valor_total"] = total_itens

    if not pedido.envio:
        for linha in linhas:
            if re.search(r"método|metodo|itens do pedido|http", linha, re.IGNORECASE):
                continue
            if re.search(
                r"retirada|mini\s*pac|sedex|correios|balcão|balcao|envio",
                linha,
                re.IGNORECASE,
            ):
                pedido.envio = linha.strip()
                break

    if not pedido.pagamento and pedido.envio:
        pedido.pagamento = pedido.envio

    if not pedido.cliente.get("nome"):
        pedido.erros.append("Cliente não identificado no PDF.")
    if not pedido.itens:
        pedido.erros.append("Nenhum item encontrado no PDF.")

    return pedido


def extrair_pedido_pdf(
    caminho_pdf: str,
    tesseract_cmd: str,
    poppler_path: str = "",
    lang: str = "por",
    on_progress: Callable[[str], None] | None = None,
) -> PedidoExtraido:
    texto_nativo = _extrair_texto_pdf_nativo(caminho_pdf)
    if texto_nativo.strip() and _contar_linhas_precificacao(texto_nativo) >= 3:
        if on_progress:
            n = _contar_linhas_precificacao(texto_nativo)
            on_progress(f"Texto nativo do PDF ({n} linha(s) Cod:) — sem OCR.")
        return parsear_texto(texto_nativo, arquivo=caminho_pdf)

    texto = extrair_texto_ocr(
        caminho_pdf,
        tesseract_cmd=tesseract_cmd,
        poppler_path=poppler_path,
        lang=lang,
        on_progress=on_progress,
    )
    return parsear_texto(texto, arquivo=caminho_pdf)
