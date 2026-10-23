#!/usr/bin/env bash
#
# install-service.sh — instala/remove o serviço de usuário do Despertador.
#
# Sem sudo. Copia a unit para ~/.config/systemd/user/, cria um symlink em
# ~/.local/bin/despertador-run apontando para o launcher do repositório e
# habilita/inicia o serviço. É idempotente: pode rodar quantas vezes quiser.
#
# Uso:
#   ./packaging/install-service.sh            # instala e inicia
#   ./packaging/install-service.sh --remove   # para, desabilita e remove
#
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_NAME="despertador.service"
UNIT_SRC="$BASE_DIR/packaging/$UNIT_NAME"
LAUNCHER_SRC="$BASE_DIR/packaging/despertador-run"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/$UNIT_NAME"
BIN_DIR="$HOME/.local/bin"
LAUNCHER_DST="$BIN_DIR/despertador-run"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33matenção:\033[0m %s\n' "$*" >&2; }

if ! command -v systemctl >/dev/null 2>&1; then
    echo "erro: systemctl não encontrado; este recurso exige systemd" >&2
    exit 1
fi

remover() {
    log "parando e desabilitando $UNIT_NAME"
    systemctl --user disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
    rm -f "$UNIT_DST" "$LAUNCHER_DST"
    systemctl --user daemon-reload
    systemctl --user reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
    log "removido. O Despertador não inicia mais junto com a sessão."
}

instalar() {
    [ -f "$UNIT_SRC" ] || { echo "erro: $UNIT_SRC não existe" >&2; exit 1; }
    [ -x "$LAUNCHER_SRC" ] || { echo "erro: $LAUNCHER_SRC não é executável" >&2; exit 1; }

    mkdir -p "$UNIT_DIR" "$BIN_DIR"
    log "instalando unit em $UNIT_DST"
    install -m 0644 "$UNIT_SRC" "$UNIT_DST"

    log "criando symlink $LAUNCHER_DST -> $LAUNCHER_SRC"
    ln -sfn "$LAUNCHER_SRC" "$LAUNCHER_DST"

    systemctl --user daemon-reload
    log "habilitando e iniciando $UNIT_NAME"
    systemctl --user enable --now "$UNIT_NAME"
    systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
    log "pronto. Log ao vivo: journalctl --user -u despertador -f"
}

if [ "${1:-}" = "--remove" ]; then
    remover
elif [ -z "${1:-}" ]; then
    instalar
else
    echo "uso: $0 [--remove]" >&2
    exit 2
fi
