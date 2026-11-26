# 🖥️ ZeroDesk

![Python](https://img.shields.io/badge/Python-3.15-3776AB?style=flat-square&logo=python&logoColor=white) ![Flask](https://img.shields.io/badge/Flask-3.1-000000?style=flat-square&logo=flask&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-telemetry-003B57?style=flat-square&logo=sqlite&logoColor=white) ![Playwright](https://img.shields.io/badge/Playwright-headless%20Chromium-2EAD33?style=flat-square&logo=playwright&logoColor=white) ![PowerShell](https://img.shields.io/badge/PowerShell-5.1-5391FE?style=flat-square&logo=powershell&logoColor=white) ![OpenSSH](https://img.shields.io/badge/OpenSSH-key%20auth-000000?style=flat-square&logo=openssh&logoColor=white) ![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-Zero%202%20W-A22846?style=flat-square&logo=raspberrypi&logoColor=white) ![Windows](https://img.shields.io/badge/Windows-11%20(AtlasOS)-0078D4?style=flat-square&logo=windows&logoColor=white) ![Tailscale](https://img.shields.io/badge/Tailscale-remote%20access-242424?style=flat-square&logo=tailscale&logoColor=white) ![Telegram](https://img.shields.io/badge/Telegram-alerts-26A5E4?style=flat-square&logo=telegram&logoColor=white)

> **Your gaming PC and your Discord bot, from anywhere.** A tiny web panel running on a **Raspberry Pi Zero 2 W** that wakes the PC with **Wake on LAN**, monitors it, runs your scripts on it over **SSH** and turns it off again — and manages the [RGL Discord BOT](https://github.com/Ren0X1/RGL-Discord-BOT) that lives on the same Pi. All from your phone, through **Tailscale**. 🚀

The Pi is always on and sips power; the PC only runs when there is something to do. From the panel you see CPU, RAM, disk and GPU at a glance, power the PC on/off/restart/sleep, lock it, keep it from locking, restart, update and configure the Discord bot, and even top up a prepaid wallet that needs a real browser — which the Pi (415 MB of RAM) could never run itself, so it borrows the PC for 30 seconds. ⚡

---

## 🧰 Tech stack

| Area | Technology |
|------|------------|
| Panel backend | Python 3.15 (via [uv](https://docs.astral.sh/uv/)) · Flask 3 · waitress |
| Panel frontend | Vanilla HTML + CSS + JS — inline SVG icons and charts, zero dependencies · *Bento* theme, dark by default with a light mode (Inter + JetBrains Mono) |
| Telemetry | SQLite (`telemetria.db`), 30 days of history, bucketed on the fly per range |
| PC control | OpenSSH Server on Windows (key auth only) + PowerShell 5.1 scripts |
| Power on | Wake on LAN magic packet (UDP broadcast from the Pi) |
| Desktop actions | Windows Task Scheduler tasks that run inside the user session |
| Browser automation | Playwright + headless Chromium (wallet top-up) |
| Alerts | Telegram Bot API |
| Services | systemd (`recarga-web`) — starts with the Pi, restarts itself; also drives the bot's `discordbot` service |
| Remote access | Tailscale (WireGuard VPN, no ports opened) |
| Hardware | Raspberry Pi Zero 2 W · Windows 11 PC (AtlasOS) |
| CI/CD | GitHub Actions — automatic versioning, tag and release on every push |

---

## ✨ What the panel does

### 🎨 The screen
A *Bento* grid of rounded cards — **dark by default**, with a sun button for light mode (remembered per browser). Figures in a monospace so they line up, one teal accent for what you can touch. On a phone the cards stack and the navigation becomes a bar at the bottom, under your thumb.

```
┌ ZD ZeroDesk ────────────────────────────── ● Todo operativo · 13:27  ☀  ⇥ ┐
│ [Inicio] Bot  Terminal  Gráficas  Sistema                                │
│ ┌ PC de juegos ─────────────────────────────────────────── [⏻ Apagar PC] ┐│
│ │ RNXPC  ● En línea · encendido hace 3 h 20 min                          ││
│ │ [CPU 18%] [RAM 41%] [Disco 56%] [GPU 9%] [47 °C]                       ││
│ └────────────────────────────────────────────────────────────────────────┘│
│ ┌ Control del PC ─┐ ┌ RGL Bot ── Activo ┐ ┌ Scripts ─────────────────┐   │
│ │ 6 buttons       │ │ v2.0.0 · 2 d · 61MB│ │ Monedero Casa Ortega     │   │
│ │ Anti-bloqueo ○  │ │ Chat de IA ●       │ │ [Simular] [Recargar 10€] │   │
│ └─────────────────┘ └────────────────────┘ └──────────────────────────┘   │
│ ┌ Terminal ─────────────────────────────┐ ┌ Raspberry Pi ─────────────┐   │
│ Rendimiento · Herramientas del bot (logs, .env, memoria IA) · Sistema    │
└──────────────────────────────────────────────────────────────────────────┘
```

### 🩺 Monitor
- 🖥️ **Summary** — the PC's name, state and uptime, one main button (*Power on* when it is off, *Shut down* when it is on) and a one-line diagnosis: *all good*, *disk C: at 92 %*, *GPU at 82 °C*, *waiting at the login screen*…
- 🔗 **Command path** — browser → Raspberry Pi → PC, each with its own status dot, so you see at a glance which link is down.
- 📊 **Live metrics** — CPU, RAM, disk C:, GPU load and GPU temperature; the bar turns amber and then red past its threshold.
- 📈 **Performance** — one large chart; pick the metric (PC CPU / RAM / GPU temp, Pi CPU / RAM / temp) in cells that already show its latest value and its peak, with **1 h · 6 h · 24 h · 7 d · 30 d** ranges, a real scale and hover tooltips. Gaps (PC off, Pi rebooting) break the line instead of lying.
- 🧾 **System** — Windows edition, CPU, GPU, public IP and session; casaortega.com, Redsys and the Telegram API with their latency; the Pi's CPU, RAM, temperature and uptime.

### 🎛️ Control
| Button | How it works |
|--------|--------------|
| ⚡ **Power on** | Wake on LAN from the Pi, then waits until SSH answers |
| ⏻ **Shut down** | `shutdown /s` over SSH, waits until the PC is really gone |
| 🔄 **Restart** | `shutdown /r`, tracks it going down **and** coming back |
| 🌙 **Sleep** | `SetSuspendState` — wakes up again with *Power on* |
| 🔒 **Lock** / 🚪 **Sign out** | Scheduled tasks that run inside the desktop session |
| 🖱️ **Anti-lock** | Nudges the mouse 1 px every 5 min so Windows never locks (off by default) |
| 🍓 **Reboot Pi** | `systemctl reboot` through an exact-command sudoers rule; the page reconnects by itself |

### 🤖 Discord bot
The [RGL Discord BOT](https://github.com/Ren0X1/RGL-Discord-BOT) runs on the same Pi. It used to have its own web panel (another Flask app, another service, more RAM); everything it did now lives here:

- 🟢 **Status** — running / stopped / crashed, version, uptime and memory of the `discordbot` service.
- 🔁 **Restart / stop / start**, and **Update**: runs the bot's `startup.sh` (apt, `git reset` to the latest `main`, Python patch, dependencies) as a job, streamed into the terminal, and checks the version that actually came up.
- 💬 **AI chat switch** — on/off instantly (`data/ai_state.json`).
- 📜 **Logs** — the last lines of its journal, errors in red.
- ✏️ **Config** — its `.env` and `.env.avisos` as a form, comments and order kept; tokens and keys are never sent to the browser (empty = unchanged). After saving it offers to restart the bot.
- 🧠 **AI memory** — edit the manual context and the learned memory as JSON (validated before saving), or wipe the learned memory in one click.

### 🧪 Scripts
- 💳 **Wallet top-up** — logs into casaortega.com, fills the Redsys gateway with the Pluxee card and waits for the bank. If the PC was off, it is woken up for the job and shut down afterwards; if it was on, it is left alone.

### 🖥️ Terminal
Every operation shows its steps (`[ OK ]`, `[FAIL]`, timings) and the PC's output live; when idle, the terminal shows the operation log.

---

## 🗺️ How it fits together

```mermaid
flowchart LR
    phone["📱 Phone / browser"] -- Tailscale --> pi["🍓 Raspberry Pi Zero 2 W<br/>Flask panel :6678"]
    pi -- "Wake on LAN" --> pc["🖥️ Windows PC"]
    pi -- "SSH (key)" --> pc
    pc -- "scheduled tasks" --> desk["👤 User session<br/>jiggler · lock"]
    pc -- "Playwright" --> shop["🛒 casaortega.com → Redsys"]
    pi -- "systemd" --> bot["🤖 Discord bot<br/>discordbot.service"]
    pi -- alerts --> tg["✈️ Telegram"]
    pc -- alerts --> tg
```

---

## 📁 Repository layout

```
ZeroDesk/
├── panel/                  # 🍓 runs on the Raspberry Pi
│   ├── app.py              #    Flask app: monitor thread, jobs, API
│   ├── templates/ static/  #    dashboard UI (panel, login) and its single stylesheet
│   ├── instalar.sh         #    one-shot installer (venv, SSH key, sudoers, systemd)
│   ├── cambiar_password.py #    set / change the panel password
│   ├── recarga-web.sudoers #    the exact commands the panel may run with sudo
│   └── recarga-web.service
├── pc/                     # 🖥️ runs on the Windows PC (called by the Pi over SSH)
│   ├── preparar_pc.ps1     #    one-shot setup: OpenSSH, firewall, WoL, scheduled tasks
│   ├── estado_pc.ps1       #    CPU / RAM / disk / GPU / session → JSON
│   ├── jiggler.ps1 · mantener_despierto.ps1 · lanzar_jiggler.vbs
│   └── sesion.ps1          #    lock / sign out
├── recarga.py              # 💳 wallet top-up (Playwright)
├── notificar.py · saldo.py · sacar_chat_id.py · recargar.bat
└── .github/workflows/release.yml
```

---

## 🚀 Setup

### 1️⃣ Windows PC
1. Clone the repo (the panel expects it at the path set in `PC_DIR`).
2. Wallet top-up (optional):
   ```powershell
   pip install -r requirements.txt
   python -m playwright install chromium-headless-shell
   copy .env.example .env   # card, Casa Ortega login, Telegram bot
   python sacar_chat_id.py  # prints your TG_CHAT_ID
   ```
3. From an **elevated** PowerShell, with the key printed by the Pi installer:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\pc\preparar_pc.ps1 -ClavePi "ssh-ed25519 AAAA... pi-recarga"
   ```
   It installs **OpenSSH Server** from Microsoft's MSI (works on AtlasOS, where Windows Update is off), allows only the Pi's key, opens port 22 **only to the Pi**, arms Wake on LAN and registers the scheduled tasks.
4. In the **BIOS/UEFI**: enable *Wake on LAN* / *Power On By PCI-E* and disable *ErP* / *Deep Sleep*. The PC must be on **Ethernet**.

### 2️⃣ Raspberry Pi
```bash
git clone https://github.com/Ren0X1/ZeroDesk.git
sudo bash ZeroDesk/panel/instalar.sh
```
The panel runs **straight from the cloned repo** (`~/ZeroDesk/panel`). The installer creates the venv, generates the SSH key for the PC, asks for the panel password, installs the sudo rule and enables the `recarga-web` service so it **starts on every boot**. Then check `~/ZeroDesk/panel/.env` (`PC_IP`, `PC_MAC`, `PC_DIR`, `TG_TOKEN`, `TG_CHAT_ID`).

### 3️⃣ Open it
`http://<pi-tailscale-ip>:6678` from any device on your tailnet. 📱

---

## 🛠️ Day to day

```bash
bash ~/ZeroDesk/panel/actualizar.sh              # 🔄 update: git pull + restart (config and history are kept)
                                                 #    the stylesheet URL carries VERSION, so browsers pick up the new look at once
systemctl status recarga-web                     # is the panel up?
journalctl -u recarga-web -n 50 --no-pager       # what happened
cd ~/ZeroDesk/panel && venv/bin/python cambiar_password.py && sudo systemctl restart recarga-web
```

On the PC nothing needs restarting: the Pi calls the scripts in `pc/` and `recarga.py` from the repo folder on every run, so a `git pull` there is enough.

---

## 🔐 Security

- 🔑 SSH into the PC with **one key only**; password login is disabled; port 22 is open **only to the Pi**.
- 🌐 The panel is meant to be reached **only through Tailscale** — nothing is exposed to the internet.
- 🧂 Panel password stored as a salted hash; **5 wrong attempts → 15 min lockout** + Telegram alert.
- 🛡️ Session cookie `HttpOnly` + `SameSite=Strict`, and a CSRF token on every action.
- 🧯 The Pi's sudo rights are limited to exact commands (`panel/recarga-web.sudoers`): reboot, restart the panel, and start / stop / restart / update the bot.
- 💳 Card data lives only in the PC's `.env` (git-ignored). The top-up is **once a day max** and **never retries by itself**: a blind retry could charge twice.

---

## 🧯 Troubleshooting

- **Power on does nothing** — Wake on LAN is off in the BIOS, *ErP* is on, or the PC is on Wi-Fi. The link LED on the NIC should stay lit while the PC is off.
- **Lock / anti-lock greyed out** — they need someone signed in on the PC; after a Wake on LAN boot the PC sits at the login screen.
- **No GPU temperature** — it comes from `nvidia-smi`; Windows' ACPI thermal zone reports a fixed value and is ignored on purpose.
- **Top-up timed out** — the bank *may* have charged anyway: check the wallet before trying again.

---

> 🛠️ **ZeroDesk** by [Ren0X1](https://github.com/Ren0X1).
