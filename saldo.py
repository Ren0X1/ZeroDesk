"""
Cuanto dinero hay en el monedero de Casa Ortega. No recarga ni toca nada.

    python saldo.py              # lo dice por consola
    python saldo.py --avisar     # y te lo manda por Telegram

Sirve para comprobar si una recarga entro de verdad cuando el script se ha
quedado sin saber que paso (por ejemplo, si el banco no devolvio el control).
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

import notificar
from recarga import (ARGS_POCA_RAM, ESPERA_MS, ESPERA_NAV_MS, UA, Fallo,
                     _aligerar, buscar_chromium, get_env, linea_saldo, login,
                     saldo_del_monedero, si_no)

load_dotenv(Path(__file__).resolve().with_name(".env"))

avisar = "--avisar" in sys.argv[1:]
headless = si_no(os.environ.get("CO_HEADLESS"), por_defecto=True)

chromium = buscar_chromium()
canal = None if chromium else ("chromium-headless-shell" if headless else None)

with sync_playwright() as p:
    navegador = p.chromium.launch(timeout=ESPERA_MS, headless=headless,
                                  executable_path=chromium, channel=canal,
                                  args=ARGS_POCA_RAM)
    try:
        contexto = navegador.new_context(viewport={"width": 1366, "height": 900},
                                         locale="es-ES", user_agent=UA)
        contexto.set_default_timeout(ESPERA_MS)
        contexto.set_default_navigation_timeout(ESPERA_NAV_MS)
        contexto.route("**/*", _aligerar)
        pagina = contexto.new_page()

        login(pagina, get_env("CO_USER"), get_env("CO_PASS"))
        saldo = saldo_del_monedero(pagina)
    finally:
        navegador.close()

if saldo:
    print(f"\nSaldo del monedero: {saldo} EUR\n")
else:
    print("\nNo se pudo leer el saldo.\n")

if avisar:
    notificar.enviar("\U0001f4b0 <b>Saldo del monedero</b>\n" + linea_saldo(saldo))

raise SystemExit(0 if saldo else 1)
