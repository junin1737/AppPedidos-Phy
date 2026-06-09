Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = appDir

logDir = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\AppPedidosCLIPP"
If Not fso.FolderExists(logDir) Then fso.CreateFolder logDir
logFile = logDir & "\inicio_vbs.log"

Function EscreverLog(msg)
  On Error Resume Next
  Set f = fso.OpenTextFile(logFile, 8, True)
  f.WriteLine Now & " " & msg
  f.Close
End Function

bundled = appDir & "\python\pythonw.exe"
If fso.FileExists(bundled) Then
  cmd = """" & bundled & """ """ & appDir & "\servidor_app.py"""
  EscreverLog "Iniciando: " & cmd
Else
  cmd = "py -3w """ & appDir & "\servidor_app.py"""
  teste = sh.Run("cmd /c py -3w -c ""import sys""", 0, True)
  If teste <> 0 Then
    cmd = "pythonw.exe """ & appDir & "\servidor_app.py"""
  End If
  EscreverLog "Iniciando (fallback): " & cmd
End If

sh.Run cmd, 0, False
