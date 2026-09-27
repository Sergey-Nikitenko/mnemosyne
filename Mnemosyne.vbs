' Mnemosyne.vbs - double-click entry point for the Mnemosyne Operator Console.
'
' Why a .vbs: a .ps1 cannot be double-clicked (Explorer opens Notepad), and a
' .lnk that points at powershell.exe needs its arguments embedded in the link.
' This stub is a *disposable launcher surface* exactly like launch-current.ps1:
' it holds no configuration of its own, it just hands off to the script with
' whatever arguments the shortcut passes. Delete it and every Mnemosyne semantic
' is intact.
'
' It runs the window HIDDEN (0 = vbHide) so double-clicking gives no console
' flash. launch-current.ps1 opens/focuses the console URL itself.
'
' Invoke from a shortcut (or by double-click, with no arguments):
'
'     wscript.exe "C:\workspace\mnemosyne\Mnemosyne.vbs"
'     wscript.exe "C:\workspace\mnemosyne\Mnemosyne.vbs" -Port 0
'
Option Explicit

Dim shell, fso, here, script, cmd, i, extra
Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

' This script's own folder = the Mnemosyne checkout, so it works from anywhere.
here   = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(here, "launch-current.ps1")

If Not fso.FileExists(script) Then
    MsgBox "launch-current.ps1 not found next to Mnemosyne.vbs:" & vbCrLf & script, _
           vbCritical, "Mnemosyne"
    WScript.Quit 1
End If

' Forward every argument the shortcut supplied (none when double-clicked).
extra = ""
For i = 0 To WScript.Arguments.Count - 1
    extra = extra & " " & Quote(WScript.Arguments(i))
Next

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command " & _
      Quote("& '" & script & "'" & extra)

' 0 = hidden window, False = do not wait for it to finish.
shell.Run cmd, 0, False

Function Quote(s)
    Quote = """" & Replace(s, """", """""") & """"
End Function
