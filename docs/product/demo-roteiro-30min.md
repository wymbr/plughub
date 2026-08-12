# Roteiro de demonstração — 30 min de demo + 30 min de discussão

> Plateia: **técnica** (arquitetos/devs).
> Bloco A: **`retencao_humano`** (o único pool com `queue_config`, fila interna pull,
> `mentionable_pools` e `on_human_end`). Bloco B: **aumento de limite de crédito**
> (`limite_ia` → `limite_processo` → `aprovacao_credito` → `limite_entrega`).
> Stack: `docker-compose.demo.yml`. Tenant: `tenant_demo`.
> Escrito em 2026-08-11; **Bloco B trocado de portabilidade para limite em 2026-08-12** (a
> portabilidade permanece como plano B ensaiado, §3).

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

**3. O Bloco B é o aumento de limite, rodado 100% ao vivo** *(decidido 2026-08-12; antes era
portabilidade, que fica como plano B em §3).*
A portabilidade obrigava a rodar o contato 1 no pré-voo, porque o processo suspende por 48 h e o resume
vinha de um `curl` fingindo ser a operadora. O aumento de limite não espera ninguém: **o aprovador é
você, na janela ao lado**. Isso troca uma encenação por três coisas reais na tela — aprovação humana em
fila pull, um valor **editado** pelo humano chegando ao cliente, e uma journey de três sessões. O preço
é ritmo: 8 minutos apertados, que exigem dois ensaios.

**4. Honestidade sobre a journey.** As três sessões do processo (intake → análise → entrega)
compartilham `root_session_id`, e isso está **medido**, não suposto:
`infra/test/probe_journey_limite.sh` → 5/0. A herança é transitiva por construção — um workflow
disparou outro e a raiz atravessou. O que **não** acontece é o merge de contatos independentes:
`journey_merge` existe como tool e **nenhum skill o chama**. Para esta plateia isso é ponto forte, não
fraqueza — mostra o modelo (proveniência ∪ alias) e nomeia exatamente o que falta (um `invoke`). Não
finja o contrário; alguém vai abrir a Vista Processos.

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
| cliente webchat | `http://localhost:5173/webchat-test.html` | select com `retencao_humano`, **`limite_ia`** e `portabilidade_ia` |
| DialogForms semeados | `/config/dialog-forms` | `dialog_wrapup_v1`, `dialog_nps_buttons`, `dialog_otp_possession`, **`dialog_limite_solicitacao`**, **`dialog_limite_aprovacao`** |
| quebras de linha no webchat | mandar qualquer mensagem com lista | as linhas **quebram** (o `.msg` ganhou `white-space: pre-wrap` em 2026-08-12). Se vier tudo numa linha corrida, o `agent-assist-ui` não foi rebuildado — é `COPY`, não volume |
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

```bash
bash infra/test/seed_customer_history_demo.sh   # histórico do cliente (Cliente 360)
```

> ✅ **Datas parametrizadas em 2026-08-12** — eram literais de junho/julho e envelheciam, forçando
> mexer no seletor de período no meio da demo. Agora ancoram em **hoje**: `seed_deploy_lens_demo.sh`
> grava D-6/D-4/D-2 · `seed_epoch_demo.sh` grava D-4 (v1.0) e D-1 (v2.0) ·
> `seed_customer_history_demo.sh` grava D-11/D-7/D-2 e a jornada aberta em D-1. Todos os três aceitam
> `ANCHOR=YYYY-MM-DD` para pinar.
>
> **Rode-os no dia da demo** (ou no dia anterior), **uma vez cada**: a janela é relativa ao momento da
> execução, então um seed de duas semanas atrás volta a cair fora do período recente. Os três agora
> **limpam as próprias linhas antes de inserir** — sem isso, re-rodar em outro dia deixaria os pontos
> antigos vivos (o `ReplacingMergeTree` só deduplica dentro da partição, e a partição é por data).
>
> ⚠️ **Duas contagens que surpreendem, e nenhuma é defeito** (medidas em 2026-08-12): a série da lente
> `deploy` é do **pool**, e `seed_deploy_lens` + `seed_epoch` escrevem no MESMO `sac_ia` — a curva
> diária mostra a **união** dos dois (4 dias, não 3). E `SkillDeployment` é **append-log**: a limpeza
> apaga linhas do ClickHouse, não deploys do registry, então **cada execução acrescenta um triângulo**,
> todos carimbados com `now()`. Vários markers no mesmo dia = execuções repetidas, não histórico.

