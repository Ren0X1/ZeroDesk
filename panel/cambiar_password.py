"""Cambia la contrasena de la web. Uso:  venv/bin/python cambiar_password.py
Despues: sudo systemctl restart recarga-web"""
import getpass
import re
from pathlib import Path

from werkzeug.security import generate_password_hash

env = Path(__file__).resolve().parent / ".env"
nueva = getpass.getpass("Contraseña nueva: ")
if nueva != getpass.getpass("Repitela: ") or len(nueva) < 6:
    raise SystemExit("No coinciden o tiene menos de 6 caracteres.")
texto = env.read_text(encoding="utf-8")
texto = re.sub(r"(?m)^WEB_PASSWORD_HASH=.*$", lambda _: f"WEB_PASSWORD_HASH={generate_password_hash(nueva)}", texto)
env.write_text(texto, encoding="utf-8")
print("Cambiada. Ahora: sudo systemctl restart recarga-web")
