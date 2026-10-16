# Guia das melhorias — o que mudou, como funciona e por quê

Legenda: 📁 onde está no código · 🖱 como usar · ⚠ limites e cuidados

---
## PARTE 1 — Problemas corrigidos

### 1. Chave secreta fixa
**Por que importa:** a `secret_key` assina o cookie de sessão. Se for pública (estava no código), qualquer pessoa que alcance o servidor pode forjar uma sessão.
**O que foi feito:** na 1ª execução é gerada uma chave aleatória de 256 bits e gravada em `secret.key` (permissão restrita quando o sistema operacional permite). Também aceita a variável `COMANDAFACIL_SECRET_KEY`.
📁 `comandafacil/security.py → load_secret_key()`
⚠ Apagar `secret.key` só desconecta os usuários. Não envie esse arquivo a ninguém.

### 2. Arquivos faltantes (templates)
Como não vieram no upload, **recriei** `templates/` e `static/`. O visual é novo (não é o seu antigo). Se você tinha customizações nos templates, me envie e eu as trago para o novo.

### 3. Comandas duplicadas por clique duplo (3 camadas de proteção)
1. **Banco:** índice único parcial `idx_one_open_per_table` — o próprio SQLite impede duas comandas abertas na mesma mesa. Se a 2ª tentativa colidir, o sistema devolve a comanda já existente.
2. **Formulário:** cada formulário leva um `_rid` único; reenviar o mesmo `_rid` (duplo clique, F5 após POST) é ignorado e avisado.
3. **Navegador:** `app.js` desativa o botão logo após o envio.
4. **Fechamento:** `UPDATE … WHERE status='aberta'` + checagem de `rowcount` dentro de uma transação `BEGIN IMMEDIATE`: só um fechamento vence, mesmo com duas requisições simultâneas.
Além disso, a rota de adicionar item **não abre mais comanda sozinha**: a comanda é aberta explicitamente.
📁 `db.py (SCHEMA, transaction)`, `security.py (idempotent)`, `services.py (open_order, close_order)`, `static/app.js`

### 4. Dinheiro em centavos
**Problema:** `0.1 + 0.2 = 0.30000000000000004` em ponto flutuante; somas grandes acumulam erro.
**Solução:** todo valor é inteiro em centavos (`price_cents`, `unit_price_cents`, `total_cents`…). Entrada do usuário passa por `parse_money()` (usa `Decimal`, aceita `12,50`, `1.234,56`, `R$ 3`; rejeita negativo, `nan`, `inf`, valores absurdos). A porcentagem de serviço usa aritmética inteira com arredondamento “meio para cima”.
**Migração do seu banco antigo:** o sistema detecta o formato antigo, **constrói um banco novo** em arquivo temporário, converte (`ROUND(preço*100)`), confere as contagens de produtos/mesas/comandas/itens e só então troca os arquivos, guardando o original como `comandas_antes_da_v2_*.db`. Se algo falhar, nada é alterado. Comandas fechadas antigas ganham um pagamento com a forma que tinham. Se existirem duas comandas abertas na mesma mesa (falha da versão antiga), a mais antiga é marcada como cancelada “duplicada”.
📁 `money.py`, `db.py → _upgrade_legacy_database()` · testado em `tests/test_core.py`.

### 5. Validação da forma de pagamento
Só são aceitas: Dinheiro, Pix, Cartão de débito, Cartão de crédito, Vale-refeição (lista em `services.PAYMENT_METHODS`; para incluir outra, edite a lista). Qualquer outro texto é recusado no servidor.

