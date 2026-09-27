# Lo llama la Pi por SSH cada 10 min: CPU, RAM, GPU, tiempo encendido, si hay alguien
# con sesion iniciada, si el jiggler esta activo y el estado.json de la recarga.
# Devuelve una sola linea JSON.
$ErrorActionPreference = 'SilentlyContinue'
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
$pidFile = Join-Path $env:TEMP 'mantener_despierto.pid'
$jiggler = $false
if (Test-Path $pidFile) {
    $p = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    $jiggler = [bool]($p -and $p.ProcessName -eq 'powershell')
}
# Hay sesion si el explorer de alguien esta corriendo (el SSH no cuenta).
$sesion = [bool](Get-Process explorer -ErrorAction SilentlyContinue)
# Temperatura y carga de la GPU (la unica temperatura fiable sin drivers extra:
# la zona termica ACPI de Windows da un valor fijo que no sirve).
$gpu_temp = $gpu_uso = $null
$smi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source
if ($smi) {
    $g = (& $smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits | Select-Object -First 1) -split ','
    if ($g.Count -ge 2) { $gpu_temp = [int]$g[0].Trim(); $gpu_uso = [int]$g[1].Trim() }
}
$disco = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Ethernet -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
$estado = $null
$f = Join-Path (Split-Path $PSScriptRoot) 'estado.json'
if (Test-Path $f) { $estado = Get-Content $f -Raw | ConvertFrom-Json }
[pscustomobject]@{
    cpu        = [int]$cpu
    ram_total  = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    ram_libre  = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    disco_total = [math]::Round($disco.Size / 1GB)
    disco_libre = [math]::Round($disco.FreeSpace / 1GB)
    gpu_temp   = $gpu_temp
    gpu_uso    = $gpu_uso
    equipo     = $env:COMPUTERNAME
    so         = "$($os.Caption -replace '^Microsoft ', '') (build $($os.BuildNumber))"
    arq        = $os.OSArchitecture
    cpu_nombre = ((Get-CimInstance Win32_Processor | Select-Object -First 1).Name).Trim()
    gpu_nombre = (Get-CimInstance Win32_VideoController | Where-Object Name -notmatch 'Basic|Virtual|Parsec' | Select-Object -First 1).Name
    ip         = $ip
    uptime_s   = [int]((Get-Date) - $os.LastBootUpTime).TotalSeconds
    sesion     = $sesion
    jiggler    = $jiggler
    recarga    = $estado
} | ConvertTo-Json -Compress -Depth 3
