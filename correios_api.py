"""Integração com as APIs dos Correios (Correios API / CWS).

Cliente HTTP com token JWT, pré-postagem, rótulo PDF, rastreio e consulta
de valor tarifado. Credenciais em config.ini [correios] (código de acesso
gerado no CWS, não é senha do site).
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import requests

import config as app_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes — hosts, serviços do contrato, status de pré-postagem, layouts PDF
# ---------------------------------------------------------------------------

# Hosts por ambiente (base de todas as APIs)
HOSTS = {
    "producao": "https://api.correios.com.br",
    "homologacao": "https://apihom.correios.com.br",
}

# Renova o token com folga, dentro da tolerância de 30 min citada na doc da API Token.
MARGEM_RENOVACAO = timedelta(minutes=30)

# Serviços habilitados no contrato (rótulo -> código Correios).
SERVICOS: dict[str, str] = {
    "SEDEX": "03220",
    "PAC": "03298",
    "Mini Envios": "04227",
}
# Código -> rótulo (para exibir na tela a partir do que está gravado na etiqueta).
SERVICOS_POR_CODIGO: dict[str, str] = {cod: nome for nome, cod in SERVICOS.items()}

# Status da pré-postagem (campo statusAtual na consulta v2).
STATUS_PREPOSTADO = 2   # rótulo só pode ser emitido neste status
STATUS_POSTADO = 3
STATUS_CANCELADO = 5
STATUS_PENDENTE = 7     # estado inicial logo após criar (promove sozinho p/ 2)
STATUS_TERMINAIS = {STATUS_POSTADO, STATUS_CANCELADO}


def servico_por_texto(texto: str) -> tuple[str, str] | None:
    """Detecta o serviço a partir do texto 'Envio:' da observação da nota.

    Retorna (nome, codigo) ou None. Avalia 'MINI' antes de 'PAC' porque
    'Mini Pac' contém 'PAC'.
    """
    t = (texto or "").strip().upper()
    if not t:
        return None
    if "MINI" in t:
        return ("Mini Envios", SERVICOS["Mini Envios"])
    if "SEDEX" in t:
        return ("SEDEX", SERVICOS["SEDEX"])
    if "PAC" in t:
        return ("PAC", SERVICOS["PAC"])
    return None

# Formato do objeto (codigoFormatoObjetoInformado).
FORMATO_ENVELOPE = "1"
FORMATO_PACOTE = "2"
FORMATO_ROLO = "3"

# Layout de impressão do rótulo (config rotulo_formato -> layoutImpressao da API).
ROTULO_LAYOUTS = {
    "100x150": "LINEAR_100_150",
    "100x80": "LINEAR_100_80",
    "a4": "LINEAR_A4",
    "padrao": "PADRAO",
    "": "",
}


def layout_rotulo(formato: str | None) -> str:
    """Converte o formato do config (ex.: '100x150') no layoutImpressao da API."""
    chave = (formato or "").strip().lower().replace(" ", "")
    return ROTULO_LAYOUTS.get(chave, "LINEAR_100_150")


# ---------------------------------------------------------------------------
# Exceções e token JWT (cache com renovação automática)
# ---------------------------------------------------------------------------

class CorreiosError(RuntimeError):
    """Erro de comunicação/autorização com as APIs dos Correios."""

    def __init__(self, mensagem: str, *, status: int | None = None, corpo: Any = None):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo


class RotuloRefazerError(CorreiosError):
    """O recibo do rótulo falhou/expirou (PPN-295 ou timeout): refazer a solicitação.

    Diferente de um erro terminal: sinaliza que basta pedir um NOVO recibo
    (nova solicitação de rótulo) e tentar de novo — o objeto continua válido.

    `por_falha=True`  -> os Correios reportaram falha na geração (PPN-295): vale
                          pedir um recibo novo imediatamente.
    `por_falha=False` -> apenas estourou o tempo de espera (ainda em geração):
                          o recibo continua válido; geralmente é só aguardar.
    """

    def __init__(self, mensagem: str, *, status: int | None = None,
                 corpo: Any = None, por_falha: bool = False):
        super().__init__(mensagem, status=status, corpo=corpo)
        self.por_falha = por_falha


@dataclass
class TokenInfo:
    token: str
    expira_em: datetime | None
    ambiente: str = ""
    cartao_postagem: str | None = None
    contrato: str | None = None
    emissao: datetime | None = None
    bruto: dict = field(default_factory=dict)

    def valido(self, agora: datetime | None = None) -> bool:
        if not self.token:
            return False
        if self.expira_em is None:
            return True
        agora = agora or datetime.now()
        return agora < (self.expira_em - MARGEM_RENOVACAO)


# ---------------------------------------------------------------------------
# Utilitários — datas, dígitos, endereço, mensagens de erro HTTP
# ---------------------------------------------------------------------------

def _parse_dt(valor: str | None) -> datetime | None:
    """Converte datas dos Correios (ISO sem timezone, ex.: 2025-06-23T10:26:51)."""
    if not valor:
        return None
    texto = str(valor).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None


def _somente_digitos(valor: Any) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _formatar_valor(valor: Any) -> str:
    """Valor monetário no formato esperado pelos Correios (ex.: '123.45')."""
    try:
        return f"{float(str(valor).replace(',', '.')):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _split_ddd(numero: Any) -> tuple[str, str]:
    """Separa um telefone em (ddd, numero). Aceita 10/11 dígitos."""
    dig = _somente_digitos(numero)
    if len(dig) >= 10:
        return dig[:2], dig[2:]
    return "", dig


def montar_endereco(dados: dict) -> dict:
    """Monta o bloco `endereco` no formato dos Correios a partir de um dict simples."""
    end = {
        "cep": _somente_digitos(dados.get("cep")),
        "logradouro": (dados.get("logradouro") or "").strip(),
        "numero": str(dados.get("numero") or "").strip(),
        "bairro": (dados.get("bairro") or "").strip(),
        "cidade": (dados.get("cidade") or "").strip(),
        "uf": (dados.get("uf") or "").strip().upper(),
    }
    complemento = (dados.get("complemento") or "").strip()
    if complemento:
        end["complemento"] = complemento
    return end


def _mensagem_erro(resp: requests.Response) -> str:
    try:
        corpo = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:500]
    if isinstance(corpo, dict):
        for chave in ("msgs", "mensagem", "message", "msg", "erro", "error"):
            val = corpo.get(chave)
            if val:
                return val if isinstance(val, str) else "; ".join(map(str, val))
    return str(corpo)[:500]


# ---------------------------------------------------------------------------
# CorreiosClient — autenticação, pré-postagem, rótulo, rastreio, valor postado
# ---------------------------------------------------------------------------

class CorreiosClient:
    """Cliente HTTP com gestão automática do token (thread-safe)."""

    def __init__(self, cfg: dict | None = None, *, session: requests.Session | None = None):
        self._cfg = cfg or app_config.get_correios_config()
        self._timeout = int(self._cfg.get("timeout", 30) or 30)
        self._session = session or requests.Session()
        self._token: TokenInfo | None = None
        self._lock = threading.Lock()

    # --- configuração ---

    @property
    def ambiente(self) -> str:
        return self._cfg.get("ambiente", "producao")

    @property
    def base_url(self) -> str:
        return HOSTS.get(self.ambiente, HOSTS["producao"])

    def _exigir_credenciais(self) -> None:
        faltando = [
            nome
            for nome in ("usuario", "codigo_acesso", "cartao_postagem")
            if not (self._cfg.get(nome) or "").strip()
        ]
        if faltando:
            raise CorreiosError(
                "Credenciais incompletas em config.ini [correios]: "
                + ", ".join(faltando)
            )

    def _basic_auth(self) -> str:
        usuario = self._cfg["usuario"].strip()
        codigo = self._cfg["codigo_acesso"].strip()
        bruto = f"{usuario}:{codigo}".encode("utf-8")
        return "Basic " + base64.b64encode(bruto).decode("ascii")

    # --- token ---

    def obter_token(self, *, forcar: bool = False) -> str:
        with self._lock:
            if not forcar and self._token and self._token.valido():
                return self._token.token
            self._token = self._gerar_token()
            return self._token.token

    def _gerar_token(self) -> TokenInfo:
        self._exigir_credenciais()
        url = f"{self.base_url}/token/v1/autentica/cartaopostagem"
        headers = {
            "Authorization": self._basic_auth(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {"numero": self._cfg["cartao_postagem"].strip()}
        resp: requests.Response | None = None
        for tentativa in range(3):
            try:
                resp = self._session.post(
                    url, json=body, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                if tentativa < 2:
                    time.sleep(2.0 * (tentativa + 1))
                    continue
                raise CorreiosError(f"Falha de conexão ao gerar token: {exc}") from exc

            if resp.status_code in (502, 503, 504) and tentativa < 2:
                time.sleep(2.0 * (tentativa + 1))
                continue
            break

        assert resp is not None
        if resp.status_code not in (200, 201):
            raise CorreiosError(
                f"Erro ao gerar token ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise CorreiosError("Resposta do token não é JSON válido.") from exc

        cartao = (data.get("cartaoPostagem") or {})
        return TokenInfo(
            token=data.get("token", ""),
            expira_em=_parse_dt(data.get("expiraEm")),
            emissao=_parse_dt(data.get("emissao")),
            ambiente=data.get("ambiente", ""),
            cartao_postagem=cartao.get("numero"),
            contrato=cartao.get("contrato"),
            bruto=data,
        )

    # --- requisições autenticadas (base para as próximas APIs) ---

    def _request(
        self,
        metodo: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
        accept: str = "application/json",
    ) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        cabecalhos = {
            "Authorization": f"Bearer {self.obter_token()}",
            "Accept": accept,
        }
        if json is not None:
            cabecalhos["Content-Type"] = "application/json"
        if headers:
            cabecalhos.update(headers)

        resp: requests.Response | None = None
        for tentativa in range(3):
            try:
                resp = self._session.request(
                    metodo.upper(),
                    url,
                    params=params,
                    json=json,
                    headers=cabecalhos,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                if tentativa < 2:
                    time.sleep(2.0 * (tentativa + 1))
                    continue
                raise CorreiosError(
                    f"Falha de conexão em {metodo} {path}: {exc}"
                ) from exc

            if resp.status_code in (502, 503, 504) and tentativa < 2:
                time.sleep(2.0 * (tentativa + 1))
                continue

            if resp.status_code in (401, 403):
                cabecalhos["Authorization"] = f"Bearer {self.obter_token(forcar=True)}"
                try:
                    resp = self._session.request(
                        metodo.upper(),
                        url,
                        params=params,
                        json=json,
                        headers=cabecalhos,
                        timeout=self._timeout,
                    )
                except requests.RequestException as exc:
                    raise CorreiosError(
                        f"Falha de conexão (retry) em {metodo} {path}: {exc}"
                    ) from exc
            return resp

        assert resp is not None
        return resp

    # --- pré-postagem ---

    def _remetente_payload(self) -> dict:
        """Monta o bloco `remetente` a partir do config.ini [correios]."""
        rem = self._cfg.get("remetente") or {}
        ddd_cel, cel = _split_ddd(rem.get("celular"))
        ddd_tel, tel = _split_ddd(rem.get("telefone"))
        payload: dict[str, Any] = {
            "nome": (rem.get("nome") or "").strip(),
            "cpfCnpj": _somente_digitos(rem.get("cnpj")),
            "endereco": montar_endereco(rem),
        }
        if rem.get("email"):
            payload["email"] = rem["email"].strip()
        if cel:
            payload["dddCelular"] = ddd_cel
            payload["celular"] = cel
        if tel:
            payload["dddTelefone"] = ddd_tel
            payload["telefone"] = tel
        return payload

    def montar_destinatario(self, dados: dict) -> dict:
        """Monta o bloco `destinatario` a partir de um dict simples (vindo da nota).

        O celular do destinatário NÃO é enviado: não é obrigatório para a
        pré-postagem e muitas vezes vem cadastrado errado pelo cliente.
        """
        payload: dict[str, Any] = {
            "nome": (dados.get("nome") or "").strip(),
            "endereco": montar_endereco(dados),
        }
        doc = _somente_digitos(dados.get("cpfCnpj") or dados.get("cnpj") or dados.get("cpf"))
        if doc:
            payload["cpfCnpj"] = doc
        if dados.get("email"):
            payload["email"] = dados["email"].strip()
        return payload

    def criar_prepostagem(
        self,
        *,
        destinatario: dict,
        codigo_servico: str,
        peso_g: int | str,
        formato: str = FORMATO_PACOTE,
        altura_cm: int | str | None = None,
        largura_cm: int | str | None = None,
        comprimento_cm: int | str | None = None,
        diametro_cm: int | str | None = None,
        observacao: str = "",
        nota_fiscal: str | None = None,
        chave_nfe: str | None = None,
        serie_nota: str | None = None,
        servicos_adicionais: list[str] | None = None,
        declaracao_conteudo: list[dict] | None = None,
        extras: dict | None = None,
    ) -> dict:
        """Cria uma pré-postagem nos Correios e retorna o JSON de resposta.

        `destinatario` é um dict simples (nome, cep, logradouro, numero, bairro,
        cidade, uf, complemento, celular, email, cpfCnpj). Peso em gramas e
        dimensões em centímetros vêm por nota.

        Quando `chave_nfe` (chave de acesso da NF-e, 44 dígitos) é informada, ela
        é enviada como `chaveNFe` e os Correios vinculam a NF-e real ao objeto —
        assim NÃO é gerada uma Declaração de Conteúdo eletrônica (DC-e). O número
        e a série da nota são extraídos da própria chave quando não informados,
        garantindo consistência. A declaração de conteúdo continua sendo enviada
        (obrigatória na criação), mas serve só para a descrição impressa no rótulo.
        """
        body: dict[str, Any] = {
            "remetente": self._remetente_payload(),
            "destinatario": self.montar_destinatario(destinatario),
            "codigoServico": str(codigo_servico).strip(),
            "pesoInformado": str(int(float(peso_g))) if peso_g not in (None, "") else "0",
            "codigoFormatoObjetoInformado": str(formato),
            "cienteObjetoNaoProibido": "1",
        }
        if altura_cm not in (None, ""):
            body["alturaInformada"] = str(altura_cm)
        if largura_cm not in (None, ""):
            body["larguraInformada"] = str(largura_cm)
        if comprimento_cm not in (None, ""):
            body["comprimentoInformado"] = str(comprimento_cm)
        if diametro_cm not in (None, ""):
            body["diametroInformado"] = str(diametro_cm)
        if observacao:
            body["observacao"] = observacao
        if nota_fiscal:
            body["numeroNotaFiscal"] = _somente_digitos(nota_fiscal)
        # NF-e: informar a chave evita a geração da DC-e (emiteDCe = N).
        chave = _somente_digitos(chave_nfe)
        if len(chave) == 44:
            body["chaveNFe"] = chave
            # número (pos. 26-34) e série (pos. 23-25) saem da própria chave.
            if not body.get("numeroNotaFiscal"):
                body["numeroNotaFiscal"] = str(int(chave[25:34]))
            if not serie_nota:
                serie_nota = str(int(chave[22:25]))
        if serie_nota not in (None, ""):
            body["serieNotaFiscal"] = str(serie_nota).strip()
        if servicos_adicionais:
            body["servicosAdicionais"] = [str(c) for c in servicos_adicionais]
        if declaracao_conteudo:
            body["itensDeclaracaoConteudo"] = [
                {
                    "conteudo": str(item.get("conteudo", "")).strip(),
                    "quantidade": str(item.get("quantidade", "1")),
                    "valor": _formatar_valor(item.get("valor", 0)),
                }
                for item in declaracao_conteudo
            ]
        if extras:
            body.update(extras)

        resp = self._request("POST", "/prepostagem/v1/prepostagens", json=body)
        if resp.status_code not in (200, 201):
            raise CorreiosError(
                f"Erro ao criar pré-postagem ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise CorreiosError("Resposta da pré-postagem não é JSON válido.") from exc

    def consultar_prepostagem(
        self,
        *,
        id_prepostagem: str | None = None,
        codigo_objeto: str | None = None,
    ) -> dict | None:
        """Consulta uma pré-postagem por id ou por código de objeto (v2).

        Retorna o item (dict) com todos os dados, ou None se não encontrado.
        """
        params: dict[str, str] = {}
        if codigo_objeto:
            params["codigoObjeto"] = str(codigo_objeto).strip().upper().replace(" ", "")
        elif id_prepostagem:
            params["idPrePostagem"] = str(id_prepostagem).strip()
        else:
            raise CorreiosError("Informe codigo_objeto ou id_prepostagem.")
        resp = self._request("GET", "/prepostagem/v2/prepostagens", params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise CorreiosError(
                f"Erro ao consultar pré-postagem ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise CorreiosError("Resposta da consulta não é JSON válido.") from exc
        itens = data.get("itens") if isinstance(data, dict) else None
        if isinstance(itens, list):
            return itens[0] if itens else None
        return data

    def aguardar_prepostado(
        self,
        codigo_objeto: str,
        *,
        tentativas: int = 20,
        intervalo: float = 3.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> bool:
        """Aguarda a pré-postagem sair de 'Pendente' (7) para 'Pré-postado' (2).

        O rótulo só pode ser emitido quando a pré-postagem está pré-postada; logo
        após a criação ela fica alguns segundos em 'Pendente'. Levanta CorreiosError
        em estados terminais (Cancelado/Postado/Expirado). Retorna True se ficou
        pré-postada; False se ainda não promoveu dentro do tempo (deixa tentar).
        """
        cod = str(codigo_objeto).strip().upper().replace(" ", "")
        total = max(1, tentativas)
        for i in range(1, total + 1):
            item = self.consultar_prepostagem(codigo_objeto=cod)
            status = item.get("statusAtual") if item else None
            desc = (item or {}).get("descStatusAtual", "") or ""
            low = desc.lower()
            if status == STATUS_PREPOSTADO:
                return True
            if (status in STATUS_TERMINAIS
                    or "cancel" in low or "postado" in low or "expirad" in low):
                raise CorreiosError(
                    f"Pré-postagem {cod} está '{desc or status}' — "
                    "não é possível gerar rótulo (somente quando 'Pré-postado')."
                )
            if on_progress:
                try:
                    on_progress(f"Aguardando pré-postagem ficar pronta… ({i}/{total})")
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(intervalo)
        return False

    # --- rótulo (etiqueta PDF) ---

    def solicitar_rotulo(
        self,
        ids_prepostagem: list[str],
        *,
        tipo: str = "P",
        formato: str = "ET",
        layout: str | None = None,
        imprime_remetente: str = "S",
    ) -> str:
        """Solicita a geração assíncrona do rótulo e retorna o idRecibo."""
        ids = [str(i).strip() for i in ids_prepostagem if str(i).strip()]
        if not ids:
            raise CorreiosError("Nenhum id de pré-postagem informado para o rótulo.")
        body: dict[str, Any] = {
            "idsPrePostagem": ids,
            "tipoRotulo": tipo,
            "formatoRotulo": formato,
        }
        if imprime_remetente:
            body["imprimeRemetente"] = imprime_remetente
        if layout:
            body["layoutImpressao"] = layout
        resp = self._request(
            "POST", "/prepostagem/v1/prepostagens/rotulo/assincrono/pdf", json=body
        )
        if resp.status_code not in (200, 201):
            raise CorreiosError(
                f"Erro ao solicitar rótulo ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        data = resp.json()
        id_recibo = data.get("idRecibo") or data.get("id") or data.get("recibo")
        if not id_recibo:
            raise CorreiosError("Recibo do rótulo não retornado.", corpo=data)
        return str(id_recibo)

    def baixar_rotulo(
        self,
        id_recibo: str,
        *,
        tentativas: int = 40,
        intervalo: float = 3.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> bytes:
        """Faz polling do download e retorna o PDF (bytes) do rótulo.

        Ciclo de vida do recibo (descoberto na API real):
        - PPN-291 "ainda não foi gerado, consulte novamente" -> ainda gerando.
          Aqui apenas AGUARDAMOS no mesmo recibo (não pedimos outro): pedir um
          recibo novo joga o job para o fim da fila e ele nunca termina.
        - PPN-295 "falha na geração... gere um novo recibo" -> falhou de fato;
          sinaliza RotuloRefazerError(por_falha=True) para refazer a solicitação.
        - Cancelado/Postado/Expirado -> erro terminal.
        Se estourar o tempo ainda em PPN-291, levanta RotuloRefazerError
        (por_falha=False): o recibo segue válido, foi só o tempo de espera.
        """
        path = f"/prepostagem/v1/prepostagens/rotulo/download/assincrono/{id_recibo}"
        ultimo = ""
        total = max(1, tentativas)
        for i in range(1, total + 1):
            resp = self._request("GET", path)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                b64 = data.get("dados") or data.get("rotulo") or data.get("pdf")
                if b64:
                    try:
                        return base64.b64decode(b64)
                    except (ValueError, TypeError) as exc:
                        raise CorreiosError("Rótulo retornou base64 inválido.") from exc
                msg = _mensagem_erro(resp)
                low = msg.lower()
                # PPN-295: geração falhou -> precisa de um novo recibo.
                if ("295" in low
                        or "falha na geração" in low
                        or "falha na geracao" in low
                        or "gere um novo recibo" in low
                        or "nova solicitação de rótulo" in low):
                    logger.warning("Rótulo recibo %s: PPN-295 (falha): %s", id_recibo, msg)
                    raise RotuloRefazerError(
                        msg, status=resp.status_code, corpo=resp.text, por_falha=True
                    )
                processando = (
                    not msg
                    or "291" in low
                    or "ainda não foi gerado" in low
                    or "ainda nao foi gerado" in low
                    or "consulte novamente" in low
                    or "pendente" in low  # ainda promovendo Pendente -> Pré-postado
                )
                if not processando:
                    # Cancelado / Postado / Expirado / outro -> erro terminal
                    logger.warning("Rótulo recibo %s: erro terminal: %s", id_recibo, msg)
                    raise CorreiosError(msg, status=resp.status_code, corpo=resp.text)
                ultimo = msg or "ainda processando"
            elif resp.status_code in (202, 404, 425):
                ultimo = "ainda processando"
            else:
                logger.warning(
                    "Rótulo recibo %s: HTTP %s: %s",
                    id_recibo, resp.status_code, _mensagem_erro(resp),
                )
                raise CorreiosError(
                    f"Erro ao baixar rótulo ({resp.status_code}): {_mensagem_erro(resp)}",
                    status=resp.status_code,
                    corpo=resp.text,
                )
            if on_progress:
                try:
                    on_progress(f"Gerando rótulo nos Correios… ({i}/{total})")
                except Exception:  # noqa: BLE001 - progresso nunca quebra o fluxo
                    pass
            time.sleep(intervalo)
        # Estourou o tempo ainda em PPN-291: recibo continua válido (só demorou).
        logger.info("Rótulo recibo %s: tempo esgotado ainda gerando (%s)", id_recibo, ultimo)
        raise RotuloRefazerError(
            f"Tempo esgotado aguardando o rótulo (ainda em geração: {ultimo}).",
            por_falha=False,
        )

    def gerar_rotulo_pdf(
        self,
        ids_prepostagem: list[str],
        *,
        tipo: str = "P",
        formato: str = "ET",
        layout: str | None = None,
        imprime_remetente: str = "S",
        tentativas: int = 60,
        intervalo: float = 3.0,
        resolicitacoes: int = 3,
        on_progress: Callable[[str], None] | None = None,
    ) -> bytes:
        """Solicita e baixa o rótulo em PDF (bytes) de uma ou mais pré-postagens.

        A geração do rótulo nos Correios é assíncrona. A estratégia é:
        1) solicitar UM recibo e aguardar nele por bastante tempo
           (tentativas × intervalo ≈ 2 min) — o serviço costuma levar alguns
           segundos, mas sob fila pode demorar bem mais;
        2) só pedir um recibo NOVO quando os Correios reportam falha real
           (PPN-295). Em caso de timeout (ainda gerando), tentamos mais um recibo
           como último recurso, mas sem rejogar a fila a cada poucos segundos.
        """
        ultimo = ""
        total = max(1, resolicitacoes)
        if on_progress:
            try:
                on_progress("Solicitando rótulo nos Correios…")
            except Exception:  # noqa: BLE001
                pass
        recibo = self.solicitar_rotulo(
            ids_prepostagem, tipo=tipo, formato=formato,
            layout=layout, imprime_remetente=imprime_remetente,
        )
        # Ciclo de vida observado na API: um recibo fica em PPN-291 ("ainda
        # gerando") por ~3 min e então retorna PPN-295. Só APÓS o PPN-295 é que
        # um recibo NOVO entrega o PDF (quase imediato). Re-solicitar durante o
        # PPN-291 reinicia a fila e nunca converge — por isso, em timeout
        # seguimos aguardando o MESMO recibo; só refazemos no PPN-295.
        for tentativa in range(1, total + 1):
            try:
                return self.baixar_rotulo(
                    recibo, tentativas=tentativas, intervalo=intervalo,
                    on_progress=on_progress,
                )
            except RotuloRefazerError as exc:
                ultimo = str(exc)
                logger.info(
                    "gerar_rotulo_pdf: recibo %s não concluiu (por_falha=%s): %s",
                    recibo, exc.por_falha, ultimo,
                )
                if exc.por_falha:
                    # PPN-295: recibo maduro -> um novo recibo já entrega o PDF.
                    if on_progress:
                        try:
                            on_progress("Rótulo pronto, baixando…")
                        except Exception:  # noqa: BLE001
                            pass
                    recibo = self.solicitar_rotulo(
                        ids_prepostagem, tipo=tipo, formato=formato,
                        layout=layout, imprime_remetente=imprime_remetente,
                    )
                # timeout (PPN-291): segue aguardando o MESMO recibo
                continue
        raise CorreiosError(
            "Os Correios não conseguiram gerar o rótulo a tempo. "
            "Geralmente é fila/instabilidade temporária no serviço de rótulos — "
            "aguarde alguns minutos e clique em Imprimir novamente.\n\n"
            f"Detalhe técnico: {ultimo}"
        )

    def cancelar_prepostagem(self, id_prepostagem: str) -> bool:
        """Cancela uma pré-postagem ainda não postada."""
        resp = self._request(
            "DELETE", f"/prepostagem/v1/prepostagens/{str(id_prepostagem).strip()}"
        )
        if resp.status_code not in (200, 204):
            raise CorreiosError(
                f"Erro ao cancelar pré-postagem ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        return True

    def _extrair_valor_dict(self, data: dict | None) -> float | None:
        """Lê valor tarifado de qualquer dict retornado pela API."""
        if not isinstance(data, dict):
            return None
        for chave in (
            "valorAtendimento", "valor_atendimento",
            "valorServico", "valor_servico",
            "valorDeclarado", "valor",
        ):
            bruto = data.get(chave)
            if bruto is None:
                continue
            try:
                v = float(str(bruto).replace(",", "."))
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return None

    def _normalizar_item_postada(self, data: Any, cod_fallback: str) -> dict | None:
        """Aceita dict, lista ou envelope {itens: [...]}."""
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict) and isinstance(data.get("itens"), list):
            itens = data["itens"]
            data = itens[0] if itens else None
        if not isinstance(data, dict):
            return None
        valor_f = self._extrair_valor_dict(data)
        return {
            "codigo_objeto": (data.get("codigoObjeto") or cod_fallback).strip(),
            "valor_atendimento": valor_f,
            "data_postagem": _parse_dt(
                data.get("dataPostagem") or data.get("dtPostagem")
            ),
            "codigo_servico": (data.get("codigoServico") or "").strip(),
            "nome_servico": (data.get("nomeServico") or "").strip(),
            "peso_tarifado": data.get("pesoTarifadoObjeto") or data.get("peso"),
            "numero_atendimento": (data.get("numeroAtendimento") or "").strip(),
            "raw": data,
        }

    def obter_valor_tarifado(
        self,
        *,
        codigo_objeto: str | None = None,
        id_prepostagem: str | None = None,
    ) -> dict | None:
        """Busca valor tarifado após postagem (postada v1, depois pré-postagem v2)."""
        cod = (codigo_objeto or "").strip().upper().replace(" ", "")
        id_pp = (id_prepostagem or "").strip()

        if cod:
            resp = self._request(
                "GET",
                "/prepostagem/v1/prepostagens/postada",
                params={"codigoObjeto": cod},
            )
            if resp.status_code == 200:
                try:
                    mov = self._normalizar_item_postada(resp.json(), cod)
                except ValueError:
                    mov = None
                if mov and mov.get("valor_atendimento") is not None:
                    return mov
            elif resp.status_code not in (404,):
                raise CorreiosError(
                    f"Erro ao consultar postagem ({resp.status_code}): "
                    f"{_mensagem_erro(resp)}",
                    status=resp.status_code,
                    corpo=resp.text,
                )

        prep = self.consultar_prepostagem(
            codigo_objeto=cod or None,
            id_prepostagem=id_pp or None,
        )
        if prep:
            valor_f = self._extrair_valor_dict(prep)
            if valor_f is not None:
                return {
                    "codigo_objeto": (prep.get("codigoObjeto") or cod).strip(),
                    "valor_atendimento": valor_f,
                    "data_postagem": _parse_dt(
                        prep.get("dataPostagem") or prep.get("dtPostagem")
                    ),
                    "codigo_servico": (prep.get("codigoServico") or "").strip(),
                    "nome_servico": (prep.get("nomeServico") or "").strip(),
                    "peso_tarifado": prep.get("pesoInformado") or prep.get("peso"),
                    "numero_atendimento": (prep.get("numeroAtendimento") or "").strip(),
                    "raw": prep,
                }
        return None

    def consultar_postagem(self, codigo_objeto: str) -> dict | None:
        """Consulta o movimento de postagem (valor tarifado após postar).

        GET /prepostagem/v1/prepostagens/postada — retorna None se ainda não
        postado ou não encontrado.
        """
        cod = str(codigo_objeto).strip().upper().replace(" ", "")
        resp = self._request(
            "GET",
            "/prepostagem/v1/prepostagens/postada",
            params={"codigoObjeto": cod},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise CorreiosError(
                f"Erro ao consultar postagem ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise CorreiosError("Resposta da postagem não é JSON válido.") from exc
        return self._normalizar_item_postada(data, cod)

    def rastrear(self, codigo_objeto: str, *, resultado: str = "T") -> dict:
        """Consulta o rastreamento de um objeto na API Rastro.

        resultado: "T" = todos os eventos, "U" = apenas o último.
        Retorna o objeto (dict) com a lista de eventos.
        """
        cod = str(codigo_objeto).strip().upper().replace(" ", "")
        resp = self._request(
            "GET",
            f"/srorastro/v1/objetos/{cod}",
            params={"resultado": resultado},
        )
        if resp.status_code != 200:
            raise CorreiosError(
                f"Erro ao rastrear objeto ({resp.status_code}): {_mensagem_erro(resp)}",
                status=resp.status_code,
                corpo=resp.text,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise CorreiosError("Resposta do rastreamento não é JSON válido.") from exc
        objetos = data.get("objetos") if isinstance(data, dict) else None
        if isinstance(objetos, list) and objetos:
            return objetos[0]
        return data

    def testar_conexao(self) -> TokenInfo:
        """Gera o token e retorna os metadados (para diagnóstico)."""
        self.obter_token(forcar=True)
        assert self._token is not None
        return self._token


def _mascarar(token: str) -> str:
    if not token:
        return "(vazio)"
    return f"{token[:12]}…{token[-6:]} ({len(token)} chars)"


if __name__ == "__main__":
    cliente = CorreiosClient()
    print(f"Ambiente: {cliente.ambiente}  | base: {cliente.base_url}")
    try:
        info = cliente.testar_conexao()
    except CorreiosError as erro:
        print(f"ERRO: {erro}")
        raise SystemExit(1)
    print("Token gerado com sucesso!")
    print(f"  ambiente   : {info.ambiente}")
    print(f"  cartão     : {info.cartao_postagem}")
    print(f"  contrato   : {info.contrato}")
    print(f"  emissão    : {info.emissao}")
    print(f"  expira em  : {info.expira_em}")
    print(f"  token      : {_mascarar(info.token)}")
