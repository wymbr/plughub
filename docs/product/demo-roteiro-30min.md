# Roteiro de demonstração — 30 min de demo + 30 min de discussão

> Plateia: **técnica** (arquitetos/devs). Espinha: **`retencao_humano`** (o único pool com
> `queue_config`, fila interna pull, `mentionable_pools` e `on_human_end`).
> Stack: `docker-compose.demo.yml`. Tenant: `tenant_demo`.
> Escrito em 2026-08-11, contra o estado as-built do repositório.

---

## 0. Decisões que estruturam o roteiro

Quatro escolhas, cada uma por um motivo verificado no código:

**1. `sac_ia` NÃO é a espinha — `retencao_humano` é.**
`sac_ia` (`infra/registry/tenant_demo.yaml:149-166`) declara **um único hook**: `on_contact_end → nps_ia`.
Não tem `queue_config`, não tem `on_human_end`, não tem `mentionable_pools`. Fila, wrap-up e `@mention`
simplesmente não existem nele. Quem tem tudo isso é `retencao_humano` (`:319-399`).
`sac_ia` entra na demo no papel certo: **destino de transferência** (está em
`supervisor_config.escalation_pools`, `:353-358`).

**2. Wrap-up configurado como `detached` para a demo.**
Hoje o YAML diz `dispatch: inline` (`:377`). Em `inline` o item de wrap-up se **auto-reivindica** e o
formulário abre sozinho — não há nada para mostrar. Em `detached` ele vira um item visível na inbox pull
com o selo *"Reservado a você"*, e você demonstra **fila pull + fila push no mesmo bloco**.
A diferença entre os dois modos é literalmente uma flag (`session.wrap_up_auto_attend`, injetada em
`channel-gateway/.../main.py:1290-1291`) — narre isso e você cobre os dois modos mostrando um.

**3. O contato 1 da journey roda ANTES da demo; ao vivo só o contato 2.**
O intake tem 4 turnos e o processo suspende por 48 h. Rodar tudo ao vivo custa ~5 min e três pontos de
falha. Rodando o contato 1 + o resume da operadora no pré-voo, você começa o Bloco B com uma **sessão
`suspended` visível no Monitor** — que é a prova, não a encenação — e demonstra ao vivo só a parte
interessante: reconhecimento por identidade, OTP, preview mascarado e resume.

**4. Honestidade sobre a journey.** `journey_merge` **nunca é chamado** pelos skills de portabilidade.
Session A (intake) e Session B (processo) compartilham `root_session_id` — isso é journey real e drilla
em 3 níveis. O **contato 2 é uma journey separada**, ligada à primeira por identidade
(`resume_origin=identity`), não por merge. Para essa plateia isso é um **ponto forte**, não fraqueza:
mostra o modelo (proveniência ∪ alias) e onde falta um `invoke journey_merge`. Não finja o contrário —
alguém vai abrir a Vista Processos e ver duas linhas.

---

## 1. Pré-voo

### T-1 dia — o que precisa estar de pé

```bash
./infra/scripts/rebuild-all.sh          # sem --wipe
```

Confirme, um a um:

| Verificação | Como | Esperado |
|---|---|---|
| platform-ui | `http://localhost:5174/login` | `admin@plughub.local` / `changeme_admin` |
| cliente webchat | `http://localhost:5173/webchat-test.html` | select com `retencao_humano` e `portabilidade_ia` |
| DialogForms semeados | `/config/dialog-forms` | `dialog_wrapup_v1`, `dialog_nps_buttons`, `dialog_otp_possession` |
| instâncias humanas órfãs | `redis-cli --scan --pattern 'tenant_demo:instance:human-*'` | poucas — o limite é **10** (`seed_pricing.py:37`) |

> ⚠️ **O cliente webchat da stack demo é `http://localhost:5173/webchat-test.html`** (servido pelo
> `agent-assist-ui`). O `infra/webchat-client/index.html` **não está** no `docker-compose.demo.yml` — só
> em `full.yml`/`visual.yml`. O WS é hardcoded (`ws://localhost:8010/ws/chat/{pool}`), o pool vem do
> `<select>` e o JWT é auto-gerado; não há CORS no channel-gateway, então o handshake passa.

