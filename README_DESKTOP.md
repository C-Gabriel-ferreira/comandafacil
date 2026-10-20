# ComandaFácil Desktop (PDV em programa)

Versão em programa do ComandaFácil, feita em **Python + PySide6 (Qt)**. Usa o mesmo banco (`comandas.db`)
e as mesmas regras de negócio testadas do sistema web (`comandafacil/services.py`).

## Como abrir
- **Instalador (site):** o botão “Baixar instalador” entrega o `ComandaFacil-Setup.exe` gerado pelo GitHub Actions (aba *Actions* → artefato `telas-do-pdv` traz prints das telas de cada versão).
- **Código-fonte:** `pip install -r requirements-desktop.txt` e `python comandafacil_desktop.py`. Autoteste da interface sem janela: `python comandafacil_desktop.py --selftest pasta`.
- O programa também liga, em segundo plano, o servidor web para os **celulares dos garçons** (endereço no menu lateral), no mesmo banco.
- Primeiro acesso: o programa pede para criar o **gerente**. Garçons são criados em Usuários no sistema web (a tela de usuários do desktop ainda não existe).
- O banco é atualizado para a v3 (tabelas do caixa) com **backup automático antes** (`backups/…pre_migracao.db`).

## Referências usadas (Saipos, Consumer, Anota AI)
| Ideia da referência | Como ficou |
|---|---|
| **Saipos**: salão, balcão e delivery numa tela única; fechamento com pagamentos variados; controle diário de caixa | Tela PDV com 3 colunas: atendimentos · cardápio · comanda. Pagamento dividido com troco. Aba **Caixa** (abertura, sangria, suprimento, fechamento com conferência) |
| **Consumer**: caixa/balcão no desktop, comanda por mesa, monitor da cozinha com status | Mesas como cartões com valor, tempo, “pedido pronto” e “pediu a conta”; observação por item; aba **Cozinha**; venda só com caixa aberto |
| **Anota AI**: pedidos de delivery com dados do cliente e mensagem ao cliente | Delivery com cliente/endereço/taxa e botão **WhatsApp** (abre a conversa com a confirmação pronta; envio manual) |

## Atalhos
`F2` buscar produto · `Enter` na busca adiciona se houver só um resultado · `F10` fechar conta.

## Perfis
- **Gerente**: tudo (caixa, produtos, relatórios, estorno, cancelar comanda com itens).
- **Garçom**: PDV e consulta do caixa; só cancela comanda vazia. Não vende com caixa fechado.

## Ainda não existe (próximos passos)
QR Code da mesa para o cliente pedir · recebimento automático de pedidos online (iFood/Anota AI) com impressão
automática · divisão de conta por item · NFC-e/SAT · tela de usuários e configurações no desktop · impressão por setor na cozinha.

## Testes
`python -m unittest discover -s tests -v` — inclui `tests/test_desktop_logic.py` (caixa, cupom, permissões, login).

## Atualizações
O programa instalado avisa sozinho quando sai versão nova (botão na barra lateral, embaixo). O gerente clica em
**Atualizar agora**: o instalador é baixado, conferido (SHA-256) e instalado por cima, mantendo dados e backups.
O botão **Novidades** mostra o histórico de cada versão. Para lançar uma versão, veja `RELEASE.md`.
