; Kitchen Search — Windows Installer
; Requires Inno Setup 6+ : https://jrsoftware.org/isinfo.php
;
; To build the installer:
;   ISCC.exe installer.iss
; or open this file in the Inno Setup GUI and click Build.
;
; Prerequisites: run build_nuitka.sh first to produce nuitka-build\emoji-kitchen\

#define AppName    "Kitchen Search"
#define AppVersion "1.0"
#define BuildDir   "nuitka-build\emoji-kitchen"

[Setup]
; Keep AppId stable across updates so Windows recognises upgrades.
AppId={{6F3A2B1C-9D4E-4F87-B532-1A2C3D4E5F60}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Kitchen Search
AppPublisherURL=https://github.com/morganrivers/kitchensearch
DefaultDirName={localappdata}\KitchenSearch
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; No admin rights needed — installs to %LOCALAPPDATA%
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=KitchenSearch-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
UninstallDisplayIcon={app}\emoji-picker-tk.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
  Description: "Create a &desktop shortcut"
Name: "startup"; \
  Description: "Start hotkey listener automatically with Windows"

[Files]
Source: "{#BuildDir}\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";                Filename: "{app}\emoji-picker-tk.exe"
Name: "{group}\{#AppName} Settings";       Filename: "{app}\kitchensearch-daemon.exe"; Parameters: "--settings"
Name: "{group}\Uninstall {#AppName}";      Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";          Filename: "{app}\emoji-picker-tk.exe"; Tasks: desktopicon

[Registry]
; Write startup entry when the checkbox is ticked; remove it on uninstall.
Root: HKCU; \
  Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\kitchensearch-daemon.exe"""; \
  Tasks: startup; \
  Flags: uninsdeletevalue

[UninstallRun]
; Clean up any startup entry the daemon may have written itself (e.g. via
; --setup), regardless of whether the installer checkbox was ticked.
Filename: "{app}\kitchensearch-daemon.exe"; \
  Parameters: "--uninstall"; \
  Flags: runhidden; \
  RunOnceId: "RemoveStartupEntry"

[Run]
; Start the hotkey daemon unconditionally after install.
Filename: "{app}\kitchensearch-daemon.exe"; \
  Flags: nowait runhidden
; Optionally launch the picker (user-visible checkbox on finish page).
Filename: "{app}\emoji-picker-tk.exe"; \
  Flags: nowait postinstall skipifsilent; \
  Description: "Launch {#AppName} now"


[Code]
var
  HotkeyPage: TInputQueryWizardPage;

function InitializeSetup(): Boolean;
begin
  if CheckForMutexes('Global\KitchenSearchDaemon') then
  begin
    MsgBox(
      'Kitchen Search is currently running.' + #13#10 +
      'Please right-click the tray icon and choose "Quit", then run the installer again.',
      mbError, MB_OK
    );
    Result := False;
    Exit;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM kitchensearch-daemon.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM emoji-picker-tk.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure InitializeWizard;
begin
  HotkeyPage := CreateInputQueryPage(
    wpSelectTasks,
    'Keyboard Shortcut',
    'Choose a global hotkey to open Kitchen Search.',
    'Type a combination using Ctrl, Alt, and/or Shift with a letter or function key.' + #13#10 +
    'Examples: Ctrl+Alt+K    Ctrl+Shift+F2    Alt+Shift+S' + #13#10 +
    'You can change this later from the Kitchen Search Settings menu.'
  );
  HotkeyPage.Add('Hotkey:', False);
  HotkeyPage.Values[0] := 'Ctrl+Alt+K';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  V: String;
begin
  Result := True;
  if CurPageID = HotkeyPage.ID then
  begin
    V := Trim(HotkeyPage.Values[0]);
    if V = '' then
    begin
      MsgBox('Please enter a hotkey, e.g. Ctrl+Alt+K', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigDir, ConfigFile, Hotkey: String;
begin
  if CurStep = ssPostInstall then
  begin
    Hotkey := Trim(HotkeyPage.Values[0]);
    if Hotkey = '' then Hotkey := 'Ctrl+Alt+K';
    ConfigDir := ExpandConstant('{localappdata}\kitchensearch');
    ForceDirectories(ConfigDir);
    ConfigFile := ConfigDir + '\picker-settings.json';
    SaveStringToFile(ConfigFile, '{"hotkey": "' + Hotkey + '"}', False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\kitchensearch');
    if DirExists(DataDir) then
    begin
      if MsgBox(
        'Remove downloaded cache and settings?' + #13#10 +
        '(' + DataDir + ')',
        mbConfirmation, MB_YESNO
      ) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
