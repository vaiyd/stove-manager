# Build src/stove_pilot/service/ui/static/css/app.css (committed output; the runtime needs no tools).
#
# Pinned build tools (downloaded next to this script, git-ignored):
#   Tailwind CSS standalone CLI v4.3.3  (tailwindcss.exe)
#   daisyUI v5.7.50                     (daisyui.mjs, daisyui-theme.mjs)
# Vendored runtime asset (not downloaded by this script, committed under static/js/):
#   HTMX v2.0.10                        (htmx.min.js, from dist/htmx.min.js of the v2.0.10 tag)
# To upgrade a tool, change the version below, delete the downloaded files, and rerun the script.
$ErrorActionPreference = "Stop"
$TailwindVersion = "v4.3.3"
$DaisyUiVersion = "v5.7.50"

$tools = $PSScriptRoot
$static = Join-Path $tools "..\src\stove_pilot\service\ui\static\css"

if (-not (Test-Path "$tools\tailwindcss.exe")) {
  Invoke-WebRequest "https://github.com/tailwindlabs/tailwindcss/releases/download/$TailwindVersion/tailwindcss-windows-x64.exe" -OutFile "$tools\tailwindcss.exe"
}
if (-not (Test-Path "$tools\daisyui.mjs")) {
  Invoke-WebRequest "https://github.com/saadeghi/daisyui/releases/download/$DaisyUiVersion/daisyui.mjs" -OutFile "$tools\daisyui.mjs"
  Invoke-WebRequest "https://github.com/saadeghi/daisyui/releases/download/$DaisyUiVersion/daisyui-theme.mjs" -OutFile "$tools\daisyui-theme.mjs"
}
& "$tools\tailwindcss.exe" -i "$static\input.css" -o "$static\app.css" --minify