### 6. Backup automático
- Ao iniciar e a cada 30 min o sistema verifica se já existe o backup do dia; se não, cria `backups/comandas_AAAA-MM-DD_HHMMSS_auto.db`. Mantém os **14 últimos automáticos**.
- Usa a API de backup do SQLite (cópia consistente mesmo com o sistema em uso — copiar o arquivo “na mão” durante o uso pode corromper).
- Antes de qualquer migração futura é criado um backup `pre_migracao`.
- 🖱 *Configurações → Backups*: “Fazer backup agora” e “Baixar”. Restaurar: feche o sistema e copie o backup sobre `comandas.db`.
⚠ Backup na mesma máquina não protege contra roubo/queima do computador: copie a pasta `backups` periodicamente para um pendrive ou nuvem.

### 7. Exclusão com confirmação no servidor
Excluir produto/mesa agora abre uma **página de confirmação** (GET) e só exclui no POST seguinte, com CSRF. Produtos já vendidos continuam no histórico (o nome e o preço ficam gravados no item). Mesas com histórico não podem ser excluídas — apenas **desativadas**.

### Extra: segurança geral
- **CSRF** em todo POST (token por sessão, comparação em tempo constante).
- **Login com senha** (hash do Werkzeug; mínimo 8 caracteres), bloqueio de 5 min após 5 falhas, sessão de 12 h, cookies `HttpOnly` e `SameSite=Lax`.
- Cabeçalhos `X-Frame-Options`, `nosniff`, `no-store`.
- Download de backup com validação rígida do nome (sem *path traversal*).

---
## PARTE 2 — Funcionalidades novas

### Relatórios 🖱 *Relatórios* (gerente)
Períodos: hoje, 7 dias, 30 dias, mês, ou datas livres. Mostra faturamento, nº de comandas, **ticket médio**, serviço, entregas, cancelamentos/estornos, e gráficos de barras por **forma de pagamento**, **tipo** (mesa/balcão/delivery), **produtos mais vendidos**, **horário** (descobrir o pico) e **dia**. Botão **Exportar CSV** (separador `;`, abre direto no Excel em português).
Regra: só entram comandas **fechadas**; estornadas saem do faturamento e aparecem na contagem de cancelamentos.
📁 `services.sales_report()`, `views/reports.py`

### Impressão de comanda/cupom 🖱 botão “🖨 Cupom”
Página formatada para **bobina de 80 mm** (`@page size:80mm`) com itens, observações, serviço, entrega, pagamentos e rodapé configurável. Em comanda aberta imprime como **conferência** (“não é documento fiscal”). *Imprimir* abre o diálogo do Windows: escolha a impressora térmica ou “Salvar como PDF”.
⚠ Não é NFC-e/SAT. Documento fiscal exige integração com emissor certificado — é um projeto à parte. A impressora térmica precisa ter driver instalado no Windows. Para bobina de 58 mm ajuste a largura em `templates/cupom.html`.

### Mesas editáveis 🖱 *Gerenciar mesas*
Adicionar uma, ou N de uma vez (numeração automática “Mesa 13…”), renomear (“Varanda 1”), desativar/reativar e excluir. Não deixa desativar mesa ocupada nem excluir mesa com histórico.

### Observações no item e taxa de serviço
Cada linha do cardápio tem campo de observação (“sem cebola”, “bem passado”). Itens iguais com a mesma observação se somam; observações diferentes ficam em linhas separadas. A **taxa de serviço** (padrão 10%, configurável 0–30%) liga/desliga por comanda; opcionalmente já nasce ligada nas mesas. O valor é gravado no fechamento — mudar a taxa depois não altera vendas antigas.

### Pagamento dividido e divisão da conta
- **Fechar conta** aceita várias linhas (ex.: R$ 30 no Pix + R$ 24 no dinheiro). “Completar valor restante” preenche a última linha; o indicador mostra *faltam* / *troco* / *exato* em tempo real.
- **Troco** só em dinheiro; o sistema grava o valor líquido recebido, então os relatórios por forma de pagamento batem com o caixa.
- **Dividir conta**: divide por N pessoas **sem perder centavos** (R$ 10,00 ÷ 3 = 3,34 + 3,33 + 3,33).
⚠ A divisão é igual; divisão por item consumido não está implementada.

