# Prepara este PC para el Panel remoto RNX PC: que la Pi pueda encenderlo
# (Wake on LAN), entrar por SSH, lanzar sus scripts y volver a apagarlo.
#
# Se lanza UNA VEZ como administrador, desde una consola elevada:
#     powershell -ExecutionPolicy Bypass -File .\pc\preparar_pc.ps1 -ClavePi "ssh-ed25519 AAAA... pi"
# (la clave publica la imprime panel/instalar.sh en la Pi)
#
# Es idempotente: se puede volver a lanzar sin romper nada.
#
# Ojo con AtlasOS: Windows Update viene parado y muchos servicios desactivados,
# asi que OpenSSH NO se instala con Add-WindowsCapability (tira de Windows
# Update y suele fallar). Se instala el MSI oficial de Microsoft desde GitHub.
param(
    [string]$PiIP      = '192.168.100.75',   # IP de la Pi en la red de casa
    [string]$PiTsIP    = '100.124.145.22',   # IP de la Pi en Tailscale
    [string]$ClavePi   = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPHfnA67qhU3xSUGKH2BTJ5BPw4eoJFG5C2dIIXL8R4L pi-recarga',
    [string]$NIC       = 'Ethernet',
    # El limpiador RNX (repo CS2-Cache-Cleaner). Si no existe, no se crea su tarea.
    [string]$Limpiador = "$env:USERPROFILE\Documents\Git\CS2-Cache-Cleaner\RNX_Cache_Cleaner_v4.bat"
)
$ErrorActionPreference = 'Stop'
$PI_IP = $PiIP; $PI_TS_IP = $PiTsIP; $CLAVE_PI = $ClavePi

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host 'Hay que lanzarlo como administrador.' -ForegroundColor Red; exit 1 }

Write-Host '== 1/6 · OpenSSH Server =='
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $sshd) {
    $rel = Invoke-RestMethod 'https://api.github.com/repos/PowerShell/Win32-OpenSSH/releases/latest' -Headers @{ 'User-Agent' = 'preparar_pc' }
    $msi = $rel.assets | Where-Object name -like 'OpenSSH-Win64-v*.msi' | Select-Object -First 1
    $dest = Join-Path $env:TEMP $msi.name
    Write-Host "   bajando $($msi.name)..."
    Invoke-WebRequest $msi.browser_download_url -OutFile $dest -UseBasicParsing
    # ADDLOCAL=Server: solo el servidor; el cliente ssh.exe de Windows ya esta.
    Start-Process msiexec.exe -ArgumentList '/i', "`"$dest`"", '/qn', 'ADDLOCAL=Server' -Wait
    $sshd = Get-Service sshd
}
Set-Service sshd -StartupType Automatic
Start-Service sshd
Write-Host "   sshd: $((Get-Service sshd).Status), arranque automatico"

Write-Host '== 2/6 · Clave de la Pi =='
# El usuario es administrador: en ese caso Windows NO mira ~/.ssh/authorized_keys
# sino este fichero, y exige que solo lo puedan leer SYSTEM y Administradores.
$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
New-Item -ItemType Directory -Force (Split-Path $ak) | Out-Null
if (-not (Test-Path $ak) -or -not (Select-String -Path $ak -SimpleMatch $CLAVE_PI -Quiet)) {
    Add-Content -Path $ak -Value $CLAVE_PI -Encoding ascii
}
icacls $ak /inheritance:r /grant 'SYSTEM:F' /grant '*S-1-5-32-544:F' | Out-Null
Write-Host '   clave autorizada'

# Solo clave, nada de contrasenas por SSH.
$cfg = 'C:\ProgramData\ssh\sshd_config'
$txt = Get-Content $cfg -Raw
if ($txt -notmatch '(?m)^PasswordAuthentication no') {
    $txt = $txt -replace '(?m)^#?PasswordAuthentication .*$', 'PasswordAuthentication no'
    if ($txt -notmatch '(?m)^PasswordAuthentication no') { $txt = "PasswordAuthentication no`r`n" + $txt }
    Set-Content $cfg $txt -Encoding ascii
    Restart-Service sshd
}
Write-Host '   contrasenas por SSH desactivadas'

Write-Host '== 3/6 · Cortafuegos =='
# Solo la Pi (por la red de casa o por Tailscale) puede entrar al puerto 22.
Get-NetFirewallRule -DisplayName 'SSH desde la Pi' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
New-NetFirewallRule -DisplayName 'SSH desde la Pi' -Direction Inbound -Protocol TCP -LocalPort 22 `
    -RemoteAddress $PI_IP, $PI_TS_IP -Action Allow -Profile Any | Out-Null
Write-Host "   puerto 22 abierto solo para $PI_IP y $PI_TS_IP"

Write-Host '== 4/6 · Wake on LAN =='
Set-NetAdapterAdvancedProperty -Name $NIC -DisplayName 'Wake on Magic Packet' -DisplayValue 'Enabled' -ErrorAction SilentlyContinue
$pm = Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceWakeEnable |
      Where-Object InstanceName -like "*$((Get-NetAdapter -Name $NIC).PnPDeviceID)*"
if ($pm) { $pm | Set-CimInstance -Property @{ Enable = $true } }
powercfg /deviceenablewake "$((Get-NetAdapter -Name $NIC).InterfaceDescription)" 2>$null
# El arranque rapido rompe el WoL desde apagado; en este PC ya esta quitado,
# pero AtlasOS a veces lo vuelve a poner al actualizar.
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' HiberbootEnabled 0
Write-Host "   tarjeta lista. MAC: $((Get-NetAdapter -Name $NIC).MacAddress)"

Write-Host '== 5/6 · Tareas programadas =='
# El raton y el limpiador solo funcionan dentro de la sesion del usuario, no en
# la de SSH: la Pi las dispara y corren en el escritorio.
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$vbs = Join-Path $PSScriptRoot 'lanzar_jiggler.vbs'
Register-ScheduledTask -TaskName 'Jiggler' -Force -Description 'Anti-bloqueo: mueve el raton cada 5 min (Panel remoto RNX PC)' `
    -Action (New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbs`"") `
    -Principal (New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited) `
    -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew) | Out-Null
