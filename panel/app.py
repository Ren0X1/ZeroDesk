"""
ZeroDesk: panel en la Pi para vigilar y manejar el PC y el bot de Discord
desde cualquier sitio (por Tailscale).

Que vigila (hilo `monitor`, siempre en marcha):
  - el PC: si esta encendido (su SSH contesta) y, cada 10 min, CPU, RAM, GPU,
    tiempo encendido, si hay sesion iniciada y si el jiggler esta activo;
  - la propia Pi: CPU, RAM y temperatura, cada minuto;
  - casaortega.com, la pasarela Redsys y la API de Telegram, cada 5 min.
Las lecturas se guardan 30 dias en SQLite (telemetria.db) para las graficas.

Que hace (cada accion es un "trabajo" en un hilo aparte, de uno en uno):
  - encender (Wake on LAN) y apagar el PC;
  - recargar / probar la recarga del monedero de Casa Ortega: enciende el PC
    si hace falta, lanza recarga.py por SSH y, si lo encendio el panel, lo
    vuelve a apagar;
  - activar y desactivar el jiggler (directo, no es un trabajo);
  - el bot de Discord (el RGL BOT, en la misma Pi): estado, arrancar, parar,
    reiniciar, actualizar (su startup.sh), logs, su .env y la memoria de su IA.

El candado de "una recarga al dia" lo pone recarga.py en el PC: por muchas
veces que se pulse el boton, solo se cobra una.

Configuracion: .env junto a este fichero (ver .env.example).
"""
import contextlib
import ctypes
import functools
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from waitress import serve
from werkzeug.security import check_password_hash

AQUI = Path(__file__).resolve().parent

# La Pi tiene 415 MB. glibc abre una zona de memoria por hilo y no devuelve lo
# que se libera: el panel llegaba a ~170 MB. Dos zonas como mucho y, en cada
# vuelta del monitor, malloc_trim devuelve al sistema lo que sobra.
try:
    _libc = ctypes.CDLL("libc.so.6")
    _libc.mallopt(-8, 2)  # M_ARENA_MAX
except OSError:  # fuera de Linux (pruebas en Windows)
    _libc = None


def devolver_memoria() -> None:
    if _libc:
        _libc.malloc_trim(0)

load_dotenv(AQUI / ".env")

PUERTO = int(os.environ.get("WEB_PORT", "6678"))
HASH = os.environ["WEB_PASSWORD_HASH"]
PC_IP = os.environ.get("PC_IP", "192.168.100.10")
PC_MAC = os.environ.get("PC_MAC", "FC:34:97:E6:DD:1D")
PC_USER = os.environ.get("PC_USER", "alex-")
PC_KEY = os.path.expanduser(os.environ.get("PC_KEY", "~/.ssh/id_ed25519_pc"))
PC_DIR = os.environ.get("PC_DIR", r"C:\Users\alex-\casaortega")
BROADCAST = os.environ.get("WOL_BROADCAST", "192.168.100.255")
IMPORTE = os.environ.get("IMPORTE", "10")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

ESPERA_ARRANQUE = 240   # s que se espera a que Windows responda tras el WoL
ESPERA_APAGADO = 180    # s que se espera a que el PC deje de responder
ESPERA_RECARGA = 600    # s maximos para todo recarga.py
CADA_PC = 15            # s entre comprobaciones de si el PC esta encendido
CADA_PI = 60            # s entre lecturas de la Pi
CADA_METRICAS = int(os.environ.get("CADA_METRICAS", "600"))  # s entre lecturas del PC
CADA_WEBS = 300         # s entre comprobaciones de casaortega/redsys/telegram
RETENCION = 30 * 86400
EVENTOS = 80
INTENTOS = 5            # fallos de contrasena antes de bloquear
BLOQUEO = 15 * 60       # s de bloqueo

ESTADO = AQUI / "estado.json"       # saldo y ultima recarga
HISTORIAL = AQUI / "eventos.json"   # registro de operaciones
DB = AQUI / "telemetria.db"

# rango -> (segundos que abarca, tamano de cada tramo de la grafica)
RANGOS = {"1h": (3600, 60), "6h": (6 * 3600, 300), "24h": (86400, 900),
          "7d": (7 * 86400, 3600), "30d": (30 * 86400, 4 * 3600)}

# Version del repo: la hoja de estilos la lleva en la URL para que el navegador
# no se quede con la antigua tras actualizar.
try:
    VERSION = (AQUI.parent / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    VERSION = "0"

UA = "Mozilla/5.0 (X11; Linux aarch64) PanelRNX/2.0"
NOMBRE = "ZeroDesk"

app = Flask(__name__)
app.secret_key = os.environ["WEB_SECRET"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
)


# --- ficheros ---------------------------------------------------------------

def leer_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def escribir_json(ruta: Path, datos) -> None:
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta)


def guardar_estado(**cambios) -> None:
    estado = leer_json(ESTADO, {})
    estado.update(cambios)
    escribir_json(ESTADO, estado)


eventos: list = leer_json(HISTORIAL, [])
_io = threading.Lock()


def evento(nivel: str, texto: str) -> None:
    """nivel: ok | err | info"""
    with _io:
        eventos.insert(0, {"t": int(time.time()), "nivel": nivel, "texto": texto})
        del eventos[EVENTOS:]
        escribir_json(HISTORIAL, eventos)


# --- telemetria (SQLite) -----------------------------------------------------

