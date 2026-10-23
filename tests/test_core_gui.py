"""Testes das operações de alto nível do núcleo usadas pela GUI."""

from __future__ import annotations

import pytest

from despertador import core


def test_criar_alarme_normaliza_e_valida():
    alarme = core.criar_alarme("7:05", label="acordar", days=[2, 0, 2], snooze_minutes=0)
    assert alarme["time"] == "07:05:00"
    assert alarme["days"] == [0, 2]
    assert alarme["snooze_minutes"] == 1
    assert alarme["enabled"] is True

    with pytest.raises(ValueError):
        core.criar_alarme("25:00")
    with pytest.raises(ValueError):
        core.criar_alarme("07:00", days=[7])


def test_adicionar_editar_alternar_e_remover(tmp_path):
    caminho = tmp_path / "alarms.json"
    novo = core.adicionar_alarme("07:30", label="teste", days=[0, 1], caminho=caminho)
    assert novo["id"] == 1

    segundo = core.adicionar_alarme("08:00", caminho=caminho)
    assert segundo["id"] == 2

    editado = core.atualizar_alarme(1, hora="06:15", label="novo", caminho=caminho)
    assert editado["time"] == "06:15:00"
    assert editado["label"] == "novo"
    assert editado["days"] == [0, 1]

    desativado = core.alternar_alarme(1, ativo=False, caminho=caminho)
    assert desativado["enabled"] is False
    assert desativado["snooze_until"] is None

    invertido = core.alternar_alarme(1, caminho=caminho)
    assert invertido["enabled"] is True

    assert core.remover_alarme(2, caminho=caminho) is True
    assert core.remover_alarme(99, caminho=caminho) is False
    assert [a["id"] for a in core.carregar_alarmes(caminho)["alarms"]] == [1]


def test_atualizar_alarme_inexistente_levanta_keyerror(tmp_path):
    with pytest.raises(KeyError):
        core.atualizar_alarme(42, hora="07:00", caminho=tmp_path / "alarms.json")


def test_atualizar_alarme_permite_limpar_dias_e_som(tmp_path):
    caminho = tmp_path / "alarms.json"
    core.adicionar_alarme("07:30", days=[0, 2], sound="/tmp/a.ogg", caminho=caminho)
    alvo = core.atualizar_alarme(1, days=None, sound=None, caminho=caminho)
    assert alvo["days"] is None
    assert alvo["sound"] is None
    assert core.carregar_alarmes(caminho)["alarms"][0]["days"] is None


def test_aplicar_edicao_preserva_historico():
    alarme = core.criar_alarme("07:00", alarme_id=3)
    alarme["last_fired"] = "2026-10-20T07:00:00"
    alarme["snooze_until"] = "2026-10-20T07:05:00"

    core.aplicar_edicao(alarme, "07:45", label="editado", days=[5])

    assert alarme["id"] == 3
    assert alarme["time"] == "07:45:00"
    assert alarme["label"] == "editado"
    assert alarme["days"] == [5]
    assert alarme["last_fired"] == "2026-10-20T07:00:00"
    assert alarme["snooze_until"] == "2026-10-20T07:05:00"


def test_tocador_som_usa_campainha_quando_nao_ha_player(monkeypatch):
    monkeypatch.setattr(core, "escolher_som", lambda _custom: None)
    monkeypatch.setattr(core, "escolher_player", lambda _arquivo: None)

    toques = []
    tocador = core.TocadorSom(campainha=lambda: toques.append(1))
    assert tocador.usando_campainha is True

    tocador.iniciar()
    assert tocador.tocando is True
    assert len(toques) == 1

    tocador.atualizar(forcar_campainha=True)
    assert len(toques) == 2

    tocador.parar()
    assert tocador.tocando is False
    tocador.atualizar(forcar_campainha=True)
    assert len(toques) == 2


def test_tocador_som_com_player_falso(monkeypatch):
    class ProcessoFalso:
        def __init__(self):
            self.encerrado = False

        def poll(self):
            return 0 if self.encerrado else None

        def terminate(self):
            self.encerrado = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.encerrado = True

    criados = []
    monkeypatch.setattr(core, "escolher_som", lambda _custom: "/tmp/som.oga")
    monkeypatch.setattr(core, "escolher_player", lambda _arquivo: ["paplay", "x"])

    def fake_popen(*args, **kwargs):
        processo = ProcessoFalso()
        criados.append(processo)
        return processo

    monkeypatch.setattr(core.subprocess, "Popen", fake_popen)

    tocador = core.TocadorSom()
    assert tocador.usando_campainha is False
    tocador.iniciar()
    assert len(criados) == 1
    tocador.atualizar()  # processo ainda ativo: não recria
    assert len(criados) == 1

    criados[0].encerrado = True
    tocador.atualizar()  # processo terminou: recria para manter o loop
    assert len(criados) == 2

    tocador.parar()
    assert tocador.tocando is False


def test_config_gui_padrao_quando_arquivo_nao_existe(tmp_path):
    assert core.carregar_config_gui(tmp_path / "gui.json") == {"tema": "claro"}


def test_config_gui_salva_e_carrega_tema(tmp_path):
    caminho = tmp_path / "config" / "gui.json"
    core.salvar_config_gui({"tema": "escuro"}, caminho)
    assert core.carregar_config_gui(caminho) == {"tema": "escuro"}

    core.salvar_config_gui({"tema": "claro"}, caminho)
    assert core.carregar_config_gui(caminho) == {"tema": "claro"}


def test_config_gui_rejeita_tema_invalido(tmp_path):
    with pytest.raises(ValueError):
        core.salvar_config_gui({"tema": "neon"}, tmp_path / "gui.json")
