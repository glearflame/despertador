#!/usr/bin/env python3
"""Despertador de linha de comando, em Python puro (só biblioteca padrão).

Uso principal:
    ./despertador.py add 07:30 --label "acordar"
    ./despertador.py run

Os alarmes ficam em $XDG_CONFIG_HOME/despertador/alarms.json
(padrão: ~/.config/despertador/alarms.json).

A interface gráfica fica em `despertador_gui.py`; o núcleo compartilhado está
no pacote `despertador`.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from despertador import inhibit
from despertador.core import (
    __version__,
    _ocorrencia_na_janela,
    _recortar,
    arquivo_alarmes,
    carregar_alarmes,
    encontrar_alarme,
    formatar_dias,
    formatar_duracao,
    formatar_hora,
    formatar_proxima,
    parse_days,
    parse_time,
    proxima_geral,
    proxima_ocorrencia,
    registrar_disparo,
    salvar_alarmes,
    tocar,
)

# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def cmd_add(args) -> int:
    try:
        h, m, s = parse_time(args.time)
    except ValueError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2
    hora_normalizada = f"{h:02d}:{m:02d}:{s:02d}"

    dias = None
    if args.days:
        try:
            dias = parse_days(args.days)
        except ValueError as erro:
            print(f"erro: {erro}", file=sys.stderr)
            return 2

    if args.sound and not Path(args.sound).expanduser().is_file():
        print(f"aviso: som {args.sound!r} não existe (será usado no toque se aparecer)")
    if args.snooze < 1:
        print("erro: --snooze deve ser >= 1 minuto", file=sys.stderr)
        return 2

    estado = carregar_alarmes()
    novo = {
        "id": estado["next_id"],
        "time": hora_normalizada,
        "label": args.label or "",
        "days": dias,
        "once": bool(args.once),
        "sound": args.sound,
        "enabled": True,
        "snooze_minutes": args.snooze,
        "last_fired": None,
        "snooze_until": None,
    }
    estado["alarms"].append(novo)
    estado["next_id"] += 1
    salvar_alarmes(estado)

    hoje = datetime.now()
    quando = proxima_ocorrencia(novo, hoje)
    detalhe = f"próximo: {formatar_proxima(quando, hoje)}" if quando else "sem próxima ocorrência"
    print(f"alarme {novo['id']} adicionado: {hora_normalizada} ({formatar_dias(novo)}) {detalhe}")
    return 0


def cmd_list(args) -> int:
    estado = carregar_alarmes()
    alarmes = estado["alarms"]
    if not alarmes:
        print("nenhum alarme cadastrado. Use: despertador add 07:30 --label acordar")
        return 0

    agora = datetime.now()
    cabecalho = (
        f"{'ID':>3}  {'HORÁRIO':<8}  {'DIAS':<17}  {'LABEL':<18}  {'ATIVO':<5}  PRÓXIMA"
    )
    print(cabecalho)
    print("-" * len(cabecalho))
    for alarme in sorted(alarmes, key=lambda a: a["time"]):
        quando = proxima_ocorrencia(alarme, agora)
        if quando is not None:
            proxima = formatar_proxima(quando, agora)
        elif not alarme.get("enabled", True):
            proxima = "-"
        elif alarme.get("once") and alarme.get("last_fired"):
            proxima = "já disparou"
        else:
            proxima = "-"
        print(
            f"{alarme['id']:>3}  {formatar_hora(alarme):<8}  "
            f"{_recortar(formatar_dias(alarme), 17)}  "
            f"{_recortar(alarme.get('label') or '-', 18)}  "
            f"{'sim' if alarme.get('enabled', True) else 'não':<5}  {proxima}"
        )
    return 0


def _mudar_estado(args, ativo: bool) -> int:
    estado = carregar_alarmes()
    alvo = encontrar_alarme(estado, args.id)
    if alvo is None:
        print(f"erro: alarme {args.id} não existe", file=sys.stderr)
        return 1
    alvo["enabled"] = ativo
    if not ativo:
        alvo["snooze_until"] = None
    salvar_alarmes(estado)
    print(f"alarme {alvo['id']} {'habilitado' if ativo else 'desabilitado'}")
    return 0


def cmd_enable(args) -> int:
    return _mudar_estado(args, True)


def cmd_disable(args) -> int:
    return _mudar_estado(args, False)


def cmd_rm(args) -> int:
    estado = carregar_alarmes()
    antes = len(estado["alarms"])
    estado["alarms"] = [a for a in estado["alarms"] if a["id"] != args.id]
    if len(estado["alarms"]) == antes:
        print(f"erro: alarme {args.id} não existe", file=sys.stderr)
        return 1
    salvar_alarmes(estado)
    print(f"alarme {args.id} removido")
    return 0


def cmd_next(args) -> int:
    estado = carregar_alarmes()
    agora = datetime.now()
    proximo = proxima_geral(estado["alarms"], agora)
    if proximo is None:
        print("nenhum alarme ativo.")
        return 0
    alarme, quando = proximo
    faltam = (quando - agora).total_seconds()
    rotulo = f" [{alarme['label']}]" if alarme.get("label") else ""
    print(
        f"próximo alarme: {quando.strftime('%H:%M:%S')} "
        f"({formatar_dias(alarme)}){rotulo} — em {formatar_duracao(faltam)}"
    )
    return 0


def agora_local() -> datetime:
    """Relógio local do processo.

    Existe como função (em vez de `datetime.now()` espalhado) para que os
    testes possam injetar um relógio falso sem dormir de verdade.
    """
    return datetime.now()


def _entrada_interativa() -> bool:
    """True quando há terminal para ler 's' (soneca) e 'q' (parar)."""
    return sys.stdin.isatty()


def _ajustar_inibidor(
    inibidor, alarmes: list[dict], agora: datetime, manter: bool = False
) -> None:
    """Inicia/renova o inhibitor com alarme pendente; libera quando não há.

    `manter=True` serve para o instante do toque: um alarme `--once` deixa de
    ter "próxima ocorrência" exatamente na hora em que toca, mas o PC não pode
    dormir no meio do toque. Nesse caso não iniciamos nem liberamos nada.
    """
    if inibidor is None:
        return
    if inhibit.what_para_inibir(alarmes, agora):
        inibidor.iniciar(alarmes, agora)
    elif not manter:
        inibidor.parar()


def _laco_alarmes(
    *,
    grace_hours: float,
    ring_seconds: float,
    snooze: int,
    interativo: bool,
    continuo: bool = False,
    inibidor=None,
    relogio=None,
    tocar_fn=None,
    registrar_fn=None,
    dormir=None,
) -> int:
    """Laço principal: mantém o PC acordado e toca os alarmes na hora.

    O laço dorme em fatias curtas (no máximo 30 s) para reagir a alterações no
    JSON de alarmes e a mudanças de horário do sistema. O inhibitor é renovado
    a cada volta — o que cobre a soneca, porque `proxima_ocorrencia` já a
    considera — e só é liberado quando não resta nenhum alarme pendente.
    """
    relogio = relogio or agora_local
    tocar_fn = tocar_fn or tocar
    registrar_fn = registrar_fn or registrar_disparo
    dormir = dormir or time.sleep

    while True:
        estado = carregar_alarmes()
        agora = relogio()

        vencidos = []
        for alarme in estado["alarms"]:
            ocorrencia = _ocorrencia_na_janela(alarme, agora, grace_hours)
            if ocorrencia is not None:
                vencidos.append((ocorrencia, alarme))

        # Adquire/renova antes de tocar: o PC precisa continuar acordado
        # durante o toque e durante a soneca.
        _ajustar_inibidor(inibidor, estado["alarms"], agora, manter=bool(vencidos))

        if vencidos:
            ocorrencia, alarme = min(vencidos, key=lambda item: item[0])
            atraso = agora - ocorrencia
            perdido = atraso > timedelta(seconds=60)
            acao, snooze_min = tocar_fn(
                alarme,
                ocorrencia,
                ring_seconds,
                snooze,
                perdido,
                interativo,
            )
            registrar_fn(alarme["id"], ocorrencia, acao, snooze_min)
            if acao == "parar" and alarme.get("once"):
                print(f"alarme {alarme['id']} era --once e foi desativado.")
            if not interativo and not continuo:
                # Sem terminal não há soneca/parar: toca --ring-seconds e encerra.
                return 0
            # Modo contínuo (systemd/daemon): continua o laço para os próximos
            # alarmes, mesmo sem TTY e mesmo depois de um EOF hipotético.
            continue

        # Sem ocorrência vencida: se não há alarme pendente, o PC pode dormir
        # (o `_ajustar_inibidor` acima já liberou o inhibitor nesse caso).

        proximo = proxima_geral(estado["alarms"], agora)
        fatia = 30.0
        if proximo is not None:
            restante = (proximo[1] - agora).total_seconds()
            fatia = min(fatia, max(0.2, restante))
        dormir(fatia)


def _rodar(args, *, interativo: bool) -> int:
    """Executa o laço com inhibitor; compartilhado por `run` e `inhibit`."""

    def _por_sinal(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _por_sinal)
    except (ValueError, OSError):
        pass

    inibidor = inhibit.InibidorSono()
    try:
        return _laco_alarmes(
            grace_hours=args.grace_hours,
            ring_seconds=args.ring_seconds,
            snooze=args.snooze,
            interativo=interativo,
            continuo=args.continuo,
            inibidor=inibidor,
        )
    except KeyboardInterrupt:
        print("\ndespertador encerrado.")
        return 0
    finally:
        inibidor.parar()


def cmd_run(args) -> int:
    interativo = _entrada_interativa()
    print("despertador rodando em primeiro plano (Ctrl+C para sair).")
    print(f"arquivo: {arquivo_alarmes()}")

    agora = agora_local()
    proximo = proxima_geral(carregar_alarmes()["alarms"], agora)
    if proximo:
        print(
            f"próximo: {proximo[1].strftime('%H:%M')} ({formatar_dias(proximo[0])}) "
            f"em {formatar_duracao((proximo[1] - agora).total_seconds())}"
        )
    else:
        print("nenhum alarme ativo; aguardando alterações no arquivo.")
    print("o PC não vai dormir enquanto houver alarme ativo (inibidor do logind).")
    sys.stdout.flush()

    return _rodar(args, interativo=interativo)


def _inibidores_do_despertador() -> list[str]:
    """Linhas de `systemd-inhibit --list` cujo WHO é o Despertador."""
    try:
        saida = subprocess.run(
            ["systemd-inhibit", "--list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        linha
        for linha in saida.splitlines()
        if linha.startswith(inhibit.WHO_PADRAO)
    ]


def cmd_inhibit(args) -> int:
    """Mantém o inhibitor e toca os alarmes. É o mesmo laço de `run`.

    O nome `inhibit` é mantido por compatibilidade: o comando faz exatamente o
    que `run` faz (adquire o inhibitor apenas enquanto houver alarme pendente,
    toca na hora certa, aceita soneca/parar e libera no fim).
    """
    if args.status:
        linhas = _inibidores_do_despertador()
        if linhas:
            print("inibidor do Despertador ativo:")
            for linha in linhas:
                print(f"  {linha}")
        else:
            print("nenhum inibidor do Despertador ativo.")

        agora = agora_local()
        proximo = proxima_geral(carregar_alarmes()["alarms"], agora)
        if proximo is None:
            print("alarmes: nenhum ativo (o PC pode dormir).")
        else:
            alarme, quando = proximo
            print(
                f"próximo alarme: {quando.strftime('%H:%M')} "
                f"({formatar_dias(alarme)}) em "
                f"{formatar_duracao((quando - agora).total_seconds())}"
            )
        return 0

    interativo = _entrada_interativa()
    print("despertador inibindo a suspensão e tocando os alarmes (Ctrl+C para sair).")
    print(f"arquivo: {arquivo_alarmes()}")

    agora = agora_local()
    proximo = proxima_geral(carregar_alarmes()["alarms"], agora)
    if proximo is not None:
        print(
            f"próximo: {proximo[1].strftime('%H:%M')} ({formatar_dias(proximo[0])}) "
            f"em {formatar_duracao((proximo[1] - agora).total_seconds())}"
        )
    else:
        print("nenhum alarme ativo; o PC pode dormir (nada a inibir).")
    sys.stdout.flush()

    return _rodar(args, interativo=interativo)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="despertador",
        description="Despertador de linha de comando (Python puro, sem dependências).",
        epilog="Exemplos:\n"
        "  despertador add 07:30 --label acordar\n"
        "  despertador add 06:45 --days seg,ter,qua,qui,sex --label trabalho\n"
        "  despertador run\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"despertador {__version__}")

    sub = parser.add_subparsers(dest="comando", metavar="comando")

    add = sub.add_parser("add", help="adiciona um alarme")
    add.add_argument("time", help="horário HH:MM ou HH:MM:SS")
    add.add_argument("--label", default="", help="rótulo livre (ex.: acordar)")
    add.add_argument("--days", default=None, help="dias da semana (ex.: seg,ter ou 'seg ter')")
    add.add_argument("--once", action="store_true", help="toca uma vez e se desativa")
    add.add_argument("--sound", default=None, help="arquivo de som customizado")
    add.add_argument("--snooze", type=int, default=5, metavar="MIN", help="minutos de soneca")
    add.set_defaults(func=cmd_add)

    lista = sub.add_parser("list", help="lista os alarmes")
    lista.set_defaults(func=cmd_list)

    remover = sub.add_parser("rm", help="remove um alarme")
    remover.add_argument("id", type=int)
    remover.set_defaults(func=cmd_rm)

    habilitar = sub.add_parser("enable", help="ativa um alarme")
    habilitar.add_argument("id", type=int)
    habilitar.set_defaults(func=cmd_enable)

    desabilitar = sub.add_parser("disable", help="desativa um alarme")
    desabilitar.add_argument("id", type=int)
    desabilitar.set_defaults(func=cmd_disable)

    proximo = sub.add_parser("next", help="mostra o próximo alarme (sem loop)")
    proximo.set_defaults(func=cmd_next)

    executar = sub.add_parser("run", help="loop em primeiro plano que toca os alarmes")
    executar.add_argument(
        "--ring-seconds",
        type=float,
        default=60.0,
        metavar="N",
        help="duração do toque quando a entrada não é interativa (padrão: 60)",
    )
    executar.add_argument(
        "--grace-hours",
        type=float,
        default=2.0,
        metavar="X",
        help="janela para considerar um alarme perdido (padrão: 2)",
    )
    executar.add_argument(
        "--snooze",
        type=int,
        default=5,
        metavar="MIN",
        help="minutos de soneca padrão caso o alarme não tenha o seu (padrão: 5)",
    )
    executar.add_argument(
        "--continuo",
        action="store_true",
        help=(
            "continua o laço após tocar, mesmo sem TTY (ideal para o serviço "
            "systemd); sem esta opção o modo não interativo encerra após o toque"
        ),
    )
    executar.set_defaults(func=cmd_run)

    inibir = sub.add_parser(
        "inhibit",
        help="mantém o PC acordado e toca os alarmes (systemd-inhibit)",
    )
    inibir.add_argument(
        "--status",
        action="store_true",
        help="mostra se há inibidor ativo e por quê, sem bloquear",
    )
    inibir.add_argument(
        "--ring-seconds",
        type=float,
        default=60.0,
        metavar="N",
        help="duração do toque quando a entrada não é interativa (padrão: 60)",
    )
    inibir.add_argument(
        "--grace-hours",
        type=float,
        default=2.0,
        metavar="X",
        help="janela para considerar um alarme perdido (padrão: 2)",
    )
    inibir.add_argument(
        "--snooze",
        type=int,
        default=5,
        metavar="MIN",
        help="minutos de soneca padrão caso o alarme não tenha o seu (padrão: 5)",
    )
    inibir.add_argument(
        "--continuo",
        action="store_true",
        help="continua o laço após tocar, mesmo sem TTY",
    )
    inibir.set_defaults(func=cmd_inhibit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "comando", None) or not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
