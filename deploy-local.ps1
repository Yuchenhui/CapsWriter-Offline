# 把本仓库 (fork 的 marshall 分支) 部署到本机安装目录, 然后重启服务端和客户端.
# 安装目录里的 exe / internal / models 来自上游 release zip, 这里只覆盖 Python 源码、配置和热词.
#   pwsh -File deploy-local.ps1            # 部署并重启
#   pwsh -File deploy-local.ps1 -NoRestart # 只拷文件 (热词文件本身会被自动热加载)
param(
    [string]$InstallDir = 'C:\Users\Marshall\Apps\CapsWriter-Offline',
    [switch]$NoRestart
)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
if (-not (Test-Path "$InstallDir\start_server.exe")) { throw "不是 CapsWriter 安装目录: $InstallDir" }

# /S 递归, /XO 不跳旧文件 (以仓库为准), /NJH /NJS /NDL /NP 精简输出; robocopy 退出码 <8 都算成功
robocopy "$src\core" "$InstallDir\core" *.py /S /NJH /NJS /NDL /NP | Out-Host
if ($LASTEXITCODE -ge 8) { throw "robocopy core 失败: $LASTEXITCODE" }
robocopy "$src\LLM" "$InstallDir\LLM" *.py /NJH /NJS /NDL /NP | Out-Host
if ($LASTEXITCODE -ge 8) { throw "robocopy LLM 失败: $LASTEXITCODE" }
foreach ($f in 'config_client.py', 'config_server.py', 'hot.txt', 'hot-rule.txt', 'hot-server.txt') {
    Copy-Item "$src\$f" "$InstallDir\$f" -Force
}
$global:LASTEXITCODE = 0
if ($NoRestart) { 'copied, no restart'; return }

$log = "$InstallDir\logs\server_latest.log"
# server_latest.log 是追加写的, 只看重启之后新增的行, 否则旧的"就绪"行会让客户端过早启动
$skip = @(Get-Content $log -EA SilentlyContinue).Count
Get-Process start_server, start_client -EA SilentlyContinue | Stop-Process -Force -Confirm:$false
Start-Sleep 2
Start-Process "$InstallDir\start_server.exe" -WorkingDirectory $InstallDir -WindowStyle Hidden   # 不闪黑窗口; 托盘「显示/隐藏」仍可调出
foreach ($i in 1..60) {
    if (@(Get-Content $log -EA SilentlyContinue | Select-Object -Skip $skip) -match 'TaskHandler 开始工作循环') { break }
    Start-Sleep 1
}
Start-Process "$InstallDir\start_client.exe" -WorkingDirectory $InstallDir -WindowStyle Hidden
Start-Sleep 5
Select-String "$InstallDir\logs\server_latest.log" -Pattern '全系统初始化完成' | Select-Object -Last 1 | ForEach-Object Line
Select-String "$InstallDir\logs\client_latest.log" -Pattern 'WebSocket 建立成功|Traceback' | Select-Object -Last 1 | ForEach-Object Line
