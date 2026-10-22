"""Inibidor do logind: impede suspender/hibernar/desligar com alarme armado.

A garantia vem de um processo `systemd-inhibit` mantido em primeiro plano
enquanto existir pelo menos um alarme ativo. Nada aqui altera configuração do
sistema; ao liberar, o processo é encerrado e o PC volta a poder dormir.

As funções de decisão (`what_para_inibir`, `motivo`, `decidir_inibicao` e
`texto_estado`) são puras para poderem ser testadas sem tocar no sistema.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime

from .core import DEVNULL, formatar_proxima, proxima_geral, proxima_ocorrencia

WHAT_ALARME_ATIVO = "idle:sleep:shutdown"
WHO_PADRAO = "Despertador"


# ---------------------------------------------------------------------------
# Decisão pura (testável sem systemd)
# ---------------------------------------------------------------------------


def alarme_armado(alarme: dict, agora: datetime | None = None) -> bool:
    """True se o alarme ainda tem uma ocorrência futura (ou soneca pendente)."""
    return proxima_ocorrencia(alarme, agora or datetime.now()) is not None


def what_para_inibir(
    alarmes: list[dict] | None, agora: datetime | None = None
) -> str:
    """`"idle:sleep:shutdown"` se há alarme armado; string vazia se não há."""
    agora = agora or datetime.now()
    for alarme in alarmes or []:
        if alarme_armado(alarme, agora):
            return WHAT_ALARME_ATIVO
    return ""


def motivo(alarmes: list[dict] | None, agora: datetime | None = None) -> str:
    """Texto curto para `--why`, referente à próxima ocorrência."""
    agora = agora or datetime.now()
    proximo = proxima_geral(alarmes or [], agora)
    if proximo is None:
        return "Despertador: nenhum alarme ativo"
    _alarme, quando = proximo
    return f"Despertador: alarme às {quando.strftime('%H:%M')}"


def decidir_inibicao(
    alarmes: list[dict] | None, agora: datetime | None = None, inibido: bool = False
) -> str:
    """Decide o que fazer com o inhibitor: "iniciar", "parar" ou "manter"."""
    deve = bool(what_para_inibir(alarmes, agora))
    if deve and not inibido:
        return "iniciar"
    if not deve and inibido:
        return "parar"
    return "manter"


def texto_estado(alarmes: list[dict] | None, agora: datetime | None = None) -> str:
    """Frase para exibir na interface sobre o estado de suspensão do PC."""
    agora = agora or datetime.now()
    proximo = proxima_geral(alarmes or [], agora)
    if proximo is None:
        return "PC pode dormir (nenhum alarme ativo)"
    _alarme, quando = proximo
    return f"🔒 PC não vai dormir até {formatar_proxima(quando, agora)}"


# ---------------------------------------------------------------------------
# Processo inibidor
# ---------------------------------------------------------------------------


class InibidorSono:
    """Mantém `systemd-inhibit ... sleep infinity` vivo enquanto necessário.

    O processo roda em uma sessão própria (`start_new_session=True`) para que
    `parar()` consiga encerrar também o filho (`sleep`) via grupo de processos,
    sem deixar órfãos.
    """

    def __init__(
        self,
        alarmes: list[dict] | None = None,
        agora: datetime | None = None,
        comando_base: list[str] | None = None,
        who: str = WHO_PADRAO,
        aviso=None,
    ):
        self.alarmes = list(alarmes or [])
        self.agora = agora
        self.comando_base = list(comando_base) if comando_base else None
        self.who = who
        self.aviso = aviso

        self._processo: subprocess.Popen | None = None
        self._what = ""
        self._motivo = ""

    # -- estado ---------------------------------------------------------

    @property
    def ativo(self) -> bool:
        """True se o subprocesso inibidor existe e está vivo."""
        return self._processo is not None and self._processo.poll() is None

    @property
    def pid(self) -> int | None:
        return self._processo.pid if self._processo is not None else None

    @property
    def what(self) -> str:
        return self._what

    @property
    def motivo_atual(self) -> str:
        return self._motivo

    # -- montagem do comando --------------------------------------------

    def _avisar(self, mensagem: str) -> None:
        if self.aviso is not None:
            try:
                self.aviso(mensagem)
            except Exception:
                pass
        print(f"aviso: {mensagem}", file=sys.stderr)

    def _executavel(self) -> list[str] | None:
        if self.comando_base:
            return list(self.comando_base)
        caminho = shutil.which("systemd-inhibit")
        if caminho is None:
            return None
        return [caminho]

    def montar_comando(self) -> list[str] | None:
        base = self._executavel()
        if base is None:
            return None
        return [
            *base,
            f"--what={self._what}",
            f"--who={self.who}",
            f"--why={self._motivo}",
            "--mode=block",
            "sleep",
            "infinity",
        ]

    # -- ciclo de vida ---------------------------------------------------

    def iniciar(
        self, alarmes: list[dict] | None = None, agora: datetime | None = None
    ) -> bool:
        """Adquire (ou mantém) o inhibitor. Devolve True se está ativo."""
        if alarmes is not None:
            self.alarmes = list(alarmes)
        if agora is not None:
            self.agora = agora

        what = what_para_inibir(self.alarmes, self.agora)
        if not what:
            self.parar()
            return False

        novo_motivo = motivo(self.alarmes, self.agora)
        if self.ativo:
            if what == self._what and novo_motivo == self._motivo:
                return True
            # Estado dos alarmes mudou: reinicia para atualizar o --why.
            self.parar()

        self._what = what
        self._motivo = novo_motivo
        comando = self.montar_comando()
        if comando is None:
            self._what = ""
            self._motivo = ""
            self._avisar(
                "systemd-inhibit não encontrado; o PC pode suspender/hibernar "
                "mesmo com alarme ativo"
            )
            return False

        try:
            self._processo = subprocess.Popen(
                comando,
                stdout=DEVNULL,
                stderr=DEVNULL,
                start_new_session=True,
            )
        except OSError as erro:
            self._processo = None
            self._what = ""
            self._motivo = ""
            self._avisar(f"não consegui iniciar o inibidor de suspensão: {erro}")
            return False
        return True

    def parar(self) -> None:
        """Encerra o inibidor (e o filho `sleep`) sem deixar processo órfão."""
        processo = self._processo
        self._processo = None
        self._what = ""
        self._motivo = ""
        if processo is None:
            return

        if processo.poll() is None:
            self._sinalizar(processo, signal.SIGTERM)
            try:
                processo.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._sinalizar(processo, signal.SIGKILL)
                try:
                    processo.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        # Fecha qualquer descritor restante do Popen.
        if processo.stdout is not None:
            processo.stdout.close()
        if processo.stderr is not None:
            processo.stderr.close()

    @staticmethod
    def _sinalizar(processo: subprocess.Popen, sinal: int) -> None:
        """Sinaliza o grupo do processo; cai para `terminate/kill` se preciso."""
        try:
            os.killpg(os.getpgid(processo.pid), sinal)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                if sinal == signal.SIGKILL:
                    processo.kill()
                else:
                    processo.terminate()
            except (ProcessLookupError, OSError):
                pass

    # -- context manager -------------------------------------------------

    def __enter__(self) -> InibidorSono:
        self.iniciar()
        return self

    def __exit__(self, *_exc) -> None:
        self.parar()
