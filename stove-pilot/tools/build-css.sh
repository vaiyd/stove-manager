#!/usr/bin/env sh
# Build src/stove_pilot/service/ui/static/css/app.css (committed output; the runtime needs no tools).
#
# Pinned build tools (downloaded next to this script, git-ignored):
#   Tailwind CSS standalone CLI v4.3.3  (tailwindcss)
#   daisyUI v5.7.50                     (daisyui.mjs, daisyui-theme.mjs)
# Vendored runtime asset (not downloaded by this script, committed under static/js/):
#   HTMX v2.0.10                        (htmx.min.js, from dist/htmx.min.js of the v2.0.10 tag)
set -eu
TAILWIND_VERSION="v4.3.3"
DAISYUI_VERSION="v5.7.50"

TOOLS="$(cd "$(dirname "$0")" && pwd)"
STATIC="$TOOLS/../src/stove_pilot/service/ui/static/css"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ASSET="tailwindcss-linux-x64" ;;
  Linux-aarch64|Linux-arm64) ASSET="tailwindcss-linux-arm64" ;;
  Darwin-arm64) ASSET="tailwindcss-macos-arm64" ;;
  Darwin-x86_64) ASSET="tailwindcss-macos-x64" ;;
  *) echo "error: unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x "$TOOLS/tailwindcss" ]; then
  curl -fsSL "https://github.com/tailwindlabs/tailwindcss/releases/download/$TAILWIND_VERSION/$ASSET" -o "$TOOLS/tailwindcss"
  chmod +x "$TOOLS/tailwindcss"
fi
if [ ! -f "$TOOLS/daisyui.mjs" ]; then
  curl -fsSL "https://github.com/saadeghi/daisyui/releases/download/$DAISYUI_VERSION/daisyui.mjs" -o "$TOOLS/daisyui.mjs"
  curl -fsSL "https://github.com/saadeghi/daisyui/releases/download/$DAISYUI_VERSION/daisyui-theme.mjs" -o "$TOOLS/daisyui-theme.mjs"
fi
"$TOOLS/tailwindcss" -i "$STATIC/input.css" -o "$STATIC/app.css" --minify
