"""
Recarga del monedero virtual de casaortega.com.

Pensado para correr solo (timer de systemd en la Raspberry Pi), pero sigue
valiendo a mano en Windows con recargar.bat.

Configuracion: el fichero .env que hay junto a este script.
    CO_USER=tu_email@ejemplo.com
    CO_PASS=tu_password
    CO_AMOUNT=10
    CO_CARD=...   CO_EXP=MM/AA   CO_CVV=...
    TG_TOKEN=...  TG_CHAT_ID=...      (avisos por Telegram)
Las variables de entorno mandan sobre el .env.

Opciones (por .env o por parametro):
    CO_HEADLESS   sin ventana. Por defecto: si en Linux, no en Windows.
    CO_CHROMIUM   ruta del Chromium a usar (en la Pi, /usr/bin/chromium).
    CO_SIMULAR    --simular : lo hace TODO menos pulsar Pagar. No cobra.
    --forzar      recarga aunque ya se haya recargado hoy.
    CO_DEBUG      vuelca gateway.json de la pasarela.

Solo recarga UNA VEZ AL DIA: estado.json guarda el dia de la ultima recarga
buena, asi que ni un reinicio de la Pi ni un lanzamiento a mano cobran dos
veces. No reintenta solo a proposito: aqui se mueve dinero y un reintento a
ciegas puede cobrar dos veces. Si falla, avisa por Telegram y para.

Pasos: login -> monedero -> deposito -> pasarela Redsys -> vuelta al comercio.
"""
import json
import os
import re
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import notificar

AQUI = Path(__file__).resolve().parent
load_dotenv(AQUI / ".env")

TZ = ZoneInfo(os.environ.get("CO_TZ", "Europe/Madrid"))

LOGIN_URL = "https://casaortega.com/es/iniciar-sesion"
WALLET_URL = "https://casaortega.com/es/module/wim_walletpayments/view"

OUT = AQUI / "out"
LOGS = AQUI / "logs"
ESTADO = AQUI / "estado.json"
OUT.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

MIN_AMOUNT = 6.99
MAX_AMOUNT = 40.0
LOGS_QUE_SE_GUARDAN = 21

# La Pi tarda ~40 s en cargar una pagina de casaortega tirando de swap, asi que
# los tiempos de espera de Playwright (30 s por defecto) se quedan cortos y
# revientan por nada. Se pueden ajustar con CO_ESPERA (en segundos).
ESPERA = max(15, int(os.environ.get("CO_ESPERA", "60")))
ESPERA_MS = ESPERA * 1000
ESPERA_NAV_MS = ESPERA_MS * 2          # cargar una pagina es lo mas lento
# Lo que se espera a que el banco devuelva el control. En un PC normal el 3DS
# resuelve en segundos; si en un minuto no ha vuelto, es que no va a volver.
ESPERA_BANCO = max(15, int(os.environ.get("CO_ESPERA_BANCO", "60")))
# Si un paso pasa de aqui sin terminar, se avisa por Telegram de que se atasca.
ATASCO = max(10, int(os.environ.get("CO_AVISO_ATASCO", "40")))

# Chromium en una Zero 2 W: 415 MB de RAM dan para muy poco, asi que se le
# quita todo lo que no haga falta para rellenar un formulario.
# OJO: nada de --single-process ni --no-zygote, que este Chromium se cae con
# SIGTRAP al arrancar (probado en la Pi).
ARGS_POCA_RAM = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--renderer-process-limit=1",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-breakpad",
    "--disable-sync",
    "--disable-translate",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--js-flags=--max-old-space-size=128",
    # La pasarela de un banco mira si el navegador huele a robot.
    "--disable-blink-features=AutomationControlled",
]

UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/152.0.0.0 Safari/537.36")

# Lo que no hace falta para rellenar un formulario y se come la RAM y el ancho
# de banda. Se puede apagar con CO_BLOQUEAR_RECURSOS=false si algo se rompe.
RECURSOS_PESADOS = ("image", "media", "font")
# En la pasarela NO se bloquea nada: el boton de Pagar (#divImgAceptar) es una
# imagen, y sin ella no se pondria visible y no se podria pulsar.
DOMINIOS_INTOCABLES = ("redsys.es", "sis.redsys.es")


