; Compilar: instalador\Gerar Instalador.bat
; Python + dependencias ja vêm em payload\python (montado por preparar_payload.ps1)

#define MyAppName "AppPedidos CLIPP"
#define MyAppVersion "1.2.1"
#define MyAppPublisher "AppPedidos Phy"
#define MyAppURL "https://www.tiaocards.com.br/"
#define SourceDir ".."

[Setup]
AppId={{A7B3C9D1-4E2F-4A8B-9C1D-2E3F4A5B6C7D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\AppPedidos CLIPP
DefaultGroupName={#MyAppName}
OutputDir=dist
OutputBaseFilename=AppPedidosCLIPP-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#SourceDir}\app_icon.ico
UninstallDisplayIcon={app}\app_icon.ico
DisableProgramGroupPage=no
MinVersion=10.0

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "startup"; Description: "Iniciar servidor com o Windows"; GroupDescription: "Opcoes:"; Flags: checkedonce
Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos:"; Flags: checkedonce

[Files]
; Python pronto (tkinter + pip + fdb + pystray)
Source: "payload\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
; Aplicacao
Source: "{#SourceDir}\*.py"; DestDir: "{app}"; Flags: ignoreversion; Excludes: "gerar_pdf_estudo.py,_reparar_visibilidade.py,aplicacao_vendas.py,importar_site.py,comparar_vendas_clipp.py,extrator_ocr.py"
Source: "{#SourceDir}\*.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\*.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\app_icon.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\config.ini.exemplo"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\extensao_chrome\*"; DestDir: "{app}\extensao_chrome"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\AppPedidos CLIPP.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\Diagnostico (ver erros)"; Filename: "{app}\Abrir Servidor (diagnostico).bat"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\Pasta extensao Chrome"; Filename: "explorer.exe"; Parameters: """{app}\extensao_chrome"""; IconFilename: "{app}\app_icon.ico"
Name: "{group}\Configurar config.ini"; Filename: "notepad.exe"; Parameters: """{app}\config.ini"""; IconFilename: "{app}\app_icon.ico"
Name: "{group}\Ajuda instalacao"; Filename: "{app}\LEIA-ME-INSTALACAO.md"; IconFilename: "{app}\app_icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\AppPedidos CLIPP.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon
Name: "{autodesktop}\Extensao Chrome CLIPP"; Filename: "explorer.exe"; Parameters: """{app}\extensao_chrome"""; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\AppPedidos CLIPP.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"; Tasks: startup

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\pos_instalacao.ps1"" -AppDir ""{app}"""; StatusMsg: "Verificando instalacao..."; Flags: runhidden waituntilterminated
Filename: "{app}\AppPedidos CLIPP.bat"; Description: "Iniciar {#MyAppName} agora"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not FileExists(ExpandConstant('{app}\config.ini')) then
      CopyFile(ExpandConstant('{app}\config.ini.exemplo'), ExpandConstant('{app}\config.ini'), False);
  end;
end;

[Messages]
brazilianportuguese.WelcomeLabel2=Instala o servidor AppPedidos CLIPP com Python e bibliotecas ja inclusos.%n%nNao precisa instalar Python no PC.%n%nApos instalar: configure config.ini e carregue a extensao Chrome.
