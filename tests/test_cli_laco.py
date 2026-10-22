"""Testes do laço da CLI (`run` e `inhibit`) com relógio/som/inhibitor falsos.

O bug que originou estes testes: `inhibit` mantinha o PC acordado até o
horário do alarme, mas ao chegar a hora apenas imprimia uma mensagem e saía —
sem tocar nada. Os testes abaixo exigem que o som seja acionado de verdade.
"""

from __future__ import annotations

import importlib.util
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

CAMINHO_CLI = Path(__file__).resolve().parents[1] / "despertador.py"
AGORA = datetime(2026, 10, 22, 5, 0, 2)


@pytest.fixture
def cli():
    """Carrega `despertador.py` (script) sem confundir com o pacote homônimo."""
    spec = importlib.util.spec_from_file_location("despertador_cli", CAMINHO_CLI)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def alarme(**mudancas):
    base = {
        "id": 1,
        "time": "05:00:00",
        "label": "acordar",
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


class InibidorFalso:
    """Registra só as transições, para provar quando o inhibitor foi liberado."""

    def __init__(self, *_args, **_kwargs):
        self.ativo = False
        self.historico: list[str] = []
        # Compatibilidade com a API real (o código antigo imprimia estes campos).
        self.pid = 4242
        self.motivo_atual = "Despertador: alarme às 05:00"

    def iniciar(self, alarmes=None, agora=None) -> bool:
        if not self.ativo:
            self.historico.append("iniciar")
        self.ativo = True
        return True

    def parar(self) -> None:
        if self.ativo:
            self.historico.append("parar")
        self.ativo = False


class DatetimeFalso(datetime):
    """`datetime` com `now()` fixo, para o código que chama `datetime.now()`."""

    @classmethod
    def now(cls, tz=None):
        return AGORA


class RelogioFalso:
    def __init__(self, agora: datetime):
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora


class FimDoTeste(Exception):
    """Interrompe o laço infinito nos testes."""


def _preparar(tmp_path, monkeypatch, cli, alarmes):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cli.salvar_alarmes({"next_id": max(a["id"] for a in alarmes) + 1, "alarms": alarmes})
    monkeypatch.setattr(cli, "datetime", DatetimeFalso)
    # `raising=False`: no código antigo a função não existia (usava isatty direto).
    monkeypatch.setattr(cli, "_entrada_interativa", lambda: False, raising=False)

    def dormir_proibido(_segundos):
        raise AssertionError("o laço ficou esperando em vez de tocar o alarme")

    # O alarme já está vencido: não deveria haver nenhuma espera.
    monkeypatch.setattr(cli, "time", types.SimpleNamespace(sleep=dormir_proibido))
    inibidor = InibidorFalso()
    monkeypatch.setattr(cli.inhibit, "InibidorSono", lambda *a, **k: inibidor)
    return inibidor


def test_inhibit_toca_o_alarme_ao_chegar_a_hora(cli, tmp_path, monkeypatch):
    """Regressão do bug: o inhibitor não pode "chegar e sair" em silêncio."""
    inibidor = _preparar(tmp_path, monkeypatch, cli, [alarme()])

    tocados = []

    def tocar_falso(_alarme, ocorrencia, ring_seconds, snooze, perdido, interativo):
        tocados.append(
            {
                "ocorrencia": ocorrencia,
                "ring_seconds": ring_seconds,
                "perdido": perdido,
                "interativo": interativo,
                "inibidor_ativo": inibidor.ativo,
            }
        )
        return "parar", snooze

    monkeypatch.setattr(cli, "tocar", tocar_falso)

    assert cli.main(["inhibit"]) == 0

    assert len(tocados) == 1, "o alarme das 05:00 não foi tocado"
    assert tocados[0]["ocorrencia"] == datetime(2026, 10, 22, 5, 0, 0)
    assert tocados[0]["ring_seconds"] == 60.0
    assert tocados[0]["perdido"] is False
    assert tocados[0]["interativo"] is False
    assert tocados[0]["inibidor_ativo"] is True, "tocou sem o PC estar protegido"
    assert inibidor.historico == ["iniciar", "parar"], "inhibitor não foi liberado"


def test_run_tambem_inibe_e_toca(cli, tmp_path, monkeypatch):
    """`run` é o modo principal: precisa do inhibitor e do toque."""
    inibidor = _preparar(tmp_path, monkeypatch, cli, [alarme(time="05:00:00")])
    tocados = []

    def tocar_falso(_alarme, ocorrencia, *_resto):
        tocados.append((ocorrencia, inibidor.ativo))
        return "parar", 5

    monkeypatch.setattr(cli, "tocar", tocar_falso)

    assert cli.main(["run", "--ring-seconds", "1"]) == 0
    assert tocados == [(datetime(2026, 10, 22, 5, 0, 0), True)]
    assert inibidor.historico == ["iniciar", "parar"]


def test_soneca_mantem_inibidor_e_parar_libera(cli, tmp_path, monkeypatch):
    """Soneca não libera o PC; sem alarme pendente o inhibitor é liberado."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cli.salvar_alarmes({"next_id": 2, "alarms": [alarme(once=True)]})

    relogio = RelogioFalso(datetime(2026, 10, 22, 5, 0, 1))

    inibidor = InibidorFalso()
    monkeypatch.setattr(cli.inhibit, "InibidorSono", lambda *a, **k: inibidor)

    toques = []
    dormidas = []

    def tocar_falso(_alarme, ocorrencia, _ring, snooze, _perdido, interativo):
        toques.append((ocorrencia, interativo, inibidor.ativo))
        return ("soneca", 5) if len(toques) == 1 else ("parar", 5)

    def registrar_falso(alarme_id, ocorrencia, acao, snooze_min):
        estado = cli.carregar_alarmes()
        alvo = cli.encontrar_alarme(estado, alarme_id)
        if acao == "soneca":
            alvo["snooze_until"] = (
                relogio.agora + timedelta(minutes=snooze_min)
            ).isoformat(timespec="seconds")
            alvo["last_fired"] = ocorrencia.isoformat(timespec="seconds")
        else:
            alvo["last_fired"] = ocorrencia.isoformat(timespec="seconds")
            alvo["snooze_until"] = None
            if alvo.get("once"):
                alvo["enabled"] = False
        cli.salvar_alarmes(estado)

    def dormir_falso(segundos):
        dormidas.append(segundos)
        if len(toques) >= 2:
            raise FimDoTeste
        relogio.agora += timedelta(seconds=segundos)

    monkeypatch.setattr(cli, "tocar", tocar_falso)
    monkeypatch.setattr(cli, "registrar_disparo", registrar_falso)

    with pytest.raises(FimDoTeste):
        cli._laco_alarmes(
            grace_hours=2.0,
            ring_seconds=60.0,
            snooze=5,
            interativo=True,
            inibidor=inibidor,
            relogio=relogio,
            dormir=dormir_falso,
        )

    assert [t[0] for t in toques] == [
        datetime(2026, 10, 22, 5, 0, 0),
        datetime(2026, 10, 22, 5, 5, 1),
    ], "a soneca não tocou de novo"
    assert all(t[2] for t in toques), "o PC ficou desprotegido durante o toque"
    assert inibidor.historico == ["iniciar", "parar"]
    assert all(0.2 <= s <= 30.0 for s in dormidas)


def test_laco_reage_a_novo_alarme_no_json(cli, tmp_path, monkeypatch):
    """Fatias de no máximo 30 s e releitura do JSON a cada volta."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cli.salvar_alarmes({"next_id": 1, "alarms": []})

    relogio = RelogioFalso(datetime(2026, 10, 22, 22, 0, 0))
    inibidor = InibidorFalso()
    dormidas = []
    tocados = []

    def dormir_falso(segundos):
        dormidas.append(segundos)
        if len(dormidas) == 1:
            cli.salvar_alarmes({"next_id": 2, "alarms": [alarme(time="22:00:10")]})
            relogio.agora = datetime(2026, 10, 22, 22, 0, 11)
        else:
            raise FimDoTeste

    def tocar_falso(_alarme, ocorrencia, *_resto):
        tocados.append(ocorrencia)
        return "parar", 5

    monkeypatch.setattr(cli, "tocar", tocar_falso)

    assert (
        cli._laco_alarmes(
            grace_hours=2.0,
            ring_seconds=5.0,
            snooze=5,
            interativo=False,
            inibidor=inibidor,
            relogio=relogio,
            dormir=dormir_falso,
        )
        == 0
    )

    assert dormidas == [30.0], "o laço deveria esperar no máximo 30 s"
    assert tocados == [datetime(2026, 10, 22, 22, 0, 10)]
    assert inibidor.historico == ["iniciar"]


def test_inhibit_status_apenas_informa(cli, tmp_path, monkeypatch, capsys):
    """`--status` continua não bloqueando e não toca nada."""
    _preparar(tmp_path, monkeypatch, cli, [alarme()])
    monkeypatch.setattr(cli, "_inibidores_do_despertador", lambda: [])
    monkeypatch.setattr(
        cli,
        "tocar",
        lambda *_a, **_k: pytest.fail("--status não deveria tocar alarme"),
    )

    assert cli.main(["inhibit", "--status"]) == 0
    saida = capsys.readouterr().out
    assert "nenhum inibidor do Despertador ativo" in saida
    assert "próximo alarme: 05:00" in saida