### T-1 dia — o Bloco B não tem pré-voo, mas exige ENSAIO

O aumento de limite roda inteiro ao vivo: não há contato a pré-executar nem `curl` fingindo ser a
operadora (era o que a portabilidade obrigava). O que ele exige é ritmo — **percorra os três acessos
duas vezes, cronometrando**. São 8 minutos apertados.

```bash
bash infra/test/smoke_limite_tres_acessos.sh   # 16/0 — o gate do cenário
bash infra/test/probe_journey_limite.sh        # 5/0 — a journey de 3 sessões
```

⚠️ **Telefone novo a cada ensaio, e um virgem guardado para a apresentação.** Duas razões
independentes: o índice de pendências é chaveado por sessão (só a mais recente é lida — número reusado
esconde os pedidos anteriores) e, depois do primeiro OTP bem-sucedido, a âncora fica `possessed`
**durável**, então no ensaio seguinte o fluxo **pula o OTP** e a cena de 16:00 desaparece.

⚠️ **O aprovador tem de ser o `operator`, não o admin.** `admin` está em `masking.supervisor_roles` e
casa a regra `* → plain`: veria tudo em claro, e a cena de mascaramento (17:45) não existiria. O ABAC
`approvals.{operacao,decide}` foi concedido ao `operator@plughub.local` em 2026-08-12
(`infra/seed/seed_auth.py`). Confira antes:

```bash
curl -s -X POST http://localhost:3202/auth/login -H content-type:application/json \
  -d '{"tenant_id":"tenant_demo","email":"operator@plughub.local","password":"changeme_operator"}' \
| python3 -c "import sys,json,base64;t=json.load(sys.stdin)['access_token'].split('.')[1];\
print(json.loads(base64.urlsafe_b64decode(t+'==')).get('module_config',{}).get('approvals'))"
```

Esperado: `operacao` e `decide` em `read_write`. Vazio ou `None` ⇒
`docker compose -f docker-compose.demo.yml run --rm auth-seed`.

⚠️ **As 3 regras de masking do pacote de aprovação** (`session.numero_cartao → last_4`,
`session.cpf_titular → last_2`, `session.limite_solicitado → financial`) são seed-if-absent **por
chave**, e `masking.context_rules` é uma chave só que guarda o array inteiro — em base já semeada elas
não entram sozinhas. Confira em `/config/masking` que existem, para o role `operator`. E **nunca**
ponha o catch-all de `operator` em `hidden`: `applyContextMaskingDynamic` faz `continue` em campo
oculto, derruba `session.dialog_form_id`/`session.decisions`, e **a tela de aprovação some em
silêncio**.

### T-10 min — telas e terminais

- **Janela 1** — Chrome, `http://localhost:5174/console`, logado como `admin@plughub.local`, **sem pool
  marcado ainda** (o cliente precisa cair na fila primeiro). Serve o Bloco A **e** a contraprova de
  masking às 18:30.
- **Janela 2** — `http://localhost:5173/webchat-test.html`, pool `retencao_humano`, **não conectado**.
- **Janela 3** — segunda aba do webchat, pool **`limite_ia`**, para o Bloco B.
- **Janela 4** — `http://localhost:5174/analise/agents`, período **últimos 14 dias** (os seeds gravam
  relativo a hoje desde 2026-08-12; não há mais data de junho para caçar).
- **Janela 5** — ⚠️ **janela anônima** (ou outro perfil do Chrome), `.../console`, logada como
  **`operator@plughub.local`** / `changeme_operator`, para a aprovação do Bloco B. Tem de ser sessão de
  navegador separada: o JWT vive no `localStorage` por origem, e logar como operator na mesma janela
  **derruba o admin** — você perderia a contraprova das 18:30, que é o ponto alto do bloco.
- **Terminal visível** (é parte da narrativa, não bastidor):
  ```bash
  docker compose -f docker-compose.demo.yml logs -f channel-gateway | grep -E 'OTP-DEV|session_id'
  ```

