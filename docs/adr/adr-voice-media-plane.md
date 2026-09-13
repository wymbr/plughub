# ADR — Arco de voz própria: plano de mídia da plataforma

- **Status:** proposto
- **Data:** 2026-08-19
- **Escopo:** o **plano de mídia** da PlugHub — terminação SIP, SFU, STT/TTS, perna do agente e
  gravação. Vale para qualquer origem de chamada (operadora direta, tronco de PABX, browser) e
  **não depende de PABX nenhum**.
- **Relação com o CTI:** é o **modo SIP** de
  [`adr-cti-gateway-multi-driver.md`](adr-cti-gateway-multi-driver.md) §0, extraído para arco
  próprio. Os dois modos são ofertas paralelas, não fases um do outro.
- **Emendado em 2026-09-12**, por decisão do dono, depois de uma discussão sobre ambiente de teste
  e sobre integração com IP Office: **V1** passa a comprar **uma** camada em vez de três (só a
  sala é nossa); **as fases foram reordenadas** — WebRTC primeiro, perna SIP como última milha com
  gatilho comercial; **§8** foi reagrupado, e quase nada bloqueia o V-F0. O ADR de CTI ganhou, no
  mesmo dia, a distinção entre **presença** e **roteamento unificado**.

---

## 1. Problema

A plataforma **afirma** ter voz e **não tem**. Três dívidas separadas descrevem o mesmo buraco, e
individualmente nenhuma se justifica:

- o canal `voice` (Twilio) está documentado como implementado e **não roda** (§2 A1/A2);
- o Arc 15 (WebRTC/LiveKit) está marcado ✅ e é **placebo de ponta a ponta** (A3);
- o discador tem desenho fechado (`dialer-compliance-invariants.md`, `outbound.md` §pacing) e
  **depende de um plano de mídia que não existe**.

Juntas, são um arco com dono: **a plataforma passa a ser capaz de terminar, tratar, distribuir e
gravar voz**, com a central legada reduzida a PABX interno (quando houver central).

O achado que motivou extrair este arco do ADR de CTI: no modo SIP, a parte Avaya de uma
integração é *um tronco e um `REFER`*. Todo o resto vale igual contra Cisco, Mitel, 3CX ou contra
nenhum PABX. Chamar isso de "integração com IP Office" escondia o custo (parece driver, é pilha
de telecom) e o valor (parece de um cliente, é capacidade de produto).

---

## 2. Achados que determinam o desenho