### T-1 dia — mudar o wrap-up para `detached`

```bash
DISPATCH=detached bash infra/test/smoke_internal_work_queue.sh apply
```

O que ele muta, de forma **persistente e sem rollback automático**:
`PUT /v1/pools/retencao_humano` (substitui o objeto `hooks` inteiro; o delta real vs. o YAML é só
`inline → detached`) · auto-provisiona `retencao_humano-int` · **republica `skill_wrapup_detached_v1` a
partir do YAML** (sobrescreve edição feita no editor) · `set-next` + `promote` no slot do
`wrapup_detached_ia`. Usa `x-service-token`, não ADMIN_TOKEN. **Não re-rode** — ele re-promove e reseta
o marcador (aviso no próprio script, `:255-257`).

### T-1 dia — seeds de Analytics (Bloco C)

```bash
bash infra/test/seed_deploy_lens_demo.sh    # lente deploy, modo diário + markers
bash infra/test/seed_epoch_demo.sh          # lente deploy, modo epoch (por versão)
```

> ⚠️ **As datas são hardcoded e estão no passado**, sem env var para parametrizar:
> `seed_deploy_lens_demo.sh` grava **15, 17 e 19/06/2026** · `seed_epoch_demo.sh` grava **18 e
> 21/06/2026** · `seed_customer_history_demo.sh` grava **01, 05, 10 e 12/07/2026**.
> Hoje é 11/08/2026 → **o seletor de período da UI precisa apontar para 13–21/06/2026**, senão a tela
> vem vazia. Se sobrar 20 min de preparo, o melhor investimento do dia é trocar as datas literais por
> `now() - INTERVAL n DAY` nesses três scripts — some o passo mais frágil da demo.

### T-30 min — rodar o contato 1 da journey

Webchat → pool **`portabilidade_ia`** → Conectar:

| Turno | O que fazer |
|---|---|
| 1 | digitar `11987654321` |
| 2 | clicar **📶 Vivo** |
| 3 | digitar `11987654321` — **telefone, nunca e-mail** ⚠️ |

⚠️ O branch `confirmation_channel: email` **não tem adapter** (`skill_portabilidade_demo_v1.yaml:40-41`):
com e-mail, o processo morre em timeout de 600 s e o cenário inteiro se perde.

Fim esperado: *"✅ Solicitação de portabilidade registrada com sucesso!"*. Anote o `session_id`.

Depois, simule a resposta da operadora (**não espere as 48 h**):

```bash
# token da Session B (o processo suspenso)
docker compose -f docker-compose.demo.yml exec -T redis \
  redis-cli HGETALL tenant_demo:resume_tokens | paste - -

# aprovar — porta INTERNA (/v1/...), que preserva `decision`
curl -X POST http://localhost:8010/v1/channels/webhook/resume/<TOKEN> \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"tenant_demo","payload":{"decision":"approved","source":"operadora"}}'
```

⚠️ **Não use a porta externa** `/channel/webhook/resume/{token}`: ela descarta `decision` e `source`
de propósito (`main.py:1690-1693`) e você perde o controle do branch.
A partir daqui você tem **24 h** de janela (o `delegate` do processo) — folga de sobra.

### T-10 min — telas e terminais

- **Janela 1** — Chrome, `http://localhost:5174/console`, logado como `admin@plughub.local`, **sem pool
  marcado ainda** (o cliente precisa cair na fila primeiro).
- **Janela 2** — `http://localhost:5173/webchat-test.html`, pool `retencao_humano`, **não conectado**.
- **Janela 3** — segunda aba do webchat, pool `portabilidade_ia`, para o Bloco B.
- **Janela 4** — `http://localhost:5174/analise/agents`, já com o período **13–21/06/2026** selecionado.
- **Terminal visível** (é parte da narrativa, não bastidor):
  ```bash
  docker compose -f docker-compose.demo.yml logs -f channel-gateway | grep -E 'OTP-DEV|session_id'
  ```

