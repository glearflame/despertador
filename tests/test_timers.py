"""Testes do cronômetro/temporizador e da formatação de duração (sem Tk)."""

from __future__ import annotations

import pytest

from despertador.timers import Cronometro, Temporizador, formatar_tempo, parse_duracao

# --------------------------------------------------------------- formatação


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [
        (0, "00:00"),
        (5, "00:05"),
        (65, "01:05"),
        (599, "09:59"),
        (600, "10:00"),
        (3599, "59:59"),
        (3600, "01:00:00"),
        (3661, "01:01:01"),
        (86399, "23:59:59"),
        (90061, "25:01:01"),
        (1.9, "00:01"),
        (-3, "00:00"),
    ],
)
def test_formatar_tempo(segundos, esperado):
    assert formatar_tempo(segundos) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("90", 90.0),
        ("05:00", 300.0),
        ("1:02:03", 3723.0),
        ("00:00:30", 30.0),
    ],
)
def test_parse_duracao_valida(texto, esperado):
    assert parse_duracao(texto) == esperado


@pytest.mark.parametrize("texto", ["", "  ", "abc", "1:99", "1:2:99", "1:2:3:4", "0", "-5"])
def test_parse_duracao_invalida(texto):
    with pytest.raises(ValueError):
        parse_duracao(texto)


# --------------------------------------------------------------- cronômetro


def test_cronometro_inicia_pausa_e_retoma():
    cronometro = Cronometro()
    cronometro.iniciar(agora=100.0)
    assert cronometro.rodando is True
    assert cronometro.decorrido(agora=105.0) == 5.0

    cronometro.pausar(agora=105.0)
    assert cronometro.rodando is False
    assert cronometro.decorrido(agora=500.0) == 5.0

    cronometro.iniciar(agora=500.0)
    assert cronometro.decorrido(agora=507.0) == 12.0


def test_cronometro_voltas_e_zerar():
    cronometro = Cronometro()
    cronometro.iniciar(agora=0.0)
    assert cronometro.volta(agora=10.0) == 10.0
    assert cronometro.volta(agora=25.5) == 25.5
    assert cronometro.voltas == [10.0, 25.5]

    cronometro.zerar()
    assert cronometro.decorrido(agora=99.0) == 0.0
    assert cronometro.voltas == []
    assert cronometro.rodando is False


def test_cronometro_alternar_e_idempotencia():
    cronometro = Cronometro()
    cronometro.alternar(agora=1.0)
    cronometro.iniciar(agora=2.0)  # já rodando: não reinicia
    assert cronometro.decorrido(agora=3.0) == 2.0
    cronometro.alternar(agora=3.0)
    assert cronometro.rodando is False
    cronometro.pausar(agora=10.0)  # já pausado: no-op
    assert cronometro.decorrido(agora=10.0) == 2.0


def test_cronometro_usa_relogio_injetado():
    tempo = {"agora": 0.0}
    cronometro = Cronometro(relogio=lambda: tempo["agora"])
    cronometro.iniciar()
    tempo["agora"] = 7.5
    assert cronometro.decorrido() == 7.5


# -------------------------------------------------------------- temporizador


def test_temporizador_conta_pausa_e_retoma():
    timer = Temporizador()
    timer.iniciar(10, agora=0.0)
    assert timer.rodando is True
    assert timer.restante(agora=4.0) == 6.0

    timer.pausar(agora=4.0)
    assert timer.restante(agora=100.0) == 6.0
    assert timer.atingiu_zero(agora=100.0) is False

    timer.retomar(agora=100.0)
    assert timer.restante(agora=103.0) == 3.0
    assert timer.atingiu_zero(agora=105.0) is False


def test_temporizador_avisa_uma_unica_vez_ao_zerar():
    timer = Temporizador()
    timer.iniciar(2, agora=0.0)
    assert timer.atingiu_zero(agora=1.9) is False
    assert timer.atingiu_zero(agora=2.0) is True
    assert timer.rodando is False
    assert timer.restante(agora=50.0) == 0.0
    assert timer.atingiu_zero(agora=60.0) is False


def test_temporizador_zerar_permite_nova_contagem():
    timer = Temporizador()
    timer.iniciar(60, agora=0.0)
    timer.zerar()
    assert timer.total == 0.0
    assert timer.iniciado is False
    assert timer.restante(agora=10.0) == 0.0

    timer.iniciar(5, agora=20.0)
    assert timer.atingiu_zero(agora=25.0) is True


def test_temporizador_alternar():
    timer = Temporizador()
    timer.iniciar(30, agora=0.0)
    timer.alternar(agora=10.0)  # pausa
    assert timer.rodando is False
    timer.alternar(agora=100.0)  # retoma
    assert timer.rodando is True
    assert timer.restante(agora=105.0) == 15.0


def test_temporizador_duracao_invalida():
    with pytest.raises(ValueError):
        Temporizador().iniciar(0)
    with pytest.raises(ValueError):
        Temporizador().iniciar(-1)
