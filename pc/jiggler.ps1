# Enciende o apaga el jiggler desde la Pi (por SSH):  jiggler.ps1 on|off
# El raton solo se puede mover desde la sesion del usuario, no desde la de SSH,
# asi que "on" dispara la tarea programada "Jiggler", que corre en tu sesion.
param([Parameter(Mandatory)][ValidateSet('on','off')][string]$Accion)
$pidFile = Join-Path $env:TEMP 'mantener_despierto.pid'
if ($Accion -eq 'off') {
    if (Test-Path $pidFile) {
        Stop-Process -Id ([int](Get-Content $pidFile)) -Force -ErrorAction SilentlyContinue
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    'off'; exit 0
}
if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { 'sin_sesion'; exit 2 }
schtasks /run /tn Jiggler | Out-Null
'on'