> ⚠️ **Ensaie o OTP no máximo 3×** por número em 15 min — o rate-limit é 3 challenges/900 s por âncora
> (`identity/otp.py:44-48`). E note: depois do **primeiro** OTP bem-sucedido a âncora fica `possessed`
> **durável** — no ensaio seguinte o fluxo pula o OTP. Se quiser demonstrar o OTP ao vivo, **ensaie com
> outro número** e guarde `11987654321` virgem para a apresentação.

---

## 2. Roteiro minuto a minuto

### 00:00–04:00 · Abertura — o modelo, não o produto

Uma tela só: **Monitor › Sessions** (`/monitor`). Três afirmações, cada uma apontando para a tela:

1. **Todo contato é uma sala de conferência.** Humano e IA são participantes simétricos, com `role`
   (`primary`/`specialist`/`supervisor`/`evaluator`) e visibilidade por mensagem (`all` /`agents_only`/
   lista de participantes). Não existe "atendimento humano" e "bot" como caminhos distintos.
2. **Três níveis fechados:** `segment` (janela de um participante) → `session` (um contato) →
   `journey` (processo sobre N contatos, **derivado** de proveniência ∪ alias, nunca entidade).
3. **O pool é a unidade endereçável.** Nunca o skill. Fila, capacidade, hooks e webhook penduram no
   pool; o skill é detalhe interno do deploy.

Feche com a promessa: *"nos próximos 20 minutos os três níveis aparecem na tela, e no fim eu mostro a
medição saindo do mesmo dado."*

### 04:00–14:00 · Bloco A — um contato, ponta a ponta

| min | Ação | O que apontar |
|---|---|---|
| 04:00 | **Janela 2**: pool `retencao_humano` → Conectar → digitar *"preciso cancelar meu plano"* | Cliente cai na **fila**: *"Olá! No momento todos os nossos especialistas estão ocupados…"*. Isso é o `queue_config` — um **agente de fila** (`skill_fila_v1`) atendendo de verdade, com LLM, não uma mensagem de espera. Se o cliente escrever de novo, ele responde. |
| 05:00 | **Janela 1**: abrir o combo de pools no Header → marcar `retencao_humano` | **Abrir o WebSocket É o `agent_login`** (`server.ts:3113` → `registerHumanAgent`). Não há botão "ficar disponível". Repare que **dois** WS abrem: o pool e o espelho `-int`. |
| 05:30 | Voltar à Janela 2 | *"Ótima notícia! Um de nossos especialistas está disponível agora…"* → o contato migra para o humano. **Push**: o contato foi entregue, ninguém puxou. |
| 06:00 | Trocar 2 mensagens | Painel direito → aba **Contexto**: ContextStore por namespace, com confiança e origem por tag. |
| 07:00 | Digitar `@copilot` e depois `@copilot ativa` | A bolha sai **`agents_only`** — o cliente não vê (confirme na Janela 2). Resposta: `💡 Co-pilot: …`. Aponte a aba **Ações**: cada agente mencionável tem alias, estado e botão **Acionar** que renderiza um form a partir do `delegation_input` do YAML. `mentionable_pools` é o **domínio fechado** de quem pode ser chamado. |
| 09:00 | Digitar `@auth` | O especialista de autenticação entra na conferência. Operador vê: *"🔐 …O PIN do cliente NÃO aparecerá neste chat — somente o resultado."* Na Janela 2 o cliente digita `123456` num campo mascarado. Operador vê só *"✅ PIN validado"*. **A prova é a ausência**: `@masked.*` nunca toca Redis, `pipeline_state`, stream ou log. |
| 10:30 | Abrir `/config/masking` em aba nova (20 s) | O outro mecanismo, complementar: política de leitura **por tag × role** — `caller.cpf → last_2`, `caller.telefone → last_4`, `account.limite_credito → hidden`, `*` para supervisor → `plain`. Mascaramento é config, não código. |
| 11:00 | Voltar ao Console → **Encerrar** | O contato **some da lista na hora**. Toast: *"Atendimento encerrado. Wrap-up na sua fila."* → **fecha o G1**: o wrap-up não infla o TMA porque não está dentro do contato. |
| 11:15 | Apontar a **Janela 2** | O cliente já recebeu o **NPS**: *"Em uma escala de 0 a 10…"* com 11 botões. Peça 9. → *"Agradecemos sua avaliação! ✅"*. O **operador não vê** (`visibility: [customer_participant_id]`) — decisão de produto, não acidente. |
| 11:45 | Coluna esquerda, metade de baixo: **Filas (pull)** | Item em **"Pós-atendimento — retencao_humano"**, com selo **"Reservado a você"** e idade colorida por SLA. Isto é o **ramal**: pull direcionado por `assigned_to`, sem transbordo. Clicar a linha = **preview read-only**; reivindicar é o botão **Atender (Pull)** no centro. |
| 12:15 | **Atender (Pull)** | O `DialogFormRenderer` toma a coluna central: **esquerda** = transcrição da conversa referenciada (briefing), **direita** = o form. Preencha *Resolvido* + resumo → **Enviar**. |
| 13:00 | Narrar sem clicar | Duas frases de fecho do bloco: (a) trocar `dispatch: detached` por `inline` faz esse mesmo item **se auto-reivindicar** e o form abrir sozinho — mesma máquina, uma flag; (b) esse renderer **não é do wrap-up**: é o tratamento genérico de collect-form no Console. O gate de aprovação de deploy usa exatamente esta tela. |
| 13:30 | Header → **Pausar** → escolher *Reunião* + nota → **Retomar** | Arc 8: alimenta `agent_pause_intervals` e a lente `pause_reason`, que aparece no Bloco C. |

