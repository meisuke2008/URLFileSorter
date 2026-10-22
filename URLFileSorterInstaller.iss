#define MyAppName "URL File Sorter"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "MUYA"
#define MyAppExeName "app.exe"
#define NativeHostName "com.url_file_sorter"

[Setup]
AppId={{B218F6A1-BAA2-4B75-A2C0-1DF694A0A201}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/meisuke2008/URLFileSorter
AppSupportURL=https://github.com/meisuke2008/URLFileSorter/issues
VersionInfoVersion=1.0.0.0
DefaultDirName={localappdata}\URLFileSorter
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=URLFileSorterSetup
Compression=lzma
SolidCompression=yes
UninstallDisplayName={#MyAppName}

[Files]
Source: "{#SourcePath}\native-host\dist\app.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\{#NativeHostName}"; ValueType: string; ValueName: ""; ValueData: "{app}\app.json"; Flags: uninsdeletekey

[UninstallDelete]
Type: files; Name: "{app}\app.json"
Type: dirifempty; Name: "{app}"

[Code]
var
    ExtensionPage: TInputQueryWizardPage;

function ValidExtensionId(const Value: String): Boolean;
var
    I: Integer;
begin
    Result := False;
    if Length(Value) <> 32 then Exit;
    for I := 1 to Length(Value) do
        if (Value[I] < 'a') or (Value[I] > 'p') then Exit;
    Result := True;
end;

procedure InitializeWizard;
begin
    ExtensionPage := CreateInputQueryPage(wpSelectDir,
        'Chrome extension ID', 'Allow only your installed URL File Sorter extension',
        'Open chrome://extensions, enable Developer mode, and copy the 32-character ID for URL File Sorter.');
    ExtensionPage.Add('Extension ID:', False);
    ExtensionPage.Values[0] := ExpandConstant('{param:EXTENSIONID|}');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
    Result := True;
    if CurPageID = ExtensionPage.ID then begin
        ExtensionPage.Values[0] := Trim(ExtensionPage.Values[0]);
        if not ValidExtensionId(ExtensionPage.Values[0]) then begin
            MsgBox('Enter a 32-character extension ID using lowercase letters a through p.', mbError, MB_OK);
            Result := False;
        end;
    end;
end;

function EscapeJsonPath(const Path: String): String;
begin
    Result := Path;
    StringChangeEx(Result, '\', '\\', True);
    StringChangeEx(Result, '"', '\"', True);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
    Result := '';
    if not ValidExtensionId(Trim(ExtensionPage.Values[0])) then
        Result := 'A valid Chrome extension ID is required. For silent installation, pass /EXTENSIONID=your_extension_id.';
end;

procedure CreateNativeHostManifest;
var
    JsonText, AppExePath, ManifestPath, ExtensionId: String;
begin
    ExtensionId := Trim(ExtensionPage.Values[0]);
    if not ValidExtensionId(ExtensionId) then
        RaiseException('A valid Chrome extension ID is required.');
    AppExePath := ExpandConstant('{app}\{#MyAppExeName}');
    ManifestPath := ExpandConstant('{app}\app.json');
    JsonText := '{' + #13#10 +
        '  "name": "{#NativeHostName}",' + #13#10 +
        '  "description": "URL File Sorter Native Host",' + #13#10 +
        '  "path": "' + EscapeJsonPath(AppExePath) + '",' + #13#10 +
        '  "type": "stdio",' + #13#10 +
        '  "allowed_origins": ["chrome-extension://' + ExtensionId + '/"]' + #13#10 +
        '}';
    if not SaveStringToFile(ManifestPath, Utf8Encode(JsonText), False) then
        RaiseException('Could not create app.json.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
    if CurStep = ssPostInstall then CreateNativeHostManifest;
end;
