# Bloquear o cerrar la sesion del usuario desde la Pi (por SSH):
#     sesion.ps1 bloquear | cerrar
# Las dos cosas solo se pueden hacer DESDE la sesion del usuario, no desde la
# de SSH, asi que se disparan las tareas programadas "RNX Bloquear" y
# "RNX Cerrar sesion", que corren en el escritorio (las crea preparar_pc.ps1).
param([Parameter(Mandatory)][ValidateSet('bloquear','cerrar')][string]$Accion)
if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { 'sin_sesion'; exit 2 }
$tarea = if ($Accion -eq 'bloquear') { 'RNX Bloquear' } else { 'RNX Cerrar sesion' }
Start-ScheduledTask $tarea
'ok'
