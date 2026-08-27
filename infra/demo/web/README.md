# `infra/demo/web/` — ativos de DEMO servidos como estatico

Paginas autocontidas usadas para demonstrar e testar a plataforma a mao. **Nao sao
produto**: o produto e o `platform-ui`.

| arquivo | o que e |
|---|---|
| `webchat-test.html` | simulador de CLIENTE — abre um contato pelo webchat. Fala direto com o channel-gateway (`ws://localhost:8010/ws/chat`), sem proxy |
| `webrtc-widget.html` | widget de teste do canal WebRTC |

Servidos pelo serviço `demo-assets` do compose, em **http://localhost:5173/** — a
mesma porta e as mesmas URLs de antes, quando estas paginas viviam dentro do
`agent-assist-ui` (aposentado em 2026-08-27; ver `CHANGELOG.md`).

Para acrescentar um ativo: solte o arquivo aqui. Sem build, sem etapa de deploy —
e essa a razao de eles nao morarem dentro de um app.

⚠️ **Nao mover para dentro do `platform-ui`.** Sao harness de demo; junta-los ao
produto funde as duas coisas que a direcao de ambientes componiveis separa
(ver `TODO.md` § "Ambientes componiveis — PISO x PACOTES DE CONTEUDO").