### 14:00–22:00 · Bloco B — a journey

| min | Ação | O que apontar |
|---|---|---|
| 14:00 | **Monitor › Sessions**, filtro por status | A sessão do processo de portabilidade em **`suspended`**. *"Isto foi criado há 20 minutos por um cliente. O agente devolveu a vaga ao pool e a sessão persiste — o `session_id` é o mesmo através de N ciclos de suspend/resume."* |
| 15:00 | Mostrar o `curl` de resume no terminal (já executado) | *"A operadora respondeu por webhook. Um workflow é um canal como outro qualquer — `webhook` — e o trigger cria uma sessão normal, roteada por um pool normal."* |
| 16:00 | **Janela 3**: webchat, pool `portabilidade_ia` → Conectar | Contato **novo**, canal novo, cliente que não traz nenhum identificador de sessão. |
| 16:30 | Digitar `11987654321` → clicar **📶 Vivo** → digitar `11987654321` | Narre a feiura com honestidade: *"o intake re-coleta antes de resolver identidade — é ergonomia a corrigir, não arquitetura."* |
| 17:30 | *"Para acessar atendimentos anteriores com segurança…"* → **✅ Verificar meu número** | **A plataforma é autoridade de posse de canal, não de identidade de registro.** O `resume_token` **nunca sai** enquanto a âncora for `claimed`; só `possessed` (⇒ OTP verificado) libera. Isso é `verification_required`, e ele **não vaza** se existe ou não pendência. |
| 18:00 | Pegar o código no terminal (`[OTP-DEV] … code=NNNNNN`) e digitar | O form de OTP é um `DialogForm` renderizado pelo `dialog_runner` — **mesmo primitivo** do wrap-up e do NPS. O código nunca passa pela mão do agente: gerar/enviar/verificar ficam no `OtpService`. |
| 19:00 | Aparece o menu de continuidade | ⚠️ O número vem **mascarado**: `***4321`. Segunda prova de masking, agora num preview de contexto cross-sessão. Clicar **✅ Confirmar portabilidade**. |
| 19:30 | Clicar **✅ Sim, confirmar portabilidade** | (Sim, são duas confirmações — o delegate re-pergunta. Narre como redundância a limpar.) O processo suspenso **retoma e completa**. |
| 20:30 | **Analytics › Processos** (`/analise/processos`) | Drill de **3 níveis**: journey → sessions → segments. Abra a journey do intake: Session A + Session B sob a mesma raiz. |
| 21:30 | **A frase honesta** | *"O contato de hoje aparece como journey própria. `root_session_id` liga proveniência; o que ligaria contatos independentes é `journey_merge`, e nenhum destes skills o chama — a pendência já carrega o `root_session_id`, falta um `invoke`. É lacuna de configuração de fluxo, não de modelo."* Para arquiteto, isso vale mais que uma tela perfeita. |

