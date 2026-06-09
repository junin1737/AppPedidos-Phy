# Extensão Chrome — importar sem abrir outro navegador

O RPA (Playwright) **não consegue** controlar o Chrome que você abre pelo ícone do Windows — o Google não permite, por segurança. Por isso existia o Chrome separado (`.rpa_profile`).

A **extensão** resolve isso: ela roda **dentro do seu Chrome**, na guia onde você já fez login e 2FA.

## Instalação (uma vez)

1. Execute **`Servidor Extensao CLIPP.bat`** e deixe a janela aberta.
2. No Chrome: `chrome://extensions`
3. Ative **Modo do desenvolvedor**
4. **Carregar sem compactação** → escolha a pasta `extensao_chrome` deste projeto
5. Fixe a extensão na barra (opcional)

## Uso no dia a dia

1. Servidor bat **aberto** no PC
2. No **seu Chrome normal**, abra o pedido:  
   Minha conta → Dashboard administrativo → Pedidos → detalhe do pedido
3. Clique no ícone da extensão **Tiao Cards → CLIPP**
4. **Importar esta página**

Só importa pedidos com status **Pagamento efetuado - Aguardando envio**.

## Porta do servidor

Padrão: `8765` — altere em `config.ini`:

```ini
[extensao]
porta = 8765
```

Se mudar a porta, edite também `PORTA` em `extensao_chrome/popup.js`.

## RPA vs extensão

| | RPA (`importar_site.py`) | Extensão |
|---|---|---|
| Chrome | Janela dedicada ou CDP | Seu Chrome normal |
| 2FA | No Chrome RPA | Onde você já logou |
| Servidor local | Não precisa | `Servidor Extensao CLIPP.bat` |
