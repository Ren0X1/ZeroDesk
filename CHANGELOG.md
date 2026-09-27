# Changelog

## [1.0.0.f0]

### ✨ First release
- 🖥️ **Remote control panel** served from the Raspberry Pi: Wake on LAN power-on, remote shutdown, anti-lock mouse jiggler.
- 📈 **Telemetry**: PC CPU / RAM / GPU temperature and Pi CPU / RAM / temperature, stored 30 days in SQLite, with 1H · 6H · 24H · 7D · 30D ranges.
- 🌐 **Link monitoring**: casaortega.com, Redsys payment gateway and Telegram API, with latency.
- 🚦 **DEFCON indicator** summarising the whole system at a glance.
- 🧹 **Protocols**: RNX Cache Cleaner `/todo` and T6 Open NAT (port 3074) launched remotely.
- 💳 **Wallet top-up** for Casa Ortega (Pluxee card via Redsys) behind a safety-covered red button, with dry-run mode and a one-per-day lock.
- 🔁 Pi reboot from the panel, password login with lockout, CSRF protection.
