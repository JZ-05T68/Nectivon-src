Option Explicit

Dim shell, fileSystem, installRoot, pythonw, manager, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

installRoot = fileSystem.GetParentFolderName(fileSystem.GetParentFolderName(WScript.ScriptFullName))
pythonw = fileSystem.BuildPath(installRoot, "runtime\python\pythonw.exe")
manager = fileSystem.BuildPath(installRoot, "scripts\service_manager.py")
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & manager & Chr(34) & " launch"

shell.Run command, 0, False
