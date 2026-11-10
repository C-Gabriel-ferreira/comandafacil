# ComandaFácil — comandas, relatórios e estoque na web

Sistema web (Flask + SQLite) que funciona em qualquer navegador: PC, tablet ou celular.
Detalhes de cada recurso em **GUIA_DAS_MELHORIAS.md**.

## Opção A — Rede local do estabelecimento (mais simples)
1. Instale o Python 3.10+ (marque "Add to PATH").
2. Dê dois cliques em `INICIAR_COMANDAFACIL.bat` (Linux/macOS: `./iniciar_servidor.sh`).
3. No PC: http://127.0.0.1:5000. Nos celulares/tablets **no mesmo Wi-Fi**: `http://IP-DO-PC:5000` (descubra o IP com `ipconfig`). Se o Windows perguntar sobre o firewall, permita em "Rede privada".
4. Primeiro acesso: crie a conta de **gerente**; crie os garçons em *Usuários*.
⚠ Em rede local o acesso é HTTP (sem criptografia): use apenas em Wi-Fi privado com senha. Deixe o PC ligado enquanto houver atendimento.
Já tinha dados da versão antiga? Copie o `comandas.db` para a pasta antes de iniciar: a conversão é automática e o original é preservado.

## Opção B — Online, com HTTPS (acesso de qualquer lugar)
Requer um servidor (VPS Linux com Docker) e um domínio apontando para ele.
```
cp .env.example .env        # preencha DOMINIO e COMANDAFACIL_SECRET_KEY
docker compose up -d --build
```
O Caddy obtém o certificado HTTPS sozinho. Os dados ficam no volume `dados` (banco, backups, chave).
Outras hospedagens servem se tiverem **disco persistente** (o SQLite grava em arquivo): rode `python app.py` com `COMANDAFACIL_HOST=0.0.0.0`, `COMANDAFACIL_DATA_DIR=<disco>`, `COMANDAFACIL_HTTPS=1` e `COMANDAFACIL_BEHIND_PROXY=1`.

## Variáveis de ambiente
| Variável | Função |
|---|---|
| `COMANDAFACIL_HOST` | `127.0.0.1` (só este PC, padrão) ou `0.0.0.0` (rede/servidor) |
| `COMANDAFACIL_PORT` / `PORT` | porta (padrão 5000) |
| `COMANDAFACIL_DATA_DIR` | pasta do banco, backups e `secret.key` |
| `COMANDAFACIL_SECRET_KEY` | chave de sessão (senão é gerada em `secret.key`) |
| `COMANDAFACIL_HTTPS=1` | cookies `Secure` + HSTS (use atrás de HTTPS) |
| `COMANDAFACIL_TZ` | fuso do estabelecimento (padrão `America/Sao_Paulo`); importante em servidores em UTC |
| `COMANDAFACIL_BEHIND_PROXY=1` | confia no proxy (Caddy/Nginx) para IP e protocolo reais |
| `COMANDAFACIL_PUBLIC_URL` | endereço público (ex.: `https://comandas.seudominio.com.br`) usado nos links de e-mail e no retorno do Google. **Defina online** |
| `COMANDAFACIL_GOOGLE_CLIENT_ID` / `_CLIENT_SECRET` | ativam o botão "Entrar com o Google" |
| `COMANDAFACIL_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` / `_SECURITY` | ativam a recuperação de senha por e-mail |
`GET /healthz` responde `{"status":"ok"}` para monitoramento.

## Login com Google
O Google **não cria contas**: só entra quem já tem usuário e o **mesmo e-mail** cadastrado (o gerente preenche em *Usuários*). No 1º acesso a conta Google fica vinculada ao usuário; se o e-mail do usuário for trocado, o vínculo é desfeito.
1. Em https://console.cloud.google.com crie um projeto → *APIs e serviços* → *Tela de permissão OAuth* (tipo "Externo" serve) → *Credenciais* → *Criar credenciais* → *ID do cliente OAuth* → tipo **Aplicativo da Web**.
2. Em *URIs de redirecionamento autorizados* coloque o endereço do sistema + `/login/google/retorno`, por exemplo `http://localhost:5000/login/google/retorno` (PC local) ou `https://comandas.seudominio.com.br/login/google/retorno` (online).
3. Copie o ID e o segredo para o arquivo `.env` (na pasta do sistema; modelo em `.env.example`) e reinicie. O botão aparece sozinho.
O Google só aceita `localhost` ou um domínio com HTTPS: o botão não funciona abrindo o sistema por `http://IP-DO-PC:5000` (celulares na rede local). Nesses casos, entre com usuário e senha.

