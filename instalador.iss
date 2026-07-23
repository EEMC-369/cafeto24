; Script de Inno Setup para Cafeto24
; Diseñado para empaquetar la carpeta compilada por Nuitka (CajaCafeto24.dist)

[Setup]
AppName=Cafeto24
AppVersion=3.11.0
AppPublisher=Cafeto24
DefaultDirName={autopf}\Cafeto24
DefaultGroupName=Cafeto24
AllowNoIcons=yes
; Configurar el instalador de salida
OutputDir=.
OutputBaseFilename=Cafeto24_Setup
SetupIconFile=static\icono.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

; Requerir privilegios de administrador para la instalación (para escribir en Program Files)
PrivilegesRequired=admin

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; Crear el directorio para datos persistentes en ProgramData
Name: "{commonappdata}\Cafeto24"

[Files]
; Copiar el ejecutable compilado principal y dependencias
Source: "CajaCafeto24.dist\CajaCafeto24.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "CajaCafeto24.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Copiar carpetas de recursos directamente desde la raíz del código fuente para garantizar que siempre estén presentes
Source: "templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cafeto24"; Filename: "{app}\CajaCafeto24.exe"; IconFilename: "{app}\static\icono.ico"
Name: "{group}\{cm:UninstallProgram,Cafeto24}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Cafeto24"; Filename: "{app}\CajaCafeto24.exe"; IconFilename: "{app}\static\icono.ico"; Tasks: desktopicon

[Run]
; Asignar permisos de modificacion a todos los usuarios locales (SID *S-1-5-32-545) usando icacls de Windows
Filename: "icacls.exe"; Parameters: """{commonappdata}\Cafeto24"" /grant *S-1-5-32-545:(OI)(CI)M /T"; Flags: runhidden
Filename: "icacls.exe"; Parameters: """{app}"" /grant *S-1-5-32-545:(OI)(CI)M /T"; Flags: runhidden
; Registrar excepciones en el Cortafuegos de Windows (Firewall)
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Cafeto24"""; Flags: runhidden
Filename: "netsh.exe"; Parameters: "advfirewall firewall add rule name=""Cafeto24"" dir=in action=allow program=""{app}\CajaCafeto24.exe"" enable=yes profile=any"; Flags: runhidden
Filename: "{app}\CajaCafeto24.exe"; Description: "{cm:LaunchProgram,Cafeto24}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Limpiar la regla del firewall al desinstalar
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Cafeto24"""; Flags: runhidden

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // Cerrar el proceso si esta corriendo para evitar bloqueo de archivos
  Exec('taskkill.exe', '/f /im CajaCafeto24.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
