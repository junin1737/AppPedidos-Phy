# Instalação — AppPedidos CLIPP (servidor + extensão Chrome)

## O que foi instalado

- **Aplicativo visual** — janela com status do servidor, log recente e atalhos rápidos.
- **Ícone na bandeja** — fica perto do relógio do Windows; fechar a janela minimiza para a bandeja (servidor continua ativo).
- **Pasta `extensao_chrome`** — extensão para importar pedidos do Tiao Cards.
- **Início com o Windows** — ativado na instalação (pode desligar na janela ou no menu da bandeja).

## Como instalar neste PC

### Opção A — ZIP (recomendado se o Defender bloquear o `.exe`)

1. Gere: **`instalador\Gerar ZIP (sem Defender).bat`**
2. Copie **`instalador\dist\AppPedidosCLIPP.zip`** para a outra máquina
3. Extraia o ZIP
4. Execute **`INSTALAR.bat`** como Administrador

### Opção B — Setup.exe

1. Gere: **`instalador\Gerar Instalador.bat`**
2. Copie **`instalador\dist\AppPedidosCLIPP-Setup.exe`**
3. Se o **Windows Defender** alertar, veja a seção abaixo (falso positivo comum)

**Abrir o servidor:** atalho **AppPedidos CLIPP** (não use `Servidor Extensao CLIPP.bat`).

**Se não abrir janela:** menu Iniciar → **Diagnóstico (ver erros)** ou log em `%LOCALAPPDATA%\AppPedidosCLIPP\`.

## Windows Defender (falso positivo)

Instaladores **Inno Setup** sem assinatura digital costumam ser marcados como `Trojan:Win32/Bearfoos` ou similar. **Não é vírus** — é o nosso instalador com Python embutido.

**Se confiar no arquivo** (você mesmo gerou ou recebeu de quem desenvolveu):

1. Segurança do Windows → **Proteção contra vírus e ameaças**
2. **Histórico de proteção** → encontre `AppPedidosCLIPP-Setup.exe`
3. **Ações** → **Restaurar** e depois **Permitir no dispositivo**

Ou adicione **exclusão** na pasta onde está o instalador antes de executar.

**Alternativa:** use o **ZIP** (opção A) — raramente é bloqueado.

Reportar falso positivo à Microsoft (opcional):  
https://www.microsoft.com/wdsi/filesubmission

## Primeira configuração (uma vez por PC)

1. Abra o menu da bandeja (ícone azul) → **Editar config.ini**.
2. Ajuste o caminho do banco `.FDB`, usuário/senha Firebird e `fbclient_path`.
3. Salve o arquivo.

## Extensão Chrome (cada máquina / cada usuário do Chrome)

1. Menu da bandeja → **Abrir pasta da extensão Chrome**  
   (ou copie a pasta `extensao_chrome` para um pen drive e leve a outro PC).
2. No Chrome: `chrome://extensions`
3. Ative **Modo do desenvolvedor**
4. **Carregar sem compactação** → selecione a pasta `extensao_chrome`
5. Fixe o ícone na barra (opcional)

> A extensão não vai para a Chrome Web Store; em cada PC é «Carregar sem compactação» apontando para a pasta copiada.

## Uso diário

1. O servidor sobe sozinho com o Windows (janela + ícone na bandeja).
2. Abra o pedido no Tiao Cards no **seu Chrome** (já logado).
3. Clique na extensão → **Importar esta página**.

Feche a janela do app quando quiser — o servidor **permanece na bandeja**. Para encerrar de vez: botão **Sair** na janela ou menu da bandeja.

## Porta do servidor

Padrão: **8765** — altere em `config.ini` na seção `[extensao]`.  
Se mudar, edite também `PORTA` em `extensao_chrome/popup.js`.

## Atualizar em outras máquinas

Copie a pasta de instalação inteira (ou só `extensao_chrome` + `servidor_app.py` + dependências) e rode o instalador de novo, ou substitua os arquivos e reinicie o servidor pela bandeja (Sair → abrir de novo).

## Log

Menu da bandeja → **Ver log do servidor** (`servidor_clipp.log`).