| # | Achado | Evidência |
|---|---|---|
| A1 | **O canal `voice` não roda.** `handle_inbound` chama `_open_session`, `_route_inbound`, `_publish_inbound`, `_normalize_text`, `_normalize_menu_result` — os cinco **não existem**, e estão **mockados** nos testes. Em runtime real levanta `AttributeError` antes de publicar qualquer coisa | `adapters/voice.py:236,247,433,558,565`; `adapters/base.py:44-77`; `tests/test_voice_adapter.py:116-121` |
| A2 | Correlatos no mesmo adapter: `channel_name` em vez de `channel`; `_collect_loop` prometido e inexistente, `stt_queue` nunca drenada (**collect por voz morto**, só DTMF); `hangup` lê chave nunca escrita; `_get_contact_id` retorna `None` por construção; `deliver_outbound` nunca invocado | `voice.py:90, 11-13, 624-629, 657, 884, 1032-1037, 772`; `outbound_consumer.py:95-106` |
| A3 | **O SFU nunca foi provisionado.** Zero serviço LiveKit em compose algum, zero env `LIVEKIT_*`, SDK não é dependência; sem credencial o provider liga `_dev_mode` e devolve token, room, participantes e egress **placebo**. O `arc15-webrtc.md` prescreve topologia Kubernetes e **não há manifesto correspondente** em `infra/` | `arc15-webrtc.md:3-17, 95-103`; `webrtc_provider.py:167, 176-178, 213-219, 259-260, 284-285, 331-337`; `channel-gateway/pyproject.toml:6-23` |
| A4 | **`add_participant(conference_sid, to, from_)`** existe com docstring *"Add a leg (human agent SIP/WebRTC) to the conference"* e **nunca é chamada**. Não há caminho de código que ponha um humano numa conferência de voz | `voice_provider.py:112, 119, 303, 808` |
| A5 | **Conflito doc×doc sobre retenção de gravação:** 5 anos num lugar, 30 dias (LGPD) noutro. Nenhum código arbitra | `channel-gateway-multi-channel.md:1371-1550` × `docs/layers/07-data-layer.md:101` |
| A6 | `voice` declara capability **só `audio`**, sem `text` — e o caminho de collect está morto (A2). A declaração não bate com a realidade em nenhuma das duas direções | `channel_capability_registry.py:32`; enum canônico em `schemas/src/skill.ts:567-575` |
| A7 | O `AttachmentStore` já é a abstração de artefato: Protocol estável (`reserve/commit/resolve/stream_bytes/soft_expire`), filesystem date-sharded, ciclo de dois estágios (soft-expire horário → 410; delete físico diário +24h de graça). O caminho de voz e o egress do Arc 15 **já apontam para ele** | `adr-webchat-channel.md:78-114`; `voice.py:410-416`; `arc15-webrtc.md:365-393` |
| A8 | A allowlist de borda cobre **prefixos HTTP** (`/voice/*`, `/webrtc/*`, `/ws/*` já externos) e é verificada por `probe_edge_surface.sh`. **Sinalização SIP não é HTTP** e está fora dessa tabela — não há classificação nem probe para ela | `webhook-patterns.md:25-83`; `infra/test/probe_edge_surface.sh` |
| A9 | O bridge **não preserva ordem entre eventos Kafka**, nem entre tópicos nem no mesmo: consumer único com `create_task` sem `await` | `conference-mechanics.md:599-620` |
| A10 | `voice` **já está** no `Literal` fechado de `ConversationInboundEvent.channel` | `routing-engine/models.py:47` |
| A11 | Premissa de arquitetura já escrita e ainda válida: **agentes IA são sempre texto**; supervisores e especialistas IA operam em texto mesmo em voz; o único streaming áudio fim-a-fim é humano↔cliente | `channel-gateway-multi-channel.md:42-56` |

---

## 3. Decisões

### V1 — Uma camada é nossa: a SALA. Interconexão e borda são contratáveis

> ⚠️ **Reescrito em 2026-09-12, por decisão do dono.** A versão original dizia que *"o SFU e a
> borda SIP são parte da unidade de deploy da PlugHub"* e que, em nuvem, *"a borda SIP e o SBC são
> responsabilidade da plataforma, não do cliente"*. Isso comprava **três** camadas quando só uma
> precisa ser nossa — e era a decisão mais cara do arco, a que transformava a PlugHub em operação
> de telecom.

Voz tem três camadas, e elas se contratam separadamente:

| Camada | Contratável? | Quem faz |
|---|---|---|
| Interconexão com a PSTN (numeração, portabilidade, fraude, regulatório) | **sim** | ITSP, ou o PABX do cliente |
| Borda / SBC (SIP público, TLS/SRTP, anti-DDoS) | **sim** | SBC gerenciado, appliance, ou o que o cliente já tem |
| **Plano de mídia — a SALA** | **não** | a plataforma |

A terceira não é contratável porque é onde vivem gravação, STT/TTS, supervisão e avaliação. Mídia
de terceiro custa exatamente aquilo que justifica a voz estar aqui.

Consequências:

- **continua valendo: nada de SFU como serviço de terceiro (SaaS).** O componente tem de ser
  auto-hospedável nas duas topologias — o que mantém LiveKit self-hosted como candidato e descarta
  qualquer SFU que só exista como nuvem alheia.
