' 开机自启 / 开始菜单用: 无窗口启动客户端 (它会再无窗口拉起服务端).
' 用 conhost --headless 而不是 Run(...,0): 默认终端是 Windows Terminal 时, 隐藏参数会被忽略, 仍弹终端窗口.
' 快捷方式指向: wscript.exe "<安装目录>\start-hidden.vbs"
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "conhost.exe --headless """ & dir & "\start_client.exe""", 0, False