### 22:00–28:30 · Bloco C — a medição sai do mesmo dado

**`/analise/agents`** — período **hoje**:

| Lente | O que a plateia acabou de gerar |
|---|---|
| `resolution` / `sessions_aht` | o contato do Bloco A, com `outcome` vindo do wrap-up |
| `wrapup` | a disposição *Resolvido* que você digitou às 12:15 (`outcome` × `issue_status`) |
| `nps` | o **9** que o cliente deu às 11:15, grão `segment` |
| `pause_reason` | a pausa *Reunião* das 13:30 |
| `availability` | seu próprio login/logout |

Frase-âncora: *"nada disto foi instrumentado à parte. É o mesmo substrato — `segments`, `session_signal`,
`agent_pause_intervals` — lido por lentes diferentes."*

Depois **troque o período para 13–21/06/2026** (diga que é dado semeado, sem fingir) e mostre a lente
**`deploy`**:

- modo **Diário + markers**: curva de qualidade por dia, **triângulo** no dia do deploy.
- toggle **Por versão** (`mode=epoch`): eixo X vira **versões**, não dias.
- Ponto que fecha a demo: *"a âncora é o **pool**, e a identidade de versão é o **momento do promote**,
  não um rótulo no YAML. É por isso que dá para perguntar `esta versão está melhor que a anterior?` e
  ter resposta."*

Se sobrar 40 s: **`/analise/customer-voice`** — a mesma lente `grain × metric` sobre `session_signal`,
com o NPS agora no grão `journey`.

### 28:30–30:00 · Fecho

Três frases, sem slide:

1. O que você viu foi **um** modelo de sessão servindo fila push, fila pull, agente humano, agente IA,
   workflow assíncrono e formulário de aprovação — não seis produtos.
2. **Sem lock-in**: agente externo (LangGraph, CrewAI) entra pelo proxy sidecar e recebe a mesma
   interceptação de MCP — validação de permissão, injection guard e auditoria — sem poder optar por sair.
3. A qualidade é medida **por deploy**, no mesmo dado da operação.

---

## 3. Plano B — por trecho

| Se falhar | Sintoma | Recuperação |
|---|---|---|
| Cliente não entra na fila | conecta e nada acontece | Já há agente logado em outra aba/instância órfã. `redis-cli --scan --pattern 'tenant_demo:instance:human-*'` e limpe antes. |
| `agent_login` recusado | linha "Disconnected" no Header, toast | `human_capacity_exhausted` (limite 10) ou `pool_kind_mismatch`. Limpe instâncias órfãs. **O Console não reconecta sozinho.** |
| Item de wrap-up não aparece na inbox | fila pull vazia após Encerrar | Deixe **um item parqueado no pré-voo** (rode um atendimento completo em `detached` no T-1 dia e **não** o conclua). Ele fica lá com o selo "Reservado a você" e salva o trecho. |
| NPS não chega | cliente não vê os botões | O cliente **fechou a aba** → `nps_on_disconnect: skip` suprime o despacho por design. Reabrir não recupera; siga em frente e explique a regra (é um bom momento). |
| OTP não libera | `verification_required` persiste | Código expirado (TTL 300 s) ou rate-limit (3/15 min). Tenha um **segundo número** ensaiado e pronto. |
| Journey não drilla | Vista Processos vazia | Use o Monitor › Sessions com filtro `suspended` — a prova do suspend/resume não depende da tela de journey. |
| Lente vazia | gráfico em branco | **Quase sempre é o período.** Deploy/epoch = junho; histórico de cliente = julho; live = hoje. |
| `quality_criteria` vazia | radar sem dados | **Esperado** — `evaluation_dimension_scores` não tem seed e o avaliador não roda no demo. **Não coloque essa lente no roteiro.** |

---

## 4. Item 3 detalhado — cobertura das 10 lentes

São **10 lentes** na UI (`AgentsBenchPage.tsx:38`) + `session_nps`, que só existe no pop-up de detalhe.

