# Changelog

## [2.0.0.f0]

### 💥 Changed
- 🤖 **The Discord bot is managed from here.** The [RGL Discord BOT](https://github.com/Ren0X1/RGL-Discord-BOT) had its own web panel on the same Pi; it is gone and everything it did lives in ZeroDesk: status (version, uptime, memory), restart / stop / start, **update** (its `startup.sh` as a job streamed to the terminal, checking the version that came up), AI chat switch, **logs**, a form editor for its `.env` and `.env.avisos` (secrets never reach the browser) and a JSON editor for the AI memory, with a one-click wipe of the learned memory. One panel, one service, less RAM.
- 🎨 **Full redesign: *Bento* theme.** Rounded cards on a neutral background, **dark by default** with a light mode (sun button, remembered per browser), Inter + JetBrains Mono, one teal accent. On a phone the cards stack and the navigation moves to a bottom bar.
- 🐍 **Python 3.15** installed with `uv` (prebuilt, no compiling on the Zero 2 W). `actualizar.sh` picks up patch releases on its own.

### 🗑️ Removed
- 🧹 **RNX Cache Cleaner** and 🌐 **Open NAT 3074** are no longer launched from the panel (`pc/limpiador.ps1`, `pc/nat.ps1`, `NAT_PS1`). `preparar_pc.ps1` removes the old *RNX Cache Cleaner* scheduled task if it is still there.

### 🔒 Security
- 🧯 sudo rules moved to `panel/recarga-web.sudoers`, still exact commands only: reboot, restart the panel, and start / stop / restart / update the bot. The bot's old `discordpanel` rule is removed by the installer.

---

## [1.0.0.f0]

### ✨ First release
- 🖥️ **Remote control panel** served from the Raspberry Pi: Wake on LAN power-on, remote shutdown, anti-lock mouse jiggler.
- 📈 **Telemetry**: PC CPU / RAM / GPU temperature and Pi CPU / RAM / temperature, stored 30 days in SQLite, with 1H · 6H · 24H · 7D · 30D ranges.
- 🌐 **Link monitoring**: casaortega.com, Redsys payment gateway and Telegram API, with latency.
- 🚦 **DEFCON indicator** summarising the whole system at a glance.
- 🧹 **Protocols**: RNX Cache Cleaner `/todo` and T6 Open NAT (port 3074) launched remotely.
- 💳 **Wallet top-up** for Casa Ortega (Pluxee card via Redsys) behind a safety-covered red button, with dry-run mode and a one-per-day lock.
- 🔁 Pi reboot from the panel, password login with lockout, CSRF protection.
