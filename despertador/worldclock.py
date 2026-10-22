"""Relógio mundial, offsets de fuso e sincronização opcional com APIs.

Tudo é biblioteca padrão. O funcionamento normal usa apenas `zoneinfo` e o
banco tz do sistema (offline). A rede só é usada por `sincronizar()`, chamada
explicitamente pelo usuário na aba de relógio mundial.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from .core import config_dir

URL_WORLDTIMEAPI = "https://worldtimeapi.org/api/timezone/{zona}"
URL_TIMEAPI = "https://timeapi.io/api/Time/current/zone?timeZone={zona}"
TIMEOUT_PADRAO = 5.0
ARQUIVO_CIDADES = "worldclock.json"

CIDADES_PADRAO: list[tuple[str, str]] = [
    ("São Paulo", "America/Sao_Paulo"),
    ("UTC", "UTC"),
    ("Nova York", "America/New_York"),
    ("Londres", "Europe/London"),
    ("Tóquio", "Asia/Tokyo"),
    ("Sydney", "Australia/Sydney"),
]

DIAS_SEMANA = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]

MESES = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]


class ErroRelogio(ValueError):
    """Resposta de API inválida ou fuso desconhecido."""


class ErroRede(OSError):
    """Falha de rede ao consultar a API de horário."""


# ---------------------------------------------------------------------------
# Parsing puro das APIs
# ---------------------------------------------------------------------------


def _como_dict(dados) -> dict:
    if isinstance(dados, dict):
        return dados
    if isinstance(dados, (str, bytes, bytearray)):
        try:
            valor = json.loads(dados)
        except (TypeError, ValueError) as erro:
            raise ErroRelogio(f"JSON inválido: {erro}") from erro
        if not isinstance(valor, dict):
            raise ErroRelogio("JSON inválido: esperava um objeto")
        return valor
    raise ErroRelogio("resposta inválida: esperava um objeto JSON")


def _checar_erro(dados: dict) -> None:
    for chave in ("error", "errors", "message"):
        valor = dados.get(chave)
        if valor:
            if isinstance(valor, (list, dict)):
                valor = json.dumps(valor, ensure_ascii=False)
            raise ErroRelogio(f"API respondeu com erro: {valor}")


def _iso(texto, campo: str) -> datetime:
    if not isinstance(texto, str) or not texto.strip():
        raise ErroRelogio(f"campo {campo!r} ausente ou vazio")
    valor = texto.strip()
    if valor.endswith(("Z", "z")):
        valor = valor[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(valor)
    except ValueError as erro:
        raise ErroRelogio(f"campo {campo!r} inválido: {texto!r}") from erro


def parse_worldtimeapi(dados: dict | str | bytes) -> datetime:
    """Converte a resposta da worldtimeapi.org num datetime com fuso.

    Aceita o dicionário já decodificado (ou o JSON cru). O campo usado é
    `datetime`, que vem com offset explícito (ex.: "-03:00" ou "Z").
    """
    corpo = _como_dict(dados)
    _checar_erro(corpo)
    if "datetime" not in corpo:
        raise ErroRelogio("campo 'datetime' ausente na resposta")
    resultado = _iso(corpo["datetime"], "datetime")
    if resultado.tzinfo is None:
        offset = corpo.get("utc_offset") or corpo.get("raw_offset")
        if isinstance(offset, str) and offset.strip():
            resultado = resultado.replace(tzinfo=_offset_para_tz(offset))
        elif isinstance(offset, (int, float)):
            resultado = resultado.replace(tzinfo=timezone(timedelta(seconds=offset)))
        else:
            raise ErroRelogio("resposta sem offset de fuso")
    return resultado


def parse_timeapi(dados: dict | str | bytes, zona: str | None = None) -> datetime:
    """Converte a resposta da timeapi.io num datetime com fuso.

    O campo `dateTime` normalmente não traz offset; nesse caso usamos o
    `timeZone` da resposta (ou o parâmetro `zona`) para localizá-lo.
    """
    corpo = _como_dict(dados)
    _checar_erro(corpo)
    if "dateTime" not in corpo:
        raise ErroRelogio("campo 'dateTime' ausente na resposta")
    resultado = _iso(corpo["dateTime"], "dateTime")
    if resultado.tzinfo is not None:
        return resultado

    nome_zona = corpo.get("timeZone") or corpo.get("timezone") or zona
    if not nome_zona:
        raise ErroRelogio("resposta sem fuso: informe 'timeZone' ou o parâmetro zona")
    try:
        resultado = resultado.replace(tzinfo=ZoneInfo(nome_zona))
    except ZoneInfoNotFoundError as erro:
        raise ErroRelogio(f"fuso desconhecido: {nome_zona!r}") from erro
    return resultado


def _offset_para_tz(texto: str) -> timezone:
    valor = texto.strip()
    if valor.endswith(("Z", "z")):
        return timezone.utc
    sinal = 1
    if valor.startswith(("+", "-")):
        sinal = -1 if valor[0] == "-" else 1
        valor = valor[1:]
    partes = valor.split(":")
    try:
        horas = int(partes[0])
        minutos = int(partes[1]) if len(partes) > 1 else 0
    except (TypeError, ValueError) as erro:
        raise ErroRelogio(f"offset inválido: {texto!r}") from erro
    if horas > 23 or minutos > 59:
        raise ErroRelogio(f"offset inválido: {texto!r}")
    return timezone(sinal * timedelta(hours=horas, minutes=minutos))


# ---------------------------------------------------------------------------
# Sincronização (rede opcional)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoSincronizacao:
    """Resultado de uma consulta bem-sucedida a uma API de horário."""

    remoto: datetime
    fonte: str
    desvio_segundos: float
    zona: str

    def descricao(self) -> str:
        return descrever_desvio(self.desvio_segundos)


def _baixar_json(url: str, timeout: float) -> dict:
    requisicao = urllib.request.Request(
        url,
        headers={"User-Agent": "despertador/1.1 (+stdlib)", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            corpo = resposta.read()
    except (urllib.error.URLError, OSError, TimeoutError) as erro:
        raise ErroRede(f"falha de rede: {erro}") from erro
    try:
        return _como_dict(corpo)
    except ErroRelogio as erro:
        raise ErroRede(f"resposta ilegível: {erro}") from erro


def sincronizar(
    zona: str = "UTC",
    timeout: float = TIMEOUT_PADRAO,
    abrir=None,
    agora=None,
) -> ResultadoSincronizacao:
    """Consulta a hora de uma API pública e mede o desvio do relógio local.

    A função é bloqueante; a GUI deve chamá-la numa thread separada.
    `abrir(url, timeout)` e `agora()` são injetáveis para testes sem rede.
    """
    buscar = abrir or _baixar_json
    relogio = agora or (lambda: datetime.now(timezone.utc))

    erros: list[str] = []
    for fonte, url, parser in (
        (
            "worldtimeapi",
            URL_WORLDTIMEAPI.format(zona=zona),
            parse_worldtimeapi,
        ),
        (
            "timeapi.io",
            URL_TIMEAPI.format(zona=zona),
            lambda dados: parse_timeapi(dados, zona),
        ),
    ):
        try:
            remoto = parser(buscar(url, timeout))
        except (ErroRelogio, ErroRede, OSError, ValueError) as erro:
            erros.append(f"{fonte}: {erro}")
            continue
        local = relogio()
        if local.tzinfo is None:
            local = local.replace(tzinfo=timezone.utc)
        desvio = (local - remoto.astimezone(timezone.utc)).total_seconds()
        return ResultadoSincronizacao(
            remoto=remoto,
            fonte=fonte,
            desvio_segundos=desvio,
            zona=zona,
        )
    raise ErroRede("; ".join(erros) or "nenhuma fonte disponível")


def descrever_desvio(segundos: float) -> str:
    """Frase em português para o desvio medido (positivo = relógio adiantado)."""
    magnitude = abs(segundos)
    if magnitude < 0.5:
        return "relógio local sincronizado (desvio desprezível)"
    numero = f"{magnitude:.1f}".replace(".", ",")
    direcao = "adiantado" if segundos > 0 else "atrasado"
    return f"relógio local {direcao} em {numero}s"


# ---------------------------------------------------------------------------
# Fusos, offsets e formatação (puro, sem rede)
# ---------------------------------------------------------------------------


def obter_zona(nome: str) -> ZoneInfo:
    try:
        return ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError) as erro:
        raise ErroRelogio(f"fuso desconhecido: {nome!r}") from erro


def zona_do_localtime(caminho: str | Path = "/etc/localtime") -> str | None:
    """Extrai o nome IANA de um /etc/localtime que aponta para zoneinfo."""
    try:
        alvo = Path(caminho).resolve()
    except OSError:
        return None
    marcador = "/zoneinfo/"
    texto = str(alvo)
    if marcador not in texto:
        return None
    candidato = texto.split(marcador, 1)[1]
    if candidato in available_timezones():
        return candidato
    return None


def zona_local_padrao() -> str:
    """Melhor palpite de fuso IANA do sistema, com fallback para UTC."""
    candidatos = []
    if os.environ.get("TZ"):
        candidatos.append(os.environ["TZ"])
    try:
        texto = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if texto:
            candidatos.append(texto)
    except OSError:
        pass
    do_localtime = zona_do_localtime()
    if do_localtime:
        candidatos.append(do_localtime)
    local = datetime.now().astimezone().tzinfo
    chave = getattr(local, "key", None)
    if chave:
        candidatos.append(chave)

    disponiveis = available_timezones()
    for candidato in candidatos:
        if candidato in disponiveis:
            return candidato
    return "UTC"


def agora_em(nome_zona: str, quando: datetime | None = None) -> datetime:
    base = quando or datetime.now(timezone.utc)
    return base.astimezone(obter_zona(nome_zona))


def offset_utc(quando: datetime, nome_zona: str) -> timedelta:
    return quando.astimezone(obter_zona(nome_zona)).utcoffset() or timedelta(0)


def formatar_offset(delta: timedelta) -> str:
    """Formata um offset como "UTC-03:00" (aceita offsets negativos)."""
    total = int(delta.total_seconds() // 60)
    sinal = "+" if total >= 0 else "-"
    total = abs(total)
    horas, minutos = divmod(total, 60)
    return f"UTC{sinal}{horas:02d}:{minutos:02d}"


def diferenca_horas(
    quando: datetime, zona_cidade: str, zona_local: str | None = None
) -> timedelta:
    """Diferença de fuso entre a cidade e o horário local (cidade - local)."""
    local = zona_local or zona_local_padrao()
    return offset_utc(quando, zona_cidade) - offset_utc(quando, local)


def formatar_diferenca(delta: timedelta) -> str:
    """Formata a diferença como "+2h", "-5h", "+5h30" ou "mesmo horário"."""
    minutos = int(round(delta.total_seconds() / 60))
    if minutos == 0:
        return "mesmo horário"
    sinal = "+" if minutos > 0 else "-"
    horas, resto = divmod(abs(minutos), 60)
    if resto:
        return f"{sinal}{horas}h{resto:02d}"
    return f"{sinal}{horas}h"


def rotulo_dia(data_alvo: date, data_referencia: date) -> str:
    """Rótulo relativo: hoje, amanhã, ontem ou o dia da semana."""
    diferenca = (data_alvo - data_referencia).days
    if diferenca == 0:
        return "hoje"
    if diferenca == 1:
        return "amanhã"
    if diferenca == -1:
        return "ontem"
    return DIAS_SEMANA[data_alvo.weekday()]


def formatar_data_extensa(quando: datetime) -> str:
    """Data completa em português, ex.: 'quarta-feira, 21 de outubro de 2026'."""
    dia = DIAS_SEMANA[quando.weekday()]
    mes = MESES[quando.month - 1]
    return f"{dia}, {quando.day} de {mes} de {quando.year}"


def nome_cidade(nome_zona: str) -> str:
    for nome, zona in CIDADES_PADRAO:
        if zona == nome_zona:
            return nome
    return nome_zona.rsplit("/", 1)[-1].replace("_", " ")


def listar_zonas(filtro: str = "") -> list[str]:
    """Lista os fusos IANA disponíveis, opcionalmente filtrados por texto."""
    termo = filtro.strip().lower().replace(" ", "_")
    zonas = sorted(available_timezones())
    if not termo:
        return zonas
    return [z for z in zonas if termo in z.lower()]


# ---------------------------------------------------------------------------
# Cidades exibidas (persistência simples)
# ---------------------------------------------------------------------------


def arquivo_cidades() -> Path:
    return config_dir() / ARQUIVO_CIDADES


def carregar_cidades(caminho: Path | None = None) -> list[str]:
    """Lê a lista de fusos escolhidos; volta ao padrão se o arquivo faltar."""
    padrao = [zona for _nome, zona in CIDADES_PADRAO]
    arquivo = caminho or arquivo_cidades()
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao
    if isinstance(bruto, dict):
        bruto = bruto.get("zones")
    if not isinstance(bruto, list):
        return padrao

    validas: list[str] = []
    disponiveis = available_timezones()
    for zona in bruto:
        if isinstance(zona, str) and zona in disponiveis and zona not in validas:
            validas.append(zona)
    return validas or padrao


def salvar_cidades(zonas: list[str], caminho: Path | None = None) -> None:
    """Grava a lista de fusos de forma atômica, validando os nomes."""
    disponiveis = available_timezones()
    limpas: list[str] = []
    for zona in zonas:
        if zona in disponiveis and zona not in limpas:
            limpas.append(zona)
    arquivo = caminho or arquivo_cidades()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_name(arquivo.name + ".tmp")
    temporario.write_text(
        json.dumps({"zones": limpas}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporario, arquivo)
