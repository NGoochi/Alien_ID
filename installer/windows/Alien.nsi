; Alien for Grasshopper: the Windows installer (NSIS 3), built by tools/package.sh:
;   makensis -DVERSION=0.02-alpha -DVIVERSION=0.0.2.0 -DGHA=<Alien.gha> -DART=<art dir> -DSRC=<this folder>
;            -DOUT=<Setup.exe> -DSIZEKB=<size> installer/windows/Alien.nsi
;
; Per user, no administrator rights: Alien.gha goes to %APPDATA%\Grasshopper\Libraries (Grasshopper's own Libraries
; folder), settings to %APPDATA%\Alien\settings.json (script folder, and the OpenRouter key if one is pasted), and an
; uninstaller to %LOCALAPPDATA%\Programs\Alien with an entry in Settings > Apps. Files an installer writes carry no
; "downloaded from the internet" mark, so Grasshopper loads Alien without the Unblock step the zip needed.

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\Alien"

Name "Alien ${VERSION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\Alien"
BrandingText "Alien ${VERSION} for Grasshopper"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${VIVERSION}"
VIAddVersionKey "ProductName" "Alien"
VIAddVersionKey "FileDescription" "Alien for Grasshopper (Rhino 8) installer"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "Alien"
VIAddVersionKey "LegalCopyright" "Nick Gauci"

Var RhinoExe
Var KeyField
Var KeyValue

; ── look ───────────────────────────────────────────────────

!define MUI_ICON "${ART}/alien.ico"
!define MUI_UNICON "${ART}/alien.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ART}/wizard-side.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${ART}/wizard-side.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "${ART}/wizard-header.bmp"
!define MUI_ABORTWARNING

; ── pages ──────────────────────────────────────────────────

!define MUI_WELCOMEPAGE_TITLE "Install Alien ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Alien adds nodes to Grasshopper that write their own script from a plain-English description, plus a suite of architectural nodes (site maps, views, levels, grids, walls, curtain walls, structure).$\r$\n$\r$\nThis installs Alien for your Windows account. It needs Rhino 8, and no administrator rights.$\r$\n$\r$\nPlease close Rhino before you continue."
!insertmacro MUI_PAGE_WELCOME
Page custom KeyPage KeyPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "Alien is installed"
!define MUI_FINISHPAGE_TEXT "Rhino opens with Grasshopper. Alien is on the Alien tab: place a node and click PANEL on it to open the Alien panel.$\r$\n$\r$\nThe panel's Help button (?) shows how to connect OpenRouter if you skipped it, and how to get started."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start Rhino 8 with Grasshopper"
!define MUI_FINISHPAGE_RUN_FUNCTION StartRhino
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishShow
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ── finding and closing Rhino ──────────────────────────────

Function .onInit
  ; Rhino 8 writes its folder to the registry; fall back to the default install folder.
  SetRegView 64
  ReadRegStr $0 HKLM "SOFTWARE\McNeel\Rhinoceros\8.0\Install" "Path"
  ReadRegStr $1 HKLM "SOFTWARE\McNeel\Rhinoceros\8.0\Install" "InstallPath"
  StrCpy $RhinoExe ""
  ${If} ${FileExists} "$0\Rhino.exe"
    StrCpy $RhinoExe "$0\Rhino.exe"
  ${ElseIf} ${FileExists} "$1\System\Rhino.exe"
    StrCpy $RhinoExe "$1\System\Rhino.exe"
  ${ElseIf} ${FileExists} "$PROGRAMFILES64\Rhino 8\System\Rhino.exe"
    StrCpy $RhinoExe "$PROGRAMFILES64\Rhino 8\System\Rhino.exe"
  ${EndIf}
  ${If} $RhinoExe == ""
    MessageBox MB_YESNO|MB_ICONQUESTION "Rhino 8 doesn't seem to be installed on this computer.$\r$\n$\r$\nAlien is a Grasshopper plugin for Rhino 8. Install it anyway?" IDYES +2
    Abort
  ${EndIf}
FunctionEnd

; Wait until Rhino is closed: Grasshopper keeps Alien.gha open while Rhino runs, so it can't be replaced.
!macro WAIT_FOR_RHINO
  retry:
    nsExec::Exec 'cmd.exe /c tasklist /FI "IMAGENAME eq Rhino.exe" /NH | find /I "Rhino.exe" >nul'
    Pop $0
    ${If} $0 == "0"
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Rhino is running. Close Rhino (File > Exit), then click Retry." IDRETRY retry
      Abort "Cancelled: Rhino is still running."
    ${EndIf}
!macroend

Function WaitForRhino
  !insertmacro WAIT_FOR_RHINO
FunctionEnd

Function un.WaitForRhino
  !insertmacro WAIT_FOR_RHINO
