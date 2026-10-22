"""Testes do relógio mundial: parsing puro, offsets e sincronização sem rede."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from despertador import worldclock as w

# ------------------------------------------------------- parse_worldtimeapi


def test_parse_worldtimeapi_offset_negativo():
    resposta = {
        "datetime": "2026-10-21T12:34:56.123456-03:00",
        "utc_offset": "-03:00",
        "timezone": "America/Sao_Paulo",
    }
    resultado = w.parse_worldtimeapi(resposta)
    esperado = datetime(
        2026, 10, 21, 12, 34, 56, 123456, tzinfo=timezone(-timedelta(hours=3))
    )
    assert resultado == esperado
    assert resultado.utcoffset() == -timedelta(hours=3)


def test_parse_worldtimeapi_formato_z():
    resultado = w.parse_worldtimeapi({"datetime": "2026-10-21T15:34:56Z"})
    assert resultado.tzinfo is not None
    assert resultado.astimezone(timezone.utc).hour == 15


def test_parse_worldtimeapi_json_cru():
    texto = json.dumps({"datetime": "2026-10-21T15:00:00+00:00"})
    assert w.parse_worldtimeapi(texto).utcoffset() == timedelta(0)


def test_parse_worldtimeapi_usa_utc_offset_quando_datetime_e_ingenuo():
    resposta = {"datetime": "2026-10-21T12:00:00", "utc_offset": "+05:30"}
    resultado = w.parse_worldtimeapi(resposta)
    assert resultado.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    "resposta",
    [
        {},
        {"datetime": ""},
        {"datetime": "não é data"},
        {"error": "unknown location"},
        {"errors": [{"detail": "invalid"}]},
        ["lista"],
    ],
)
def test_parse_worldtimeapi_invalido(resposta):
    with pytest.raises(w.ErroRelogio):
        w.parse_worldtimeapi(resposta)


def test_parse_worldtimeapi_json_invalido():
    with pytest.raises(w.ErroRelogio):
        w.parse_worldtimeapi("{isso não é json")


# ------------------------------------------------------------ parse_timeapi


def test_parse_timeapi_com_offset():
    resposta = {"dateTime": "2026-10-21T12:34:56-03:00", "timeZone": "America/Sao_Paulo"}
    resultado = w.parse_timeapi(resposta)
    assert resultado.utcoffset() == -timedelta(hours=3)


def test_parse_timeapi_com_z():
    resposta = {"dateTime": "2026-10-21T15:00:00Z"}
    resultado = w.parse_timeapi(resposta)
    assert resultado.astimezone(timezone.utc).hour == 15


def test_parse_timeapi_sem_offset_usa_timezone_da_resposta():
    resposta = {"dateTime": "2026-10-21T12:00:00", "timeZone": "America/Sao_Paulo"}
    resultado = w.parse_timeapi(resposta)
    assert resultado.tzinfo is not None
    assert resultado.utcoffset() == -timedelta(hours=3)


def test_parse_timeapi_sem_offset_usa_parametro_zona():
    resultado = w.parse_timeapi({"dateTime": "2026-01-15T12:00:00"}, zona="Europe/London")
    assert resultado.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "resposta, zona",
    [
        ({}, None),
        ({"dateTime": ""}, None),
        ({"dateTime": "12:00:00"}, None),
        ({"dateTime": "2026-10-21T12:00:00"}, None),
        ({"dateTime": "2026-10-21T12:00:00"}, "Marte/Olympus"),
        ({"status": 400, "message": "invalid timezone"}, None),
    ],
)
def test_parse_timeapi_invalido(resposta, zona):
    with pytest.raises(w.ErroRelogio):
        w.parse_timeapi(resposta, zona=zona)


def test_parse_timeapi_json_invalido():
    with pytest.raises(w.ErroRelogio):
        w.parse_timeapi(b"{quebrado")


# --------------------------------------------------------------- offsets


QUANDO = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def test_mesmo_dia_em_sao_paulo():
    local = w.agora_em("America/Sao_Paulo", QUANDO)
    assert (local.hour, local.minute, local.day) == (12, 0, 21)


def test_dia_seguinte_em_toquio():
    local = w.agora_em("Asia/Tokyo", QUANDO)
    assert (local.hour, local.day) == (0, 22)
    assert w.rotulo_dia(local.date(), datetime(2026, 10, 21).date()) == "amanhã"


def test_meia_noite_versus_meio_dia():
    madrugada = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
    sao_paulo = w.agora_em("America/Sao_Paulo", madrugada)
    londres = w.agora_em("Europe/London", madrugada)
    assert (sao_paulo.hour, sao_paulo.minute) == (0, 0)
    assert (londres.hour, londres.minute) == (4, 0)


def test_formata_offset_positivo_negativo_e_fracionario():
    assert w.formatar_offset(-timedelta(hours=3)) == "UTC-03:00"
    assert w.formatar_offset(timedelta(hours=5, minutes=30)) == "UTC+05:30"
    assert w.formatar_offset(timedelta(0)) == "UTC+00:00"


def test_diferenca_entre_fusos_positiva_e_negativa():
    toquio = w.diferenca_horas(QUANDO, "Asia/Tokyo", "America/Sao_Paulo")
    honolulu = w.diferenca_horas(QUANDO, "Pacific/Honolulu", "Europe/London")
    assert toquio == timedelta(hours=12)
    assert w.formatar_diferenca(toquio) == "+12h"
    assert honolulu == -timedelta(hours=11)
    assert w.formatar_diferenca(honolulu) == "-11h"


def test_diferenca_fracionaria_e_mesmo_horario():
    assert w.formatar_diferenca(timedelta(minutes=330)) == "+5h30"
    assert w.formatar_diferenca(timedelta(0)) == "mesmo horário"


def test_rotulo_dia_atravessa_mes_e_ano():
    referencia = datetime(2026, 12, 31).date()
    assert w.rotulo_dia(datetime(2027, 1, 1).date(), referencia) == "amanhã"
    assert w.rotulo_dia(datetime(2026, 12, 30).date(), referencia) == "ontem"
    assert w.rotulo_dia(datetime(2026, 12, 28).date(), referencia) == "segunda-feira"


def test_formatar_data_extensa():
    assert w.formatar_data_extensa(datetime(2026, 10, 21)) == (
        "quarta-feira, 21 de outubro de 2026"
    )


# ----------------------------------------------------------- sincronização


def test_sincronizar_mede_desvio_com_fonte_primaria():
    remoto = datetime(2026, 10, 21, 15, 0, 0, tzinfo=timezone.utc)
    local = remoto + timedelta(seconds=2.3)

    def abrir(url, timeout):
        assert "worldtimeapi" in url
        assert timeout == 5.0
        return {"datetime": "2026-10-21T15:00:00+00:00"}

    resultado = w.sincronizar(
        "UTC", abrir=abrir, agora=lambda: local
    )
    assert resultado.fonte == "worldtimeapi"
    assert resultado.desvio_segundos == pytest.approx(2.3)
    assert resultado.descricao() == "relógio local adiantado em 2,3s"


def test_sincronizar_usa_alternativa_quando_primaria_falha():
    chamadas = []

    def abrir(url, timeout):
        chamadas.append(url)
        if "worldtimeapi" in url:
            raise w.ErroRede("sem rede")
        return {"dateTime": "2026-10-21T12:00:00", "timeZone": "America/Sao_Paulo"}

    local = datetime(2026, 10, 21, 12, 0, 0, 500000, tzinfo=timezone(-timedelta(hours=3)))
    resultado = w.sincronizar("America/Sao_Paulo", abrir=abrir, agora=lambda: local)
    assert resultado.fonte == "timeapi.io"
    assert resultado.desvio_segundos == pytest.approx(0.5)
    assert len(chamadas) == 2


def test_sincronizar_sem_rede_levanta_erro_com_as_duas_fontes():
    def abrir(url, timeout):
        raise w.ErroRede("Name or service not known")

    with pytest.raises(w.ErroRede) as info:
        w.sincronizar("UTC", abrir=abrir)
    mensagem = str(info.value)
    assert "worldtimeapi" in mensagem
    assert "timeapi.io" in mensagem
    assert "sem rede" not in mensagem


def test_sincronizar_resposta_invalida_cai_para_alternativa():
    def abrir(url, timeout):
        if "worldtimeapi" in url:
            return {"erro_inesperado": True}
        return {"dateTime": "2026-10-21T15:00:00Z"}

    resultado = w.sincronizar("UTC", abrir=abrir, agora=lambda: datetime.now(timezone.utc))
    assert resultado.fonte == "timeapi.io"


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [
        (0.0, "relógio local sincronizado (desvio desprezível)"),
        (2.34, "relógio local adiantado em 2,3s"),
        (-5.0, "relógio local atrasado em 5,0s"),
    ],
)
def test_descrever_desvio(segundos, esperado):
    assert w.descrever_desvio(segundos) == esperado


# ------------------------------------------------------- cidades e listagem


def test_listar_zonas_filtra_por_texto():
    zonas = w.listar_zonas("tokyo")
    assert "Asia/Tokyo" in zonas
    assert all("tokyo" in z.lower() for z in zonas)


def test_listar_zonas_sem_filtro_traz_varias():
    assert len(w.listar_zonas()) > 100


def test_cidades_padrao_e_persistencia(tmp_path):
    caminho = tmp_path / "worldclock.json"
    padrao = w.carregar_cidades(caminho)
    assert padrao == [zona for _nome, zona in w.CIDADES_PADRAO]

    escolhidas = ["Asia/Tokyo", "Europe/Lisbon", "Asia/Tokyo", "Inválido/Nada"]
    w.salvar_cidades(escolhidas, caminho)
    assert w.carregar_cidades(caminho) == ["Asia/Tokyo", "Europe/Lisbon"]


def test_carregar_cidades_arquivo_corrompido_volta_ao_padrao(tmp_path):
    caminho = tmp_path / "worldclock.json"
    caminho.write_text("{quebrado", encoding="utf-8")
    assert w.carregar_cidades(caminho) == [zona for _nome, zona in w.CIDADES_PADRAO]


def test_nome_cidade():
    assert w.nome_cidade("America/Sao_Paulo") == "São Paulo"
    assert w.nome_cidade("America/Argentina/Buenos_Aires") == "Buenos Aires"


def test_zona_do_localtime_extrai_nome_iana(tmp_path):
    arquivo = tmp_path / "zoneinfo" / "Asia" / "Tokyo"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_text("", encoding="utf-8")
    assert w.zona_do_localtime(arquivo) == "Asia/Tokyo"


def test_zona_do_localtime_sem_marcador(tmp_path):
    arquivo = tmp_path / "localtime"
    arquivo.write_text("", encoding="utf-8")
    assert w.zona_do_localtime(arquivo) is None
