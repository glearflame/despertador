"""Núcleo do despertador: parsing, ocorrências, persistência e som.

Este módulo não conhece interface: serve tanto à CLI (`despertador.py`) quanto
à GUI (`despertador_gui.py`). Tudo aqui é biblioteca padrão.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

__version__ = "1.3.0"

APP_NAME = "despertador"
ARQUIVO_ALARMES = "alarms.json"
ARQUIVO_CONFIG_GUI = "gui.json"

DIAS_CURTOS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
DIAS_ALIASES = {
    "seg": 0,
    "segunda": 0,
    "ter": 1,
    "terca": 1,
    "terça": 1,
    "qua": 2,
    "quarta": 2,
    "qui": 3,
    "quinta": 3,
    "sex": 4,
    "sexta": 4,
    "sab": 5,
    "sáb": 5,
    "sábado": 5,
    "sabado": 5,
    "dom": 6,
    "domingo": 6,
}

# Sons padrão testados em ordem, caso nenhum --sound seja informado.
SONS_PADRAO = [
    "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "/usr/share/sounds/alsa/Front_Center.wav",
]

HORA_RE = re.compile(r"^(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?::(?P<s>[0-5]\d))?$")

DEVNULL = subprocess.DEVNULL

# Sentinela para distinguir "não alterar" de "definir como None" na edição.
NAO_INFORMADO = object()


# ---------------------------------------------------------------------------
# Parsing de horário e dias
# ---------------------------------------------------------------------------


def parse_time(texto: str) -> tuple[int, int, int]:
    """Converte "HH:MM" ou "HH:MM:SS" em (hora, minuto, segundo).

    Levanta ValueError com mensagem clara para entradas inválidas.
    """
    if texto is None:
        raise ValueError("horário vazio: use HH:MM ou HH:MM:SS")
    valor = texto.strip()
    if not valor:
        raise ValueError("horário vazio: use HH:MM ou HH:MM:SS")
    achado = HORA_RE.match(valor)
    if not achado:
        raise ValueError(
            f"horário inválido: {texto!r}. Use HH:MM (ex.: 07:30) ou HH:MM:SS (ex.: 07:30:15)"
        )
    hora = int(achado.group("h"))
    minuto = int(achado.group("m"))
    segundo = int(achado.group("s") or 0)
    return hora, minuto, segundo


def formatar_hora(alarme: dict) -> str:
    """Devolve o horário do alarme no formato usado em `list`."""
    h, m, s = parse_time(alarme.get("time", ""))
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_days(texto: str) -> list[int]:
    """Converte "seg,ter" ou "seg ter" numa lista ordenada de inteiros (0=segunda)."""
    if not texto or not texto.strip():
        raise ValueError("lista de dias vazia: informe algo como seg,ter,qua")
    partes = [p for p in re.split(r"[,;\s]+", texto.strip()) if p]
    if not partes:
        raise ValueError("lista de dias vazia: informe algo como seg,ter,qua")
    dias: list[int] = []
    for parte in partes:
        chave = parte.strip().lower().rstrip(".")
        if not chave:
            continue
        if chave not in DIAS_ALIASES:
            validos = ", ".join(DIAS_CURTOS)
            raise ValueError(f"dia inválido: {parte!r}. Use: {validos}")
        dia = DIAS_ALIASES[chave]
        if dia not in dias:
            dias.append(dia)
    if not dias:
        raise ValueError("nenhum dia válido informado")
    return sorted(dias)


def formatar_dias(alarme: dict) -> str:
    """Texto curto com os dias do alarme."""
    dias = alarme.get("days")
    if not dias:
        return "todos"
    if len(dias) >= 7:
        return "todos"
    return ",".join(DIAS_CURTOS[d] for d in sorted(dias))


# ---------------------------------------------------------------------------
# Cálculo de ocorrências
# ---------------------------------------------------------------------------


def _dias_do_alarme(alarme: dict) -> set[int] | None:
    dias = alarme.get("days")
    if not dias:
        return None
    return set(dias)


def _parse_dt(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _ocorrencia_na_janela(alarme: dict, agora: datetime, grace_hours: float) -> datetime | None:
    """Ocorrência agendada que já passou mas ainda está dentro da tolerância."""
    if not alarme.get("enabled", True):
        return None

    janela = timedelta(hours=grace_hours)
    snooze = _parse_dt(alarme.get("snooze_until"))
    if snooze is not None and snooze <= agora and agora - snooze <= janela:
        return snooze
    if alarme.get("once") and alarme.get("last_fired"):
        return None

    h, m, s = parse_time(alarme.get("time", ""))
    dias = _dias_do_alarme(alarme)
    limite = 2 + int(grace_hours // 24) + 1
    ultimo_disparo = _parse_dt(alarme.get("last_fired"))

    for offset in range(limite):
        dia = agora.date() - timedelta(days=offset)
        if dias is not None and dia.weekday() not in dias:
            continue
        ocorrencia = datetime.combine(dia, dtime(h, m, s))
        if ocorrencia > agora:
            continue
        if agora - ocorrencia > janela:
            return None
        if ultimo_disparo is not None and ultimo_disparo >= ocorrencia:
            return None
        return ocorrencia
    return None


def ocorrencia_pendente(
    alarme: dict, agora: datetime, grace_hours: float = 2.0
) -> datetime | None:
    """Ocorrência vencida dentro da janela de tolerância (API pública)."""
    return _ocorrencia_na_janela(alarme, agora, grace_hours)


def proxima_ocorrencia(alarme: dict, agora: datetime) -> datetime | None:
    """Próxima vez que o alarme deve tocar (estritamente depois de `agora`)."""
    if not alarme.get("enabled", True):
        return None

    snooze = _parse_dt(alarme.get("snooze_until"))
    if snooze is not None and snooze > agora:
        return snooze
    if alarme.get("once") and alarme.get("last_fired"):
        return None

    h, m, s = parse_time(alarme.get("time", ""))
    dias = _dias_do_alarme(alarme)

    for offset in range(0, 8):
        dia = agora.date() + timedelta(days=offset)
        if dias is not None and dia.weekday() not in dias:
            continue
        candidato = datetime.combine(dia, dtime(h, m, s))
        if candidato > agora:
            return candidato
    return None


def deve_disparar_perdido(alarme: dict, agora: datetime, grace_hours: float) -> bool:
    """True se existe ocorrência vencida dentro da janela de tolerância."""
    return _ocorrencia_na_janela(alarme, agora, grace_hours) is not None


def proxima_geral(alarmes: list[dict], agora: datetime) -> tuple[dict, datetime] | None:
    """Alarme habilitado mais próximo."""
    melhor: tuple[dict, datetime] | None = None
    for alarme in alarmes:
        quando = proxima_ocorrencia(alarme, agora)
        if quando is None:
            continue
        if melhor is None or quando < melhor[1]:
            melhor = (alarme, quando)
    return melhor


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------


def config_dir() -> Path:
    """Diretório de configuração, respeitando $XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    raiz = Path(base).expanduser() if base else Path.home() / ".config"
    return raiz / APP_NAME