def _aligerar(ruta):
    """Corta imagenes/video/fuentes, pero deja la pasarela del banco intacta."""
    peticion = ruta.request
    if any(d in peticion.url for d in DOMINIOS_INTOCABLES):
        return ruta.continue_()
    if peticion.resource_type in RECURSOS_PESADOS:
        return ruta.abort()
    return ruta.continue_()


class Fallo(Exception):
    """Algo ha salido mal y hay que avisar."""


class Vigilante:
    """Va contando en que paso vamos y avisa por Telegram si uno se eterniza.

    Corre en un hilo aparte porque el paso que se atasca esta bloqueado
    esperando a Playwright: desde el hilo principal no se podria avisar de
    nada hasta que ese paso terminase (que es justo lo que no pasa).

    Avisa UNA sola vez por paso, para no llenar el chat de mensajes.
    """

    def __init__(self, umbral=ATASCO):
        self.umbral = umbral
        self.paso = "arrancando"
        self.desde = time.monotonic()
        self.avisados = set()
        self._parar = threading.Event()
        self._hilo = None

    def empieza(self, nombre: str) -> None:
        """Marca el paso en el que estamos ahora."""
        self.paso = nombre
        self.desde = time.monotonic()
        print(f"[paso] {nombre}")

    @property
    def tardando(self) -> int:
        return int(time.monotonic() - self.desde)

    def arrancar(self):
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        return self

    def parar(self):
        self._parar.set()

    def _bucle(self):
        while not self._parar.wait(5):
            paso, tardando = self.paso, self.tardando
            if tardando >= self.umbral and paso not in self.avisados:
                self.avisados.add(paso)
                notificar.enviar(
                    "⏳ <b>Esto va lento</b>\n"
                    f"Llevo <b>{tardando}s</b> en: {notificar.esc(paso)}\n"
                    "<i>Sigo esperando; si no sale, te aviso del fallo.</i>"
                )


# ---------------------------------------------------------------- utilidades
class _Tee:
    """Escribe a la consola y al log del dia, con la hora delante de cada linea."""

    def __init__(self, *destinos):
        self.destinos = destinos
        self._linea_nueva = True

    def write(self, dato):
        if not dato:
            return
        salida = []
        for trozo in dato.splitlines(keepends=True):
            if self._linea_nueva and trozo.strip():
                salida.append(datetime.now(TZ).strftime("[%H:%M:%S] "))
            salida.append(trozo)
            self._linea_nueva = trozo.endswith("\n")
        texto = "".join(salida)
        for d in self.destinos:
            try:
                d.write(texto)
                d.flush()
            except Exception:
                pass

    def flush(self):
        for d in self.destinos:
            try:
                d.flush()
            except Exception:
                pass


def hoy() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def abrir_log():
    """Abre el log del dia y borra los viejos."""
    for viejo in sorted(LOGS.glob("recarga-*.log"))[:-LOGS_QUE_SE_GUARDAN]:
        try:
            viejo.unlink()
        except OSError:
            pass
    return open(LOGS / f"recarga-{hoy()}.log", "a", encoding="utf-8")


def cargar_estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_estado(estado: dict) -> None:
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def si_no(valor, por_defecto=False) -> bool:
    if valor is None or str(valor).strip() == "":
        return por_defecto
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "yes", "y", "on")


def get_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise Fallo(f"Falta la variable {name} en el .env")
    return val


def parse_amount(raw: str) -> str:
    """Valida importe y devuelve la cadena con punto decimal."""
    try:
        v = float(str(raw).replace(",", "."))
    except ValueError:
        raise Fallo(f"Importe invalido: {raw!r}")
    if v < MIN_AMOUNT or v > MAX_AMOUNT:
        raise Fallo(f"Importe {v} fuera del rango permitido ({MIN_AMOUNT} - {MAX_AMOUNT})")
    # Dos decimales, separador punto (el input es type=number)
    return f"{v:.2f}"