def db():
    """Conexion para usar con `with db() as con:`; se cierra al salir."""
    con = sqlite3.connect(DB, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS muestras (origen TEXT, t INTEGER, cpu REAL, ram REAL, temp REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS i_muestras ON muestras (origen, t)")
    return _cerrando(con)


@contextlib.contextmanager
def _cerrando(con):
    try:
        with con:  # commit o rollback
            yield con
    finally:
        con.close()


def guardar_muestra(origen, cpu, ram, temp) -> None:
    ahora = int(time.time())
    with _io, db() as con:
        con.execute("INSERT INTO muestras VALUES (?,?,?,?,?)", (origen, ahora, cpu, ram, temp))
        con.execute("DELETE FROM muestras WHERE t < ?", (ahora - RETENCION,))


def series(rango: str) -> dict:
    abarca, tramo = RANGOS[rango]
    desde = int(time.time()) - abarca
    out = {}
    with db() as con:
        for origen in ("pc", "pi"):
            filas = con.execute(
                "SELECT (t / ?) * ? AS b, AVG(cpu), AVG(ram), AVG(temp) FROM muestras "
                "WHERE origen = ? AND t >= ? GROUP BY b ORDER BY b",
                (tramo, tramo, origen, desde)).fetchall()
            out[origen] = [[b + tramo // 2,
                            None if c is None else round(c, 1),
                            None if r is None else round(r, 1),
                            None if tp is None else round(tp, 1)] for b, c, r, tp in filas]
    return {"rango": rango, "desde": desde, "hasta": int(time.time()), "tramo": tramo,
            "cada": {"pc": CADA_METRICAS, "pi": CADA_PI}, **out}


# --- PC -------------------------------------------------------------------

def pc_encendido(timeout=1.5) -> bool:
    """Encendido = su SSH contesta. Un ping no vale: responde antes de que
    Windows este listo para recibir ordenes."""
    try:
        with socket.create_connection((PC_IP, 22), timeout=timeout):
            return True
    except OSError:
        return False


def wake_on_lan() -> None:
    mac = bytes.fromhex(PC_MAC.replace(":", "").replace("-", ""))
    paquete = b"\xff" * 6 + mac * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for destino in (BROADCAST, "255.255.255.255"):
            for puerto in (9, 7):
                s.sendto(paquete, (destino, puerto))


def ssh(comando: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["ssh", "-i", PC_KEY,
         "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new",
         "-o", "ConnectTimeout=10",
         "-o", "ServerAliveInterval=15",
         f"{PC_USER}@{PC_IP}", comando],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")


def ssh_corto(comando: str, timeout=40) -> tuple[int, str]:
    proc = ssh(comando)
    try:
        salida, _ = proc.communicate(timeout=timeout)
        return proc.returncode, salida.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, ""


def ps_pc(script: str, *args) -> str:
    return (f'powershell -NoProfile -ExecutionPolicy Bypass -File '
            f'"{PC_DIR}\\pc\\{script}" ' + " ".join(args))


def telegram(texto: str) -> None:
    """Avisos de lo que pasa en la Pi. De la recarga ya avisa recarga.py."""
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    try:
        datos = urllib.parse.urlencode(
            {"chat_id": TG_CHAT_ID, "text": texto, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", datos, timeout=15)
    except Exception:
        pass


# --- monitor ----------------------------------------------------------------

servicios = {
    "pc": {"estado": "desconocido", "desde": None, "comprobado": None, "info": None},
    "casaortega": {"ok": None, "ms": None, "comprobado": None, "error": None},
    "redsys": {"ok": None, "ms": None, "comprobado": None, "error": None},
    "telegram": {"ok": None, "ms": None, "comprobado": None, "error": None, "bot": None},
    "pi": {},
    "red": {"ip_publica": None, "comprobado": None},
}
despertar_monitor = threading.Event()
_ultima_muestra = 0.0


def comprobar_web(nombre: str, url: str, valida=None) -> None:
    t0 = time.monotonic()
    s = servicios[nombre]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            cuerpo = r.read(200_000)
            codigo = r.status
        ok = codigo < 400 and (valida(cuerpo) if valida else True)
        s.update(ok=ok, error=None if ok else f"HTTP {codigo}")
    except urllib.error.HTTPError as e:
        # Un 4xx es que el servidor esta ahi y contesta: para Redsys vale.
        ok = nombre == "redsys" and e.code < 500
        s.update(ok=ok, error=None if ok else f"HTTP {e.code}")
    except Exception as e:
        s.update(ok=False, error=(str(e) or type(e).__name__)[:120])
    s.update(ms=int((time.monotonic() - t0) * 1000), comprobado=int(time.time()))


def comprobar_telegram() -> None:
    s = servicios["telegram"]
    if not TG_TOKEN:
        s.update(ok=False, error="Sin token", comprobado=int(time.time()))
        return

    def valida(cuerpo):
        datos = json.loads(cuerpo)
        s["bot"] = "@" + datos.get("result", {}).get("username", "")
        return datos.get("ok")
    comprobar_web("telegram", f"https://api.telegram.org/bot{TG_TOKEN}/getMe", valida)


def ip_publica() -> None:
    """La IP publica de casa (la Pi y el PC salen por el mismo router)."""
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            ip = r.read(64).decode().strip()
        if re.fullmatch(r"[\d.]{7,15}|[0-9a-fA-F:]{3,39}", ip):
            servicios["red"].update(ip_publica=ip, comprobado=int(time.time()))
    except Exception:
        pass


_cpu_prev = None


def stats_pi(guardar: bool) -> bool:
    """Lee la Pi. True si ha guardado la muestra (la primera lectura no
    puede: la CPU se calcula por diferencia con la anterior)."""
    global _cpu_prev
    try:
        campos = list(map(int, open("/proc/stat").readline().split()[1:]))
        inactivo, total = campos[3] + campos[4], sum(campos)
        cpu = None
        if _cpu_prev:
            dt = total - _cpu_prev[1]
            cpu = round(100 * (1 - (inactivo - _cpu_prev[0]) / dt)) if dt else 0
        _cpu_prev = (inactivo, total)
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        temp = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
        uptime = float(open("/proc/uptime").read().split()[0])
        usada = (mem["MemTotal"] - mem["MemAvailable"]) / 1024
        servicios["pi"] = {
            "cpu": cpu, "ram_usada": round(usada), "ram_total": round(mem["MemTotal"] / 1024),
            "ram_pct": round(100 * usada * 1024 / mem["MemTotal"]),
            "temp": round(temp, 1), "uptime_s": int(uptime), "comprobado": int(time.time()),
        }
        if guardar and cpu is not None:
            guardar_muestra("pi", cpu, servicios["pi"]["ram_pct"], round(temp, 1))
            return True
    except Exception:
        pass
    return False


def muestra_pc() -> None:
    """CPU, RAM, GPU, sesion y jiggler del PC. Tambien trae el estado.json de
    la recarga, por si se ha recargado a mano con recargar.bat."""
    global _ultima_muestra
    _ultima_muestra = time.monotonic()
    codigo, salida = ssh_corto(ps_pc("estado_pc.ps1"))
    linea = next((l for l in salida.splitlines() if l.startswith("{")), None)
    if codigo != 0 or not linea:
        return
    try:
        d = json.loads(linea)
    except ValueError:
        return
    ram_pct = round(100 * (1 - d["ram_libre"] / d["ram_total"])) if d.get("ram_total") else None
    servicios["pc"]["info"] = {
        "cpu": d.get("cpu"), "ram_pct": ram_pct,
        "ram_usada": round(d["ram_total"] - d["ram_libre"], 1), "ram_total": d.get("ram_total"),
        "gpu_temp": d.get("gpu_temp"), "gpu_uso": d.get("gpu_uso"),
        "uptime_s": d.get("uptime_s"), "sesion": d.get("sesion"), "jiggler": d.get("jiggler"),
        "disco_total": d.get("disco_total"), "disco_libre": d.get("disco_libre"),
        "disco_pct": round(100 * (1 - d["disco_libre"] / d["disco_total"])) if d.get("disco_total") else None,
        "equipo": d.get("equipo"), "so": d.get("so"), "arq": d.get("arq"),
        "cpu_nombre": d.get("cpu_nombre"), "gpu_nombre": d.get("gpu_nombre"), "ip": d.get("ip"),
        "t": int(time.time()),
    }
    guardar_muestra("pc", d.get("cpu"), ram_pct, d.get("gpu_temp"))

    rec = d.get("recarga") or {}
    if rec.get("ultima_hora"):
        propia = leer_json(ESTADO, {})
        hora = rec["ultima_hora"][:16]
        if hora > (propia.get("ultima") or ""):
            cambios = {"ultima": hora}
            if rec.get("ultimo_saldo"):
                cambios["saldo"] = rec["ultimo_saldo"]
            guardar_estado(**cambios)


def monitor() -> None:
    ultima_web = ultima_pi = 0.0
    while True:
        if stats_pi(time.monotonic() - ultima_pi >= CADA_PI):
            ultima_pi = time.monotonic()

        pc = servicios["pc"]
        antes = pc["estado"]
        if not (trabajo_actual and trabajo_actual.d["activo"] and trabajo_actual.pc_transicion):
            ahora = "encendido" if pc_encendido() else "apagado"
            if ahora != antes:
                pc["desde"] = int(time.time()) if antes != "desconocido" else None
                pc["estado"] = ahora
                if ahora == "apagado":
                    pc["info"] = None
            pc["comprobado"] = int(time.time())
            if ahora == "encendido" and (
                    antes != "encendido" or time.monotonic() - _ultima_muestra >= CADA_METRICAS):
                try:
                    muestra_pc()
                except Exception:
                    pass

        if time.monotonic() - ultima_web >= CADA_WEBS:
            ultima_web = time.monotonic()
            comprobar_web("casaortega", "https://casaortega.com/es/")
            comprobar_web("redsys", "https://sis.redsys.es/sis/realizarPago")
            comprobar_telegram()
            ip_publica()

        devolver_memoria()
        despertar_monitor.wait(CADA_PC)
        despertar_monitor.clear()


# --- trabajos ---------------------------------------------------------------

LINEA_UTIL = re.compile(r"^\[\d\d:\d\d:\d\d\] \[\w")
cerrojo = threading.Lock()
trabajo_actual = None


class Trabajo:
    def __init__(self, tipo, titulo, pasos):
        self.pc_transicion = False   # mientras enciende/apaga, el monitor no pisa el estado
        self.d = {
            "id": secrets.token_hex(4), "tipo": tipo, "titulo": titulo,
            "activo": True, "inicio": int(time.time()), "log": [], "resultado": None,
            "pasos": [{"k": k, "t": t, "d": d, "s": "", "time": ""} for k, t, d in pasos],
        }

    def paso(self, k, s, d=None, t0=None):
        for p in self.d["pasos"]:
            if p["k"] == k:
                p["s"] = s
                if d is not None:
                    p["d"] = d
                if t0 is not None:
                    p["time"] = f"{int(time.monotonic() - t0)} s"

    def log(self, linea):
        self.d["log"].append(linea)
        del self.d["log"][:-300]

    def fin(self, tipo, titulo, detalle=""):
        self.d["resultado"] = {"tipo": tipo, "titulo": titulo, "detalle": detalle}


def lanzar(trabajo: Trabajo, funcion, *args) -> None:
    global trabajo_actual
    trabajo_actual = trabajo

    def correr():
        try:
            funcion(trabajo, *args)
        except Exception as exc:  # que el panel nunca se quede colgado
            trabajo.fin("err", "Error interno del panel", str(exc)[:300])
            evento("err", f"{trabajo.d['titulo']}: error interno ({exc})")
            telegram(f"❌ <b>{NOMBRE}</b>: error interno\n<code>{exc}</code>")
        finally:
            trabajo.pc_transicion = False
            trabajo.d["activo"] = False
            cerrojo.release()
            despertar_monitor.set()
    threading.Thread(target=correr, daemon=True).start()


def _despertar(tr: Trabajo, t0) -> bool:
    """Wake on LAN y esperar a que conteste el SSH. False si no arranca."""
    tr.pc_transicion = True
    servicios["pc"]["estado"] = "encendiendo"
    tr.paso("wol", "run")
    wake_on_lan()
    tr.paso("wol", "done", "Paquete mágico enviado", t0)
    tr.paso("boot", "run", "Esperando respuesta del SSH")
    t1 = time.monotonic()
    ultimo_wol = t1
    while not pc_encendido():
        if time.monotonic() - t1 > ESPERA_ARRANQUE:
            tr.paso("boot", "fail", f"Sin respuesta en {ESPERA_ARRANQUE // 60} min", t1)
            servicios["pc"]["estado"] = "apagado"
            return False
        if time.monotonic() - ultimo_wol > 20:
            wake_on_lan()
            ultimo_wol = time.monotonic()
        time.sleep(3)
    # sshd acepta conexiones un poco antes de que el perfil este listo.
    time.sleep(10)
    tr.paso("boot", "done", "Windows operativo", t1)
    servicios["pc"].update(estado="encendido", desde=int(time.time()))
    tr.pc_transicion = False
    return True


def _apagar(tr: Trabajo, espera_s: int) -> bool:
    tr.pc_transicion = True
    servicios["pc"]["estado"] = "apagando"
    codigo, _ = ssh_corto(f'shutdown /s /t {espera_s} /c "Apagado ordenado por el {NOMBRE}"', 30)
    if codigo != 0:
        servicios["pc"]["estado"] = "encendido"
        return False
    t = time.monotonic()
    while pc_encendido() and time.monotonic() - t < ESPERA_APAGADO:
        time.sleep(3)
    apagado = not pc_encendido()
    servicios["pc"].update(estado="apagado" if apagado else "encendido",
                           desde=int(time.time()) if apagado else servicios["pc"]["desde"],
                           info=None if apagado else servicios["pc"]["info"])
    return apagado


def t_encender(tr: Trabajo) -> None:
    t0 = time.monotonic()
    if pc_encendido(timeout=3):
        tr.paso("wol", "skip", "Ya estaba encendido")
        tr.paso("boot", "skip", "Ya estaba encendido")
        tr.fin("ok", "El PC ya estaba encendido")
        return
    if _despertar(tr, t0):
        s = int(time.monotonic() - t0)
        tr.fin("ok", "PC en línea", f"Arranque completado en {s} s.")
        evento("ok", f"PC encendido por Wake on LAN ({s} s)")
    else:
        tr.fin("err", "El PC no ha arrancado",
               "Comprobar Wake on LAN en la BIOS, la alimentación y el cable de red.")
        evento("err", "El PC no ha respondido al Wake on LAN")
        telegram(f"❌ <b>{NOMBRE}</b>: el PC no ha respondido al Wake on LAN.")


def _esperar_caida(tr: Trabajo, k: str, t0) -> bool:
    tr.paso(k, "run", "Esperando a que deje de responder")
    t = time.monotonic()
    while pc_encendido() and time.monotonic() - t < ESPERA_APAGADO:
        time.sleep(3)
    if pc_encendido():
        tr.paso(k, "fail", "Sigue respondiendo", t0)
        return False
    tr.paso(k, "done", "Fuera de línea", t0)
    return True


def t_reiniciar(tr: Trabajo) -> None:
    t0 = time.monotonic()
    tr.paso("orden", "run")
    if not pc_encendido(timeout=3):
        tr.paso("orden", "fail", "PC apagado")
        tr.paso("baja", "skip", ""); tr.paso("sube", "skip", "")
        tr.fin("err", "PC fuera de línea", "Usar Encender.")
        return
    tr.pc_transicion = True
    servicios["pc"]["estado"] = "reiniciando"
    codigo, _ = ssh_corto(f'shutdown /r /t 10 /c "Reinicio ordenado por el {NOMBRE}"', 30)
    if codigo != 0:
        servicios["pc"]["estado"] = "encendido"
        tr.paso("orden", "fail", "Orden rechazada")
        tr.paso("baja", "skip", ""); tr.paso("sube", "skip", "")
        tr.fin("err", "No se ha podido reiniciar")
        return
    tr.paso("orden", "done", "Reinicio programado en 10 s", t0)
    if not _esperar_caida(tr, "baja", t0):
        tr.paso("sube", "skip", "")
        tr.fin("err", "El PC no se ha reiniciado", "Alguna aplicación puede estar bloqueando el apagado.")
        evento("err", "El PC no se ha reiniciado")
        return
    tr.paso("sube", "run", "Esperando respuesta del SSH")
    t1 = time.monotonic()
    while not pc_encendido():
        if time.monotonic() - t1 > ESPERA_ARRANQUE:
            tr.paso("sube", "fail", f"Sin respuesta en {ESPERA_ARRANQUE // 60} min", t1)
            servicios["pc"].update(estado="apagado", desde=int(time.time()), info=None)
            tr.fin("err", "El PC no ha vuelto", "Comprobar el PC en persona.")
            evento("err", "El PC no ha vuelto tras el reinicio")
            return
        time.sleep(3)
    tr.paso("sube", "done", "Windows operativo", t1)
    servicios["pc"].update(estado="encendido", desde=int(time.time()))
    s = int(time.monotonic() - t0)
    tr.fin("ok", "PC reiniciado", f"Operativo de nuevo en {s} s.")
    evento("ok", f"PC reiniciado desde el panel ({s} s)")


def t_suspender(tr: Trabajo) -> None:
    t0 = time.monotonic()
    tr.paso("orden", "run")
    if not pc_encendido(timeout=3):
        tr.paso("orden", "fail", "PC apagado")
        tr.paso("baja", "skip", "")
        tr.fin("err", "PC fuera de línea")
        return
    tr.pc_transicion = True
    servicios["pc"]["estado"] = "suspendiendo"
    # La conexion se corta al dormirse el PC: no se espera respuesta.
    subprocess.Popen(["ssh", "-i", PC_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                      f"{PC_USER}@{PC_IP}", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tr.paso("orden", "done", "Suspensión solicitada", t0)
    if _esperar_caida(tr, "baja", t0):
        servicios["pc"].update(estado="apagado", desde=int(time.time()), info=None)
        tr.fin("ok", "PC suspendido", "Se despierta con Encender (Wake on LAN).")
        evento("info", "PC suspendido desde el panel")
    else:
        servicios["pc"]["estado"] = "encendido"
        tr.fin("err", "El PC no se ha suspendido", "Alguna aplicación o driver puede estar impidiéndolo.")
        evento("err", "El PC no se ha suspendido")


def t_apagar(tr: Trabajo) -> None:
    t0 = time.monotonic()
    tr.paso("orden", "run")
    if not pc_encendido(timeout=3):
        tr.paso("orden", "skip", "Ya estaba apagado")
        tr.paso("espera", "skip", "")
        tr.fin("ok", "El PC ya estaba apagado")
        return
    tr.paso("orden", "done", "Apagado programado en 15 s", t0)
    tr.paso("espera", "run", "Esperando a que deje de responder")
    if _apagar(tr, 15):
        tr.paso("espera", "done", "Fuera de línea", t0)
        tr.fin("ok", "PC apagado")
        evento("info", "PC apagado desde el panel")
    else:
        tr.paso("espera", "fail", "Sigue respondiendo", t0)
        tr.fin("err", "El PC no se ha apagado", "Alguna aplicación puede estar bloqueando el apagado.")
        evento("err", "El PC no se ha apagado")


def t_recargar(tr: Trabajo, simular: bool) -> None:
    t0 = time.monotonic()
    tr.paso("check", "run")
    ya_encendido = pc_encendido(timeout=3)
    tr.paso("check", "done", "En línea" if ya_encendido else "Fuera de línea", t0)

    if ya_encendido:
        tr.paso("wol", "skip", "Ya estaba encendido")
        tr.paso("boot", "skip", "Ya estaba encendido")
    elif not _despertar(tr, t0):
        tr.paso("rec", "skip", "No iniciada")
        tr.paso("off", "skip", "")
        tr.fin("err", "El PC no ha arrancado",
               "Sin cargo. Comprobar Wake on LAN en la BIOS, la alimentación y el cable de red.")
        evento("err", "Recarga abortada: el PC no ha respondido al Wake on LAN")
        telegram(f"❌ <b>{NOMBRE}</b>: el PC no ha arrancado para la recarga. Sin cargo.")
        return

    tr.paso("rec", "run", "Conectando con el PC")
    t2 = time.monotonic()
    orden = f'cd /d "{PC_DIR}" && set CO_HEADLESS=true&& python -u recarga.py'
    if simular:
        orden += " --simular"
    proc = ssh(orden)
    saldo = error = None
    saltado = False
    try:
        for linea in proc.stdout:
            linea = linea.rstrip()
            if not LINEA_UTIL.match(linea):
                continue
            tr.log(linea)
            cuerpo = linea[11:]
            if m := re.match(r"\[paso\] (.+)", cuerpo):
                tr.paso("rec", "run", m.group(1).capitalize())
            elif cuerpo.startswith("[fin]"):
                if m := re.search(r"saldo: ([\d.,]+)", cuerpo):
                    saldo = m.group(1)
            elif cuerpo.startswith("[saltado]"):
                saltado = True
            elif cuerpo.startswith("[ERROR]"):
                error = cuerpo[len("[ERROR] "):]
            if time.monotonic() - t2 > ESPERA_RECARGA:
                proc.kill()
                error = error or f"Sin terminar en {ESPERA_RECARGA // 60} min"
                break
        codigo = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        codigo = -1

    if codigo == 255 and not tr.d["log"]:
        error = "Sin acceso SSH al PC"

    if codigo == 0 and saltado:
        tr.paso("rec", "done", "Recarga de hoy ya hecha", t2)
        tr.fin("ok", "Recarga de hoy ya realizada", "Sin nuevo cargo.")
        evento("info", "Recarga omitida: la de hoy ya estaba hecha")
    elif codigo == 0 and simular:
        if saldo:
            guardar_estado(saldo=saldo)
        tr.paso("rec", "done", "Tarjeta introducida, pago no enviado", t2)
        tr.fin("ok", "Simulación correcta", "Recorrido completo verificado. Sin cargo.")
        evento("ok", "Simulación de recarga correcta (sin cargo)")
    elif codigo == 0:
        guardar_estado(ultima=datetime.now().strftime("%Y-%m-%d %H:%M"), **({"saldo": saldo} if saldo else {}))
        tr.paso("rec", "done", f"Saldo: {saldo} €" if saldo else "Completada", t2)
        tr.fin("ok", f"Recarga de {IMPORTE} € completada", f"Saldo: {saldo} €." if saldo else "")
        evento("ok", f"Recarga de {IMPORTE} €" + (f" · saldo {saldo} €" if saldo else ""))
    else:
        tr.paso("rec", "fail", error or f"Código de salida {codigo}", t2)
        detalle = "Motivo y captura enviados a Telegram."
        if error and "rechazado" in error:
            detalle = "Pago rechazado por el banco. Sin cargo."
        elif error and "OJO" in error:
            detalle = "El cargo puede haberse producido: revisar el monedero antes de reintentar."
        elif codigo == 255:
            detalle = "Sin cargo. Revisar el servicio SSH del PC."
        tr.fin("err", "Simulación fallida" if simular else "Recarga fallida", detalle)
        evento("err", ("Simulación" if simular else "Recarga") + f" fallida: {error or f'código {codigo}'}")

    if ya_encendido:
        tr.paso("off", "skip", "Estaba encendido: se mantiene")
        return
    tr.paso("off", "run")
    if _apagar(tr, 30):
        tr.paso("off", "done", "Fuera de línea")
    else:
        tr.paso("off", "fail", "El PC sigue encendido")
        telegram(f"⚠️ <b>{NOMBRE}</b>: el PC no se ha apagado tras la recarga.")


# --- bot de Discord -----------------------------------------------------------
# Antes el bot tenia su propio panel (otro Flask, otro servicio, otra RAM).
# Ahora todo lo suyo vive aqui: estado, arrancar/parar/reiniciar, actualizar,
# logs, editor de su .env y de la memoria de la IA.

BOT_DIR = Path(os.environ.get("BOT_DIR", "/home/renox/discord-bot"))
BOT_SERVICIO = os.environ.get("BOT_SERVICIO", "discordbot")
BOT_ENVS = {"env": BOT_DIR / ".env", "avisos": BOT_DIR / ".env.avisos"}
BOT_IA = {"contexto": BOT_DIR / "data" / "ai_context.json",
          "saved": BOT_DIR / "data" / "ai_saved.json"}
BOT_IA_ESTADO = BOT_DIR / "data" / "ai_state.json"
ESPERA_BOT_UPDATE = 1800   # s maximos para startup.sh (lleva un apt upgrade)
# Claves del .env que no se ensenan nunca en el navegador.
# Solo si la clave TERMINA asi: AI_MAX_TOKENS no es un secreto, AI_API_KEY si.
SECRETO = re.compile(r"(^|_)(TOKEN|KEY|SECRET|PASSWORD|HASH|CLAVE)$", re.I)
# Comandos con sudo: DEBEN coincidir con /etc/sudoers.d/recarga-web.
BOT_ORDENES = {
    "start": ["sudo", "-n", "/usr/bin/systemctl", "start", BOT_SERVICIO],
    "stop": ["sudo", "-n", "/usr/bin/systemctl", "stop", BOT_SERVICIO],
    "restart": ["sudo", "-n", "/usr/bin/systemctl", "restart", BOT_SERVICIO],
}
_bot_cache = {"t": 0.0, "d": None}


def bot_estado(fresco: bool = False) -> dict:
    """Estado del servicio del bot. Se cachea 10 s: el panel pregunta cada
    poco y systemctl no es gratis en una Zero 2 W."""
    if not fresco and _bot_cache["d"] and time.monotonic() - _bot_cache["t"] < 10:
        return _bot_cache["d"]
    d = {"estado": "desconocido", "sub": "", "pid": None, "uptime_s": None,
         "ram_mb": None, "reinicios": None, "version": None, "ia": None}
    try:
        r = subprocess.run(["systemctl", "show", BOT_SERVICIO, "-p", "ActiveState", "-p", "SubState",
                            "-p", "MainPID", "-p", "ActiveEnterTimestampMonotonic", "-p", "NRestarts"],
                           capture_output=True, text=True, timeout=10)
        v = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
        d["estado"] = v.get("ActiveState", "desconocido")
        d["sub"] = v.get("SubState", "")
        pid = int(v.get("MainPID") or 0)
        d["reinicios"] = int(v.get("NRestarts") or 0)
        if pid:
            d["pid"] = pid
            inicio = int(v.get("ActiveEnterTimestampMonotonic") or 0)
            if inicio:   # systemd usa CLOCK_MONOTONIC, igual que time.monotonic() en Linux
                d["uptime_s"] = max(0, int(time.monotonic() - inicio / 1e6))
            for linea in open(f"/proc/{pid}/status"):
                if linea.startswith("VmRSS:"):
                    d["ram_mb"] = round(int(linea.split()[1]) / 1024)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        d["version"] = (BOT_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    d["ia"] = bool(leer_json(BOT_IA_ESTADO, {"enabled": True}).get("enabled", True))
    _bot_cache.update(t=time.monotonic(), d=d)
    return d


def env_leer(ruta: Path) -> list[dict]:
    """El .env como lista de lineas: comentarios tal cual y variables con su
    valor, salvo los secretos (solo se dice si estan puestos)."""
    filas = []
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return filas
    for linea in lineas:
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            k = k.strip()
            if SECRETO.search(k):
                filas.append({"k": k, "v": "", "secreto": True, "puesto": bool(v.strip())})
            else:
                filas.append({"k": k, "v": v})
        else:
            filas.append({"c": linea})
    return filas


def env_escribir(ruta: Path, cambios: dict) -> int:
    """Aplica {clave: valor} conservando comentarios y orden. Un secreto vacio
    se deja como estaba. Devuelve cuantas variables han cambiado."""
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    nuevas, n = [], 0
    for linea in lineas:
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, viejo = s.partition("=")
            k = k.strip()
            if k in cambios:
                nuevo = str(cambios[k]).replace("\n", " ").strip()
                if not (SECRETO.search(k) and nuevo == "") and nuevo != viejo:
                    nuevas.append(f"{k}={nuevo}")
                    n += 1
                    continue
        nuevas.append(linea)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text("\n".join(nuevas) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ruta)
    return n


def t_bot_actualizar(tr: Trabajo) -> None:
    """startup.sh del bot: apt, git reset al ultimo main, Python, pip y reinicio."""
    t0 = time.monotonic()
    antes = bot_estado(fresco=True).get("version")
    tr.paso("sync", "run", "apt, GitHub, Python y dependencias")
    proc = subprocess.Popen(["sudo", "-n", "/bin/bash", str(BOT_DIR / "startup.sh")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    fallo_git = False
    try:
        for linea in proc.stdout:
            linea = linea.rstrip()
            if not linea:
                continue
            if linea.startswith("==>"):
                tr.log(linea)
                tr.paso("sync", "run", linea[4:].strip()[:120])
            elif "!!!" in linea or "fallo" in linea.lower() or linea.lstrip().startswith(("Version:", "(")):
                tr.log(linea.strip())
                fallo_git = fallo_git or "SINCRONIZACION HA FALLADO" in linea
            if time.monotonic() - t0 > ESPERA_BOT_UPDATE:
                proc.kill()
                break
        codigo = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        codigo = -1
    if codigo != 0:
        tr.paso("sync", "fail", f"startup.sh ha salido con {codigo}", t0)
        tr.paso("check", "skip", "")
        tr.fin("err", "No se ha podido actualizar el bot", "Mirar los logs del bot.")
        evento("err", f"Actualización del bot fallida (código {codigo})")
        return
    tr.paso("sync", "done", "Hecho", t0)
    tr.paso("check", "run", "Esperando a que el bot arranque")
    t1 = time.monotonic()
    d = bot_estado(fresco=True)
    while d["estado"] != "active" and time.monotonic() - t1 < 60:
        time.sleep(3)
        d = bot_estado(fresco=True)
    if d["estado"] != "active":
        tr.paso("check", "fail", f"Servicio {d['estado']}", t1)
        tr.fin("err", "El bot no ha arrancado", "Mirar los logs del bot.")
        evento("err", "El bot no ha arrancado tras actualizar")
    elif fallo_git:
        tr.paso("check", "fail", f"Sigue en {d['version']}", t1)
        tr.fin("err", "El bot arranca, pero con el código de antes",
               "La sincronización con GitHub ha fallado: mirar permisos del repo.")
        evento("err", "Bot reiniciado sin actualizar: falló la sincronización con GitHub")
    else:
        tr.paso("check", "done", f"Versión {d['version']}", t1)
        cambio = f"{antes} → {d['version']}" if antes != d["version"] else f"ya estaba en {d['version']}"
        tr.fin("ok", "Bot actualizado", cambio)
        evento("ok", f"Bot actualizado ({cambio})")


PASOS_RECARGA = [
    ("check", "Comprobar el PC", "Conexión SSH"),
    ("wol", "Encender el PC", "Wake on LAN"),
    ("boot", "Esperar a Windows", "Hasta que responda el SSH"),
    ("rec", "Recarga", "Login → monedero → Redsys → banco"),
    ("off", "Apagar el PC", "Solo si se ha encendido para la recarga"),
]
ACCIONES = {
    "recargar": lambda: (Trabajo("recargar", f"Recarga del monedero · {IMPORTE} €", PASOS_RECARGA), t_recargar, False),
    "probar": lambda: (Trabajo("probar", "Simulación de recarga (sin cargo)", PASOS_RECARGA), t_recargar, True),
    "encender": lambda: (Trabajo("encender", "Encendido del PC", [
        ("wol", "Wake on LAN", "Paquete mágico a la tarjeta de red"),
        ("boot", "Arranque de Windows", "Hasta que responda el SSH")]), t_encender),
    "apagar": lambda: (Trabajo("apagar", "Apagado del PC", [
        ("orden", "Orden de apagado", "shutdown por SSH"),
        ("espera", "Confirmación", "Hasta que deje de responder")]), t_apagar),
    "reiniciar": lambda: (Trabajo("reiniciar", "Reinicio del PC", [
        ("orden", "Orden de reinicio", "shutdown /r por SSH"),
        ("baja", "Apagado", "Hasta que deje de responder"),
        ("sube", "Arranque", "Hasta que vuelva a responder el SSH")]), t_reiniciar),
    "suspender": lambda: (Trabajo("suspender", "Suspensión del PC", [
        ("orden", "Orden de suspensión", "SetSuspendState por SSH"),
        ("baja", "Confirmación", "Hasta que deje de responder")]), t_suspender),
    "bot_actualizar": lambda: (Trabajo("bot_actualizar", "Actualización del bot de Discord", [
        ("sync", "startup.sh", "apt, GitHub, Python y dependencias"),
        ("check", "Arranque", "Hasta que el servicio vuelva a estar activo")]), t_bot_actualizar),
}


# --- web --------------------------------------------------------------------

fallos = {"n": 0, "hasta": 0.0}


def requiere_login(f):
    @functools.wraps(f)
    def envuelta(*a, **kw):
        if not session.get("ok"):
            if request.path.startswith("/api/"):
                return jsonify(error="Sesión caducada"), 401
            return redirect(url_for("entrar"))
        return f(*a, **kw)
    return envuelta


def requiere_csrf(f):
    @functools.wraps(f)
    def envuelta(*a, **kw):
        if request.headers.get("X-CSRF") != session.get("csrf"):
            return jsonify(error="Sesión desincronizada: recargar la página"), 403
        return f(*a, **kw)
    return envuelta


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    error = None
    if request.method == "POST":
        ahora = time.time()
        if ahora < fallos["hasta"]:
            error = f"Acceso bloqueado. Reintentar en {int(fallos['hasta'] - ahora) // 60 + 1} min."
        elif check_password_hash(HASH, request.form.get("password", "")):
            fallos["n"] = 0
            session.clear()
            session["ok"] = True
            session["csrf"] = secrets.token_urlsafe(16)
            session.permanent = True
            return redirect(url_for("panel"))
        else:
            fallos["n"] += 1
            quedan = INTENTOS - fallos["n"]
            if quedan <= 0:
                fallos["n"] = 0
                fallos["hasta"] = ahora + BLOQUEO
                error = f"Código incorrecto. Acceso bloqueado {BLOQUEO // 60} min."
                telegram(f"🔒 <b>{NOMBRE}</b>: 5 intentos de acceso fallidos. Bloqueado 15 min.")
                evento("err", "5 intentos de acceso fallidos: bloqueo de 15 min")
            else:
                error = f"Código incorrecto. Intentos restantes: {quedan}."
    return render_template("entrar.html", error=error, v=VERSION)


@app.post("/salir")
def salir():
    session.clear()
    return redirect(url_for("entrar"))


@app.get("/")
@requiere_login
def panel():
    return render_template("panel.html", csrf=session["csrf"], importe=IMPORTE, v=VERSION)


@app.get("/api/estado")
@requiere_login
def api_estado():
    estado = leer_json(ESTADO, {})
    return jsonify(
        ahora=int(time.time()),
        hoy=datetime.now().strftime("%Y-%m-%d"),
        servicios=servicios,
        monedero={"saldo": estado.get("saldo"), "ultima": estado.get("ultima"), "importe": IMPORTE},
        eventos=eventos[:30],
        trabajo=trabajo_actual.d if trabajo_actual else None,
        cada_pc=CADA_METRICAS,
        bot=bot_estado(),
    )


@app.get("/api/series")
@requiere_login
def api_series():
    rango = request.args.get("rango", "24h")
    if rango not in RANGOS:
        return jsonify(error="Rango no válido"), 400
    return jsonify(series(rango))


@app.post("/api/accion/<nombre>")
@requiere_login
@requiere_csrf
def api_accion(nombre):
    if nombre not in ACCIONES:
        return jsonify(error="Operación desconocida"), 404
    if not cerrojo.acquire(blocking=False):
        return jsonify(error="Otra operación en curso"), 409
    trabajo, funcion, *args = ACCIONES[nombre]()
    lanzar(trabajo, funcion, *args)
    return jsonify(ok=True, id=trabajo.d["id"])


@app.post("/api/jiggler/<modo>")
@requiere_login
@requiere_csrf
def api_jiggler(modo):
    if modo not in ("on", "off"):
        return jsonify(error="Modo desconocido"), 404
    if not pc_encendido():
        return jsonify(error="PC fuera de línea"), 409
    codigo, salida = ssh_corto(ps_pc("jiggler.ps1", modo), 30)
    if "sin_sesion" in salida:
        return jsonify(error="Sin sesión iniciada en el PC: el ratón solo se mueve dentro de una sesión"), 409
    if codigo != 0:
        return jsonify(error="Sin respuesta del PC"), 502
    time.sleep(2 if modo == "on" else 0)
    muestra_pc()
    evento("info", "Jiggler activado" if modo == "on" else "Jiggler desactivado")
    return jsonify(ok=True, info=servicios["pc"]["info"])


@app.post("/api/pc/sesion/<accion>")
@requiere_login
@requiere_csrf
def api_sesion(accion):
    if accion not in ("bloquear", "cerrar"):
        return jsonify(error="Acción desconocida"), 404
    if not pc_encendido():
        return jsonify(error="PC fuera de línea"), 409
    codigo, salida = ssh_corto(ps_pc("sesion.ps1", accion), 30)
    if "sin_sesion" in salida:
        return jsonify(error="No hay ninguna sesión iniciada en el PC"), 409
    if codigo != 0:
        return jsonify(error="Sin respuesta del PC"), 502
    evento("info", "Sesión bloqueada desde el panel" if accion == "bloquear" else "Sesión cerrada desde el panel")
    return jsonify(ok=True)


@app.post("/api/bot/<orden>")
@requiere_login
@requiere_csrf
def api_bot(orden):
    if orden not in BOT_ORDENES:
        return jsonify(error="Orden desconocida"), 404
    if trabajo_actual and trabajo_actual.d["activo"] and trabajo_actual.d["tipo"] == "bot_actualizar":
        return jsonify(error="El bot se está actualizando: esperar a que termine"), 409
    r = subprocess.run(BOT_ORDENES[orden], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return jsonify(error=(r.stderr or "La orden ha fallado").strip()[:200]), 500
    nombres = {"start": "arrancado", "stop": "parado", "restart": "reiniciado"}
    evento("info", f"Bot {nombres[orden]} desde el panel")
    time.sleep(1.5)
    return jsonify(ok=True, bot=bot_estado(fresco=True))


@app.get("/api/bot/logs")
@requiere_login
def api_bot_logs():
    n = min(max(int(request.args.get("n", "150") or 150), 20), 1000)
    # renox esta en el grupo adm: puede leer el diario sin sudo
    try:
        r = subprocess.run(["journalctl", "-u", BOT_SERVICIO, "-n", str(n), "--no-pager", "-o", "short"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return jsonify(lineas=[f"No se pueden leer los logs: {exc}"])
    return jsonify(lineas=(r.stdout or r.stderr or "").splitlines()[-n:])


@app.get("/api/bot/config/<cual>")
@requiere_login
def api_bot_config(cual):
    if cual not in BOT_ENVS:
        return jsonify(error="Fichero desconocido"), 404
    if not BOT_ENVS[cual].exists():
        return jsonify(error=f"No existe {BOT_ENVS[cual].name}"), 404
    return jsonify(fichero=BOT_ENVS[cual].name, filas=env_leer(BOT_ENVS[cual]))


@app.post("/api/bot/config/<cual>")
@requiere_login
@requiere_csrf
def api_bot_config_guardar(cual):
    if cual not in BOT_ENVS or not BOT_ENVS[cual].exists():
        return jsonify(error="Fichero desconocido"), 404
    cambios = (request.get_json(silent=True) or {}).get("cambios") or {}
    if not isinstance(cambios, dict):
        return jsonify(error="Formato no válido"), 400
    with _io:
        n = env_escribir(BOT_ENVS[cual], cambios)
    if n:
        evento("info", f"{BOT_ENVS[cual].name} del bot: {n} variable{'s' if n > 1 else ''} cambiada{'s' if n > 1 else ''}")
    return jsonify(ok=True, cambiadas=n)


@app.get("/api/bot/ia")
@requiere_login
def api_bot_ia():
    def texto(ruta):
        try:
            return ruta.read_text(encoding="utf-8")
        except OSError:
            return '{\n  "servidores": []\n}'
    return jsonify(enabled=bot_estado()["ia"], **{k: texto(r) for k, r in BOT_IA.items()})


@app.post("/api/bot/ia")
@requiere_login
@requiere_csrf
def api_bot_ia_guardar():
    datos = request.get_json(silent=True) or {}
    if "enabled" in datos:
        escribir_json(BOT_IA_ESTADO, {"enabled": bool(datos["enabled"])})
        _bot_cache["t"] = 0
        evento("info", "Chat de IA del bot " + ("activado" if datos["enabled"] else "desactivado"))
        return jsonify(ok=True)
    nuevos = {}
    for k in BOT_IA:
        if k in datos:
            try:
                json.loads(datos[k])
            except (TypeError, ValueError) as exc:
                return jsonify(error=f"JSON no válido en {BOT_IA[k].name}: {exc}"), 400
            nuevos[k] = datos[k]
    for k, txt in nuevos.items():
        tmp = BOT_IA[k].with_suffix(".tmp")
        tmp.write_text(txt, encoding="utf-8")
        tmp.replace(BOT_IA[k])
    if nuevos:
        evento("info", "Memoria de la IA del bot editada desde el panel")
    return jsonify(ok=True)


@app.post("/api/pi/reiniciar")
@requiere_login
@requiere_csrf
def api_reiniciar_pi():
    if trabajo_actual and trabajo_actual.d["activo"]:
        return jsonify(error="Operación en curso: esperar a que termine"), 409
    evento("info", "Reinicio de la Raspberry Pi ordenado desde el panel")
    # Unos segundos de margen para que la respuesta llegue al navegador.
    # Permiso en /etc/sudoers.d/recarga-web (solo para este comando).
    threading.Timer(3, lambda: subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "reboot"])).start()
    return jsonify(ok=True)


if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    serve(app, host="0.0.0.0", port=PUERTO, threads=3)
