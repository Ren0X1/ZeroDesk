"""
Avisos por Telegram.

Se configura en el .env que hay junto a este fichero:
    TG_TOKEN=123456789:AA...      (el que da @BotFather)
    TG_CHAT_ID=123456789          (tu id; lo saca sacar_chat_id.py)

Si falta la configuracion no se revienta: se avisa por consola y se sigue.
Nunca deja que un fallo de Telegram tumbe la recarga.
"""
import html
import os

import requests

API = "https://api.telegram.org/bot{token}/{metodo}"
TIMEOUT = 20


def esc(texto) -> str:
    """Escapa para el parse_mode HTML de Telegram."""
    return html.escape(str(texto), quote=False)


def _config():
    token = (os.environ.get("TG_TOKEN") or "").strip()
    chat = (os.environ.get("TG_CHAT_ID") or "").strip()
    return token, chat


def enviar(texto: str, foto=None) -> bool:
    """Manda un mensaje (y opcionalmente una captura). Devuelve si se pudo."""
    token, chat = _config()
    if not token or not chat:
        print("[telegram] sin TG_TOKEN/TG_CHAT_ID: no se manda nada")
        return False

    try:
        r = requests.post(
            API.format(token=token, metodo="sendMessage"),
            data={"chat_id": chat, "text": texto, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=TIMEOUT,
        )
        if not r.ok:
            print(f"[telegram] sendMessage {r.status_code}: {r.text[:200]}")
            return False
        print("[telegram] aviso enviado")
    except Exception as exc:
        print(f"[telegram] no se pudo avisar: {exc}")
        return False

    if foto and os.path.exists(foto):
        try:
            with open(foto, "rb") as f:
                r = requests.post(
                    API.format(token=token, metodo="sendPhoto"),
                    data={"chat_id": chat},
                    files={"photo": (os.path.basename(foto), f, "image/png")},
                    timeout=TIMEOUT * 3,
                )
            if not r.ok:
                print(f"[telegram] sendPhoto {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            print(f"[telegram] no se pudo mandar la captura: {exc}")

    return True
