# Lanza y sigue el limpiador RNX (/todo) desde la Pi, por SSH.
#   limpiador.ps1 lanzar          -> numero de lineas del log antes de empezar
#   limpiador.ps1 estado -Desde N -> JSON con el estado de la tarea y las lineas nuevas del log
# El limpiador reinicia el Explorador e importa la config del raton en HKCU, asi
# que tiene que correr en la sesion del usuario: lo hace la tarea programada
# "RNX Cache Cleaner" (sesion interactiva, maximos privilegios). Su codigo no se toca.
param([Parameter(Mandatory)][ValidateSet('lanzar','estado')][string]$Accion, [int]$Desde = 0)
$log = 'C:\Users\alex-\Documents\Git\CS2-Cache-Cleaner\RNX_Cleaner.log'
$lineas = if (Test-Path $log) { @(Get-Content $log) } else { @() }
if ($Accion -eq 'lanzar') {
    if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { 'sin_sesion'; exit 2 }
    if ((Get-ScheduledTask 'RNX Cache Cleaner').State -eq 'Running') { 'ya_en_marcha'; exit 3 }
    $lineas.Count
    Start-ScheduledTask 'RNX Cache Cleaner'
    exit 0
}
$t = Get-ScheduledTask 'RNX Cache Cleaner'
[pscustomobject]@{
    estado    = [string]$t.State
    resultado = (Get-ScheduledTaskInfo 'RNX Cache Cleaner').LastTaskResult
    lineas    = @($lineas | Select-Object -Skip $Desde)
} | ConvertTo-Json -Compress