- **a borda pública deixa de ser obrigação da plataforma.** No cenário preferido de implantação o
  cliente **já tem SBC**, e o desvio seletivo do tráfego acontece nele — sem tronco interno do
  PABX e, portanto, **sem licença de canal** e sem o dobro de canais na central (o corolário do
  ADR de CTI §0 vale só para a topologia por tronco interno).
- **no deploy on-premise não há WAN no caminho da voz**, porque plataforma e central estão na
  mesma rede. Continua sendo a configuração de melhor qualidade e menor superfície.
- **o dimensionamento de mídia entra no dimensionamento da plataforma** (CPU/banda por chamada
  concorrente) — agora só o da sala e o do conversor, não o da borda.
- **o que sobra de telecom para nós é o enlace com o SBC**: codec, transporte de DTMF e TLS/SRTP.
  Deixa de ser decisão de arquitetura e vira **checklist de projeto por cliente** (§8).

### V2 — Reconstruir o canal `voice`; **não** criar canal novo

`voice` já está no `Literal` fechado (A10) e já significa a coisa certa: *o cliente chega por
telefonia*. O que muda é a implementação, não o conceito. `webrtc` continua sendo canal separado
e correto: ali o **cliente** chega pelo browser.

A perna do **agente** não é canal — é sempre WebRTC contra a sala, nos dois casos. Isso já é o
desenho do Arc 15 e não precisa de conceito novo.

**Twilio sobrevive, com papel menor e mais honesto:** deixa de ser *media gateway + conference* e
passa a ser **um `IVoiceProvider` entre outros — um tronco CPaaS**, para o cliente que não tem
central nem tronco SIP de operadora. É exatamente o papel para o qual a interface foi desenhada;
o que estava errado era a implementação carregar o plano de mídia junto.

### V3 — **Um único plano de mídia: a SALA.** Entrada por SIP ou por navegador; internamente, sempre a sala

Uma sala por sessão (`plughub-{session_id}`), invariante herdado do Arc 15. A perna SIP entra na
sala como participante; o agente entra na sala como participante; o supervisor entra `hidden`.
**Não se cria um segundo modelo de conferência** — o modelo de três camadas
(contato × segmento × infraestrutura da sala) vale sem emenda.

