#!/usr/bin/env bash
# Instala el Panel remoto RNX PC en la Raspberry Pi.
#
# Desde la carpeta panel/ del repo, UNA VEZ:
#     sudo bash instalar.sh
# Es idempotente: se puede relanzar para actualizar sin perder la configuracion.
set -euo pipefail

USUARIO="${SUDO_USER:-renox}"
ORIGEN="$(cd "$(dirname "$0")" && pwd)"
DESTINO="/home/$USUARIO/recarga-web"
PUERTO=6678

echo "== 1/6 · Ficheros en $DESTINO =="
install -d -o "$USUARIO" -g "$USUARIO" -m 700 "$DESTINO" "$DESTINO/templates" "$DESTINO/static"
install -o "$USUARIO" -g "$USUARIO" -m 644 "$ORIGEN"/app.py "$ORIGEN"/cambiar_password.py "$ORIGEN"/requirements.txt "$ORIGEN"/.env.example "$DESTINO/"
install -o "$USUARIO" -g "$USUARIO" -m 644 "$ORIGEN"/templates/*.html "$DESTINO/templates/"
install -o "$USUARIO" -g "$USUARIO" -m 644 "$ORIGEN"/static/* "$DESTINO/static/"

echo "== 2/6 · Entorno de Python =="
[ -x "$DESTINO/venv/bin/python" ] || sudo -u "$USUARIO" python3 -m venv "$DESTINO/venv"
sudo -u "$USUARIO" "$DESTINO/venv/bin/pip" install -q -r "$DESTINO/requirements.txt"

echo "== 3/6 · Clave SSH para entrar al PC =="
CLAVE="/home/$USUARIO/.ssh/id_ed25519_pc"
[ -f "$CLAVE" ] || sudo -u "$USUARIO" ssh-keygen -q -t ed25519 -N '' -C 'pi-recarga' -f "$CLAVE"
echo "   Clave publica (pasala a pc/preparar_pc.ps1 -ClavePi):"
echo "   $(cat "$CLAVE.pub")"

echo "== 4/6 · Configuracion (.env) =="
if [ ! -f "$DESTINO/.env" ]; then
    sudo -u "$USUARIO" cp "$DESTINO/.env.example" "$DESTINO/.env"
    SECRETO="$("$DESTINO/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sed -i "s|^WEB_SECRET=.*|WEB_SECRET=$SECRETO|" "$DESTINO/.env"
    echo "   .env creado. Ahora la contrasena del panel:"
    (cd "$DESTINO" && sudo -u "$USUARIO" venv/bin/python cambiar_password.py)
    echo "   Revisa PC_IP, PC_MAC y los TG_* en $DESTINO/.env"
else
    echo "   .env ya existe: no se toca"
fi
chmod 600 "$DESTINO/.env"

echo "== 5/6 · Permiso para reiniciar la Pi desde el panel =="
echo "$USUARIO ALL=(root) NOPASSWD: /usr/bin/systemctl reboot" > /etc/sudoers.d/recarga-web
chmod 440 /etc/sudoers.d/recarga-web
visudo -cf /etc/sudoers.d/recarga-web

echo "== 6/6 · Servicio =="
sed "s|/home/renox|/home/$USUARIO|g; s|^User=.*|User=$USUARIO|" "$ORIGEN/recarga-web.service" > /etc/systemd/system/recarga-web.service
systemctl daemon-reload
# enable: arranca solo cada vez que se enciende la Pi.
systemctl enable recarga-web
systemctl restart recarga-web
sleep 3
systemctl --no-pager --lines=0 status recarga-web || true
echo
echo "Listo: http://$(tailscale ip -4 2>/dev/null || hostname -I | cut -d' ' -f1):$PUERTO"
