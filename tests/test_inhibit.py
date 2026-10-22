"""Testes do inibidor de suspensão (sem tocar no systemd real)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

from despertador import inhibit


def alarme(**mudancas):
    base = {
        "id": 1,
        "time": "05:00:00",
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


AGORA = datetime(2026, 10, 21, 22, 0, 0)


# --------------------------------------------------------------- decisão pura


def test_what_com_alarme_ativo():
    assert inhibit.what_para_inibir([alarme()], AGORA) == "idle:sleep:shutdown"


def test_what_com_alarme_inativo():
    assert inhibit.what_para_inibir([alarme(enabled=False)], AGORA) == ""


def test_what_lista_vazia_e_none():
    assert inhibit.what_para_inibir([], AGORA) == ""
    assert inhibit.what_para_inibir(None, AGORA) == ""


def test_what_varios_ativos_continua_inibindo():
    alarmes = [alarme(id=1, time="06:00:00"), alarme(id=2, time="07:00:00")]
    assert inhibit.what_para_inibir(alarmes, AGORA) == "idle:sleep:shutdown"


def test_what_ignora_once_ja_disparado():
    disparado = alarme(once=True, last_fired="2026-10-21T05:00:00")
    assert inhibit.what_para_inibir([disparado], AGORA) == ""


def test_what_considera_soneca_pendente():
    soneca = alarme(
        snooze_until=(AGORA + timedelta(minutes=3)).isoformat(timespec="seconds")
    )
    assert inhibit.what_para_inibir([soneca], AGORA) == "idle:sleep:shutdown"


def test_motivo_usa_proxima_ocorrencia():
    assert inhibit.motivo([alarme()], AGORA) == "Despertador: alarme às 05:00"


def test_motivo_sem_alarme_ativo():
    assert inhibit.motivo([], AGORA) == "Despertador: nenhum alarme ativo"
    assert (
        inhibit.motivo([alarme(enabled=False)], AGORA)
        == "Despertador: nenhum alarme ativo"
    )


def test_motivo_escolhe_o_mais_proximo_entre_varios():
    alarmes = [alarme(id=1, time="09:00:00"), alarme(id=2, time="06:30:00")]
    assert inhibit.motivo(alarmes, AGORA) == "Despertador: alarme às 06:30"


@pytest.mark.parametrize(
    ("alarmes", "inibido", "esperado"),
    [
        ([alarme()], False, "iniciar"),
        ([alarme()], True, "manter"),
        ([alarme(enabled=False)], True, "parar"),
        ([], False, "manter"),
    ],
)
def test_decidir_inibicao(alarmes, inibido, esperado):
    assert inhibit.decidir_inibicao(alarmes, AGORA, inibido) == esperado


def test_texto_estado():
    assert inhibit.texto_estado([], AGORA) == "PC pode dormir (nenhum alarme ativo)"
    texto = inhibit.texto_estado([alarme()], AGORA)
    assert texto.startswith("🔒 PC não vai dormir até")
    assert "05:00" in texto


# ------------------------------------------------------- processo (fake script)


def _esperar_morrer(pid: int, limite: float = 3.0) -> bool:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _script(tmp_path, corpo: str) -> str:
    caminho = tmp_path / "fake-systemd-inhibit.sh"
    caminho.write_text("#!/bin/sh\n" + corpo + "\n", encoding="utf-8")
    caminho.chmod(0o755)
    return str(caminho)


def test_inibidor_inicia_e_parar_encerra(tmp_path):
    script = _script(tmp_path, "exec sleep 30")
    inibidor = inhibit.InibidorSono([alarme()], AGORA, comando_base=[script])

    assert inibidor.iniciar() is True
    assert inibidor.ativo is True
    pid = inibidor.pid
    assert pid is not None and _vivo(pid)

    inibidor.parar()

    assert inibidor.ativo is False
    assert inibidor.pid is None
    assert _esperar_morrer(pid), f"processo {pid} continuou vivo depois de parar()"


def test_parar_mata_tambem_o_filho_sem_orfao(tmp_path):
    pid_file = tmp_path / "filho.pid"
    script = _script(
        tmp_path,
        f'sleep 30 &\necho $! > "{pid_file}"\nwait',
    )
    inibidor = inhibit.InibidorSono([alarme()], AGORA, comando_base=[script])
    assert inibidor.iniciar() is True

    fim = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < fim:
        time.sleep(0.02)
    assert pid_file.exists(), "o script fake não registrou o PID do filho"
    filho = int(pid_file.read_text().strip())
    assert _vivo(filho)

    inibidor.parar()

    assert _esperar_morrer(filho), f"filho {filho} ficou órfão depois de parar()"


def test_iniciar_sem_alarme_ativo_nao_cria_processo(tmp_path):
    script = _script(tmp_path, "exec sleep 30")
    inibidor = inhibit.InibidorSono([alarme(enabled=False)], AGORA, comando_base=[script])
    assert inibidor.iniciar() is False
    assert inibidor.ativo is False
    assert inibidor.pid is None


def test_iniciar_reinicia_quando_motivo_muda(tmp_path):
    script = _script(tmp_path, "exec sleep 30")
    inibidor = inhibit.InibidorSono([alarme(time="05:00:00")], AGORA, comando_base=[script])
    assert inibidor.iniciar() is True
    primeiro_pid = inibidor.pid

    assert inibidor.iniciar([alarme(time="06:00:00")]) is True
    assert inibidor.pid != primeiro_pid
    assert _esperar_morrer(primeiro_pid)
    assert "06:00" in inibidor.motivo_atual
    inibidor.parar()
    assert inibidor.ativo is False


def test_sem_systemd_inhibit_degrada_com_aviso(monkeypatch):
    avisos: list[str] = []
    monkeypatch.setattr(inhibit.shutil, "which", lambda _nome: None)
    inibidor = inhibit.InibidorSono([alarme()], AGORA, aviso=avisos.append)

    assert inibidor.iniciar() is False
    assert inibidor.ativo is False
    assert avisos and "systemd-inhibit" in avisos[0]


def test_context_manager_libera(tmp_path):
    script = _script(tmp_path, "exec sleep 30")
    with inhibit.InibidorSono([alarme()], AGORA, comando_base=[script]) as inibidor:
        assert inibidor.ativo is True
        pid = inibidor.pid
    assert inibidor.ativo is False
    assert _esperar_morrer(pid)