def buscar_chromium():
    """Navegador a usar, o None para el que trae Playwright.

    Por defecto se usa el de Playwright. El Chromium que trae Raspberry Pi OS
    (/usr/bin/chromium) IGNORA --headless y levanta el navegador entero con su
    interfaz, que en 415 MB de RAM no entra ni de broma: se queda a medias y
    acaba matando la pestana. Con CO_CHROMIUM se puede forzar uno concreto.
    """
    return (os.environ.get("CO_CHROMIUM") or "").strip() or None


def captura(page, nombre: str):
    """Screenshot para mandar por Telegram. Devuelve la ruta o None."""
    if page is None:
        return None
    destino = OUT / f"{nombre}.png"
    try:
        page.screenshot(path=str(destino))
        return str(destino)
    except Exception as exc:
        print(f"  (no se pudo capturar la pantalla: {exc})")
        return None


# ------------------------------------------------------------------- la web
def accept_cookies(page) -> None:
    """Acepta el banner cookiesplus si esta presente."""
    selectors = [
        "button.cookiesplus-accept",
        "#onetrust-accept-btn-handler",
        "button:has-text('Aceptar todas')",
        "button:has-text('Aceptar')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click()
                page.wait_for_timeout(400)
                return
        except PWTimeout:
            continue
        except Exception:
            continue


def login(page, email: str, password: str) -> None:
    print(f"[login] {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    accept_cookies(page)

    form = page.locator("form#login-form")
    form.locator("input[name='email']").fill(email)
    form.locator("input[name='password']").fill(password)
    form.locator("button#submit-login").click()

    # Tras login PrestaShop suele redirigir a /mi-cuenta o a la URL del parametro back
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)

    if "iniciar-sesion" in page.url:
        # Volcado del posible mensaje de error
        err = page.locator(".alert-danger, .alert.alert-warning").first
        msg = err.inner_text() if err.count() else "(sin mensaje)"
        raise Fallo(f"Login fallido. URL actual: {page.url} | Mensaje: {msg}")

    print(f"[login] OK -> {page.url}")


def open_deposit_form(page) -> None:
    print(f"[monedero] {WALLET_URL}")
    page.goto(WALLET_URL, wait_until="domcontentloaded")
    accept_cookies(page)
    page.wait_for_timeout(500)

    # El campo de importe puede estar dentro de un modal/colapsable que se
    # abre con el boton "DEPOSITAR FONDOS EN MONEDERO VIRTUAL".
    deposit_input = page.locator("#walletDepositValue")
    if not deposit_input.is_visible():
        try:
            page.get_by_role("button", name="DEPOSITAR FONDOS EN MONEDERO VIRTUAL").click()
        except Exception:
            page.locator("button.btn.btn-primary", has_text="DEPOSITAR").first.click()
        deposit_input.wait_for(state="visible", timeout=ESPERA_MS)


def submit_deposit(page, amount: str) -> None:
    print(f"[deposito] importe={amount}")
    page.locator("#walletDepositValue").fill(amount)

    # Submit del form #wimwallet-payment-form
    with page.expect_navigation(wait_until="domcontentloaded", timeout=ESPERA_NAV_MS):
        page.locator("form#wimwallet-payment-form button[type=submit]").first.click()

    print(f"[deposito] redirigido a: {page.url}")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def fill_card(page, card: str, exp: str, cvv: str, pagar: bool = True) -> None:
    """Rellena tarjeta/caducidad/CVV en la pasarela Redsys y pulsa Pagar."""
    page.wait_for_load_state("domcontentloaded")
    # Asegurar que los inputs estan visibles
    page.locator("#card-number").wait_for(state="visible", timeout=ESPERA_NAV_MS)

    card_digits = _digits(card)
    exp_digits = _digits(exp)  # ej. "01/28" -> "0128"
    cvv_digits = _digits(cvv)

    if len(card_digits) < 13 or len(card_digits) > 19:
        raise Fallo(f"Numero de tarjeta invalido (longitud {len(card_digits)})")
    if len(exp_digits) != 4:
        raise Fallo(f"Caducidad invalida {exp!r}; esperado formato MM/AA")
    if len(cvv_digits) not in (3, 4):
        raise Fallo(f"CVV invalido (longitud {len(cvv_digits)})")

    print("[redsys] rellenando tarjeta")
    # Usar press() en lugar de fill() para que se disparen los handlers de
    # formateo (Redsys auto-formatea con espacios y agrupa caducidad MM/AA).
    page.locator("#card-number").click()
    page.locator("#card-number").press_sequentially(card_digits, delay=20)

    page.locator("#card-expiration").click()
    page.locator("#card-expiration").press_sequentially(exp_digits, delay=20)

    page.locator("#card-cvv").click()
    page.locator("#card-cvv").press_sequentially(cvv_digits, delay=20)

    # Espera a que el boton Pagar se habilite (estaba deshabilitado en el screenshot)
    pay_btn = page.locator("#divImgAceptar")
    pay_btn.wait_for(state="visible", timeout=ESPERA_MS)
    page.wait_for_timeout(500)

    if not pagar:
        print("[simulacion] tarjeta rellenada; NO se pulsa Pagar")
        return

    print("[redsys] click en Pagar")
    pay_btn.click()


# Lo que pinta la pasarela cuando el banco tumba el pago. Si sale algo de esto
# no hace falta esperar al reloj: ya sabemos que no ha colado, y ademas sabemos
# que NO se ha cobrado nada (lo ha rechazado el banco, no se ha quedado a medias).
RECHAZOS = re.compile(
    r"(transacci[oó]n denegada[^.\n]*"
    r"|denegada por su entidad"
    r"|operaci[oó]n denegada"
    r"|no autorizada"
    r"|tarjeta caducada"
    r"|saldo insuficiente"
    r"|introduzca otro n[uú]mero de tarjeta)",
    re.I,
)


def motivo_de_rechazo(page):
    """Si la pasarela esta enseñando un rechazo, devuelve el texto. Si no, None."""
    try:
        if "casaortega.com" in page.url:
            return None            # ya hemos vuelto al comercio: no es rechazo
        texto = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None
    m = RECHAZOS.search(texto or "")
    return " ".join(m.group(0).split()) if m else None


def complete_payment(page, timeout_s: int = ESPERA_BANCO):
    """
    Tras pulsar Pagar viene el 3DS del banco. Espera a que la pasarela vuelva a
    casaortega.com o muestre un boton 'Continuar' que pulsamos automaticamente.

    Devuelve (ok, motivo):
      (True,  None)     el pago ha ido y hemos vuelto al comercio.
      (False, "texto")  el banco lo ha rechazado y lo dice en pantalla.
      (False, None)     se acabo el tiempo sin saber que ha pasado.

    La diferencia entre los dos ultimos importa: un rechazo del banco es
    seguro (no se ha cobrado nada), un timeout no (podria haberse cobrado).
    """
    print(f"[3ds] esperando a que vuelva del banco (max {timeout_s}s)...")
    deadline = time.monotonic() + timeout_s
    continuar_selectors = [
        "a:has-text('Continuar')",
        "button:has-text('Continuar')",
        "input[type=button][value*='Continuar' i]",
        "input[type=submit][value*='Continuar' i]",
    ]
    while time.monotonic() < deadline:
        url = page.url
        if "casaortega.com" in url:
            print(f"[ok] volvimos al comercio: {url}")
            return True, None

        # Si el banco ya ha dicho que no, no hay nada que esperar: la pasarela
        # se queda en su pantalla de error y agotariamos el reloj para nada.
        motivo = motivo_de_rechazo(page)
        if motivo:
            print(f"[rechazo] el banco lo ha tumbado: {motivo}")
            return False, motivo

        for sel in continuar_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=400):
                    print(f"[redsys] click en Continuar via {sel}")
                    try:
                        with page.expect_navigation(wait_until="domcontentloaded", timeout=ESPERA_MS):
                            btn.click()
                    except PWTimeout:
                        pass
                    if "casaortega.com" in page.url:
                        print(f"[ok] volvimos al comercio: {page.url}")
                        return True, None
                    break
            except Exception:
                continue
        page.wait_for_timeout(1500)
    print("[timeout] no se completo el flujo en el tiempo previsto.")
    return False, None