### Transferir e juntar mesas 🖱 dentro da comanda
*Transferir* move a comanda para uma mesa livre. *Juntar* traz os itens de outra mesa para a atual; a outra comanda vira “juntada” (fica rastreável) e a mesa é liberada.

### Delivery e balcão 🖱 *+ Balcão / Delivery* na tela de mesas
Comandas sem mesa, com nome, telefone, endereço e taxa de entrega (soma no total, sem serviço). Aparecem numa seção própria enquanto abertas e entram nos relatórios por tipo.

### WhatsApp ao lado do sistema 🖱 botão verde “WhatsApp” (topo) e botão “Mensagem” na comanda
**Limite técnico:** o WhatsApp Web envia cabeçalhos que proíbem ser exibido dentro de `<iframe>` de outro site. Isso vale para qualquer navegador e não tem contorno legítimo.
**O que foi feito (modo acoplado):**
- “WhatsApp” abre o WhatsApp Web numa janela posicionada no lado direito da tela (largura ajustável: o sistema ocupa de 40% a 75% da tela, salva no navegador).
- “Abrir o sistema em janela própria” abre o sistema na metade esquerda. (Navegadores só deixam um site redimensionar janelas que ele mesmo abriu.)
- Na comanda, “Mensagem” monta a confirmação do pedido (itens, observações, totais, endereço); você edita e clica em “Abrir conversa”: a conversa abre **na mesma janela do WhatsApp** já acoplada, com a mensagem preenchida. Telefone é normalizado (adiciona 55). Também há “Copiar mensagem”.
- Sem pop-ups liberados, o sistema avisa em vez de falhar em silêncio.
⚠ O sistema **não envia sozinho** nem lê conversas (envio automático arrisca bloqueio da conta). Se mudar a largura, feche a janela do WhatsApp e acople de novo.
**Alternativas reais de integração total:** (a) API oficial do WhatsApp Business/Cloud API da Meta — caixa de conversas dentro do sistema; exige conta verificada, número dedicado, endereço público HTTPS e tem custo por conversa; (b) aplicativo de desktop (Electron) com WhatsApp em painel interno, que já fizemos antes.

### Estoque 🖱 *Produtos → Controlar estoque*
Opcional por produto: quantidade atual e **mínimo**. Ao lançar item o estoque baixa; remover/diminuir devolve; **vender além do estoque é bloqueado**. Alerta de estoque baixo na tela inicial. Cada movimentação (venda, devolução, entrada, baixa manual, cancelamento) fica registrada em `stock_movements`. 🖱 “Ajustar” aceita `+20` (entrada) ou `-2` (perda) com motivo.
⚠ É controle por unidade do produto final; **não** faz ficha técnica (ingredientes).

### Usuários e perfis 🖱 *Usuários* (gerente)
| | Garçom | Gerente |
|---|---|---|
| Mesas, comandas, itens, fechar, imprimir, WhatsApp, transferir/juntar | ✔ | ✔ |
| Cancelar comanda vazia | ✔ | ✔ |
| Cancelar com itens / estornar venda fechada | ✖ | ✔ |
| Produtos, estoque, mesas, relatórios, config, usuários, auditoria | ✖ | ✔ |
| Ver faturamento do dia | ✖ | ✔ |
O sistema não permite remover o último gerente ativo. Cada um altera a própria senha em *Senha*.

### Cancelamento / estorno 🖱 botão na comanda
Exige **motivo** e (para venda fechada) perfil gerente. A comanda vira “cancelada”, **não é apagada** (histórico preservado), sai dos relatórios de faturamento e opcionalmente devolve os itens ao estoque. Tudo vai para a **Auditoria** (quem, quando, motivo).

### Auditoria 🖱 *Auditoria*
Registra logins (e tentativas falhas), aberturas, fechamentos, cancelamentos, alterações de produto/estoque/usuário/configuração e backups, com busca.

