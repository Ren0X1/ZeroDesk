#!/usr/bin/env bash
# Trae la ultima version del repo y reinicia el panel.
#     bash ~/ZeroDesk/panel/actualizar.sh
# La configuracion (.env), el historico (telemetria.db) y el registro no se
# tocan: estan fuera de git.
set -euo pipefail
PANEL="$(cd "$(dirname "$0")" && pwd)"
git -C "$PANEL/.." pull --ff-only
"$PANEL/venv/bin/pip" install -q -r "$PANEL/requirements.txt"
sudo systemctl restart recarga-web
sleep 3
systemctl is-active recarga-web
