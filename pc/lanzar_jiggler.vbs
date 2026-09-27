' Arranca el jiggler (mantener_despierto.ps1, en esta misma carpeta) sin que se
' vea ni un parpadeo de ventana. Lo lanza la tarea programada "Jiggler".
Set fso = CreateObject("Scripting.FileSystemObject")
ps1 = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "mantener_despierto.ps1")
CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """", 0, False