# El modulo del monedero pinta el saldo aqui: "El saldo actual de tu monedero
# virtual es: 352.00€". OJO con buscar "el primer importe con € de la pagina":
# esto es una tienda y hay 277 precios por medio.
SALDO_CAJA = ".wallet-current-balance"
SALDO_FRASE = re.compile(
    r"saldo actual de tu monedero virtual es[:\s]*([\d.,]+)\s*€", re.I)
IMPORTE = re.compile(r"(\d[\d.,]*)\s*€")


def _euros(bruto: str) -> str:
    """Normaliza el importe a la espanola: '352.00' -> '352,00'.

    La web lo escribe con punto decimal, pero por si algun dia cambia se busca
    el separador que deje dos digitos detras y se tira el resto.
    """
    s = (bruto or "").strip()
    m = re.search(r"[.,](\d{2})$", s)
    if not m:
        return re.sub(r"[.,]", "", s)
    entero = re.sub(r"[.,]", "", s[:m.start()])
    return f"{entero},{m.group(1)}"


def saldo_en_pantalla(page):
    """Saca el saldo de la pagina que ya esta abierta, sin navegar a ningun lado.

    Se usa cuando ya estamos en el monedero, para no cargarlo dos veces.
    """
    if page is None:
        return None
    # 1) La caja donde el modulo lo pinta.
    try:
        caja = page.locator(SALDO_CAJA).first
        if caja.count():
            m = IMPORTE.search(caja.inner_text(timeout=ESPERA_MS))
            if m:
                return _euros(m.group(1))
    except Exception as exc:
        print(f"  (no estaba {SALDO_CAJA}: {exc})")
    # 2) Si le cambian la clase, la frase sigue estando.
    try:
        m = SALDO_FRASE.search(page.locator("body").inner_text(timeout=ESPERA_MS))
        if m:
            return _euros(m.group(1))
    except Exception as exc:
        print(f"  (no se pudo leer el saldo de la pagina: {exc})")
    return None


