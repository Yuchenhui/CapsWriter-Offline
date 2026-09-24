' 开机自启 / 开始菜单用: 无窗口启动客户端 (它会再无窗口拉起服务端).
' 用 conhost --headless 而不是 Run(...,0): 默认终端是 Windows Terminal 时, 隐藏参数会被忽略, 仍弹终端窗口.
' 快捷方式指向: wscript.exe "<安装目录>\start-hidden.vbs"
' 已在运行就不再起第二份 (两份客户端 = 两个键盘钩子抢右 Alt). 提示用英文: WSH 按 ANSI 读本文件, 中文会乱码.
Set procs = GetObject("winmgmts:\\.\root\cimv2").ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='start_client.exe'")
If procs.Count > 0 Then
    CreateObject("WScript.Shell").Popup "CapsWriter is already running (see tray icon).", 3, "CapsWriter", 64
    WScript.Quit
End If
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run sh.ExpandEnvironmentStrings("%WINDIR%") & "\System32\conhost.exe --headless """ & dir & "\start_client.exe""", 0, False