def arquivo_alarmes() -> Path:
    return config_dir() / ARQUIVO_ALARMES


def arquivo_config_gui() -> Path:
    """Preferências da GUI (tema etc.), no mesmo diretório dos alarmes."""
    return config_dir() / ARQUIVO_CONFIG_GUI


def carregar_config_gui(caminho: Path | None = None) -> dict:
    """Lê as preferências da GUI; devolve o padrão se o arquivo faltar."""
    arquivo = caminho or arquivo_config_gui()
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tema": "claro"}
    if not isinstance(bruto, dict):
        return {"tema": "claro"}
    tema = bruto.get("tema")
    if tema not in ("claro", "escuro"):
        tema = "claro"
    return {"tema": tema}


def salvar_config_gui(config: dict, caminho: Path | None = None) -> None:
    """Grava as preferências da GUI de forma atômica."""
    arquivo = caminho or arquivo_config_gui()
    tema = config.get("tema", "claro")
    if tema not in ("claro", "escuro"):
        raise ValueError(f"tema inválido: {tema!r} (use 'claro' ou 'escuro')")
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_name(arquivo.name + ".tmp")
    temporario.write_text(
        json.dumps({"tema": tema}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporario, arquivo)


def _estado_vazio() -> dict:
    return {"next_id": 1, "alarms": []}


def _sanitizar_alarme(bruto: dict) -> dict | None:
    """Valida um item vindo do JSON; devolve None se for irrecuperável."""
    if not isinstance(bruto, dict):
        return None
    try:
        parse_time(str(bruto.get("time", "")))
    except ValueError:
        return None
    try:
        identificador = int(bruto.get("id"))
    except (TypeError, ValueError):
        return None

    dias = bruto.get("days")
    dias_ok: list[int] | None = None
    if dias:
        try:
            dias_ok = sorted({int(d) for d in dias if 0 <= int(d) <= 6}) or None
        except (TypeError, ValueError):
            dias_ok = None

    return {
        "id": identificador,
        "time": str(bruto["time"]),
        "label": str(bruto.get("label") or ""),
        "days": dias_ok,
        "once": bool(bruto.get("once", False)),
        "sound": bruto.get("sound") or None,
        "enabled": bool(bruto.get("enabled", True)),
        "snooze_minutes": _int_ou(bruto.get("snooze_minutes"), 5),
        "last_fired": bruto.get("last_fired") or None,
        "snooze_until": bruto.get("snooze_until") or None,
    }


def _int_ou(valor, padrao: int) -> int:
    try:
        return max(1, int(valor))
    except (TypeError, ValueError):
        return padrao


def carregar_alarmes(caminho: Path | None = None) -> dict:
    """Lê o JSON de alarmes. Corrompido -> avisa, guarda .bak e começa vazio."""
    arquivo = caminho or arquivo_alarmes()
    if not arquivo.exists():
        return _estado_vazio()

    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        _preservar_corrompido(arquivo, erro)
        return _estado_vazio()

    if not isinstance(bruto, dict) or not isinstance(bruto.get("alarms", []), list):
        _preservar_corrompido(arquivo, ValueError("estrutura inesperada"))
        return _estado_vazio()

    estado = _estado_vazio()
    for item in bruto.get("alarms", []):
        limpo = _sanitizar_alarme(item)
        if limpo is not None:
            estado["alarms"].append(limpo)

    max_id = max((a["id"] for a in estado["alarms"]), default=0)
    try:
        next_id = int(bruto.get("next_id", max_id + 1))
    except (TypeError, ValueError):
        next_id = max_id + 1
    estado["next_id"] = max(next_id, max_id + 1)
    return estado


def _preservar_corrompido(arquivo: Path, erro: Exception) -> None:
    aviso = f"aviso: não consegui ler {arquivo} ({erro}); começando com lista vazia"
    print(aviso, file=sys.stderr)
    try:
        backup = arquivo.with_name(arquivo.name + ".bak")
        shutil.copy2(arquivo, backup)
        print(f"aviso: conteúdo antigo preservado em {backup}", file=sys.stderr)
    except OSError as erro_backup:
        print(f"aviso: falha ao criar backup: {erro_backup}", file=sys.stderr)


def salvar_alarmes(estado: dict, caminho: Path | None = None) -> None:
    """Gravação atômica: temporário no mesmo diretório + os.replace."""
    arquivo = caminho or arquivo_alarmes()
    pasta = arquivo.parent
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(pasta, 0o700)
    except OSError:
        pass

    descritor, nome_temp = tempfile.mkstemp(prefix=".alarms-", dir=str(pasta))
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as manipulador:
            json.dump(estado, manipulador, ensure_ascii=False, indent=2)
            manipulador.write("\n")
            manipulador.flush()
            os.fsync(manipulador.fileno())
        os.chmod(nome_temp, 0o600)
        os.replace(nome_temp, arquivo)
    except BaseException:
        try:
            os.unlink(nome_temp)
        except OSError:
            pass
        raise


def encontrar_alarme(estado: dict, alarme_id: int) -> dict | None:
    for alarme in estado["alarms"]:
        if alarme["id"] == alarme_id:
            return alarme
    return None


def registrar_disparo(
    alarme_id: int,
    ocorrencia: datetime,
    acao: str,
    snooze_min: int,
    caminho: Path | None = None,
) -> None:
    """Atualiza last_fired/snooze/once depois de um disparo."""
    estado = carregar_alarmes(caminho)
    alvo = encontrar_alarme(estado, alarme_id)
    if alvo is None:
        return
    if acao == "soneca":
        alvo["snooze_until"] = (datetime.now() + timedelta(minutes=snooze_min)).isoformat(
            timespec="seconds"
        )
        # marca a ocorrência original como tratada, senão ela redispara na hora
        alvo["last_fired"] = ocorrencia.isoformat(timespec="seconds")
    else:
        alvo["last_fired"] = ocorrencia.isoformat(timespec="seconds")
        alvo["snooze_until"] = None
        if alvo.get("once"):
            alvo["enabled"] = False
    salvar_alarmes(estado, caminho)


# ---------------------------------------------------------------------------
# Operações de alto nível usadas pela GUI (e testáveis)
# ---------------------------------------------------------------------------


def criar_alarme(
    hora: str,
    label: str = "",
    days: list[int] | None = None,
    once: bool = False,
    sound: str | None = None,
    snooze_minutes: int = 5,
    alarme_id: int = 1,
    enabled: bool = True,
) -> dict:
    """Monta e valida um alarme completo, sem tocar no disco."""
    h, m, s = parse_time(hora)
    dias = sorted({int(d) for d in days}) if days else None
    if dias is not None and any(d < 0 or d > 6 for d in dias):
        raise ValueError("dias devem estar entre 0 (segunda) e 6 (domingo)")
    return {
        "id": int(alarme_id),
        "time": f"{h:02d}:{m:02d}:{s:02d}",
        "label": label or "",
        "days": dias,
        "once": bool(once),
        "sound": sound or None,
        "enabled": bool(enabled),
        "snooze_minutes": _int_ou(snooze_minutes, 5),
        "last_fired": None,
        "snooze_until": None,
    }


def adicionar_alarme(
    hora: str,
    label: str = "",
    days: list[int] | None = None,
    once: bool = False,
    sound: str | None = None,
    snooze_minutes: int = 5,
    caminho: Path | None = None,
) -> dict:
    """Cria, persiste e devolve um novo alarme."""
    estado = carregar_alarmes(caminho)
    novo = criar_alarme(
        hora,
        label=label,
        days=days,
        once=once,
        sound=sound,
        snooze_minutes=snooze_minutes,
        alarme_id=estado["next_id"],
    )
    estado["alarms"].append(novo)
    estado["next_id"] += 1
    salvar_alarmes(estado, caminho)
    return novo


def aplicar_edicao(
    alarme: dict,
    hora: str,
    label: str = "",
    days: list[int] | None = None,
    once: bool = False,
    sound: str | None = None,
    snooze_minutes: int = 5,
) -> dict:
    """Aplica campos editáveis a um alarme existente (sem gravar)."""
    validado = criar_alarme(
        hora,
        label=label,
        days=days,
        once=once,
        sound=sound,
        snooze_minutes=snooze_minutes,
        alarme_id=alarme.get("id", 1),
        enabled=alarme.get("enabled", True),
    )
    validado["last_fired"] = alarme.get("last_fired")
    validado["snooze_until"] = alarme.get("snooze_until")
    alarme.update(validado)
    return alarme


def atualizar_alarme(
    alarme_id: int,
    hora: str | None = None,
    label: str | None = None,
    days=NAO_INFORMADO,
    once: bool | None = None,
    sound=NAO_INFORMADO,
    snooze_minutes: int | None = None,
    caminho: Path | None = None,
) -> dict:
    """Edita e persiste um alarme existente. Levanta KeyError se não existir."""
    estado = carregar_alarmes(caminho)
    alvo = encontrar_alarme(estado, alarme_id)
    if alvo is None:
        raise KeyError(f"alarme {alarme_id} não existe")
    aplicar_edicao(
        alvo,
        hora=alvo["time"] if hora is None else hora,
        label=alvo.get("label", "") if label is None else label,
        days=alvo.get("days") if days is NAO_INFORMADO else days,
        once=alvo.get("once", False) if once is None else once,
        sound=alvo.get("sound") if sound is NAO_INFORMADO else sound,
        snooze_minutes=(
            alvo.get("snooze_minutes", 5) if snooze_minutes is None else snooze_minutes
        ),
    )
    salvar_alarmes(estado, caminho)
    return alvo


def remover_alarme(alarme_id: int, caminho: Path | None = None) -> bool:
    """Remove o alarme; devolve False se o id não existir."""
    estado = carregar_alarmes(caminho)
    antes = len(estado["alarms"])
    estado["alarms"] = [a for a in estado["alarms"] if a["id"] != alarme_id]
    if len(estado["alarms"]) == antes:
        return False
    salvar_alarmes(estado, caminho)
    return True


def alternar_alarme(
    alarme_id: int, ativo: bool | None = None, caminho: Path | None = None
) -> dict:
    """Ativa/desativa (ou inverte) um alarme e persiste."""
    estado = carregar_alarmes(caminho)
    alvo = encontrar_alarme(estado, alarme_id)
    if alvo is None:
        raise KeyError(f"alarme {alarme_id} não existe")
    alvo["enabled"] = (not alvo.get("enabled", True)) if ativo is None else bool(ativo)
    if not alvo["enabled"]:
        alvo["snooze_until"] = None
    salvar_alarmes(estado, caminho)
    return alvo


# ---------------------------------------------------------------------------
# Formatação auxiliar
# ---------------------------------------------------------------------------


def formatar_duracao(segundos: float) -> str:
    total = int(max(0, segundos))
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}min"
    if minutos:
        return f"{minutos}min {seg:02d}s"
    return f"{seg}s"