## Recuperação de senha
Precisa de um e-mail para enviar o link (vale 30 minutos, uso único, no máximo 3 pedidos por hora por conta). Preencha no `.env` os campos `COMANDAFACIL_SMTP_*` (no Gmail: `smtp.gmail.com`, porta 587, e uma *senha de app*) e reinicie. Sem isso, a tela "Esqueci minha senha" orienta a pedir a um gerente, que redefine a senha em *Usuários*. Cada usuário precisa ter e-mail cadastrado para receber o link.

**Sem e-mail (código do suporte):** o dono pede ajuda a você, que gera um código assinado de uso único: `python tools/gerar_licenca.py reset --email dono@gmail.com --horas 24`. O cliente abre `/login/codigo` (link na tela "Esqueci minha senha"), cola o código e define a nova senha. Funciona offline, só vale para o e-mail indicado, expira no prazo e não pode ser reutilizado. A senha é validada antes de o código ser consumido, então um erro de digitação não o queima.

## Licença e limite de usuários
Cada cliente recebe uma **licença** (texto `CF1.…`) que define quantos usuários ativos ele pode ter (o gerente conta) e até quando vale. No **primeiro acesso** (`/setup`) há um campo opcional de licença: com ela, o e-mail do gerente vem da licença e ela já fica ativada; sem ela, o sistema entra em modo de avaliação. Depois, a licença também pode ser colada em *Usuários → Licença*. A licença traz o **e-mail do responsável** (o Gmail do gerente principal): só um gerente com esse e-mail cadastrado consegue ativá-la. O **login com Google é só para o gerente**; garçons e demais usuários entram com usuário e senha.
- Sem licença: modo de avaliação com 3 usuários ativos (constante `EVAL_MAX_USERS` em `comandafacil/license.py`).
- Licença vencida: ninguém é bloqueado no meio do expediente, mas não dá para criar nem reativar usuários.
- A licença é conferida offline (assinatura Ed25519); o sistema só tem a chave **pública**.

**Para o vendedor** (você, não o cliente):
1. Uma vez: `python tools/gerar_licenca.py init`. Cria `chave_privada_licenca.pem` (guarde com cópia de segurança; já está no `.gitignore`/`.dockerignore`; **nunca** envie ao cliente) e grava a chave pública em `comandafacil/license_key.py`. Faça isso **antes** de gerar o sistema que vai para os clientes.
2. Para cada cliente: `python tools/gerar_licenca.py emitir --cliente "Restaurante do Zé" --email dono@gmail.com --usuarios 5 --dias 365` e envie o texto impresso.
Perder a chave privada impede emitir novas licenças para os sistemas já entregues. O aplicativo de desktop (PySide6) ainda **não** aplica o limite de usuários: ele é feito na versão web.

## Testes
`python -m unittest discover -s tests -v` .

## Segurança (resumo)
- Senhas com hash (scrypt), mínimo de 8 caracteres e recusa de senhas óbvias (`12345678`, `senha123`, igual ao usuário).
- Login com limite de tentativas por usuário, por IP e por conta (a mensagem diz quanto esperar) e sem diferença de tempo entre usuário existente e inexistente.
- Trocar ou redefinir a senha derruba as outras sessões abertas daquele usuário. A sessão dura 12 h; "Manter conectado" estende para 14 dias.
- Proteção contra CSRF em todo formulário, cabeçalhos `Content-Security-Policy`, `X-Frame-Options`, `Permissions-Policy`, e corpo de requisição limitado a 2 MB.
- O endereço pós-login (`?next=`) só aceita caminhos internos. A exportação CSV neutraliza fórmulas de planilha.
- Recuperação de senha por e-mail só é oferecida online (`COMANDAFACIL_HTTPS=1`) se `COMANDAFACIL_PUBLIC_URL` estiver definido, para o link não depender do cabeçalho `Host`.
- O primeiro acesso (`/setup`) cria o gerente: em servidor na internet, abra o endereço e crie a conta logo após subir o sistema.
- Os contadores de tentativas ficam na memória do processo: rode **um** processo (padrão do `python app.py` e do Docker deste projeto).

## Cuidados
- Faça cópia da pasta de dados/`backups` fora do servidor periodicamente (há também download em *Configurações*).
- Não use `COMANDAFACIL_BEHIND_PROXY=1` sem proxy na frente (permitiria forjar o IP e burlar o bloqueio de tentativas de login).
- SQLite atende bem um restaurante (dezenas de usuários simultâneos); para várias lojas num só servidor seria preciso outro banco.
- **WhatsApp:** o botão "Mensagem" da comanda monta o texto e abre a conversa do cliente no WhatsApp Web, em outra aba. O sistema não envia nada sozinho. Integração dentro da tela exigiria a API oficial do WhatsApp Business (Meta).