| Lente | Fonte | Estado para a demo |
|---|---|---|
| `resolution` | `segments` | ✅ **live** — sai do Bloco A |
| `sessions_aht` | `segments` | ✅ **live** |
| `wrapup` | `segments.issue_status` | ✅ **live** — o form que você preenche |
| `nps` | `session_signal` grain=segment | ✅ **live** — o 9 do cliente |
| `pause_reason` | `agent_pause_intervals` | ✅ **live** — a pausa de 20 s |
| `availability` | `agent_login_intervals` + pausas | ✅ **live**, mas com N=1: a curva de ocupação fica pobre |
| `escalation_reason` | `segments` c/ `escalation_reason != ''` | ⚠️ exige um **transfer** — adicione um contato extra no pré-voo, transferido para `sac_ia` |
| `deploy` (diário) | `evaluation_finalized` + REST do registry | ✅ `seed_deploy_lens_demo.sh` — **junho/2026** |
| `deploy` (epoch) | `evaluation_finalized` ⋈ `segments.deploy_version` | ✅ `seed_epoch_demo.sh` — **N=6, abaixo do `min_sample`=30**, sai com aviso *"Low sample"*. Ou aumente o N no script, ou **antecipe o aviso na narrativa** ("o gráfico se recusa a fingir confiança que não tem") — o que, para plateia técnica, é melhor que esconder |
| `quality` | `evaluation_results` ⋈ atribuição | ⚠️ só via `test_t11_quality_report.sh` (fixe `CAMP=`), datas **19/06/2026** |
| `quality_criteria` | `evaluation_dimension_scores` | ❌ **sem seed em todo o repo.** Única via é o pipeline real (`evaluation.events` → consumer). **Fora do roteiro.** |

**Duas dívidas de preparo, em ordem de retorno:**

1. **Parametrizar as datas** dos três seeds (`now() - INTERVAL n DAY`). Elimina o único passo do roteiro
   em que você precisa mexer no seletor de período no meio da demo. ~20 min.
2. **Um gerador de volume.** Não existe nada parametrizável por N no repositório — todo seed é `INSERT`
   hardcoded de dezenas de linhas. Com N=1 as lentes *funcionam* mas não *impressionam*. Um script que
   gere ~200 segmentos + avaliações em 14 dias resolveria `availability`, `quality` e o `min_sample` do
   epoch de uma vez. ~1 h.

---

## 5. O que mais vale percorrer — ranqueado

Para os **30 min de discussão**, com script curto de cada um. Os três primeiros são os que mais
provavelmente mudam a conversa com arquitetos.

**① Aprovação humana como passo de workflow (3 min) — o de maior retorno.**
Pool `aprovacao_deploy` (`dispatch_mode: pull`) + `skill_gate_promocao_v1` + form
`dialog_promocao_deploy`. O aprovador é um **agente logado**, o item cai na **mesma inbox pull** do
wrap-up e é renderizado pelo **mesmo `DialogFormRenderer`**. A tese fica visível sem slide: *aprovação
não é um módulo, é um `collect` a um pool.* Amarra direto no Bloco A.

**② Auditoria LGPD (2 min).** `/audit` → aba **MCP Calls**: toda chamada de ferramenta que a IA fez,
com `allowed`, `injection_detected`, `duration_ms` e `source` (`in_process` | `proxy_sidecar`). O ponto
que fecha: **a política de auditoria é definida na ferramenta, não na chamada — o chamador não pode
optar por sair**. É a resposta concreta para *"como vocês governam agente de terceiro?"*.

**③ Editor de DialogForms (60 s).** `/config/dialog-forms` → abrir `dialog_wrapup_v1` → mostrar que
adicionar uma pergunta é **config**. O skill de wrap-up não muda: ele passa as respostas inteiras +
o `dialog_form_id` para `segment_outcome_record`. Quatro superfícies (chat, inline, Console, página web)
consomem o mesmo JSON. Se a plateia perguntar "e para customizar por cliente?", esta é a resposta.

**④ Capacidade por tipo de licença (2 min).** Monitor › Pools: um cartão **por tipo**, e **não existe
`available` escalar no topo**. Humano e IA são moedas não-fungíveis; somá-las é falácia de aditividade.
Vale a pena mostrar porque quase todo concorrente soma. Conecta com a admissão: humano é gateado por
**login**, IA por **sessão**.

