# Mueve el raton 1 pixel (y lo devuelve) cada 5 minutos para que el PC no se
# bloquee ni se suspenda. Lo arranca la tarea programada "Jiggler" (via
# lanzar_jiggler.vbs) y se enciende/apaga desde el panel con jiggler.ps1.
# A mano se para con:  Stop-Process -Id (Get-Content $env:TEMP\mantener_despierto.pid)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Despierto {
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, uint d, UIntPtr e);
    [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);
}
"@

$pidFile = "$env:TEMP\mantener_despierto.pid"
# Si ya hay uno corriendo, no se lanza otro.
if (Test-Path $pidFile) {
    $viejo = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($viejo -and $viejo.ProcessName -eq 'powershell' -and $viejo.Id -ne $PID) { exit 0 }
}
$PID | Set-Content $pidFile

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED: ni suspender ni apagar pantalla.
[Despierto]::SetThreadExecutionState(0x80000003) | Out-Null

while ($true) {
    # mouse_event (no SetCursorPos) porque es input real: reinicia el contador de inactividad.
    [Despierto]::mouse_event(0x0001, 1, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 100
    [Despierto]::mouse_event(0x0001, -1, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Seconds 300
}
