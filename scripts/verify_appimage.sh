#!/usr/bin/env bash
#
# verify_appimage.sh — prova que dist/Despertador-x86_64.AppImage abre a GUI.
#
# Faz três checagens headless (Xvfb), como na especificação:
#   1. execução normal + screenshot com ImageMagick (> 10 KB)
#   2. execução com ambiente limpo (`env -i`) — prova que não depende do venv
#      nem de PYTHONPATH do projeto
#   3. segunda execução + screenshot — prova que cache/sujeira da primeira não
#      atrapalha
#
# Uso:
#   ./scripts/verify_appimage.sh [arquivo-de-screenshot]
#
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APPIMAGE="$BASE_DIR/dist/Despertador-x86_64.AppImage"
SHOT="${1:-/tmp/appimage-run.png}"
SHOT2="/tmp/appimage-run-2.png"
LOG_DIR="$BASE_DIR/build"
TAMANHO_MINIMO=10240  # 10 KB

# Sempre reexecuta dentro de um Xvfb próprio (a menos que o chamador peça o
# contrário com DESPERTADOR_USE_DISPLAY=1). Não confiar no DISPLAY herdado: ele
# pode apontar para um X inacessível (import falha) ou para o desktop real do
# usuário (o screenshot sairia "válido" mesmo com a GUI quebrada).
if [ "${DESPERTADOR_VERIFY_IN_XVFB:-0}" != "1" ]; then
    if [ "${DESPERTADOR_USE_DISPLAY:-0}" = "1" ] && [ -n "${DISPLAY:-}" ]; then
        :
    else
        command -v xvfb-run >/dev/null 2>&1 || {
            echo "erro: xvfb-run não encontrado (pacote xvfb); defina DESPERTADOR_USE_DISPLAY=1 para usar o DISPLAY atual" >&2
            exit 1
        }
        export DESPERTADOR_VERIFY_IN_XVFB=1
        exec xvfb-run -a --server-args="-screen 0 1280x800x24" "$0" "$@"
    fi
fi

command -v import >/dev/null 2>&1 || {
    echo "erro: 'import' (ImageMagick) não encontrado" >&2
    exit 1
}

if [ ! -x "$APPIMAGE" ]; then
    echo "erro: $APPIMAGE não existe ou não é executável; rode ./build_appimage.sh" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
falhas=0

# analisa_screenshot <arquivo>
# Imprime bytes, mean/stddev (Q16, 0..65535) e nº de cores; devolve 0 se a
# imagem for grande o bastante e não for preta/uniforme.
analisa_screenshot() {
    local shot="$1"
    local bytes=0 mean="" desvio="" cores=""
    [ -f "$shot" ] && bytes="$(stat -c %s "$shot" 2>/dev/null || echo 0)"
    if [ "$bytes" -gt 0 ] && command -v convert >/dev/null 2>&1; then
        mean="$(convert "$shot" -format '%[mean]' info: 2>/dev/null || true)"
        desvio="$(convert "$shot" -format '%[standard-deviation]' info: 2>/dev/null || true)"
        cores="$(convert "$shot" -format '%k' info: 2>/dev/null || true)"
    fi
    echo "    screenshot: $shot (${bytes} bytes, >10 KB: $([ "$bytes" -ge "$TAMANHO_MINIMO" ] && echo sim || echo não))"
    [ -n "$mean$desvio$cores" ] && echo "    imagem: mean=$mean stddev=$desvio cores=$cores (escala Q16)"

    [ "$bytes" -ge "$TAMANHO_MINIMO" ] || return 1
    # Sem ImageMagick para analisar, aceita pelo tamanho.
    [ -n "$mean" ] || return 0
    # mean/stddev baixos = tela preta ou cor uniforme (captura inútil).
    awk -v m="$mean" -v d="$desvio" -v c="$cores" \
        'BEGIN { exit !(m + 0 >= 1000 && d + 0 >= 1000 && c + 0 >= 16) }'
}

# roda_e_fotografa <rótulo> <screenshot> [comando...]
# Inicia o comando numa sessão própria, espera 8 s, tira screenshot e encerra.
roda_e_fotografa() {
    local rotulo="$1" shot="$2"
    shift 2
    local log="$LOG_DIR/verify-${rotulo}.log"
    local err="$LOG_DIR/verify-${rotulo}-import.log"

    echo "==> [$rotulo] $*"
    rm -f "$shot" "$err"
    setsid "$@" >"$log" 2>&1 &
    local pid=$!
    sleep 8

    local vivo=0
    if kill -0 "$pid" 2>/dev/null; then
        vivo=1
    fi

    import -window root "$shot" 2>"$err" || true

    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    sleep 1
    kill -KILL -"$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true

    local imagens_ok=0
    if analisa_screenshot "$shot"; then
        imagens_ok=1
    elif [ -s "$err" ]; then
        echo "    erro do import:"; sed 's/^/      /' "$err"
    fi

    echo "    processo vivo após 8 s: $([ "$vivo" -eq 1 ] && echo sim || echo NÃO)"
    [ -s "$log" ] && { echo "    log (últimas linhas):"; tail -n 5 "$log" | sed 's/^/      /'; }

    if [ "$vivo" -eq 1 ] && [ "$imagens_ok" -eq 1 ]; then
        return 0
    fi
    falhas=$((falhas + 1))
    return 1
}

# --- 1. execução normal -----------------------------------------------------
roda_e_fotografa "01-normal" "$SHOT" \
    env APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE" || true

# --- 2. ambiente limpo (env -i) --------------------------------------------
# Sem PYTHONPATH, sem VIRTUAL_ENV, sem PATH do projeto: só HOME e o PATH básico.
echo "==> [02-env-i] env -i HOME PATH=/usr/bin:/bin APPIMAGE_EXTRACT_AND_RUN=1 xvfb-run -a"
log2="$LOG_DIR/verify-02-env-i.log"
setsid env -i HOME="$HOME" PATH=/usr/bin:/bin APPIMAGE_EXTRACT_AND_RUN=1 \
    xvfb-run -a --server-args="-screen 0 800x600x24" "$APPIMAGE" >"$log2" 2>&1 &
pid2=$!
sleep 10
if kill -0 "$pid2" 2>/dev/null; then
    echo "    processo vivo após 10 s: sim (GUI abriu sem o Python do projeto)"
else
    echo "    processo vivo após 10 s: NÃO"
    tail -n 20 "$log2" | sed 's/^/      /'
    falhas=$((falhas + 1))
fi
kill -TERM -"$pid2" 2>/dev/null || kill -TERM "$pid2" 2>/dev/null || true
sleep 1
kill -KILL -"$pid2" 2>/dev/null || true
wait "$pid2" 2>/dev/null || true

# --- 3. segunda execução ----------------------------------------------------
roda_e_fotografa "03-segunda-execucao" "$SHOT2" \
    env APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE" || true

echo
if [ "$falhas" -eq 0 ]; then
    echo "OK: todas as checagens passaram."
    exit 0
fi
echo "FALHA: $falhas checagem(ns) falharam; veja os logs em $LOG_DIR/verify-*.log" >&2
exit 1