> ⚠️ **Ensaie o OTP no máximo 3×** por número em 15 min — o rate-limit é 3 challenges/900 s por âncora
> (`identity/otp.py:44-48`). E depois do **primeiro** OTP bem-sucedido a âncora fica `possessed`
> **durável**: no ensaio seguinte o fluxo pula o OTP. Ensaie com números descartáveis e **guarde um
> virgem** para a apresentação.

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

### 14:00–22:00 · Bloco B — a journey (aumento de limite de crédito)

> **Por que este cenário e não a portabilidade** (decidido 2026-08-12): ele mostra **três acessos do
> mesmo cliente**, uma **aprovação humana em fila pull** e **dois mecanismos de masking diferentes na
> mesma tela** — e a journey de 3 sessões está *provada*, não suposta
> (`infra/test/probe_journey_limite.sh`, 5/0). A portabilidade fica como plano B ensaiado (§3).
>
> **Tudo ao vivo, sem pré-voo.** Diferente da portabilidade, nada aqui espera 48 h: o aprovador é
> você, na janela ao lado. O preço é ritmo — são 8 minutos apertados; ensaie o percurso duas vezes.
>
> ⚠️ **Um telefone novo por ensaio.** O índice de pendências é chaveado por sessão e só a mais
> recente é lida; reusar o número esconde os pedidos anteriores. E depois do primeiro OTP a âncora
> fica `possessed` **durável** — no ensaio seguinte o fluxo pula o OTP. Guarde um número virgem para
> a apresentação.

| min | Ação | O que apontar |
|---|---|---|
| 14:00 | **Janela 3**: webchat, pool **`limite_ia`** → Conectar → digitar o telefone | Contato novo. *"Nível 1: a interação. O agente resolve identidade antes de qualquer coisa."* |
| 14:30 | Preencher o formulário: cartão, CPF, limite atual, limite solicitado (**12000**) e **CVV** | Um `menu` `interaction: form` — **um turno, cinco campos**. O CVV vem com `masked: true`: vive em `@masked.*`, memória do processo. **Guarde este fato para 18:30.** |
| 15:00 | *"📋 Recebido! Vou registrar seu pedido…"* → **Monitor › Sessions** | Nasceu uma sessão **`webhook`**. *"Nível 3: o processo. Workflow é um canal como outro qualquer — o trigger cria sessão normal, roteada por pool normal. E repare: a coleta aconteceu na sessão do CLIENTE, antes do processo existir. N3 recebe dados; N3 não coleta."* |
| 15:30 | **Janela 3**, mesma aba: escrever qualquer coisa (o cliente "volta") | **Acesso 2.** O agente reconhece a identidade e encontra a pendência — mas **não a revela ainda**. |
| 16:00 | *"Para acessar com segurança…"* → **✅ Verificar meu número** → pegar `[OTP-DEV] … code=NNNNNN` no terminal e digitar | **A plataforma é autoridade de posse de canal, não de identidade de registro.** Com a âncora só `claimed`, o tool devolve `verification_required` **sem revelar se existe pendência**. O form de OTP é um `DialogForm` no `dialog_runner` — mesmo primitivo do wrap-up e do NPS — e o código nunca passa pela mão do agente: gerar/enviar/verificar ficam no `OtpService`. |
| 16:45 | Aparece o menu de continuidade → clicar **📋 Consultar status** | ⚠️ O cartão vem **`***4444`**. Esta é a **primeira** prova de masking, e é de outra natureza: o `context_preview` é uma **allowlist declarativa** — o CPF viaja no `delegate.context` e **não aparece aqui, porque não foi declarado**. Campo não declarado não chega. |
| 17:15 | **Janela 5** (Console logado como **`operator`**): marcar o pool `aprovacao_credito` → o item está na **inbox pull** → **Atender (Pull)** | Mesma inbox, mesmo `DialogFormRenderer` do wrap-up do Bloco A. *"Aprovação não é um módulo — é um `collect` a um pool."* |
| 17:45 | Aba **Contexto**, à direita | Cartão `***4444`, CPF `***25`, valor redigido, badge 🔒 PII. **Segunda** prova, agora de outra natureza ainda: política por **tag × role**, aplicada na leitura. |
| 18:00 | Na coluna central, baixar `limite_aprovado` de **12000 → 9000** e escrever um parecer | O humano **não só aprova: edita**. Guarde o número. |
| 18:30 | **Janela 1** (Console como `admin`), **mesma sessão** → aba Contexto | **A prova em 20 segundos:** mesma tela, mesma sessão, o cartão em **claro**. `*` × `supervisor` → `plain`. *"Mascaramento é config, não código."* E então o remate: *"o CVV vocês não viram em nenhuma das duas janelas — e não vão ver. Aquele não é mascarado por política; ele nunca foi persistido. Dois requisitos diferentes, dois mecanismos diferentes: **este eu escondo de quem não tem papel; este eu esqueci**."* |
| 19:30 | Voltar à **Janela 5** → **Aprovar** | O item **some da fila**, o cartão some do Console e a sessão da análise **fecha**. *"Delegate a pool humano é o último ato da sessão — continuar o processo depois devolveria o item decidido à fila na primeira queda de WS. O processo continua noutra sessão."* |
| 20:00 | **Janela 3**: cliente escreve de novo | **Acesso 3.** Sem menu, sem pergunta: `resume_policy: auto`. *"🎉 Seu aumento foi aprovado — novo limite: **R$ 9.000**."* **O valor que o humano editou chegou ao cliente**, não o que ele pediu. |
| 21:00 | **Analytics › Processos** (`/analise/processos`) | Drill de **3 níveis**: journey → sessions → segments. **Três** sessões sob uma raiz: intake → análise → entrega. ⚠️ **Não filtre por pool `limite_processo`** — a sessão da análise sai com `pool_id = aprovacao_credito` (o delegate reescreve a linha no `ReplacingMergeTree`). Busque pela raiz, ou por `limite_ia`. |
| 21:30 | **A frase honesta** | *"`root_session_id` liga proveniência, e essa herança é transitiva por construção — um workflow disparou outro e a raiz atravessou. O que ligaria contatos **independentes** é `journey_merge`, e nenhum destes skills o chama: a pendência já carrega o `root_session_id`, falta um `invoke`. É lacuna de configuração de fluxo, não de modelo."* Para arquiteto isso vale mais que uma tela perfeita. |

