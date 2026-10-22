#!/usr/bin/env bash
#
# build_appimage.sh — empacota o Despertador (GUI Tkinter) num AppImage único.
#
# Ciclo completo:
#   1. limpa artefatos antigos (build/, dist/, *.AppDir)
#   2. garante PyInstaller e Tkinter no Python de build
#   3. gera o ícone 256x256 com ImageMagick (docs/despertador.png)
#   4. empacota com PyInstaller (--onefile --windowed)
#   5. monta o AppDir (AppRun, .desktop, ícone, binário)
#   6. baixa o appimagetool (com cache) e gera dist/Despertador-x86_64.AppImage
#
# Funciona de qualquer diretório: a base é o diretório deste script.
# Sem sudo: instalação de dependência Python vai para ~/.local (ou venv em cache).
#
# Uso:
#   ./build_appimage.sh
#   PYTHON=python3.11 ./build_appimage.sh    # força um interpretador de build
#
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

APP_NAME="Despertador"
APP_ID="despertador"
ENTRY_POINT="$BASE_DIR/despertador_gui.py"
APP_DIR="$BASE_DIR/${APP_NAME}.AppDir"
BUILD_DIR="$BASE_DIR/build"
DIST_DIR="$BASE_DIR/dist"
ICON="$BASE_DIR/docs/despertador.png"
OUT="$DIST_DIR/${APP_NAME}-x86_64.AppImage"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/despertador-appimage"
APPIMAGETOOL="$CACHE_DIR/appimagetool-x86_64.AppImage"
APPIMAGETOOL_URLS=(
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
)

PYTHON="${PYTHON:-python3}"

