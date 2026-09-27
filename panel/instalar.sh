#!/usr/bin/env bash
# Instala el panel de ZeroDesk en la Raspberry Pi, funcionando DESDE EL REPO.
#
#     git clone https://github.com/Ren0X1/ZeroDesk.git ~/ZeroDesk
#     sudo bash ~/ZeroDesk/panel/instalar.sh
#
# Es idempotente: se puede relanzar sin perder la configuracion. Para
# actualizar despues basta con panel/actualizar.sh.
set -euo pipefail

USUARIO="${SUDO_USER:-renox}"
PANEL="$(cd "$(dirname "$0")" && pwd)"
PUERTO=6678

echo "== 1/5 · Entorno de Python =="
[ -x "$PANEL/venv/bin/python" ] || sudo -u "$USUARIO" python3 -m venv "$PANEL/venv"
sudo -u "$USUARIO" "$PANEL/venv/bin/pip" install -q -r "$PANEL/requirements.txt"

echo "== 2/5 · Clave SSH para entrar al PC =="
CLAVE="/home/$USUARIO/.ssh/id_ed25519_pc"
[ -f "$CLAVE" ] || sudo -u "$USUARIO" ssh-keygen -q -t ed25519 -N '' -C 'pi-recarga' -f "$CLAVE"
echo "   Clave publica (pasala a pc/preparar_pc.ps1 -ClavePi):"
echo "   $(cat "$CLAVE.pub")"

echo "== 3/5 · Configuracion (.env) =="
if [ ! -f "$PANEL/.env" ]; then
    sudo -u "$USUARIO" cp "$PANEL/.env.example" "$PANEL/.env"
    SECRETO="$("$PANEL/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sed -i "s|^WEB_SECRET=.*|WEB_SECRET=$SECRETO|" "$PANEL/.env"
    echo "   .env creado. Ahora la contrasena del panel:"
    (cd "$PANEL" && sudo -u "$USUARIO" venv/bin/python cambiar_password.py)
    echo "   Revisa PC_IP, PC_MAC, PC_DIR y los TG_* en $PANEL/.env"
else
    echo "   .env ya existe: no se toca"
fi
chmod 600 "$PANEL/.env"

echo "== 4/5 · Permisos de sudo (solo estos dos comandos) =="
# reboot: boton "Reiniciar Pi" del panel. restart: panel/actualizar.sh
cat > /etc/sudoers.d/recarga-web <<EOF
$USUARIO ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl restart recarga-web
EOF
chmod 440 /etc/sudoers.d/recarga-web
visudo -cf /etc/sudoers.d/recarga-web

echo "== 5/5 · Servicio =="
sed "s|/home/renox/ZeroDesk/panel|$PANEL|g; s|^User=.*|User=$USUARIO|" "$PANEL/recarga-web.service" > /etc/systemd/system/recarga-web.service
systemctl daemon-reload
# enable: arranca solo cada vez que se enciende la Pi.
systemctl enable recarga-web
systemctl restart recarga-web
sleep 3
systemctl --no-pager --lines=0 status recarga-web || true
echo
echo "Listo: http://$(tailscale ip -4 2>/dev/null || hostname -I | cut -d' ' -f1):$PUERTO"