**Se sobrarem 30 s — o melhor momento técnico do cenário, e ele é só narrativa:** entre o acesso 1 e a
decisão, o **mesmo workflow suspenso tem dois retomadores possíveis** — o aprovador (pelo Console) e o
cliente (cancelando). Não é acidente: o cliente pode desistir durante a análise. É seguro pela Camada F
(resume terminal-uma-vez): `SET NX` no topo de `handle_resume` e um registro terminal gravado antes do
consumo, de modo que quem perde a corrida recebe **409 nomeado** — não *"token não encontrado"*.
*"A corrida existe de propósito. O que não pode existir é a mentira sobre quem ganhou."*

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

Depois mostre a lente **`deploy`** — desde 2026-08-12 os seeds gravam em datas **relativas a hoje**,
então **o período não precisa mais ser trocado** (últimos 7–14 dias basta). Diga que é dado semeado,
sem fingir:

- ⚠️ **Nesta lente a entidade é o POOL** (`sac_ia`), não o skill — os agentes ficam desabilitados na
  lista de propósito. *"A unidade da curva é o pool porque o mesmo skill pode rodar em N pools com
  configs diferentes, e deploy é pool-centric."* Marcar o skill devolve gráfico vazio.
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
| Journey não drilla | Vista Processos vazia | Provável **filtro por pool**: a sessão da análise sai com `pool_id = aprovacao_credito`, não `limite_processo`. Busque pela raiz ou por `limite_ia`. Se ainda assim vazio, use Monitor › Sessions — a prova do suspend/resume não depende da tela de journey. |
| Item de aprovação não aparece na inbox | fila `aprovacao_credito` vazia após o acesso 1 | Confira o ABAC do operator (§1, T-1 dia): sem `approvals.operacao` a tela de aprovação **não renderiza e não avisa**. Segunda causa: catch-all de masking em `hidden`, que derruba `session.decisions` — mesmo sintoma mudo. |
| Aprovador vê tudo em claro | cartão sem `***` na aba Contexto | Você está logado como **admin/supervisor** (casam `* → plain`). A Janela 5 tem de ser o `operator`, em sessão de navegador separada. |
| Acesso 3 não entrega | cliente volta e nada acontece | O `disparar_entrega` falhou → o processo caiu em `encerrar_sem_entrega`. Verifique a sessão do pool `limite_entrega` no Monitor. Recuperação: narre o desfecho de falha **alta** (`issue_status` nomeia a causa) — é um bom momento sobre degradação não-silenciosa. |
| **Bloco B inteiro falha** | qualquer coisa acima, sem recuperação rápida | **Plano B ensaiado: a portabilidade.** Pool `portabilidade_ia`, mesmo intake, mesmo OTP, mesmo menu de continuidade. Custa o pré-voo (contato 1 + `curl` de resume na porta INTERNA `/v1/channels/webhook/resume/{token}`, que preserva `decision` — a externa a descarta de propósito). Perde a aprovação humana e o valor editado; mantém identidade, OTP, masking de preview e o drill de journey. |
| Lente vazia | gráfico em branco | Desde 2026-08-12 os seeds gravam relativo a **hoje** — se está vazio, ou o seed não rodou nesta máquina, ou rodou há muitos dias (a janela é do momento da execução). Re-rode o seed; ele limpa e regrava. |
| `quality_criteria` vazia | radar sem dados | Desde 2026-08-12 tem seed: rode `seed_volume_demo.sh`. Se ainda vazio, confira o período (o volume termina no `ANCHOR`, default hoje). |
| Board com todos os agentes iguais | três barras idênticas | O volume não rodou — os perfis diferenciados (Carla/Ana/Bruno) vêm dele. Sem ele o board tem N=1 e não compara nada. |

