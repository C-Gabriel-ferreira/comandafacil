# Novidades do ComandaFácil

Cada versão nova aparece dentro do programa (botão "Novidades") e pode ser instalada por cima, sem perder dados.

## 2.9.1 — 10/11/2026
- **Acesso pelo navegador mais confiável:** se a porta 5000 já estiver em uso (por exemplo, outra cópia do programa aberta), o ComandaFácil agora usa a próxima porta livre em vez de desligar o servidor em silêncio. A barra lateral mostra o endereço certo, tanto neste computador quanto para os celulares.
- Se o servidor web não conseguir iniciar, o PDV avisa o motivo e grava os detalhes em `servidor.log`, na pasta de dados.

## 2.9.0 — 10/11/2026
- **Login novo na janela do PDV:** entra com usuário **ou e-mail**, com opção de mostrar a senha e o botão "Esqueci minha senha".
- **Redefinir senha com código do suporte:** funciona sem internet; o código vale uma vez só.
- **Primeiro acesso com licença:** o campo de licença define o e-mail do responsável e libera o número de usuários contratado. Sem licença, o sistema funciona em avaliação (3 usuários).

## 2.8.0 — 10/11/2026
- **Nova tela de login:** visual renovado, mostrar/ocultar senha, aviso de Caps Lock e entrada por usuário **ou e-mail**.
- **Entrar com o Google (só o gerente):** opcional, ativado quando o Google está configurado no servidor (`COMANDAFACIL_GOOGLE_*`). Garçons e demais usuários continuam com usuário e senha.
- **Recuperação de senha:** link por e-mail (quando o e-mail do servidor está configurado) ou **código de uso único emitido pelo suporte**, que funciona sem internet em `/login/codigo`.
- **Licença com limite de usuários:** cada cliente recebe uma licença com o número de usuários ativos e o e-mail do responsável. O primeiro acesso aceita a licença; sem ela o sistema funciona em modo de avaliação (3 usuários). Licença vencida não bloqueia quem já usa, só impede criar ou reativar usuários.
- Cada usuário agora pode ter e-mail cadastrado (em *Usuários*).

## 2.7.0 — 20/10/2026
- **Sugestão de venda adicional no PDV:** com a comanda aberta, aparecem até 3 botões "+ produto" com o que mais sai junto com os itens já pedidos (calculado pelas suas vendas). Um toque adiciona 1 unidade. Sem histórico, sugere os mais vendidos; itens indisponíveis ou sem estoque nunca são sugeridos.

## 2.6.0 — 20/10/2026
- **Custo e margem de lucro:** no cadastro do produto há o campo "Custo (R$)" (opcional) e a margem aparece na hora. A lista de produtos mostra a margem de cada um e os Relatórios ganharam "Custo (CMV)" e "Margem" do período. O custo é gravado em cada venda, então mudar o custo depois não altera relatórios antigos, e produtos sem custo cadastrado ficam fora da conta.

## 2.5.0 — 20/10/2026
- **Impressão automática dos pedidos online:** em Pedidos online → Configurar, marque "Imprimir a ficha automaticamente ao aceitar" e escolha a impressora. Ao aceitar um pedido, a ficha sai na hora, sem janelas. Se a impressora falhar, o programa avisa e abre a ficha para imprimir manualmente.

## 2.4.0 — 20/10/2026
- **Visual novo, mais limpo:** tema claro como padrão (o escuro continua no botão Tema), cartões arredondados, laranja da marca, textos e botões maiores para toque.
- **Tela inicial:** vendido hoje (com comparação com ontem), comandas fechadas e ticket médio, comandas em aberto, vendas por hora, mais vendidos e uma lista do que precisa de atenção (caixa fechado, pedidos online novos, estoque baixo, cozinha atrasada).
- **Cozinha com cronômetro:** cada pedido mostra há quantos minutos está esperando, com faixa verde, amarela ou vermelha (limites de 10 e 20 minutos).
- **Mesas com cores por situação:** livre, ocupada, pediu a conta e pedido pronto. Produtos com faixa colorida por categoria.
- **Pagamento redesenhado:** formas de pagamento com ícone, valor que falta ou troco em destaque e botão de confirmar maior.

## 2.3.0 — 19/10/2026
- **Pedidos online:** cardápio digital para o cliente pedir pelo celular, com aba nova no PDV para aceitar ou recusar, aviso sonoro, ficha para imprimir e aviso ao cliente pelo WhatsApp.
- **Atualização dentro do programa:** o PDV avisa quando há versão nova, mostra as novidades e atualiza com um clique (seus dados e backups são mantidos).

## 2.2.0
- PDV em janela com caixa unificado, cozinha, produtos e relatórios.
- Servidor para os celulares dos garçons no mesmo programa.
- Controle de caixa: abertura, sangria, suprimento e fechamento com conferência.