Write-Host '   tarea "Jiggler" (desactivado por defecto: se activa desde el panel)'
$sesion = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'RNX Bloquear' -Force -Description 'Bloquea la sesion (Panel remoto RNX PC)' `
    -Action (New-ScheduledTaskAction -Execute 'rundll32.exe' -Argument 'user32.dll,LockWorkStation') -Principal $sesion -Settings $ajustes | Out-Null
Register-ScheduledTask -TaskName 'RNX Cerrar sesion' -Force -Description 'Cierra la sesion (Panel remoto RNX PC)' `
    -Action (New-ScheduledTaskAction -Execute 'shutdown.exe' -Argument '/l') -Principal $sesion -Settings $ajustes | Out-Null
Write-Host '   tareas "RNX Bloquear" y "RNX Cerrar sesion"'
if ($Limpiador -and (Test-Path $Limpiador)) {
    Register-ScheduledTask -TaskName 'RNX Cache Cleaner' -Force -Description 'RNX Cache Cleaner /todo (Panel remoto RNX PC)' `
        -Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ""' + $Limpiador + '" /todo"') -WorkingDirectory (Split-Path $Limpiador)) `
        -Principal (New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Highest) `
        -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew) | Out-Null
    Write-Host '   tarea "RNX Cache Cleaner" (con privilegios de administrador)'
} else {
    Write-Host '   limpiador no encontrado: tarea "RNX Cache Cleaner" omitida'
}

Write-Host '== 6/6 · Comprobacion =='
Get-Service sshd | Format-Table Name, Status, StartType -AutoSize
powercfg /devicequery wake_armed
Write-Host ''
Write-Host 'Listo. Falta UNA cosa que no se puede hacer desde Windows:' -ForegroundColor Yellow
Write-Host '  BIOS/UEFI -> activar "Wake on LAN" / "Power On By PCI-E" y quitar "ErP"/"Deep Sleep".' -ForegroundColor Yellow