---

## 4. Item 3 detalhado — cobertura das 10 lentes

São **10 lentes** na UI (`AgentsBenchPage.tsx:38`) + `session_nps`, que só existe no pop-up de detalhe.

> ✅ **`seed_volume_demo.sh` (novo, 2026-08-12) mudou esta tabela**: 200 contatos em 14 dias, com
> atribuição coerente entre as sete tabelas. Rode-o **antes** dos outros dois seeds. Medido: 8/0.
>
> ```bash
> bash infra/test/seed_volume_demo.sh          # N=200, 14 dias, determinístico
> N=500 DAYS=30 bash infra/test/seed_volume_demo.sh
> CLEAN_ONLY=1 bash infra/test/seed_volume_demo.sh   # desfaz
> ```

| Lente | Fonte | Estado para a demo |
|---|---|---|
| `resolution` | `segments` | ✅ **live** (Bloco A) **+ volume** — três humanos com perfis distintos: Carla ~0.91 · Ana ~0.82 · Bruno ~0.68. O board só prova que compara quando os números diferem |
| `sessions_aht` | `segments` | ✅ **live + volume** — AHT inversamente correlacionado à resolução |
| `wrapup` | `segments.issue_status` | ✅ **live** — o form que você preenche, sobre um fundo de 204 disposições |
| `nps` | `session_signal` grain=segment | ✅ **live** — o 9 do cliente, sobre 55 sinais semeados |
| `pause_reason` | `agent_pause_intervals` | ✅ **live + volume** — 94 pausas, 4 motivos da taxonomia real |
| `availability` | `agent_login_intervals` + pausas | ✅ **resolvido pelo volume** — 3 agentes × 10 dias úteis, 8 h/dia. Era ✅ com N=1 e curva pobre |
| `escalation_reason` | `segments` c/ `escalation_reason != ''` | ✅ **resolvido pelo volume** — 26 segmentos com a taxonomia de `agent_activity`. Era ⚠️ "adicione um contato no pré-voo" |
| `deploy` (diário) | `evaluation_finalized` + REST do registry | ✅ `seed_deploy_lens_demo.sh` — relativo a hoje (D-6/D-4/D-2). ⚠️ entidade = **POOL** `sac_ia`, não o skill |
| `deploy` (epoch) | `evaluation_finalized` ⋈ `segments.deploy_version` | ✅ **resolvido pelo volume** — v1.0 com 45 e v2.0 com 40 avaliações, **acima do `min_sample`=30**: o aviso *"Low sample"* sumiu. Era N=6 e saía com o aviso |
| `quality` | `evaluation_results` ⋈ atribuição | ✅ **resolvido pelo volume** — 116 resultados atribuídos por `segment_id`. Era ⚠️ (só via `test_t11_quality_report.sh`, datas literais de junho) |
| `quality_criteria` | `evaluation_dimension_scores` | ✅ **resolvido pelo volume** — 464 notas em 4 dimensões (Acolhimento/Diagnóstico/Resolução/Conformidade). Era **❌ sem seed em todo o repositório**, fora do roteiro por impossibilidade |