---
### Visual moderno e tema claro/escuro
- **Tema:** botão sol/lua no topo (e na tela de login). A escolha fica salva; na primeira visita segue o tema do sistema operacional. Sem “piscada” ao carregar.
- **Layout:** menu lateral com ícones (recolhe para só ícones em janelas estreitas e vira menu deslizante no celular), cartões de mesa com status e **tempo de ocupação** ao vivo, indicadores com ícones, avisos em *toasts* que somem sozinhos, diálogos animados, barras de relatório animadas.
- **Celular/tablet:** testado em 390 px; mesas em 2 colunas, cardápio e tabelas adaptados.
- **Acessibilidade:** foco visível, `aria-label` nos botões de ícone, respeita “reduzir movimento”.
- Tudo é CSS/JS próprio, sem bibliotecas nem fontes externas (funciona offline na rede local).
📁 `static/style.css` (variáveis de cor no topo: troque `--brand` para mudar a cor da marca), `templates/base.html`

### Fuso horário
Servidores costumam usar UTC, o que faria horários e “vendas de hoje” saírem errados. Todo o sistema usa `COMANDAFACIL_TZ` (padrão `America/Sao_Paulo`). 📁 `comandafacil/clock.py`

## PARTE 3 — Melhorias técnicas

### Código modular
```
app.py                  ponto de entrada web
comandafacil/
  __init__.py           fábrica da aplicação
  db.py                 esquema, migrações, backups
  money.py              centavos / formatação
  security.py           chave, CSRF, login, permissões, auditoria
  services.py           REGRAS DE NEGÓCIO (sem Flask — fáceis de testar)
  views/                auth, main (mesas/comandas), catalog, reports, admin
templates/  static/  tests/  Dockerfile  docker-compose.yml  Caddyfile
```
Regra de ouro: telas só leem formulário e mostram resultado; **regras vivem em `services.py`**.

### Migrações de banco
`PRAGMA user_version` guarda a versão. Para uma mudança futura: aumente `SCHEMA_VERSION` e adicione o SQL em `MIGRATIONS = {2: "ALTER TABLE …"}` — é aplicado automaticamente, em ordem, com backup antes. O caso especial v0 (banco antigo) está em `_upgrade_legacy_database`.

### Execução e publicação web
- **Servidor de produção:** `waitress` (Windows/Linux), com 8 threads; cai para o servidor do Flask se não estiver instalado.
- **Rede local:** `INICIAR_COMANDAFACIL.bat` libera acesso por celulares/tablets do mesmo Wi-Fi.
- **Online:** `Dockerfile` + `docker-compose.yml` + `Caddyfile` (HTTPS automático). Dados em volume persistente.
- **Endurecimento para internet:** cookies `Secure` e HSTS (`COMANDAFACIL_HTTPS=1`), `ProxyFix` para IP real atrás de proxy (`COMANDAFACIL_BEHIND_PROXY=1`), senha mínima de 8 caracteres, bloqueio de tentativas por IP, `Referrer-Policy`, `/healthz` para monitoramento.
⚠ Não testei o Docker aqui (sem Docker no ambiente); o código Python foi testado com 18 testes.

### Testes automatizados
18 testes em `tests/test_core.py` (rodam em segundos): valores monetários, totais com serviço, pagamento dividido com troco, fechamento duplicado, estoque, cancelamento nos relatórios, transferência/junção, delivery, backup válido, **migração do banco antigo**, e fluxo HTTP completo com CSRF, duplo clique, permissões do garçom e bloqueio de login.

---
## O que ainda NÃO existe (próximos passos sugeridos)
1. NFC-e/SAT (fiscal).  2. Divisão de conta por item.  3. Ficha técnica / custo do produto e lucro.  4. Impressão automática na cozinha (comanda por setor).  5. Painel de cozinha em tempo real (atualização automática das telas).  6. Controle de caixa (abertura, sangria, fechamento).  7. Restaurar backup pela tela.
