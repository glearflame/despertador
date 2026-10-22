"""Testes do despertador: funções puras + persistência."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

import despertador as d


def alarme(**mudancas):
    base = {
        "id": 1,
        "time": "07:30:00",
        "label": "",
        "days": None,
        "once": False,
        "sound": None,
        "enabled": True,
        "snooze_minutes": 5,
        "last_fired": None,
        "snooze_until": None,
    }
    base.update(mudancas)
    return base


# ---------------------------------------------------------------- parse_time


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("07:30", (7, 30, 0)),
        ("7:05", (7, 5, 0)),
        ("23:59:59", (23, 59, 59)),
        ("00:00", (0, 0, 0)),
    ],
)
def test_parse_time_valido(texto, esperado):
    assert d.parse_time(texto) == esperado


@pytest.mark.parametrize("texto", ["25:00", "7", "abc", "", "   ", "07:60", "07:30:60", "1:2"])
def test_parse_time_invalido(texto):
    with pytest.raises(ValueError) as info:
        d.parse_time(texto)
    assert info.value.args[0]


# ------------------------------------------------------- proxima_ocorrencia

# 21/10/2026 é uma quarta-feira.
QUARTA = datetime(2026, 10, 21)


def test_ocorrencia_hoje_ainda_nao_passou():
    agora = QUARTA.replace(hour=6, minute=0)
    assert d.proxima_ocorrencia(alarme(), agora) == QUARTA.replace(hour=7, minute=30)


def test_ocorrencia_hoje_ja_passou_vai_para_amanha():
    agora = QUARTA.replace(hour=8, minute=0)
    assert d.proxima_ocorrencia(alarme(), agora) == datetime(2026, 10, 22, 7, 30)


def test_ocorrencia_com_dias_especificos_pula_dias_nao_listados():
    # segunda-feira (weekday 0); de quarta a próxima segunda é 26/10
    agora = QUARTA.replace(hour=6, minute=0)
    assert d.proxima_ocorrencia(alarme(days=[0]), agora) == datetime(2026, 10, 26, 7, 30)


def test_ocorrencia_hoje_com_dia_listado():
    agora = QUARTA.replace(hour=6, minute=0)
    assert d.proxima_ocorrencia(alarme(days=[2, 4]), agora) == QUARTA.replace(hour=7, minute=30)


def test_ocorrencia_once_ja_disparado_nao_tem_proxima():
    agora = QUARTA.replace(hour=6, minute=0)
    assert d.proxima_ocorrencia(alarme(once=True, last_fired="2026-10-20T07:30:00"), agora) is None


def test_ocorrencia_once_ainda_nao_disparou():
    agora = QUARTA.replace(hour=6, minute=0)
    assert d.proxima_ocorrencia(alarme(once=True), agora) == QUARTA.replace(hour=7, minute=30)


def test_ocorrencia_desabilitado():
    agora = QUARTA.replace(hour=6, minute=0)
    assert d.proxima_ocorrencia(alarme(enabled=False), agora) is None


def test_ocorrencia_soneca_em_futuro():
    agora = QUARTA.replace(hour=7, minute=20)
    soneca = QUARTA.replace(hour=7, minute=35).isoformat()
    assert d.proxima_ocorrencia(alarme(snooze_until=soneca), agora) == QUARTA.replace(
        hour=7, minute=35
    )


# --------------------------------------------------- deve_disparar_perdido


def test_perdido_dentro_da_janela():
    agora = QUARTA.replace(hour=8, minute=0)
    assert d.deve_disparar_perdido(alarme(), agora, 2.0) is True


def test_perdido_fora_da_janela():
    agora = QUARTA.replace(hour=10, minute=0)
    assert d.deve_disparar_perdido(alarme(), agora, 2.0) is False


def test_perdido_ja_disparado():
    agora = QUARTA.replace(hour=8, minute=0)
    alarme_ja = alarme(last_fired=QUARTA.replace(hour=7, minute=30).isoformat())
    assert d.deve_disparar_perdido(alarme_ja, agora, 2.0) is False


def test_perdido_respeita_dias():
    agora = QUARTA.replace(hour=8, minute=0)
    assert d.deve_disparar_perdido(alarme(days=[0]), agora, 2.0) is False


def test_perdido_desabilitado():
    agora = QUARTA.replace(hour=8, minute=0)
    assert d.deve_disparar_perdido(alarme(enabled=False), agora, 2.0) is False


def test_soneca_nao_redispara_ocorrencia_original():
    agora = QUARTA.replace(hour=7, minute=31)
    soneca = QUARTA.replace(hour=7, minute=35).isoformat()
    com_soneca = alarme(
        once=True,
        last_fired=QUARTA.replace(hour=7, minute=30).isoformat(),
        snooze_until=soneca,
    )
    assert d.deve_disparar_perdido(com_soneca, agora, 2.0) is False
    assert d.proxima_ocorrencia(com_soneca, agora) == QUARTA.replace(hour=7, minute=35)


def test_soneca_vencida_dispara_mesmo_com_once_ja_disparado():
    agora = QUARTA.replace(hour=7, minute=36)
    com_soneca = alarme(
        once=True,
        last_fired=QUARTA.replace(hour=7, minute=30).isoformat(),
        snooze_until=QUARTA.replace(hour=7, minute=35).isoformat(),
    )
    assert d.deve_disparar_perdido(com_soneca, agora, 2.0) is True


# --------------------------------------------------------------- parse_days


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("seg,ter", [0, 1]),
        ("seg ter", [0, 1]),
        ("seg, ter qua", [0, 1, 2]),
        ("dom,seg", [0, 6]),
        ("SEG", [0]),
    ],
)
def test_parse_days_valido(texto, esperado):
    assert d.parse_days(texto) == esperado


@pytest.mark.parametrize("texto", ["seg,xyz", "", "   ", "xyz"])
def test_parse_days_invalido(texto):
    with pytest.raises(ValueError) as info:
        d.parse_days(texto)
    assert info.value.args[0]


# -------------------------------------------------------------- persistência


def test_salvar_e_recarregar(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    estado = {"next_id": 2, "alarms": [alarme()]}
    d.salvar_alarmes(estado)

    caminho = tmp_path / "despertador" / "alarms.json"
    assert caminho.is_file()
    assert (tmp_path / "despertador").stat().st_mode & 0o777 == 0o700

    carregado = d.carregar_alarmes()
    assert carregado["next_id"] == 2
    assert carregado["alarms"][0]["time"] == "07:30:00"
    assert carregado["alarms"][0]["label"] == ""


def test_salvar_e_recarregar_com_dias_e_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    estado = {
        "next_id": 2,
        "alarms": [alarme(days=[0, 2], once=True, sound="/tmp/x.wav")],
    }
    d.salvar_alarmes(estado)
    carregado = d.carregar_alarmes()
    assert carregado["alarms"][0]["days"] == [0, 2]
    assert carregado["alarms"][0]["once"] is True
    assert carregado["alarms"][0]["sound"] == "/tmp/x.wav"


def test_json_corrompido_nao_derruba_e_preserva_bak(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pasta = tmp_path / "despertador"
    pasta.mkdir()
    arquivo = pasta / "alarms.json"
    arquivo.write_text("{isso nao e json", encoding="utf-8")

    estado = d.carregar_alarmes()
    assert estado == {"next_id": 1, "alarms": []}
    assert (pasta / "alarms.json.bak").read_text(encoding="utf-8") == "{isso nao e json"

    erro = capsys.readouterr().err
    assert "aviso" in erro
    assert "alarms.json.bak" in erro


def test_json_estrutura_inesperada(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pasta = tmp_path / "despertador"
    pasta.mkdir()
    (pasta / "alarms.json").write_text(json.dumps(["nada", "a", "ver"]), encoding="utf-8")

    estado = d.carregar_alarmes()
    assert estado == {"next_id": 1, "alarms": []}
    assert (pasta / "alarms.json.bak").is_file()


def test_itens_invalidos_sao_descartados(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pasta = tmp_path / "despertador"
    pasta.mkdir()
    conteudo = {
        "next_id": 5,
        "alarms": [
            {"id": 1, "time": "25:00"},  # hora inválida
            "lixo",  # nem dicionário
            {"id": 2, "time": "08:15", "days": [1, 3]},  # válido
        ],
    }
    (pasta / "alarms.json").write_text(json.dumps(conteudo), encoding="utf-8")

    estado = d.carregar_alarmes()
    assert [a["id"] for a in estado["alarms"]] == [2]
    assert estado["next_id"] == 5