**Dívidas de preparo:**

1. ~~**Parametrizar as datas** dos três seeds.~~ ✅ **feito em 2026-08-12** — ancoram em `date -u` com
   override por `ANCHOR=`, e limpam as próprias linhas antes de inserir. Sumiu o passo de mexer no
   seletor de período no meio da demo.
2. ~~**Um gerador de volume.**~~ ✅ **feito em 2026-08-12** — `infra/test/seed_volume_demo.sh`,
   parametrizável por `N`/`DAYS`, determinístico por `SEED`, com conferência que **gateia** (8/0) e
   `CLEAN_ONLY=1` para desfazer. Resolveu de uma vez `availability`, `quality`, `quality_criteria`,
   `escalation_reason` e o `min_sample` do epoch.
3. ~~**Contato transferido para `sac_ia` no pré-voo.**~~ ✅ absorvido pelo gerador (26 segmentos com
   `escalation_reason`, da taxonomia real de `agent_activity`).

**Ordem dos seeds no dia da demo** — o volume primeiro, porque os outros dois escrevem no mesmo pool:

```bash
bash infra/test/seed_volume_demo.sh            # o fundo: 200 contatos, 14 dias
bash infra/test/seed_deploy_lens_demo.sh       # a curva diária + markers
bash infra/test/seed_epoch_demo.sh             # as duas épocas + pendentes
bash infra/test/seed_customer_history_demo.sh  # o Cliente 360
```

---

## 5. O que mais vale percorrer — ranqueado

Para os **30 min de discussão**, com script curto de cada um. Os três primeiros são os que mais
provavelmente mudam a conversa com arquitetos.

**① O MESMO renderer aprovando um deploy (2 min) — agora é uma segunda instância, não uma novidade.**
*(A aprovação humana subiu para o Bloco B em 2026-08-12; este item deixou de ser "o de maior retorno"
e virou a prova de que o mecanismo é genérico.)* Pool `aprovacao_deploy` (`dispatch_mode: pull`) +
`skill_gate_promocao_v1` + form `dialog_promocao_deploy`. Abra e diga: *"esta é a mesma inbox, o mesmo
`DialogFormRenderer` e o mesmo `collect` que vocês viram aprovar um limite de crédito há dez minutos —
só o formulário mudou. Aprovação não é um módulo; é um `collect` a um pool."* Combinado com ⑤ e com a
lente `deploy`, fecha o arco de governança **aprovar → promover → medir**.

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
- [ ] Wrap-up em `detached` aplicado e **um item parqueado** na inbox (plano B do Bloco A)
- [ ] **Janela 5 aberta em sessão anônima, logada como `operator`** — e o ABAC `approvals` conferido
- [ ] As 3 regras de masking do pacote visíveis em `/config/masking` para o role `operator`
- [ ] **Telefone virgem de OTP** anotado num post-it (ensaios feitos com outros números)
- [ ] Seeds rodados **hoje** (as datas são relativas ao momento da execução)
- [ ] `smoke_limite_tres_acessos.sh` 16/0 e `probe_journey_limite.sh` 5/0 na véspera
- [ ] Terminal com `logs -f channel-gateway | grep OTP-DEV` visível e limpo
- [ ] Notificações do SO silenciadas; zoom do browser em 110–125%
- [ ] Aba do webchat do cliente **não fechada** entre os blocos (o NPS depende disso)
