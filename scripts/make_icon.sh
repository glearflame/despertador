#!/usr/bin/env bash
#
# make_icon.sh — desenha o ícone do Despertador (PNG 256x256 com alpha) usando
# ImageMagick. Fundo circular, mostrador de relógio com dois ponteiros e duas
# sinetas, nas cores do aplicativo e legível em 48x48.
#
# Uso:
#   ./scripts/make_icon.sh                # grava docs/despertador.png
#   ./scripts/make_icon.sh /caminho/icone.png
#
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$BASE_DIR/docs/despertador.png}"

# Cores de despertador_gui.py (COR_FUNDO, COR_DESTAQUE, COR_ALERTA).
COR_FUNDO="#f2efe7"
COR_DESTAQUE="#2f4858"
COR_ALERTA="#a63a4a"
COR_BRILHO="#cfd8dc"

# Descobre o binário do ImageMagick (7.x usa `magick`, 6.x usa `convert`).
if command -v magick >/dev/null 2>&1; then
    IM=(magick)
    IDENTIFY=(magick identify)
elif command -v convert >/dev/null 2>&1; then
    IM=(convert)
    IDENTIFY=(identify)
else
    echo "erro: ImageMagick não encontrado (instale 'imagemagick')" >&2
    exit 1
fi

mkdir -p "$(dirname -- "$OUT")"

# Geometria (tela 256x256):
#   mostrador: centro (128,142), raio 90, traço de 10px
#   sinetas:   centros (68,44) e (188,44), raio 27 (desenhadas atrás do mostrador)
#   ponteiros: 10:10 (hora à esquerda, minuto à direita)
# Os textos de -draw contêm só números: as cores vão por -fill/-stroke, o que
# evita qualquer ambiguidade de parsing do MVG.
"${IM[@]}" -size 256x256 xc:none \
    -fill "$COR_DESTAQUE" -stroke none -strokewidth 1 \
    -draw "circle 68,44 68,17 circle 188,44 188,17" \
    -fill "$COR_BRILHO" \
    -draw "circle 59,35 59,26 circle 179,35 179,26" \
    -fill none -stroke "$COR_DESTAQUE" -strokewidth 9 \
    -draw "line 100,228 86,246 line 156,228 170,246" \
    -fill "$COR_FUNDO" -stroke "$COR_DESTAQUE" -strokewidth 10 \
    -draw "circle 128,142 128,52" \
    -fill none -stroke "$COR_DESTAQUE" -strokewidth 7 \
    -draw "line 128,62 128,74 line 208,142 196,142 line 128,222 128,210 line 48,142 60,142" \
    -stroke "$COR_DESTAQUE" -strokewidth 11 \
    -draw "line 128,142 90,120" \
    -strokewidth 8 \
    -draw "line 128,142 182,111" \
    -fill "$COR_ALERTA" -stroke none \
    -draw "circle 128,142 128,149" \
    "PNG32:$OUT"

# Sanidade: o arquivo precisa existir e ter exatamente 256x256.
dim="$("${IDENTIFY[@]}" -format '%wx%h' "$OUT")"
if [ "$dim" != "256x256" ]; then
    echo "erro: ícone gerado com dimensão inesperada: $dim" >&2
    exit 1
fi

echo "ícone gerado: $OUT ($dim)"
