"""
Saca tu TG_CHAT_ID: escribele algo a tu bot en Telegram y ejecuta esto.

    python sacar_chat_id.py

Lee TG_TOKEN del .env (o del entorno) y lista los chats que le han hablado.
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

token = (os.environ.get("TG_TOKEN") or "").strip()
if not token:
    sys.exit("Falta TG_TOKEN en el .env")

r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
datos = r.json()
if not datos.get("ok"):
    sys.exit(f"Telegram responde: {datos}")

vistos = {}
for upd in datos.get("result", []):
    msg = upd.get("message") or upd.get("channel_post") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        vistos[chat["id"]] = chat

if not vistos:
    sys.exit("Ningun chat todavia. Abre Telegram, buscale por su @usuario, "
             "dale a INICIAR y mandale un 'hola'. Luego repite esto.")

print("Chats que le han hablado a tu bot:\n")
for cid, chat in vistos.items():
    quien = chat.get("username") or chat.get("first_name") or chat.get("title") or "?"
    print(f"  TG_CHAT_ID={cid}    ({chat.get('type')}, @{quien})")
print("\nCopia el numero en el .env como TG_CHAT_ID.")