**⑤ Scheduler + promote agendado (2 min).** `/config/schedules` e `/monitor/schedules`. Uma agenda
aciona um **pool** (nunca um skill) por webhook; o corpo do job é um workflow que faz `invoke pool_promote`.
Combinado com ① e com a lente `deploy`, fecha um arco de governança: **aprovar → promover → medir**.

**⑥ Outbound + survey por link web (3 min).** `/config/outbound`: mailing → campaign → dispatcher/worker
por fan-out → `survey_link_create` → página pública `/survey/{token}`. O gancho técnico é a
**elegibilidade**: `contact_eligibility_check` avalia opt-out global → janela de calendário → fadiga,
nessa precedência, e o `reason` sempre **nomeia a regra** que barrou.

**⑦ Session Replayer (2 min).** Se perguntarem sobre avaliação de IA: `ensure-before-read` com
Hydrator, `ReplayContext` com `tool_trace` + `flow_definition` (trajetória esperada × real), e o limite
assumido — faithfulness sobre valor de PII em output de ferramenta **não** é suportado, por
minimização LGPD. Admitir um não-objetivo explícito costuma comprar mais credibilidade que uma feature.

**Não mostre**, mesmo se lembrar deles: o drawer *"Delegar Tarefa"* (UI morta — nada chama
`setShowDelegarDrawer(true)`), as abas *Estado/Capacidades/Orquestração* (não renderizadas), `@humanoxxx`
(alias aponta para pool inexistente no YAML), os aliases da palette `/` (hardcoded, não são os
`mentionable_pools`), e o botão *"+ Adicionar tag"* da aba Contexto (faz POST sem `Authorization` contra
endpoint que exige JWT — provável 401 na sua cara).

---

## 6. Perguntas prováveis e onde a resposta está ancorada

| Pergunta | Âncora |
|---|---|
| *"E se eu já tenho um agente em LangGraph?"* | Proxy sidecar em `localhost:7422`. Mesmos 3 checks (<1 ms): permissão via decode local de JWT, injection guard (13 padrões), registro de auditoria em `mcp.audit`. `plughub-sdk verify-portability` e `certify`. |
| *"Como vocês evitam lock-in?"* | O contrato de execução é o SDK + MCP. `skill-extract` extrai skill de agente existente; `regenerate` reescreve agente proprietário como nativo. |
| *"O supervisor consegue ver dado mascarado?"* | Sim, por role: `*` × `supervisor` → `plain` (`config-api/seed.py:472-494`). E o acesso a `original_content` grava linha imutável em `audit_access_log` — tabela `MergeTree`, **nunca deduplicada, por design LGPD**. |
| *"Quanto custa?"* | Faturamento por **capacidade configurada**, não por consumo: capacidade base pro-rata + pools de reserva por dia de ativação. `/config/billing`. |
| *"Como sei que uma versão nova do agente é melhor?"* | Lente `deploy` em modo epoch + overlay de nota provisória × pendentes de fechamento. Convergência entre as duas = sinal de confiança. `min_sample=30`. |
| *"Multi-canal?"* | `channel` é **filtro duro** de roteamento; `medium` é fator de score. Hoje só webchat está de pé no demo — WhatsApp, voz, e-mail e WebRTC/SFU têm adapter, mas não estão na stack demo. **Diga isso; não improvise.** |
| *"Dá para o agente humano e a IA atenderem juntos?"* | É o default: mesma sala, `role` diferente. Foi o `@copilot` do Bloco A. |

---

## 7. Checklist de 60 segundos antes de compartilhar a tela

- [ ] Instâncias `human-*` órfãs limpas
- [ ] Wrap-up em `detached` aplicado e **um item parqueado** na inbox (plano B)
- [ ] Contato 1 da journey rodado e **resume da operadora executado** (janela de 24 h aberta)
- [ ] Número `11987654321` **virgem de OTP** (ensaios feitos com outro número)
- [ ] Seeds de junho rodados; `/analise/agents` já aberto com período **13–21/06/2026**
- [ ] Terminal com `logs -f channel-gateway | grep OTP-DEV` visível e limpo
- [ ] Notificações do SO silenciadas; zoom do browser em 110–125%
- [ ] Aba do webchat do cliente **não fechada** entre os blocos (o NPS depende disso)