def saldo_del_monedero(page):
    """Va al monedero y lee el saldo. None si no se puede.

    Se llama tambien cuando algo ha fallado, asi que no puede reventar nunca:
    si no hay sesion o la pagina no responde, devuelve None y ya esta.
    """
    if page is None:
        return None
    try:
        page.goto(WALLET_URL, wait_until="domcontentloaded", timeout=ESPERA_NAV_MS)
        return saldo_en_pantalla(page)
    except Exception as exc:
        print(f"  (no se pudo leer el saldo: {exc})")
        return None


def linea_saldo(saldo) -> str:
    """La linea del saldo para el aviso de Telegram."""
    if saldo:
        return f"\U0001f4b0 Saldo del monedero: <b>{notificar.esc(saldo)} €</b>\n"
    return "\U0001f4b0 Saldo del monedero: <i>no se pudo leer</i>\n"


def dump_gateway(page) -> None:
    """Vuelca info de la pasarela: url, inputs y los de sus iframes."""
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)

    info = {"url": page.url, "frames": []}

    def serialize_inputs(frame):
        try:
            return frame.evaluate(
                """() => Array.from(document.querySelectorAll('input, select, button, iframe')).map(i => ({
                    tag: i.tagName.toLowerCase(),
                    type: i.type || null,
                    name: i.name || null,
                    id: i.id || null,
                    placeholder: i.placeholder || null,
                    cls: i.getAttribute('class') || null,
                    aria: i.getAttribute('aria-label') || null,
                    src: i.tagName.toLowerCase() === 'iframe' ? i.src : null,
                    text: i.tagName.toLowerCase() === 'button' ? (i.innerText || '').trim().slice(0,80) : null,
                }))"""
            )
        except Exception as e:
            return [{"error": str(e)}]

    for fr in page.frames:
        info["frames"].append({
            "name": fr.name,
            "url": fr.url,
            "is_main": fr == page.main_frame,
            "items": serialize_inputs(fr),
        })

    (OUT / "gateway.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[pasarela] volcado guardado en {OUT / 'gateway.json'}")