BUILD_BINARY=""

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33matenção:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merro:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Limpeza
# ---------------------------------------------------------------------------
limpar() {
    log "limpando artefatos anteriores"
    rm -rf "$BUILD_DIR" "$DIST_DIR" "$BASE_DIR/AppDir" "$APP_DIR"
    rm -f "$BASE_DIR"/*.spec
    mkdir -p "$BUILD_DIR" "$DIST_DIR"
}

# ---------------------------------------------------------------------------
# 2. Dependências de build (sem sudo)
# ---------------------------------------------------------------------------
PYINSTALLER_CMD=()

resolve_pyinstaller() {
    command -v "$PYTHON" >/dev/null 2>&1 || die "'$PYTHON' não encontrado no PATH"

    "$PYTHON" -c 'import tkinter' 2>/dev/null \
        || die "Tkinter ausente em $PYTHON (instale python3-tk). Não uso sudo."

    if "$PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
        PYINSTALLER_CMD=("$PYTHON" -m PyInstaller)
        log "PyInstaller já disponível: $("$PYTHON" -m PyInstaller --version)"
        return
    fi

    log "PyInstaller não encontrado; tentando instalar com --user"
    if "$PYTHON" -m pip install --user --upgrade pyinstaller >/dev/null 2>&1 \
        && "$PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
        PYINSTALLER_CMD=("$PYTHON" -m PyInstaller)
        log "PyInstaller instalado em ~/.local: $("$PYTHON" -m PyInstaller --version)"
        return
    fi

    # Fallback: venv isolada em cache (contorna PEP 668 / externally-managed).
    local venv="$CACHE_DIR/venv-build"
    warn "pip --user indisponível; criando venv de build em $venv"
    mkdir -p "$CACHE_DIR"
    "$PYTHON" -m venv "$venv" \
        || die "não consegui criar venv em $venv e PyInstaller não está disponível"
    "$venv/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$venv/bin/python" -m pip install --upgrade pyinstaller \
        || die "falha ao instalar PyInstaller (verifique a conexão de rede)"
    PYINSTALLER_CMD=("$venv/bin/python" -m PyInstaller)
    log "PyInstaller instalado em venv: $("$venv/bin/python" -m PyInstaller --version)"

    # A venv herda o tkinter do sistema; validamos por garantia.
    "$venv/bin/python" -c 'import tkinter' 2>/dev/null \
        || warn "a venv de build não vê tkinter; o empacotamento pode falhar"
}

# ---------------------------------------------------------------------------
# 3. Ícone
# ---------------------------------------------------------------------------
gerar_icone() {
    log "gerando ícone 256x256"
    chmod +x "$BASE_DIR/scripts/make_icon.sh"
    "$BASE_DIR/scripts/make_icon.sh" "$ICON"
    [ -s "$ICON" ] || die "ícone não foi gerado em $ICON"
}

# ---------------------------------------------------------------------------
# 4. PyInstaller
# ---------------------------------------------------------------------------
empacotar() {
    log "empacotando com PyInstaller (--onefile --windowed)"
    local pyi_dist="$BUILD_DIR/pyinstaller-dist"

    "${PYINSTALLER_CMD[@]}" \
        --noconfirm \
        --clean \
        --onefile \
        --windowed \
        --name despertador-app \
        --distpath "$pyi_dist" \
        --workpath "$BUILD_DIR/pyinstaller-work" \
        --specpath "$BUILD_DIR/pyinstaller-spec" \
        --paths "$BASE_DIR" \
        --hidden-import tkinter \
        --hidden-import tkinter.ttk \
        --hidden-import tkinter.filedialog \
        --hidden-import tkinter.messagebox \
        --collect-data tkinter \
        --log-level WARN \
        "$ENTRY_POINT"

    [ -x "$pyi_dist/despertador-app" ] || die "PyInstaller não gerou $pyi_dist/despertador-app"

    # Fumaça: o binário precisa rodar sem DISPLAY para --version (só argparse).
    if timeout 30 "$pyi_dist/despertador-app" --version >"$BUILD_DIR/version.txt" 2>&1; then
        log "binário responde: $(tr -d '\n' <"$BUILD_DIR/version.txt")"
    else
        warn "o binário não respondeu a --version; veja o log abaixo"
        cat "$BUILD_DIR/version.txt" >&2 || true
    fi

    BUILD_BINARY="$pyi_dist/despertador-app"
}

# ---------------------------------------------------------------------------
# 5. AppDir
# ---------------------------------------------------------------------------
montar_appdir() {
    log "montando $APP_DIR"
    mkdir -p "$APP_DIR/usr/bin" "$APP_DIR/usr/share/icons/hicolor/256x256/apps"

    install -m 0755 "$BUILD_BINARY" "$APP_DIR/usr/bin/despertador-app"

    install -m 0644 "$ICON" "$APP_DIR/${APP_ID}.png"
    install -m 0644 "$ICON" "$APP_DIR/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"
    ln -sfn "${APP_ID}.png" "$APP_DIR/.DirIcon"

    cat >"$APP_DIR/AppRun" <<'APPRUN'
#!/bin/sh
# AppRun: ponto de entrada do AppImage.
set -e
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
exec "$HERE/usr/bin/despertador-app" "$@"
APPRUN
    chmod 0755 "$APP_DIR/AppRun"

    cat >"$APP_DIR/${APP_ID}.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Despertador
GenericName=Alarm Clock
Comment=Despertador com alarmes, relógio mundial, cronômetro e temporizador
Exec=despertador-app
TryExec=despertador-app
Icon=despertador
Terminal=false
Categories=Utility;Clock;
Keywords=alarme;despertador;relogio;clock;timer;
StartupNotify=true
DESKTOP
    chmod 0644 "$APP_DIR/${APP_ID}.desktop"
}

# ---------------------------------------------------------------------------
# 6. appimagetool
# ---------------------------------------------------------------------------
baixar_appimagetool() {
    if [ -s "$APPIMAGETOOL" ] && [ -x "$APPIMAGETOOL" ]; then
        log "appimagetool em cache: $APPIMAGETOOL"
        return
    fi

    mkdir -p "$CACHE_DIR"
    local url tmp
    tmp="$APPIMAGETOOL.part"
    for url in "${APPIMAGETOOL_URLS[@]}"; do
        log "baixando appimagetool: $url"
        rm -f "$tmp"
        if command -v curl >/dev/null 2>&1; then
            curl -fL --retry 2 --connect-timeout 20 -o "$tmp" "$url" || continue
        elif command -v wget >/dev/null 2>&1; then
            wget -q -O "$tmp" "$url" || continue
        else
            die "nem curl nem wget disponíveis para baixar o appimagetool"
        fi
        if [ -s "$tmp" ] && [ "$(stat -c %s "$tmp")" -gt 1000000 ]; then
            mv -f "$tmp" "$APPIMAGETOOL"
            chmod 0755 "$APPIMAGETOOL"
            log "appimagetool salvo em $APPIMAGETOOL"
            return
        fi
        warn "download inválido de $url"
    done

    rm -f "$tmp"
    die "não consegui baixar o appimagetool (rede bloqueada?). O binário PyInstaller está em $BUILD_BINARY"
}

gerar_appimage() {
    baixar_appimagetool

    log "gerando $OUT"
    rm -f "$OUT"

    # --appimage-extract-and-run evita depender de FUSE/libfuse2.
    if ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APP_DIR" "$OUT"; then
        :
    elif rm -f "$OUT" && ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "$APP_DIR" "$OUT"; then
        warn "appimagetool rodou apenas com APPIMAGE_EXTRACT_AND_RUN=1"
    else
        warn "extract-and-run falhou; tentando extrair manualmente"
        rm -f "$OUT"
        local tmpdir
        tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/appimagetool.XXXXXX")"
        ( cd "$tmpdir" && "$APPIMAGETOOL" --appimage-extract >/dev/null ) \
            || die "não consegui extrair o appimagetool"
        ( cd "$tmpdir" && ARCH=x86_64 ./squashfs-root/AppRun "$APP_DIR" "$OUT" ) \
            || die "appimagetool falhou ao gerar o AppImage"
        rm -rf "$tmpdir"
    fi

    [ -s "$OUT" ] || die "appimagetool não gerou $OUT"
    chmod 0755 "$OUT"

    log "pronto"
    ls -lh "$OUT"
    if command -v file >/dev/null 2>&1; then
        printf 'tipo: %s\n' "$(file -b "$OUT")"
    fi
    printf 'tamanho: %s bytes\n' "$(stat -c %s "$OUT")"
}

# ---------------------------------------------------------------------------
main() {
    command -v file >/dev/null 2>&1 || warn "'file' não encontrado; o relatório de tipo será pulado"
    limpar
    resolve_pyinstaller
    gerar_icone
    empacotar
    montar_appdir
    gerar_appimage
}

main "$@"
