# Roteiro de validação — contato WebRTC com agente humano no browser (VOZ-04, item 5)

> **O que este roteiro prova** e nenhum probe automático alcança: com **login de verdade**,
> **câmera e microfone de verdade** e o **browser do próprio host**, um cliente no widget e um
> agente no Console conversam por voz, vídeo e texto, e o fim da chamada fecha o contato.
>
> Os pedaços sem browser já têm gate automático: `probe_webrtc_contact_entry.sh` (entrada e fila),
> `probe_webrtc_agent_console.sh` (atribuição, sala, token, texto nos dois sentidos) e
> `probe_webrtc_media_plane.sh` (SFU, TURN e a declaração de alcance do host, ramo A5). Este
> roteiro fecha o que sobra — e o `gate_webrtc_console_live.sh` lê o SFU **durante** a chamada,
> para que "vi o vídeo" não dependa só do olho.

**Duração estimada:** 15 minutos. **Quem executa:** uma pessoa, na máquina onde a stack demo roda.

---

## 0 · Antes de começar

| item | como conferir |
|---|---|
| Stack demo de pé, com as imagens atuais | `http://localhost:5174` abre a tela de login; `http://localhost:5173/webrtc-widget.html` abre o widget |
| Mesmo computador | o SFU anuncia `127.0.0.1` e o TURN `localhost` — **browser em outra máquina da LAN não conecta mídia**, por decisão do demo |
| Chrome ou Edge atualizado | câmera e microfone funcionando no sistema |
| **Fone de ouvido** | as duas pontas estão na mesma máquina; sem fone, o áudio realimenta |
| Probes de base verdes | no terminal do WSL, na raiz do repositório: `bash infra/test/probe_webrtc_media_plane.sh` e `bash infra/test/probe_webrtc_agent_console.sh` |

⚠️ **Com o Console logado num pool `probe_*`, os probes automáticos reprovam** — o agente real pega
os contatos deles. Rode os probes da tabela acima **antes** de logar, ou tire esses pools da seleção.

⚠️ **Versões do LiveKit andam juntas**: o SFU (`docker-compose.demo.yml`), o `livekit-client` do
Console (`packages/platform-ui/package-lock.json`) e o do widget (versão exata no CDN). Com SFU
v1.8.4 e clientes 2.20/2.22 o vídeo não publicava e a chamada reconectava a cada 15 s — texto e
entrada na sala funcionavam, então **só este roteiro pega isso**. Mudou uma das três, repita-o.

⚠️ **Se o mcp-server foi recriado com o Console aberto, recarregue o Console (Ctrl+F5) antes de
começar** (`AGH-02`): o reinício derruba o socket sem desregistrar o agente, e a instância antiga
fica pronta nos pools recebendo contato no lugar do agente real. *(A troca rápida de usuário no
mesmo pool, que causava o mesmo sintoma, foi corrigida na `AGH-01`.)*

---

## 1 · Criar o pool do teste (uma vez só)

1. Em `http://localhost:5174`, entre com `admin@plughub.local` (a senha é a do seed do demo).
2. Vá em **Configuração → Recursos → Pools → Novo Pool**.
3. Preencha:
   - **ID do Pool:** `webrtc_atendimento`
   - **Canais:** marque **`webrtc`** (só ele)
   - **Tipo do pool:** **Humano**
   - **Propósito do pool:** Contato de cliente
   - **Mídias oferecidas no WebRTC:** em *Cliente pode enviar* marque **Áudio** e **Vídeo**; em
     *Atendente pode enviar* marque **Áudio** e **Vídeo**
   - demais campos obrigatórios: os valores que o formulário sugerir
4. Salve. Quando aparecer *"Incluir no seu escopo agora?"*, responda **OK**.

**Esperado:** o pool salva sem erro. Se o formulário recusar por *"Declare as mídias oferecidas no
WebRTC"*, a política não foi marcada — é a VOZ-10 funcionando, volte ao passo 3.

5. **Saia e entre de novo** (o escopo novo só vai para o token num login novo).

---

## 2 · O agente fica disponível

1. Abra **Console** (`http://localhost:5174/console`).
2. Selecione o pool **`webrtc_atendimento`** e fique disponível para receber contato.
3. Quando o browser pedir, **permita câmera e microfone** para `localhost:5174` (pode ser só no
   momento da chamada).