def formatar_proxima(quando: datetime, agora: datetime) -> str:
    diferenca = (quando.date() - agora.date()).days
    if diferenca == 0:
        prefixo = "hoje"
    elif diferenca == 1:
        prefixo = "amanhã"
    else:
        prefixo = f"{DIAS_CURTOS[quando.weekday()]} {quando.strftime('%d/%m')}"
    return f"{prefixo} {quando.strftime('%H:%M')}"


def _recortar(texto: str, largura: int) -> str:
    if len(texto) <= largura:
        return texto.ljust(largura)
    if largura <= 1:
        return texto[:largura]
    return texto[: largura - 1] + "…"


# ---------------------------------------------------------------------------
# Som
# ---------------------------------------------------------------------------


def escolher_som(caminho_custom: str | None) -> str | None:
    """Devolve um arquivo de som existente, ou None se não houver."""
    if caminho_custom:
        arquivo = Path(caminho_custom).expanduser()
        if arquivo.is_file():
            return str(arquivo)
        print(f"aviso: som {caminho_custom!r} não encontrado; usando o padrão", file=sys.stderr)
    for candidato in SONS_PADRAO:
        if Path(candidato).is_file():
            return candidato
    return None


def escolher_player(arquivo_som: str | None) -> list[str] | None:
    """Monta o comando do primeiro player disponível, na ordem de preferência."""
    if not arquivo_som:
        return None
    opcoes = [
        ("paplay", lambda f: ["paplay", f]),
        ("aplay", lambda f: ["aplay", "-q", f]),
        ("ffplay", lambda f: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", f]),
        ("canberra-gtk-play", lambda f: ["canberra-gtk-play", "-f", f]),
    ]
    for nome, montar in opcoes:
        if shutil.which(nome):
            return montar(arquivo_som)
    return None


class TocadorSom:
    """Toca um som em loop sem bloquear a thread chamadora.

    A GUI chama `iniciar()`, `atualizar()` periodicamente e `parar()`.
    Quando não há player/arquivo, usa a campainha (callback ou "\\a").
    """

    def __init__(self, caminho_custom: str | None = None, campainha=None):
        self.arquivo = escolher_som(caminho_custom)
        self.comando = escolher_player(self.arquivo)
        self._campainha = campainha
        self._processo: subprocess.Popen | None = None
        self._ativo = False
        self._ultima_campainha = 0.0

    @property
    def tocando(self) -> bool:
        return self._ativo

    @property
    def usando_campainha(self) -> bool:
        return self.comando is None

    def iniciar(self) -> None:
        self._ativo = True
        self.atualizar(forcar_campainha=True)

    def atualizar(self, forcar_campainha: bool = False) -> None:
        """Reinicia o player quando ele termina; mantém o loop do som."""
        if not self._ativo:
            return
        if self.comando:
            if self._processo is None or self._processo.poll() is not None:
                try:
                    self._processo = subprocess.Popen(
                        self.comando, stdout=DEVNULL, stderr=DEVNULL
                    )
                except OSError:
                    self.comando = None
        if self.comando:
            return

        agora = time.monotonic()
        if forcar_campainha or agora - self._ultima_campainha >= 1.0:
            self._ultima_campainha = agora
            if self._campainha is not None:
                try:
                    self._campainha()
                except Exception:  # callback de UI nunca deve derrubar o loop
                    pass
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()

    def parar(self) -> None:
        self._ativo = False
        if self._processo is not None and self._processo.poll() is None:
            self._processo.terminate()
            try:
                self._processo.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._processo.kill()
        self._processo = None

    def __enter__(self) -> TocadorSom:
        self.iniciar()
        return self

    def __exit__(self, *_exc) -> None:
        self.parar()


def notificar(titulo: str, mensagem: str) -> None:
    """Notificação de desktop em paralelo, sem bloquear."""
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.Popen(
            ["notify-send", "-u", "critical", "-a", APP_NAME, titulo, mensagem],
            stdout=DEVNULL,
            stderr=DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def _ler_tecla() -> str | None:
    """Lê uma tecla (linha) do terminal, com timeout curto."""
    pronto, _, _ = select.select([sys.stdin], [], [], 0.5)
    if not pronto:
        return None
    linha = sys.stdin.readline()
    if linha == "":
        return "q"  # EOF: encerra o toque
    return linha.strip().lower()


def tocar(
    alarme: dict,
    ocorrencia: datetime,
    ring_seconds: float,
    snooze_padrao: int,
    perdido: bool,
    interativo: bool,
) -> tuple[str, int]:
    """Toca até o usuário decidir. Devolve ("parar"|"soneca", minutos_de_soneca)."""
    hora = ocorrencia.strftime("%H:%M")
    rotulo = alarme.get("label") or "sem rótulo"
    atraso = datetime.now() - ocorrencia
    snooze_min = _int_ou(alarme.get("snooze_minutes"), snooze_padrao)

    largura = 62
    print("=" * largura)
    if perdido:
        print(f" ALARME PERDIDO às {hora} (há {formatar_duracao(atraso.total_seconds())})")
    else:
        print(f" ALARME: {hora}")
    print(f" rótulo: {rotulo}")
    print("=" * largura)
    notificar(
        f"Alarme {hora}" + (" (perdido)" if perdido else ""),
        rotulo if rotulo != "sem rótulo" else "Hora de acordar",
    )

    arquivo_som = escolher_som(alarme.get("sound"))
    comando = escolher_player(arquivo_som)
    if comando:
        print(f"tocando com {comando[0]}: {arquivo_som}")
    else:
        if arquivo_som is None:
            print("nenhum arquivo de som encontrado; usando a campainha do terminal (\\a)")
        else:
            print("nenhum player de áudio disponível; usando a campainha do terminal (\\a)")

    if interativo:
        print(f"[s] soneca ({snooze_min} min)   [q] parar   [Enter] continuar tocando")
    else:
        print(f"entrada não interativa: tocando por {ring_seconds:g}s e encerrando")

    sys.stdout.flush()
    inicio = time.monotonic()
    processo: subprocess.Popen | None = None
    ultima_campainha = 0.0
    try:
        while True:
            if comando:
                if processo is None or processo.poll() is not None:
                    processo = subprocess.Popen(comando, stdout=DEVNULL, stderr=DEVNULL)
            else:
                agora_mono = time.monotonic()
                if agora_mono - ultima_campainha >= 1.0:
                    sys.stdout.write("\a")
                    if sys.stdout.isatty():
                        sys.stdout.write("\033[7m  >>> ALARME TOCANDO <<<  \033[0m\r")
                    else:
                        sys.stdout.write("  >>> ALARME TOCANDO <<<  \n")
                    sys.stdout.flush()
                    ultima_campainha = agora_mono

            if interativo:
                tecla = _ler_tecla()
                if tecla in ("q", "sair", "quit"):
                    print("\nAlarme parado.")
                    return "parar", snooze_min
                if tecla in ("s", "soneca"):
                    print(f"\nSoneca de {snooze_min} min.")
                    return "soneca", snooze_min
            else:
                time.sleep(0.5)
                if time.monotonic() - inicio >= ring_seconds:
                    print(f"\nfim do toque ({ring_seconds:g}s, modo não interativo).")
                    return "parar", snooze_min
    finally:
        if processo is not None and processo.poll() is None:
            processo.terminate()
            try:
                processo.wait(timeout=2)
            except subprocess.TimeoutExpired:
                processo.kill()