**Emenda 2026-08-20 — a decisão passa a ter nome, e o motivo de nomeá-la importa.** Esta é a
convergência que o repositório vinha carregando há meses **sem nunca ter sido estudada nem
decidida**, registrada quatro vezes e sempre como item em aberto:
`arc15-webrtc.md:609` (*"Bridge PSTN → WebRTC — **Deferido**, requer LiveKit SIP Ingress, precisa
avaliação"*), `CLAUDE.md:1453` (*"Decisão, não implementação pendente"*),
`plughub-descritivo-tecnico-funcional.md:1259` §25.8 (*"unificando os canais de áudio"*, uma linha,
numa lista de futuros) e `value-proposition.md:132` (*"avalia ainda fazer a ponte"*). Nenhum com
prós, contras, custo ou autor. **Uma ideia repetida em quatro documentos passa a soar como conclusão
fechada** — é a família do *valor plausível*, aplicada a decisão em vez de a estado. Aqui ela é
tomada.

**Precisão de vocabulário, e ela muda o custo percebido.** "Internamente só WebRTC" é a formulação
corrente e é **imprecisa**: dentro do SFU não trafega WebRTC, e sim RTP no modelo interno dele;
WebRTC é o transporte **voltado ao navegador**. O que se unifica é a **sala**, não o protocolo. A
formulação errada faz parecer que o lado SIP sai de graça — **a ponte SIP↔sala é componente real**,
com interworking de sinalização, negociação de codec, tradução de DTMF (`telephone-event` ↔ o que o
fluxo consome) e domínio de relógio próprio.

**O que a unificação compra:** um plano de mídia para operar, escalar e proteger, em vez de dois;
supervisão, gravação, sussurro e multi-participante implementados **uma vez** (hoje o desenho tem
supervisão por *coaching mode* do CPaaS na voz **e** por assinante oculto no WebRTC — dois
mecanismos para o mesmo recurso); STT/TTS conectando na sala uma vez, em vez de dois transportes
alimentando os mesmos provedores; e a perna do agente idêntica venha o cliente de onde vier, que é
o que permite IA, especialista e supervisor entrarem do mesmo jeito em qualquer chamada.

**O que ela custa — três itens, nenhum opinável:**

1. **Transcodificação.** PSTN é G.711, navegador prefere Opus. Ou o SFU transcodifica (CPU por
   chamada, relevante em escala) ou se força G.711 na perna do navegador (sem transcodificar, ao
   preço de qualidade e banda). A unificação torna essa decisão **mais** central, não menos — é o
   item 2 da §8.
2. **Ponto único de falha.** Toda a voz passa a depender do SFU. Combinado com V1 (a mídia acompanha
   o deploy da plataforma), isso exige SFU em alta disponibilidade em **toda** instalação, inclusive
   on-premise.
3. **`REFER` de saída no gateway SIP** — risco novo, específico deste desenho. O handoff de volta
   para um ramal do PABX depende dele. Sem suporte, a alternativa é transferência por bridge: a
   plataforma **permanece no caminho da mídia**, e a conta de canais consumidos na central muda.
   Entrou na §8.

**Corolário sobre a entrada — encerra a pergunta "SIP ou WebRTC no tronco".** Não são alternativas
para a mesma origem; quem determina é **onde o cliente está**. Cliente no telefone, vindo de um
PABX ⇒ **SIP, necessariamente** (o PABX não fala WebRTC). Cliente no site ou no aplicativo ⇒
**WebRTC, necessariamente** (não há tronco ali). A unificação é **interna**, e é justamente ela que
faz uma ligação e um atendimento iniciado no site terem o mesmo tratamento, a mesma supervisão e a
mesma gravação.

### V4 — O **bot leg** é o único ponto de conversão áudio↔texto

Preserva A11 sem exceção: agentes IA continuam sendo **texto**, o AI Gateway continua stateless e
sem saber que existe áudio, e a fronteira áudio↔texto é um único componente auditável. Toda a
plataforma acima dele — skill-flow, masking, avaliação, copilot, histórico — continua operando
sobre texto e não sabe a diferença.

### V5 — Gravação vai para o **AttachmentStore**; retenção é política **por classe de artefato**

> ⚠️ **Correção medida em 2026-09-13 (VOZ-06/07/08) — três afirmações desta seção eram falsas, e a
> decisão sobrevive a elas.** **(1)** A retenção **não** vem do env: `resolve_attachment_expiry_days`
> lê `webchat.attachment_expiry_days` da config-api, com tela; `config.py:119` é só o fallback. O que
> continua valendo é *"um número único para todas as classes"*. **(2)** A gravação **não** escrevia
> no store: `voice.py` chamava `self._store.store()`, método que o `AttachmentStore` nunca teve — o
> `AttributeError` caía no `except` e nenhuma gravação foi armazenada (consertado na VOZ-08, junto
> com `whatsapp.py`/`email.py`, que tinham `commit` sem `tenant_id`). **(3)** O store **não** tem
> ciclo de vida nenhum: o soft-expire horário e o delete diário descritos abaixo não existem em
> código (`soft_expire` sem chamador) — nada apagaria a gravação aos 30 dias porque **nada apaga
> anexo algum** (VOZ-07). Logo o *"conflito resolvido de fato, para pior"* do fim da seção não
> aconteceu: não havia gravação para apagar nem expurgo para apagá-la. A decisão — classe de
> artefato com política própria, uma entrada de config por classe — fica; ela só ganha objeto
> quando houver gravação real, e depende do expurgo existir.

Sem storage próprio de mídia. Gravação, anexo de webchat, imagem de WhatsApp e documento vivem no
mesmo store, com a mesma interface (A7) — e é o que o cliente espera ao ouvir "storage da
solução".

**Mas o store hoje tem um único ciclo de vida** (soft-expire horário, delete físico diário +24h),
que **apagaria gravação**. Logo o store ganha o conceito de **classe de artefato com política de
retenção própria**, e a classe é fato do artefato, não do canal.

**A retenção é item de configuração, um por necessidade** — namespace `storage` na config-api,
com uma entrada por classe (`call_recording`, `webchat_attachment`, `whatsapp_media`,
`survey_audio`, …), editável na tela como todo campo de config.

Isso não é preferência: **hoje é violação viva de invariante.** A retenção existe como
`attachment_expiry_days: int = 30` em `channel-gateway/config.py:119` — **env**, quando a regra da
plataforma é *"env só para segredo e topologia; config de negócio nunca em env"*; **um número
único para todas as classes**, quando a regra é *"one source per domain"*; e **sem superfície de
UI**, quando a regra é *"every config field is UI-editable"*.

**E o conflito documental de A5 (5 anos × 30 dias) já está resolvido de fato, para pior:** a
gravação de voz **já grava no AttachmentStore** (`voice.py:410-416`), e o store expira tudo com o
mesmo número. Ou seja, os 5 anos **não têm implementação** — o código apagaria a gravação aos 30
dias, e nenhum dos dois documentos descreve o que roda. Não há lado a escolher; há uma política
a criar. As duas finalidades são legítimas e coexistem (obrigação contratual/regulatória de
contact center × minimização LGPD), e é por isso que a resposta é **uma entrada de config por
classe**, com default declarado, e não uma constante escolhida por alguém.

### V6 — Tokens só do gateway, e **sem credencial o provider recusa — não degrada**

Metade da decisão é herdada e boa: tokens de mídia são emitidos exclusivamente pelo gateway,
nunca no browser, nunca com o secret exposto.

A outra metade é a correção do defeito que deixou o Arc 15 parecer pronto por meses:
**`_dev_mode` sai.** Sem credencial configurada o provider **falha alto**, com mensagem que nomeia
o que falta. Um token bem-formado e falso é o "valor plausível" na sua forma mais cara — ninguém
fica vermelho, e a ausência do plano de mídia sobrevive a uma revisão de arquitetura.

Onde um modo sem infraestrutura for realmente necessário (teste, CI), ele é **explícito e
declarado** (`MockProvider` escolhido por configuração), nunca inferido de credencial vazia.

### V7 — Aviso LGPD antes de gravar, **por segmento**, com opt-out

Invariante herdado e preservado: guard por segmento, aviso antes do início da captura, opt-out via
ContextStore com evento próprio quando a gravação é pulada. Vale igual para SIP e WebRTC — é
política de contato, não de transporte.

### V8 — A matriz de capability é **verificada**, não declarada à mão

Hoje `voice` declara `{audio}` e tem o caminho de collect morto: a declaração não bate com a
realidade nem para mais nem para menos (A6). O canal reconstruído declara o que o adapter de fato
implementa, e há gate que reprova divergência entre declaração e comportamento.

Isso importa porque a capability é o que decide se um `collect` pode rodar num canal — e um
`requires: [masked_input]` atendido por DTMF é diferente de atendido por STT, com implicações de
segurança que não podem ficar implícitas.

### V9 — **Nada entra como implementado sem gate executável, e o primeiro gate é o ambiente**

O modo de falha deste arco não é código errado: é **ambiente ausente com código verde**. Foi assim
com o Arc 15, e foi assim com o canal `voice`, cujos cinco métodos inexistentes passaram porque os
testes os mockavam.

Portanto: **a fase 0 deste arco é provisionar a infraestrutura**, e o critério de conclusão de
cada fase inclui um gate que **falha quando a infraestrutura não está de pé** — não um teste que
passa mockando o que falta.

### V10 — A borda SIP é superfície nova e precisa de classificação própria

A allowlist de borda cobre prefixos HTTP e é verificada por probe (A8). **Sinalização SIP não é
HTTP** e está inteiramente fora dessa tabela: porta, transporte, quem pode originar, TLS/SRTP
obrigatório ou não — nada disso tem hoje um lugar onde ser declarado nem um probe que reprove
exposição não classificada.

Este arco cria essa classificação. Sem ela, repetimos exatamente o problema que a allowlist HTTP
foi escrita para fechar: *"a segurança da borda era suposição não escrita"*.

---

## 4. O que este arco entrega

| Capacidade | Hoje | Depois |
|---|---|---|
| Chamada de voz percorrendo admissão → roteamento → fila → agente | não existe (A1) | sim |
| URA/IA por voz, com transcrição no histórico e na avaliação | não existe | sim |
| Agente humano atendendo voz pelo browser | não existe (A3/A4) | sim |
| Gravação por segmento com aviso LGPD | desenhada, não executável | sim, no store único |
| Supervisão e take-over em voz | desenhado (Arc 15), placebo | sim |
| Discador | bloqueado por falta de mídia | destravado |
| Handoff para ramal interno de PABX | não existe | `REFER` / bridge |

---

## 5. Fases

**V-F0 — infraestrutura de mídia de pé, e `_dev_mode` fora.** Sem funcionalidade nova. Serviço de
SFU em compose e no deploy, credenciais, SDK como dependência real, provider recusando alto sem
credencial (V6). **É o gate que torna todo o resto mensurável** — enquanto ele não existir,
qualquer fase seguinte pode ficar verde sem funcionar.

> ⚠️ **Reordenadas em 2026-09-12.** A ordem original punha a perna SIP como V-F1, antes do bot
> leg: o valor só aparecia na terceira fase, e a primeira já exigia telecom. **Tudo, menos a
> última milha, é exercitável por WebRTC — sem operadora, sem SBC, sem PABX.** A perna SIP passou
> a ser a última fase, com gatilho comercial declarado, e a validação virou V-F6.

**V-F1 — sala e perna do agente no browser.** Contato de voz/vídeo ponta a ponta por WebRTC:
admissão, roteamento, fila e alocação; o agente atende no browser. Ainda **sem IA na voz**. Fecha
A4 (o humano finalmente entra na conferência) e é a primeira vez que o canal publica algo em
`conversations.inbound`. **Sem telecom.**

**V-F2 — bot leg: STT/TTS.** URA e agente IA por voz; `notify` falado; `menu` por voz com DTMF
**e** STT — o que conserta o `collect` morto de A2. A partir daqui a voz tem transcrição, e
portanto histórico, contexto e avaliação. **Ainda sem telecom.**

**V-F3 — gravação.** Por segmento, com aviso e opt-out (V7), no AttachmentStore com classe de
retenção (V5). Requer a decisão de retenção de A5 tomada antes.

**V-F4 — supervisão.** Supervisor `hidden` com sussurro e take-over.

**V-F5 — perna SIP: a última milha.** Tronco (SBC do cliente ou ITSP) → sala, por **conversor
pronto**; `REFER`/bridge para ramal interno quando houver central — é aqui que o modo SIP encosta
numa central, e é só aqui. **É a única fase que exige telecom**, e entra por **gatilho comercial**
(o primeiro cliente com PABX/SBC, ou a oferta de voz pública), não por ordem de roteiro.

**V-F6 — validação.** E2E com traço gravado + gate de instalação limpa (`--wipe`), porque um
ambiente que só sobe porque já subiu antes não está sendo verificado.

---

## 6. Invariantes

1. **A SALA acompanha o deploy da plataforma**; interconexão e borda podem ser contratadas (V1,
   reescrito em 2026-09-12). Continua valendo: nada de SFU de terceiro, e nenhuma topologia de
   mídia separada por cliente.
2. **Sem credencial, o provider recusa.** Nenhum token, sala ou egress falso. Modo mock é
   escolhido explicitamente ou não existe.
3. **O bot leg é o único ponto de conversão áudio↔texto.** Nada acima dele sabe que há áudio.
4. **Agentes IA são texto.** Sem exceção, inclusive supervisor e especialista.
5. **Uma sala por sessão**; a perna SIP é participante, não um segundo modelo de conferência.
6. **Tokens de mídia só do gateway.**
7. **Aviso antes de gravar, por segmento, com opt-out honrado.**
8. **Um store para todo artefato**; retenção é política da classe, nunca do canal nem do store.
9. **Capability declarada é capability verificada.**
10. **Superfície SIP classificada e verificada por probe**, como a superfície HTTP já é.

---

## 7. Fora de escopo

- **Integração CTI com PABX** — [`adr-cti-gateway-multi-driver.md`](adr-cti-gateway-multi-driver.md).
- **Discador e pacing `look_ahead`** — é o **consumidor seguinte** deste arco, com desenho próprio
  já escrito; entra depois de V-F2.
- **E2EE de mídia** — inviabiliza egress do servidor; decisão própria se algum dia for requisito.
- **Origem da chamada** (DID direto da operadora × tronco do PABX) — é configuração de tronco, não
  decisão de arquitetura. As duas terminam no mesmo lugar.
  *Nota 2026-08-19:* a topologia **preferida em implantação** é o desvio por tronco/DNIS no
  SBC/PABX, por posicionamento (apresenta o conjunto como solução integrada na planta do cliente,
  permite adoção incremental e não exige renumeração). Isso **não** muda o escopo deste ADR, mas
  tem uma consequência de dimensionamento que precisa ser levantada por projeto: nessa topologia o
  enlace interno carrega **todas** as chamadas de atendimento, logo cada chamada ocupa **dois**
  recursos da central. Ver o corolário emendado em
  [`adr-cti-gateway-multi-driver.md`](adr-cti-gateway-multi-driver.md) §0.
- **Vídeo** — o transporte suporta, mas medium de vídeo tem desenho próprio no Arc 15 e não é
  requisito deste arco.

---

## 8. O que decidir, e quando

> ⚠️ **Reagrupado em 2026-09-12.** Com as fases reordenadas, **quase nada precisa ser decidido
> antes de V-F0** — e esse é o ponto da mudança. As decisões de telecom migraram para V-F5 e, em
> boa parte, deixaram de ser arquitetura: viram checklist de projeto por cliente.

**Antes de V-F0:** nada. Subir o SFU em compose não depende de nenhuma decisão abaixo.

**Antes de V-F3:** o **default de retenção por classe**. O mecanismo já está decidido (item de
config por classe no namespace `storage`, V5); o que falta é decisão de negócio/jurídico, e o A5
registra um conflito doc×doc (5 anos × 30 dias) que nenhum código arbitra.

**Antes de V-F5, e por cliente:**

- **Codec — direção fechada em 2026-09-12: G.711 nas chamadas com perna SIP.** Os navegadores
  falam PCMU/PCMA além de Opus, então o conversor faz **relay, não transcodificação**, e o custo
  por chamada fica pequeno; Opus fica para o que é só browser. Transcodificar é o que pesa, e
  evitá-lo é a decisão.
- **Transporte de DTMF fora de banda** (RFC 4733) nas duas pernas — é o que a `NIV-07` cobra.
- **TLS/SRTP obrigatório ou negociável** no enlace com o SBC (V10).
- **`REFER` de saída suportado pelo gateway?** (V3) Define se o handoff para ramal libera a
  plataforma do caminho da mídia ou a mantém em bridge.

**Sem fase atrelada:** **failover** — o que acontece com chamadas em curso quando a plataforma
cai, e se há overflow para a central no deploy on-premise.

---

## 9. Riscos

| Risco | Mitigação |
|---|---|
| Repetir o Arc 15: infraestrutura ausente com código verde | V6 (recusa alta) + V9 + V-F0 como fase própria e primeira |
| Assumir responsabilidade de telecom sem sinalizar | V1 **reescrito em 2026-09-12**: só a sala é nossa, interconexão e borda são contratadas. O que resta (codec, DTMF, TLS/SRTP no enlace) é checklist de projeto, não operação de telecom |
| Valor tardio — duas fases de infraestrutura antes de qualquer função visível | Reordenação de 2026-09-12: V-F1 e V-F2 entregam contato e IA por voz **antes** de qualquer telecom |
| Bifurcar em duas pilhas de mídia (WebRTC e SIP separadas) | V3 — uma sala por sessão, a perna SIP é participante. O custo de bifurcar não está na mídia: está em gravação, supervisão, transcrição e avaliação, que ficam ACIMA dela. Reabrir só com número de concorrência medido |
| Retenção decidida por omissão | A5 é bloqueio declarado de V-F3, não item de backlog |
| Borda SIP exposta sem classificação | V10 + probe próprio, espelhando o que a allowlist HTTP já faz |
| Concorrência de eventos na fronteira de mídia | O bridge não garante ordem (A9); o estado de perna deve ser idempotente por `(session, participante, estado)` |
| Store único apagar gravação pelo ciclo de anexo | V5 — classe de artefato com política própria, antes de existir arquivo |


---

## Apêndice — resumo denso migrado do índice do `CLAUDE.md` (2026-08-31)

> Este bloco vivia como **uma linha** do índice `docs/` no `CLAUDE.md`, onde ocupava 1717 bytes.
> Medido antes de mover: **~85% do seu vocabulário já existe neste ADR** — ele é uma condensação
> independente, não uma cópia, e por isso os ~15% restantes (achados, números e nomes de arquivo que
> só foram registrados no índice) **não existiam em lugar nenhum além dali**. Movido inteiro, sem
> resumir, porque a alternativa — cortar no CLAUDE.md e confiar que o ADR já dizia tudo — perderia
> exatamente a fração que não dá para recuperar.
>
> **É trabalho aberto**, não documentação final: a fração nova deve ser dobrada no corpo do ADR e
> este apêndice, encolhido. Enquanto isso não acontece, ele é a única cópia.

arco de VOZ PRÓPRIA (modo SIP): terminação SIP + SFU + STT/TTS + perna do agente + gravação, **independente de PABX**. Consolida TRÊS dívidas que sozinhas não se justificam: canal `voice` que **não roda** (5 métodos inexistentes em `voice.py:236`, mockados no teste ⇒ `AttributeError` real), Arc 15 **placebo** (zero LiveKit em compose, zero env, SDK fora do `pyproject`), discador bloqueado por falta de mídia. **V1: o plano de mídia NÃO tem topologia própria — acompanha o deploy da plataforma** (⇒ elimina SFU SaaS; on-prem = sem WAN e sem SBC de graça; nuvem = SBC é do produto). **V6: `_dev_mode` SAI — sem credencial o provider RECUSA**, porque token bem-formado e falso é o valor plausível mais caro (foi o que deixou o Arc 15 parecer pronto por meses); mock é escolha declarada, nunca inferida de credencial vazia. V2 reconstrói `voice` (já está no `Literal`), Twilio rebaixado a **um** `IVoiceProvider` (tronco CPaaS); `webrtc` segue canal à parte (lá o CLIENTE é browser); perna do AGENTE nunca é canal. V4 preserva "IA é sempre texto" — bot leg é o ÚNICO ponto áudio↔texto. **V5: gravação no AttachmentStore, sem store próprio — mas retenção vira política POR CLASSE** (o ciclo atual, soft-expire horário, apagaria gravação) e há **conflito doc×doc a arbitrar: 5 anos × 30 dias LGPD**. V10: **borda SIP é superfície nova, fora da allowlist HTTP** (o probe só conhece prefixo). Fases V-F0 infra de pé (fase própria, primeira) → V-F1 perna SIP entrante → V-F2 bot leg STT/TTS (conserta o `collect` morto) → V-F3 gravação → V-F4 egress+supervisão → V-F5 validação c/ instalação limpa — proposto
