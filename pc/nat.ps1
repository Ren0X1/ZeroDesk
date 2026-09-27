# Lanza el RNX Port Forwarder (repo T6-Open-Nat-Script) desde la Pi, por SSH,
# sin tocar su codigo. El script termina con "Presiona cualquier tecla para
# salir..." y esa espera no acaba nunca sin consola, asi que aqui se ejecuta
# con la salida redirigida a un fichero y, en cuanto aparece ese mensaje (o se
# pasa el tiempo), se cierra. La salida se devuelve tal cual.
param(
    [string]$Script = "$env:USERPROFILE\Documents\Git\T6-Open-Nat-Script\RNX-PortForward-3074.ps1",
    [int]$Limite = 90
)
if (-not (Test-Path $Script)) { "[!] No existe $Script"; exit 1 }
$salida = Join-Path $env:TEMP 'rnx_nat_salida.txt'
Remove-Item $salida -ErrorAction SilentlyContinue
$p = Start-Process powershell -PassThru -WindowStyle Hidden -RedirectStandardOutput $salida `
     -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$Script`""
$fin = (Get-Date).AddSeconds($Limite)
while (-not $p.HasExited -and (Get-Date) -lt $fin) {
    Start-Sleep -Milliseconds 500
    if ((Test-Path $salida) -and (Select-String -Path $salida -SimpleMatch 'Presiona cualquier tecla' -Quiet)) { break }
}
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
if (Test-Path $salida) { Get-Content $salida; Remove-Item $salida -ErrorAction SilentlyContinue }
if ((Get-Date) -ge $fin) { "[!] Sin terminar en $Limite s" }