FunctionEnd

; ── the OpenRouter key page (optional) ─────────────────────

Function KeyPage
  !insertmacro MUI_HEADER_TEXT "Connect OpenRouter (optional)" "Describing nodes and the AI nodes run on models reached through OpenRouter."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 34u "Paid from your own OpenRouter credit: a described node costs about US$0.002. Paste your API key to link it now, or leave the box empty and do it later from the Alien panel's Help (?), which walks you through it."
  Pop $0
  ${NSD_CreateLink} 0 38u 100% 11u "No key yet? Open openrouter.ai: sign up, add credit, then Settings > API Keys > Create API Key."
  Pop $0
  ${NSD_OnClick} $0 OpenKeys
  ${NSD_CreateLabel} 0 58u 100% 10u "OpenRouter API key (it starts with sk-or-v1-):"
  Pop $0
  ${NSD_CreatePassword} 0 70u 100% 13u "$KeyValue"
  Pop $KeyField
  ${NSD_CreateLabel} 0 92u 100% 26u "The key is saved in %APPDATA%\Alien\settings.json for your Windows account only. It never goes into Grasshopper files or scripts."
  Pop $0
  ${NSD_SetFocus} $KeyField
  nsDialogs::Show
FunctionEnd

Function OpenKeys
  ExecShell "open" "https://openrouter.ai/settings/keys"
FunctionEnd

Function KeyPageLeave
  ${NSD_GetText} $KeyField $KeyValue
  StrCpy $0 $KeyValue 6
  ${If} $KeyValue != ""
  ${AndIf} $0 != "sk-or-"
    MessageBox MB_OK|MB_ICONEXCLAMATION "That doesn't look like an OpenRouter API key (they start with sk-or-v1-).$\r$\n$\r$\nCheck it, or leave the box empty and link it later from the Alien panel's Help."
    Abort
  ${EndIf}
FunctionEnd

; ── install ────────────────────────────────────────────────

Section "Alien" SecMain
  Call WaitForRhino

  DetailPrint "Installing Alien into Grasshopper's Libraries folder"
  SetOutPath "$APPDATA\Grasshopper\Libraries"
  File "/oname=Alien.gha" "${GHA}"

  ; Settings: the script folder (Documents\Alien scripts) and the key, merged into any settings.json already there.
  DetailPrint "Writing Alien's settings"
  InitPluginsDir
  FileOpen $0 "$PLUGINSDIR\key.txt" w
  FileWrite $0 $KeyValue
  FileClose $0
  SetOutPath "$PLUGINSDIR"
  File "/oname=settings.ps1" "${SRC}/settings.ps1"
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\settings.ps1" -KeyFile "$PLUGINSDIR\key.txt" -Scripts "$DOCUMENTS\Alien scripts"'
  Pop $0
  Delete "$PLUGINSDIR\key.txt"
  ${If} $0 != "0"
    DetailPrint "Settings weren't written ($0). Alien makes them itself when Grasshopper starts; link the key from the panel's Help."
  ${EndIf}

  ; Uninstaller and its entry in Settings > Apps
  SetOutPath "$INSTDIR"
  File "/oname=alien.ico" "${ART}/alien.ico"
  WriteUninstaller "$INSTDIR\Uninstall Alien.exe"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "Alien for Grasshopper"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "Publisher" "Alien"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\alien.ico"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall Alien.exe"'
  WriteRegStr HKCU "${UNINST}" "QuietUninstallString" '"$INSTDIR\Uninstall Alien.exe" /S'
  WriteRegStr HKCU "${UNINST}" "URLInfoAbout" "https://github.com/NGoochi/Alien_ID"
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST}" "EstimatedSize" ${SIZEKB}
  DetailPrint "Done. Alien.gha is in $APPDATA\Grasshopper\Libraries"
SectionEnd

Function FinishShow
  ${If} $RhinoExe == ""
    SendMessage $mui.FinishPage.Run ${BM_SETCHECK} 0 0
    ShowWindow $mui.FinishPage.Run ${SW_HIDE}
  ${EndIf}
FunctionEnd

Function StartRhino
  Exec '"$RhinoExe" /nosplash /runscript="_-Grasshopper _Window _Show _Enter"'
FunctionEnd

; ── uninstall ──────────────────────────────────────────────

Section "Uninstall"
  Call un.WaitForRhino
  Delete "$APPDATA\Grasshopper\Libraries\Alien.gha"
  Delete "$INSTDIR\Uninstall Alien.exe"
  Delete "$INSTDIR\alien.ico"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST}"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also remove Alien's settings from this computer, including your OpenRouter key?$\r$\n$\r$\nYour scripts in the script folder are kept either way." /SD IDNO IDNO +2
    RMDir /r "$APPDATA\Alien"
SectionEnd
