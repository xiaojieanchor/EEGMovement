<## Repair only the configured A100 host route for the current physical gateway.
    Run in an administrator PowerShell. No VPN/default/persistent route changes. ##>
param([switch]$Restore)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$computeProfile = Get-Content -LiteralPath (Join-Path $projectRoot 'configs/gnautilus_compute.json') -Raw | ConvertFrom-Json
$prefix = "$($computeProfile.host)/32"
$receiptPath = Join-Path $projectRoot 'hub/runtime/compute-route-repair.json'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Windows 拒绝非管理员修改主机路由。请用管理员 PowerShell 运行本脚本。' }
if ($Restore) {
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($receipt.prefix -ne $prefix) { throw '路由回执与当前计算主机不一致' }
    Get-NetRoute -DestinationPrefix $prefix -InterfaceIndex $receipt.interface -NextHop $receipt.gateway -PolicyStore ActiveStore -ErrorAction SilentlyContinue |
        Where-Object RouteMetric -EQ 0 | Remove-NetRoute -Confirm:$false
    Write-Host '已移除本脚本添加的临时 A100 路由；原固定路由保留。'
    exit
}
$configs = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.InterfaceAlias -notmatch 'Clash|TUN|Loopback|WSL|vEthernet' })
if ($configs.Count -ne 1) { throw '存在多个上游物理接口，请先明确当前校园网接口；未修改路由。' }
$network = $configs[0]
$gateway = $network.IPv4DefaultGateway.NextHop
$existing = @(Get-NetRoute -DestinationPrefix $prefix -PolicyStore ActiveStore -ErrorAction SilentlyContinue)
if ($existing | Where-Object { $_.NextHop -eq $gateway -and $_.InterfaceIndex -eq $network.InterfaceIndex -and $_.RouteMetric -eq 0 }) {
    Write-Host '当前 A100 临时路由已指向本机上游网关，未重复添加。';exit
}
$receipt = @{prefix=$prefix;interface=$network.InterfaceIndex;gateway=$gateway;original=@($existing | Select-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric);created=(Get-Date).ToString('o')}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
New-NetRoute -DestinationPrefix $prefix -InterfaceIndex $network.InterfaceIndex -NextHop $gateway -RouteMetric 0 -PolicyStore ActiveStore | Out-Null
Write-Host "已添加当前会话 A100 路由：$prefix -> $gateway。中枢将自动重试；这不保证当前网络具备校园私网权限。"
