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
`GET /healthz` responde `{"status":"ok"}` para monitoramento.

## Testes
`python -m unittest discover -s tests -v` (18 testes).

## Cuidados
- Faça cópia da pasta de dados/`backups` fora do servidor periodicamente (há também download em *Configurações*).
- Não use `COMANDAFACIL_BEHIND_PROXY=1` sem proxy na frente (permitiria forjar o IP e burlar o bloqueio de tentativas de login).
- SQLite atende bem um restaurante (dezenas de usuários simultâneos); para várias lojas num só servidor seria preciso outro banco.
- **WhatsApp ao lado do sistema:** o WhatsApp Web recusa ser embutido dentro de outra página (nenhum site consegue), então o botão verde "WhatsApp" do topo abre o WhatsApp em uma janela encaixada à direita da tela. Use o botão ao lado para ajustar a largura e "Abrir o sistema em janela própria" para o sistema ocupar a metade esquerda. Permita pop-ups para o endereço do sistema. Para integração *dentro* da tela (caixa de conversas no próprio sistema) seria preciso a API oficial do WhatsApp Business (Meta), ou voltar ao aplicativo de desktop.