**Esperado:** o pool aparece na lista e a conexão fica ativa. Se o pool não aparecer, o login do
passo 1.5 não foi refeito.

---

## 3 · O cliente liga

1. Numa **outra janela** (não outra aba da mesma janela — fica mais fácil ver as duas), abra
   `http://localhost:5173/webrtc-widget.html`. O campo de pool já vem com `webrtc_atendimento`.
2. Clique **Conectar** e **permita câmera e microfone** para `localhost:5173`.

---

## 4 · Observar e anotar

Marque cada linha como ✅ / ❌. Em ❌, anote o que apareceu.

| # | onde | esperado |
|---|---|---|
| V1 | widget | *"Conectado. Aguardando agente…"* e, logo depois, *"Agente conectado (Vídeo)"*. Um aviso amarelo de sistema *"Aguardando agente disponível…"* pode aparecer — ele **não** deve vir rotulado "Agente" |
| V2 | Console | o contato novo aparece e abre a sobreposição de chamada (primeiro *conectando*, depois a mídia) — **não** um contato de webchat comum |
| V3 | os dois | o vídeo do cliente aparece no Console e o do agente no widget |
| V4 | os dois | com o fone: fale de um lado e ouça do outro, nos **dois** sentidos. Se aparecer o aviso amarelo *"O navegador bloqueou o áudio…"* (no Console ou no widget), clique nele — anote que apareceu |

**Com a chamada ainda aberta**, no terminal do WSL:

```bash
bash infra/test/gate_webrtc_console_live.sh webrtc_atendimento
```

| # | esperado |
|---|---|
| V5 | **VERDE**: L1 atribuído a humano · L2 `agent-…` e `customer-…` na sala · L3 os **dois** publicando `['audio', 'video']` · L4 identidade na sala = instância atribuída · L5 o áudio dos dois tem sinal (fale durante os ~10 s do gate). L5 verde e V4 sem som = o defeito é de REPRODUÇÃO no browser |

Se sair INCONCLUSIVO dizendo que há mais de um contato aberto, a própria saída lista os ids:
escolha o que começa com os 8 caracteres do cabeçalho do widget e rode
`bash infra/test/gate_webrtc_console_live.sh webrtc_atendimento <session_id>`.

Continue:

| # | onde | esperado |
|---|---|---|
| V6 | Console → widget | escreva uma mensagem no Console: ela chega ao widget rotulada **Agente** |
| V7 | widget → Console | escreva no widget: ela chega ao Console como fala do cliente |
| V8 | *(opcional)* ICE | no Chrome, `chrome://webrtc-internals` → a conexão do widget → par de candidatos selecionado com `127.0.0.1:7882` (UDP) |
| V9 | widget → Console | clique no botão vermelho **📵** (*Encerrar chamada*): o widget mostra *"Chamada encerrada."* e, em poucos segundos, o contato é encerrado no Console e a sobreposição de chamada fecha |

---

## 5 · Devolver o resultado

Mande de volta, em texto:

1. a tabela V1–V9 marcada;
2. a saída inteira do `gate_webrtc_console_live.sh` (V5);
3. em qualquer ❌: o horário aproximado e, se possível, um print da tela e o que o console do
   browser (F12 → Console) mostrou naquele momento.

Com isso eu confronto com os logs de gateway, bridge, mcp-server e SFU do mesmo horário.

---

## 6 · Depois

- O pool `webrtc_atendimento` pode ficar: é dado do tenant de demo, e reutilizá-lo numa rodada
  futura pula o passo 1.
- Recriou o mcp-server com o Console aberto? Recarregue o Console (ver o aviso do passo 0).

## O que este roteiro NÃO cobre

- **Browser em outra máquina** da rede: exige o IP do host nos candidatos do SFU e no TURN.
- **`VOZ-15`** (o token de mídia do agente não confere quem atende): defeito registrado, fora do
  escopo desta validação. *(O `/agent/ws` sem credencial — `CAP-19` — foi fechado; o gate é o
  `probe_agent_ws_credential.sh`.)*
- Gravação (`VOZ-06`), STT/TTS e menu por voz (`VOZ-05`).
