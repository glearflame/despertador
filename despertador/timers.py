"""Cronômetro e temporizador regressivo puros, sem tkinter.

Os widgets apenas chamam estes métodos e desenham o resultado. O relógio
interno é injetável (`relogio=`), o que permite testar sem esperar.
"""

from __future__ import annotations

import time


def formatar_tempo(segundos: float) -> str:
    """Formata segundos como "MM:SS" ou "HH:MM:SS" quando há horas.

    3661 -> "01:01:01"; 65 -> "01:05"; valores negativos viram "00:00".
    """
    total = int(max(0.0, float(segundos)))
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas:02d}:{minutos:02d}:{seg:02d}"
    return f"{minutos:02d}:{seg:02d}"


def parse_duracao(texto: str) -> float:
    """Converte "90", "05:00" ou "01:02:03" em segundos.

    Um número sem dois-pontos é interpretado como segundos.
    """
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("duração vazia: use MM:SS ou HH:MM:SS")
    valor = texto.strip()
    partes = valor.split(":")
    if len(partes) > 3:
        raise ValueError(f"duração inválida: {texto!r}")
    try:
        numeros = [int(parte) for parte in partes]
    except ValueError as erro:
        raise ValueError(f"duração inválida: {texto!r}") from erro
    if any(numero < 0 for numero in numeros):
        raise ValueError(f"duração inválida: {texto!r}")

    if len(numeros) == 1:
        total = numeros[0]
    elif len(numeros) == 2:
        minutos, seg = numeros
        if seg > 59:
            raise ValueError(f"duração inválida: {texto!r}")
        total = minutos * 60 + seg
    else:
        horas, minutos, seg = numeros
        if minutos > 59 or seg > 59:
            raise ValueError(f"duração inválida: {texto!r}")
        total = horas * 3600 + minutos * 60 + seg
    if total <= 0:
        raise ValueError("a duração deve ser maior que zero")
    return float(total)


class Cronometro:
    """Cronômetro com iniciar/pausar/zerar e voltas."""

    def __init__(self, relogio=time.monotonic):
        self._relogio = relogio
        self._inicio: float | None = None
        self._acumulado = 0.0
        self.voltas: list[float] = []

    def _hora(self, agora: float | None) -> float:
        return self._relogio() if agora is None else float(agora)

    @property
    def rodando(self) -> bool:
        return self._inicio is not None

    def iniciar(self, agora: float | None = None) -> None:
        if self.rodando:
            return
        self._inicio = self._hora(agora)

    def pausar(self, agora: float | None = None) -> None:
        if not self.rodando:
            return
        assert self._inicio is not None
        self._acumulado += self._hora(agora) - self._inicio
        self._inicio = None

    def alternar(self, agora: float | None = None) -> None:
        if self.rodando:
            self.pausar(agora)
        else:
            self.iniciar(agora)

    def zerar(self) -> None:
        self._inicio = None
        self._acumulado = 0.0
        self.voltas.clear()

    def decorrido(self, agora: float | None = None) -> float:
        total = self._acumulado
        if self.rodando:
            assert self._inicio is not None
            total += self._hora(agora) - self._inicio
        return max(0.0, total)

    def volta(self, agora: float | None = None) -> float:
        valor = self.decorrido(agora)
        self.voltas.append(valor)
        return valor


class Temporizador:
    """Contagem regressiva com iniciar/pausar/retomar/zerar."""

    def __init__(self, relogio=time.monotonic):
        self._relogio = relogio
        self.total = 0.0
        self._restante_base = 0.0
        self._inicio: float | None = None
        self._avisado = False

    def _hora(self, agora: float | None) -> float:
        return self._relogio() if agora is None else float(agora)

    @property
    def rodando(self) -> bool:
        return self._inicio is not None

    @property
    def iniciado(self) -> bool:
        return self.total > 0.0

    def iniciar(self, segundos: float, agora: float | None = None) -> None:
        if segundos <= 0:
            raise ValueError("o temporizador precisa de uma duração positiva")
        self.total = float(segundos)
        self._restante_base = self.total
        self._inicio = self._hora(agora)
        self._avisado = False

    def pausar(self, agora: float | None = None) -> None:
        if not self.rodando:
            return
        self._restante_base = self.restante(agora)
        self._inicio = None

    def retomar(self, agora: float | None = None) -> None:
        if self.rodando or self.total <= 0 or self._restante_base <= 0:
            return
        self._inicio = self._hora(agora)

    def alternar(self, agora: float | None = None) -> None:
        if self.rodando:
            self.pausar(agora)
        else:
            self.retomar(agora)

    def zerar(self) -> None:
        self.total = 0.0
        self._restante_base = 0.0
        self._inicio = None
        self._avisado = False

    def restante(self, agora: float | None = None) -> float:
        if self._inicio is None:
            return max(0.0, self._restante_base)
        gasto = self._hora(agora) - self._inicio
        return max(0.0, self._restante_base - gasto)

    def atingiu_zero(self, agora: float | None = None) -> bool:
        """True uma única vez, quando a contagem chega a zero."""
        if self._avisado or self.total <= 0:
            return False
        if self.restante(agora) > 0:
            return False
        self._inicio = None
        self._restante_base = 0.0
        self._avisado = True
        return True