# ------------------------------------------------------------------ arranque
def main() -> int:
    args = sys.argv[1:]
    forzar = "--forzar" in args
    simular = "--simular" in args or si_no(os.environ.get("CO_SIMULAR"))
    headless = si_no(os.environ.get("CO_HEADLESS"), por_defecto=(os.name != "nt"))
    debug = si_no(os.environ.get("CO_DEBUG"))
    aligerar = si_no(os.environ.get("CO_BLOQUEAR_RECURSOS"), por_defecto=True)

    estado = cargar_estado()
    if estado.get("ultima_ok") == hoy() and not forzar and not simular:
        print(f"[saltado] el monedero ya se recargo hoy ({hoy()}). "
              f"Con --forzar se recarga otra vez.")
        return 0

    page = browser = None
    # Las pruebas del fallo se recogen DENTRO del bloque de Playwright, que es
    # donde el navegador todavia esta vivo, y se usan luego en el aviso.
    foto_fallo = saldo_fallo = None
    saldo_antes = None
    inicio = time.monotonic()
    vigi = Vigilante().arrancar()
    vigi.empieza("leer la configuracion")

    try:
        email = get_env("CO_USER")
        password = get_env("CO_PASS")
        amount = parse_amount(os.environ.get("CO_AMOUNT", "10"))
        card = get_env("CO_CARD")
        exp = get_env("CO_EXP")
        cvv = get_env("CO_CVV")

        donde = socket.gethostname()
        notificar.enviar(
            ("\U0001f9ea <b>Simulacion en marcha</b>\n" if simular else
             "\U0001f504 <b>Recargando el monedero</b>\n")
            + f"Importe: <b>{notificar.esc(amount)} €</b>\n"
            f"Equipo: {notificar.esc(donde)}\n"
            f"<i>Te aviso al terminar, y tambien si se atasca.</i>"
        )

        chromium = buscar_chromium()
        # El "headless shell" es un Chromium pelado, sin nada de interfaz: en la
        # Pi es la diferencia entre arrancar y no arrancar. Solo se puede pedir
        # por canal, y el canal no se lleva con executable_path.
        canal = None if chromium else (
            (os.environ.get("CO_CANAL") or "chromium-headless-shell").strip() or None
        ) if headless else None

        print(f"[arranque] importe={amount} headless={headless} simular={simular}")
        print(f"[arranque] navegador={chromium or canal or '(chromium de Playwright)'}")

        with sync_playwright() as p:
            vigi.empieza("abrir el navegador")
            browser = p.chromium.launch(
                timeout=ESPERA_MS,
                headless=headless,
                executable_path=chromium,
                channel=canal,
                args=ARGS_POCA_RAM,
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                locale="es-ES",
                user_agent=UA,
            )
            context.set_default_timeout(ESPERA_MS)
            context.set_default_navigation_timeout(ESPERA_NAV_MS)

            if aligerar:
                # Fotos, videos y tipografias no hacen falta para rellenar un
                # formulario, y en la Pi son la diferencia entre ir y no ir.
                context.route("**/*", _aligerar)

            page = context.new_page()

            try:
                vigi.empieza("iniciar sesion")
                login(page, email, password)

                vigi.empieza("abrir el monedero")
                open_deposit_form(page)

                # Ya estamos en el monedero: el saldo se lee de aqui, sin
                # volver a cargar la pagina.
                saldo_antes = saldo_en_pantalla(page)
                notificar.enviar(
                    "\U0001f511 <b>Sesion iniciada</b>\n"
                    + linea_saldo(saldo_antes)
                    + f"Voy a meter <b>{notificar.esc(amount)} \u20ac</b>..."
                )

                vigi.empieza("enviar el deposito")
                submit_deposit(page, amount)
                if debug:
                    dump_gateway(page)

                vigi.empieza("rellenar la tarjeta en Redsys")
                fill_card(page, card, exp, cvv, pagar=not simular)

                if simular:
                    foto = captura(page, "simulacion")
                    saldo = saldo_del_monedero(page)
                    notificar.enviar(
                        "\U0001f9ea <b>Simulacion de recarga</b>\n"
                        f"El recorrido entero funciona con "
                        f"<b>{notificar.esc(amount)} €</b>.\n"
                        + linea_saldo(saldo) +
                        "<i>No se ha pulsado Pagar: no se ha cobrado nada.</i>",
                        foto,
                    )
                    print(f"[fin] simulacion terminada sin pagar (saldo: {saldo})")
                    return 0

                vigi.empieza("esperar la respuesta del banco")
                notificar.enviar(
                    "\U0001f3e6 <b>Pagando</b>\n"
                    "Tarjeta metida y <b>Pagar</b> pulsado. Esperando al "
                    f"banco (maximo {ESPERA_BANCO}s)..."
                )
                ok, motivo = complete_payment(page)
                if not ok:
                    captura(page, "timeout")
                    if motivo:
                        # El banco ha dicho que no: es seguro que no ha cobrado.
                        raise Fallo(f"El banco ha rechazado el pago: {motivo}")
                    raise Fallo(
                        f"El pago no volvio a casaortega en {ESPERA_BANCO}s y la "
                        "pasarela no dijo por que. OJO: puede haberse cobrado "
                        "igualmente, mira el monedero antes de reintentar."
                    )

                vigi.empieza("comprobar el saldo")
                saldo = saldo_del_monedero(page)
                foto = captura(page, "ok")

                estado["ultima_ok"] = hoy()
                estado["ultimo_importe"] = amount
                estado["ultima_hora"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                estado["ultimo_saldo"] = saldo
                estado.pop("ultimo_error", None)
                guardar_estado(estado)

                tardanza = int(time.monotonic() - inicio)
                texto = ("✅ <b>Monedero recargado</b>\n"
                         f"Importe: <b>{notificar.esc(amount)} €</b>\n")
                if saldo_antes:
                    ahora = notificar.esc(saldo) if saldo else "?"
                    texto += (f"Antes: {notificar.esc(saldo_antes)} €  →  "
                              f"ahora: <b>{ahora} €</b>\n")
                else:
                    texto += linea_saldo(saldo)
                texto += f"<i>Casa Ortega · {tardanza}s</i>"
                notificar.enviar(texto, foto)

                print(f"[fin] recarga completada en {tardanza}s (saldo: {saldo})")
                return 0
            except Exception:
                # Aqui dentro el navegador sigue vivo: es el unico momento
                # en el que se puede fotografiar el fallo y leer el saldo.
                # La captura primero, que ensena donde se quedo; leer el
                # saldo navega a otra pagina y borraria la prueba.
                foto_fallo = captura(page, "error")
                saldo_fallo = saldo_del_monedero(page)
                raise

    except Exception as exc:
        estado["ultimo_fallo"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        estado["ultimo_error"] = f"{vigi.paso}: {exc}"
        guardar_estado(estado)

        # Playwright ya esta cerrado: se usan las pruebas de dentro del bloque.
        foto, saldo = foto_fallo, saldo_fallo
        notificar.enviar(
            "❌ <b>No se pudo recargar el monedero</b>\n"
            f"Se atasco en: <b>{notificar.esc(vigi.paso)}</b> tras {vigi.tardando}s\n"
            f"<code>{notificar.esc(str(exc)[:600])}</code>\n"
            + linea_saldo(saldo) +
            "\n<i>No se reintenta solo. Revisa el monedero por si acaso y, si "
            "hace falta, recarga a mano.</i>",
            foto,
        )
        print(f"[ERROR] {vigi.paso}: {exc}")
        return 1

    finally:
        vigi.parar()
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    _log = abrir_log()
    sys.stdout = _Tee(sys.__stdout__, _log)
    sys.stderr = _Tee(sys.__stderr__, _log)
    try:
        codigo = main()
    except KeyboardInterrupt:
        print("[corte] cancelado a mano")
        codigo = 130
    finally:
        _log.close()
    sys.exit(codigo)
