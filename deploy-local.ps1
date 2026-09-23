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
foreach ($f in 'config_client.py', 'config_server.py', 'start-hidden.vbs') {
    Copy-Item "$src\$f" "$InstallDir\$f" -Force
}
# 词库文件以安装目录为准 (用户会在托盘里加词 / 直接编辑), 只在安装目录缺失时拷; 两边不同时提示, 由人决定往哪边同步
foreach ($f in 'hot.txt', 'hot-rule.txt', 'hot-server.txt', 'terms.txt') {
    if (-not (Test-Path "$InstallDir\$f")) { Copy-Item "$src\$f" "$InstallDir\$f" }
    elseif ((Get-FileHash "$src\$f").Hash -ne (Get-FileHash "$InstallDir\$f").Hash) { "词库 $f 与安装目录不同, 未覆盖 (安装目录为准)" }
}
$global:LASTEXITCODE = 0
if ($NoRestart) { 'copied, no restart'; return }

$log = "$InstallDir\logs\server_latest.log"
# server_latest.log 是追加写的, 只看重启之后新增的行, 否则旧的"就绪"行会让客户端过早启动
$skip = @(Get-Content $log -EA SilentlyContinue).Count
# 2026-09-23 事故: 用户按着右 Alt 时杀客户端, 新钩子只看到"松开"并吞掉 -> Alt 卡死全键盘失灵.
# 钩子侧已改为放行未见按下的松开; 这里再加一道: 右 Alt / X2 正被按着就等, 不在人说话时重启
Add-Type -Namespace K -Name S -MemberDefinition '[DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vk);'
while (([K.S]::GetAsyncKeyState(0xA5) -band 0x8000) -or ([K.S]::GetAsyncKeyState(0x06) -band 0x8000)) { '等待右 Alt / 鼠标 X2 松开...'; Start-Sleep -Milliseconds 300 }
Get-Process start_server, start_client -EA SilentlyContinue | Stop-Process -Force -Confirm:$false
Start-Sleep 2
# 只启动客户端: 它会隐藏拉起服务端 (core/client/server_launcher.py)
# conhost --headless: 默认终端是 Windows Terminal 时 -WindowStyle Hidden 不管用
Start-Process "$env:WINDIR\System32\conhost.exe" -ArgumentList '--headless', "`"$InstallDir\start_client.exe`"" -WorkingDirectory $InstallDir -WindowStyle Hidden
foreach ($i in 1..60) {
    if (@(Get-Content $log -EA SilentlyContinue | Select-Object -Skip $skip) -match 'TaskHandler 开始工作循环') { break }
    Start-Sleep 1
}
Start-Sleep 3   # 客户端连接重试
Select-String "$InstallDir\logs\server_latest.log" -Pattern '全系统初始化完成' | Select-Object -Last 1 | ForEach-Object Line
Select-String "$InstallDir\logs\client_latest.log" -Pattern 'WebSocket 建立成功|Traceback' | Select-Object -Last 1 | ForEach-Object Line
