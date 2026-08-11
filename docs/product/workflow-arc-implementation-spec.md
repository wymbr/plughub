# Spec — Arco de workflow: porta externa de resume → transição → duração → remoção do legado

**Status:** proposta, 2026-08-10. Executa o que o ADR
[`adr-journey-session-segment-model.md`](../adr/adr-journey-session-segment-model.md) decidiu (D4, D8, D9)
e fecha o que ele deixou ⏳ (forma física da transição).

**Ordem imposta pela D8**, e ela não é preferência: remover a workflow-api tira a única porta de resume
alcançável de fora sem substituto. A porta vem antes.

> **Este documento nasce de inventário estático, não de leitura de doc.** Duas afirmações que o repositório
> tratava como fato caíram na verificação (§0.1 e §3.1). Toda afirmação abaixo tem `arquivo:linha`; onde
> não tem, está marcada como **não medido**.

---

## 0. O que o inventário mudou antes da primeira linha de código

### 0.1 ⚠️ A "porta externa" não é externa — a separação é de CÓDIGO, não de topologia

O `CLAUDE.md` opõe `POST /channel/webhook/{identifier}` (borda) a `/v1/*` (rede interna). **No repositório
não existe nada que os separe:**

- `/channel/webhook/{slug}` é rota FastAPI do mesmo app — `channel-gateway/main.py:1302`.
- `/v1/channels/webhook/{skill_id}` é rota do mesmo app — `main.py:1387`.
- `docker-compose.demo.yml:1185` publica `8010:8010`. As duas atendem na mesma porta.
- Não há proxy de borda para `/channel`: `platform-ui/vite.config.ts` tem `^/v1/channels` (`:68`) e
  `^/webchat` (`:124`), e nenhuma entrada `/channel`; o `Dockerfile:158/188/232/244` idem; **não existe
  nenhum `nginx.conf` versionado** no repo.
- A única distinção real é o filtro `allowed_origins = frozenset({"external"})` (`main.py:1347`).

**Consequência para a spec:** a Fase 1 não pode prometer "expor o resume na borda", porque **não há borda no
repositório**. O que ela entrega é *paridade de classe de alcance*: uma rota de resume com exatamente o
mesmo tratamento que o trigger externo já tem (mesmo prefixo, mesmo filtro de origem, mesma função de auth).
A topologia é decisão de deploy — e o próprio `CLAUDE.md` já admite que *"nenhum teste alcança a topologia"*.
Isto vira a **Fase 0**, que é uma linha de teste, não de produto.

*Nota de método: eu escrevi no ADR §7/D8 que a rota `/v1/*` "vive no prefixo que não pode estar na borda",
tratando a borda como existente. Era descrição de intenção lida como implementação — a terceira ocorrência
da mesma armadilha nesta semana.*

### 0.2 ⚠️ O `source` do resume é asserido pelo CHAMADOR — e abrir a porta torna isso explorável

`webhook.py:131-141` documenta: o `payload` do corpo é repassado verbatim, e `_terminal_cause` /
`_resume_actor` (`webhook.py:121-163`) derivam dele. Um chamador sem JWT pode declarar
`source: "supervisor:x"` e obter o carimbo `acw_supervisor_closed` no **registro terminal durável de 25 h**
(`_RESUME_TERMINAL_TTL_S = 90000`, `webhook.py:108`) — que é o que o Console mostra ao agente como
*"encerrado por …"*.

Hoje isso é dívida catalogada (`TODO.md` § "`source` do resume é asserido pelo CLIENTE na porta pública").
**Com uma porta externa nomeada, deixa de ser teórica.** É gate da Fase 1, não follow-up dela.

### 0.3 ⚠️ O TTL de `{tenant}:resume_tokens` é do HASH, não do token

Todos os escritores aplicam `EXPIRE` **no hash inteiro** — `skill-flow-service/index.ts:463`,
`webhook.py:1723` (collect), `webhook.py:2152` (delegate em conferência). O hash é **compartilhado por todas
as sessões do tenant**, então o último escritor redefine o prazo de todos os tokens. O prazo por token existe
só como string no 3º campo do valor e é exercido apenas pelo scanner (`webhook.py:2349-2357`).

Enquanto o resume é interno, isto é um defeito de higiene. **Quando o prazo do token vira contrato com um
terceiro** ("seu link vale 48 h"), passa a ser promessa que a plataforma não cumpre: um `collect` de 1 h
escrito depois encurta o token de 48 h. Entra na Fase 1 — é o mesmo lugar onde a porta é aberta.

### 0.4 O que o inventário confirmou sem surpresa

- `POST /v1/workflow/resume` existe e é **dual-path, não proxy** (`workflow-api/router.py:255`): com
  `tenant_id` faz proxy (`:276-284`); sem, reimplementa o caminho PostgreSQL legado (`:296-347`). E **não
  repassa** `Authorization`, `resume_origin`, `pool_id` nem `instance_id` — pelo proxy, todo resume externo
  é o caminho "sem principal".
- **Auth: nenhuma** nessa rota (`router.py:259` só tem `pool=Depends(_pool)`; o app só instala CORS).
- Camada F íntegra: lock `SET NX` (`webhook.py:833-835`), registro terminal (`:896-932`), 404 × 409
  distinguidos (`:953-977`), consumido pelo front (`lib/resume-conflict.ts:66-91`).
- Precedente auto-autenticado real: `GET /survey/{token}` (`main.py:1192`) — token opaco de 24 bytes
  (`survey_web.py:563`), chave Redis própria com TTL de 7 dias (`config.py:33`), posse do token = credencial,
  sem `ChannelEndpoint` e sem header. ⚠️ **A analogia vale para autenticação, não para semântica de
  resposta**: o survey recusa reenvio com **HTTP 200 + `ok:false`** (`survey_web.py:591-596`), enquanto o
  resume distingue 404/409 com corpo estruturado. Não copiar a resposta.

---

## Fase 0 — Nomear a borda, ou parar de afirmar que ela existe

**Uma escolha, e ela é do produto:**

| Saída | O que significa |
|---|---|
| **(a) Declarar que não há borda** | O invariante do `CLAUDE.md` vira *requisito de deploy*, não descrição. O texto muda de "não pode estar na borda" para "quem publicar este serviço tem de publicar apenas `/channel/*` e `/survey/*`". |
| **(b) Construir a borda** | Reverse proxy versionado no repo, com allowlist de prefixos. Arco próprio, não este. |

**Recomendada: (a).** Construir borda no meio de um arco de modelagem junta duas coisas que devem falhar
separadas — e o risco que a borda cobre (o `POST /v1/channels/webhook/pool/{id}` anônimo, `main.py:1004-1041`)
já está nomeado e não piora com este arco.

**Entrega:** `infra/test/probe_edge_surface.sh` — enumera os prefixos que o serviço atende e **declara**
quais seriam publicáveis, reprovando se um prefixo novo aparecer sem classificação. Testemunha, não portão
de produto. Duas fontes reconciliadas: `/openapi.json` (runtime, HTTP) **∪** decoradores no fonte — porque
**WebSocket não aparece no OpenAPI**, e o runtime sozinho declararia que `/ws` não existe.

### ⚠️ A previsão que eu escrevi aqui estava errada, e o erro é o achado

Previ *"publicáveis 2 (`/channel`, `/survey`)"* — número tirado do enquadramento do `CLAUDE.md`, não da
superfície. Enumerando antes de rodar:

| Classe | Prefixos | n |
|---|---|---|
| **externo** | `/channel`, `/survey`, `/webhooks`, `/voice`, `/webrtc`, `/ws`, `/webchat` | **7** |
| **interno** | `/v1`, `/health` | **2** |
| **implícito** (invisível a qualquer enumeração) | `/openapi.json`, `/docs`, `/redoc` | 3 |

Errei por **3,5×** para menos, e sempre no mesmo sentido: esqueci que **metade da superfície externa não é
produto, é infraestrutura de canal** — `/webhooks/*` são callbacks de Meta e Twilio, `/voice/tts/*` é áudio
que o provedor busca, `/ws/*` e `/webchat/v1/*` são o browser do cliente. Nenhum é opcional, e nenhum some
se a borda for construída.

**Consequência para o invariante:** *"nunca expor `/v1/*` na borda"* é **meia regra**. A regra inteira é uma
**allowlist**, e ela tem sete entradas — enunciada como proibição, ela não diz o que fazer, e um deploy que
publique tudo menos `/v1` continua cumprindo a letra enquanto expõe `/docs`. Corrigir o texto do
`CLAUDE.md` faz parte da Fase 0.

**E o achado que a enumeração trouxe de brinde:** `/openapi.json` está **habilitado** (`main.py:468` usa o
default do FastAPI). Publicar o serviço publica o **mapa** das rotas internas — inclusive
`POST /v1/channels/webhook/pool/{pool_id}`, anônima por construção (`main.py:1004-1041`). O probe reporta
isso como testemunha; desabilitar em produção é decisão de deploy, fora deste arco.

**Números previstos para a execução:** `externos=7 · internos=2 · total=9`, zero prefixos sem
classificação, os 3 implícitos respondendo `200`, e a testemunha da rota anônima presente. Um `total` menor
que 9 significa que o parser estático perdeu decorador — o probe reprova sozinho nesse caso, comparando
runtime contra estático nos dois sentidos.

---

## Fase 1 — Porta externa de resume (D8) ✅ **2026-08-10, gate 7/7**

> **As-built — quatro deltas em relação ao que esta seção previa** (detalhe no CHANGELOG):
> **(1)** apareceu uma **quarta** diferença obrigatória: recusar `decision`, não só `source` — é ela que
> separa `task_done` de `acw_*`, logo deixá-la passar daria a um terceiro o poder de marcar como expirado
> o item de um humano. **(2)** `resume_meta` nasceu com **consumidor vivo** (fallback de resolução +
> reinserção no hash para o scanner voltar a enxergar o token), em vez de ser só substrato da Fase 2 —
> chave escrita sem leitor é o modo de falha catalogado desta casa. **(3)** `persistSuspendWebhook` teve de
> ser alargado com `reason` em **duas** declarações (`StepContext` em `executor.ts` e
> `SkillFlowEngineConfig` em `engine.ts`). **(4)** o conserto do TTL foi escrito **errado** na primeira
> versão — `-1` (chave sem expiração) tratado como "infinito a preservar" fez o hash **nunca mais receber
> TTL**; trocar *encurta* por *não expira nunca* é regressão pior, por ser silenciosa. Pego pelo P6 porque
> ele lê o TTL **antes** de julgar.
>
> **Segue aberto:** a porta não confere posse do item de pull (`approver is None` desliga o A5). Decisão e
> os três estados em `TODO.md` § "porta externa de resume × posse do item de pull".

### 1.1 A rota

```
POST /channel/webhook/resume/{token}
```

Simétrica ao trigger em prefixo e em classe de alcance. **Não** passa pelo registro de `ChannelEndpoint`:
não há endereço a registrar — o token não endereça pool nem canal, endereça *execução suspensa*. A posse do
token é a credencial, como no survey.

**Reusa `handle_resume` inteiro** (`webhook.py:_handle_resume_locked`): lock, registro terminal, 404 × 409,
consumo `HDEL`. A porta é fina de propósito — duplicar a máquina da Camada F seria criar a segunda fonte que
o arco anterior gastou seis fases removendo.

### 1.2 Três diferenças obrigatórias em relação à rota interna

1. **`source` deixa de ser asserido pelo chamador nesta porta.** O corpo aceito é reduzido a
   `{tenant_id, payload}`, e `payload.source` é **descartado e substituído** por `external` antes de chegar
   a `_terminal_cause`/`_resume_actor`. Sem principal ⇒ sem carimbo de supervisor. Fecha §0.2 **na porta que
   está sendo aberta**; a rota interna segue como está (arco próprio).
2. **`resume_origin` fixo em `token`.** É o único valor honesto por esta porta — quem chega por aqui chegou
   pelo token, e aceitar `identity`/`same_channel` de um terceiro é deixar o chamador escolher o rótulo
   analítico.
3. **Prazo por token de verdade.** Chave própria `{tenant}:resume_meta:{token}` (JSON, `SET ... EX` com o
   prazo **daquele** token), escrita no suspend, lida com `get` — não `getdel` — no resume. O hash
   `resume_tokens` continua sendo o índice; o prazo sai dele. *Mesmo padrão que o conserto do
   `sequence_index` acabou de usar no `participant_meta`, e pelo mesmo motivo: `get` sobrevive a um segundo
   consumo, `getdel` não.*

### 1.3 O que NÃO entra

- Rotacionar/registrar o token como credencial de endpoint. Ele **já é** capability de uso único (Camada F).
- Mudar a rota interna. Ela continua servindo o Console e o scanner, com principal e ABAC
  (`main.py:1602-1603`).
- Reescrever o `POST /v1/workflow/resume`. Ele morre na Fase 4, e só depois que esta porta existir.

### 1.4 Instrumento e números previstos

`infra/test/gate_external_resume.sh`, espelhando `smoke_resume_terminal_once.sh`:

| # | Caso | Previsto |
|---|---|---|
| P1 | suspend → resume por `/channel/webhook/resume/{t}` | `200`, `session_id` presente |
| P2 | mesmo token de novo | `409` `state="terminal"` |
| P3 | token inexistente | `404` |
| P4 | corpo com `source:"supervisor:x"` | `200`, e o registro terminal grava `external` — **não** `acw_supervisor_closed` |
| P5 | dois resumes concorrentes | um `200`, um `409` `state="in_flight"` |
| P6 | token de 48 h + `collect` de 1 h escrito depois, no mesmo tenant | token de 48 h **ainda vivo** (hoje: morre junto) |

P6 é o único que reprova hoje. **Prever P6 vermelho antes de rodar** — se vier verde de primeira, o teste não
alcançou a condição.

---

## Fase 2 — A transição como primeira classe (D4) ✅ **2026-08-11, probe verde (lente B pareada)**

> **As-built:** o desenho de §2.1 valeu inteiro (tabela própria, chaveada pelo `resume_token`, dois
> escritores com o resume mandando a linha completa). Três deltas: **(1)** nenhum tópico novo — o
> `session_suspended` já ia a `conversations.events` e o bridge já tinha token/prazo em escopo; **(2)**
> `date` particiona pelo **início** da lacuna (o fim separaria abertura e fechamento em partições
> diferentes na virada do mês, e o RMT nunca as deduplicaria); **(3)** o `date`/`row_version` são
> derivados no row builder, nunca recebidos do parser — duas fontes para a chave de partição é como se
> ganha uma linha que não substitui a anterior.
>
> **Medido:** `suspend_reason=input · outcome=resumed · resume_origin=token · lacuna=1 014 ms`, com
> `versoes=2 outcomes=['resumed','open']` na lente sem `FINAL`.

### 2.1 A forma física — decidida aqui, que o ADR deixou ⏳

Três candidatos: colunas em `segments` · tabela nova · evento próprio.

**Decisão: tabela própria `analytics.session_transitions`, `ReplacingMergeTree`, chaveada pelo
`resume_token`.**

O argumento que decide não é gosto: **o token JÁ é a identidade da lacuna.** Ele nasce no suspend, vive
exatamente enquanto a transição está aberta e morre no resume (`HDEL`, `webhook.py:1205`). Chavear por
`(tenant_id, session_id, resume_token)` não inventa identidade nenhuma — nomeia a que existe. Colunas em
`segments` recairiam no que a D4 recusou (fato do futuro pendurado no passado), e evento sem tabela repete
`workflow_events`, cujo produtor morreu e deixou a tabela vazia (§8.1 do ADR: **zero linhas**).

⚠️ **Invariante RMT — o escritor do resume manda a LINHA INTEIRA.** É o erro que o `CLAUDE.md` marca como
origem de três bugs de `sessions` num dia. Ele consegue: `resume_meta` (Fase 1.2) é lido com `get` **antes**
do `HDEL` e carrega `suspend_reason`, `step_id`, `expires_at`, `opened_at`. Sem a Fase 1, este escritor não
tem como cumprir o invariante — **é por isso que a ordem D8 é técnica, não burocrática**.

### 2.2 Colunas

| Coluna | Origem | Hoje vive em |
|---|---|---|
| `resume_token` | suspend | `{tenant}:resume_tokens` |
| `session_id`, `step_id` | suspend | idem |
| `suspend_reason` (`approval\|input\|webhook\|timer`) | suspend | **só em `workflow_events`, tabela vazia — net new** |
| `resume_expires_at` | suspend | **só em Redis, sem report — net new** |
| `suspended_at` / `resumed_at` | suspend / resume | derivável de `segments`, passa a ter nome |
| `resume_origin` | resume | evento `session_resumed`, sem persistência analítica |
| `outcome` (`resumed\|expired\|cancelled`) | resume ou scanner | — |

`segments` não ganha coluna nenhuma. `sessions` também não.

### 2.3 O que a transição destrava, e que hoje se adivinha

- *"Por que esta sessão está parada, e até quando?"* — pergunta sobre o presente, hoje respondida lendo o
  segmento **anterior**.
- **A atribuição de agente em qualidade.** `_session_agent_attribution_sql` (`reports_query.py:2183-2209`)
  credita a sessão ao **último** primário: na `f1ecc571` isso é o segmento nativo de **10 ms** que só
  processou o resume, não o humano que trabalhou 3,9 s. Com a lacuna nomeada, dá para atribuir a quem
  trabalhou. *(Achado de 2026-08-10, registrado no ADR §2.3 e no `TODO.md`.)*
- ⚠️ **Não destrava contagem de ciclos por contagem de segmentos** — a composição varia **entre duas
  execuções do mesmo caminho** (medido: mesma smoke, mesmo pool, `queue` numa e não na outra). A transição
  substitui a contagem; não a conserta.

### 2.4 Instrumento

`infra/test/probe_transition.sh` sobre um ciclo real (`smoke_formfill_renderer.sh` + claim/submit).
**Previsto:** `1` linha de transição, `suspend_reason` não-vazio, `resumed_at − suspended_at` ≈ a lacuna
entre `ended_at` do segmento 1 e `started_at` do segmento 3 (na sessão medida, **6 min 23 s**), `outcome =
resumed`. Uma linha com `suspend_reason` vazio é reprovação, não ruído: significa que o escritor do resume
não reidratou.

---

## Fase 3 — D9: duas grandezas, dois nomes ✅ **2026-08-11 (probe verde, 1 052 ms × 29 ms)**

> **As-built:** os dois nomes saem nos dois grãos; `handle_time_ms`/`business_duration_ms` ficam como
> **alias de compat** (UI migra em fatia própria); o quarto nome (`duration_ms` em
> `/sessions/customer/{id}`) morreu; a afirmação falsa do `CLAUDE.md` foi removida com o motivo duplo
> (falsa **e** conceitualmente errada).
> **Limite:** o FILTRO do tempo-agente não foi coberto — a sessão medida não tem segmento de fila para
> excluir, então `agent == soma bruta` por ausência, não por acerto. Cobrir pede sessão de push.

### 3.1 ⚠️ O inventário achou TRÊS comportamentos vivos, não dois — e um quarto nome

O ADR §8.1b diz "duas vivas". **Subestimou.** O mesmo nome `handle_time_ms`:

1. **coluna crua** — `query.py:94`, `admin_query.py:115/168`, `timeseries_query.py:279`, `reports_query.py:708`
2. **recomputado por canal** — `reports_query.py:647-657`: webhook ⇒ `closed_at − min(segments.started_at)`
3. **wall-clock ao vivo** — `sessions.py:127`: `now − opened_at`, sem tocar a coluna
4. **e sai da API com outro nome** — `sessions.py:268-279` renomeia para **`duration_ms`** em
   `/sessions/customer/{id}`, consumido pela `HistoricoTab.tsx:204`

**A afirmação do `CLAUDE.md` (*"TMA webhook = `SUM(segment.duration_ms)`"*) é falsa**, e o comentário
adjacente ao código diz o oposto: `reports_query.py:645-646` — *"Inclui as esperas (suspends) — é a duração
real do caso"*. Nenhuma query no repositório soma `segments.duration_ms` por sessão para produzir handle
time. O refino está registrado como adiado em `reports_query.py:898-899`, no grão **journey**.

⚠️ **Os dois grãos já discordam por decisão, não por descuido:** sessão **inclui** o suspenso de propósito
(`:645-646`); journey registra **excluir** o suspenso como refino pendente (`:898-899`). Unificar é escolher
um dos dois — e a §7 do ADR existe para que não discordem.

⚠️ **"`handle_time_ms` é NULL para webhook" é EMPÍRICO, não derivável.** `models.py:319-330` produz NULL só
nas condições listadas; não há caminho no código que garanta que uma sessão webhook nunca receba um
`contact_closed` com `started_at`+`ended_at` válidos. **Medir antes de desenhar em cima disto.**

### 3.2 Os dois nomes

| Nome novo | Unidade | Fórmula | Substitui |
|---|---|---|---|
| **`agent_time_ms`** | agente × tempo | `Σ segments.duration_ms` com os filtros abaixo | nada — é net new |
| **`elapsed_time_ms`** | tempo | wall-clock: sessão `closed_at − início`; journey `min(opened) → max(closed)` | os 3 comportamentos de `handle_time_ms` + o `duration_ms` de `sessions.py:287` |

```sql
-- filtros de agent_time_ms, com precedente vivo em reports_query.py:3725-3743 (busy_ms)
agent_type != 'system'                 -- sintético: outage, mute queue. Zero recurso.
AND role IN ('primary','specialist')   -- exclui 'queue': espera não é trabalho
AND duration_ms IS NOT NULL            -- segmento aberto some silenciosamente no sum()
```

⚠️ **`agent_time_ms` NÃO é uma duração e a UI não pode rotulá-la como tal.** Segmentos se sobrepõem por três
mecanismos (`@mention` é **sempre** paralelo e é rotina; especialista de conferência nasce dentro da janela
do primary; hooks posatt são paralelos entre si). Logo `Σ ≥ wall-clock` com sobreposição e `Σ ≤` com lacunas.
**Nunca comparar as duas, nunca somar uma para obter a outra.** Não existe no repositório query que faça
união de intervalos — e escrever uma não está neste arco.

### 3.3 Escopo, e o que fica de fora

**Entra:** os dois nomes nos dois grãos (sessão e journey — consertar só um deixaria os níveis
discordando); `handle_time_ms` fica como **coluna de armazenamento**, marcada obsoleta, sem leitor novo;
o quarto nome (`sessions.py:287`) morre.

**Não entra:** união de intervalos; mudar o que `avgOrNull` faz com webhook — depende da medição de §3.1, e
hoje a exclusão é **efeito colateral de a coluna ser NULL**, não decisão. Vira item com dado, não sem.

**Migração de UI:** 3 telas para `elapsed_time_ms` (`ListaTab.tsx:232`, `AnaliseTab.tsx:71/348`,
`SessionList.tsx:59`) + `AnaliseJourneysPage.tsx:456` + `HistoricoTab.tsx:204`. **Não** tocar
`avg_duration_ms` / `aht_ms` / `busy_ms`: são grão **segmento** e já estão certos.

### 3.4 Instrumento

`infra/test/probe_duration_definitions.sh` sobre a mesma sessão dos outros probes.
**Previsto para `f1ecc571`:** `elapsed_time_ms` ≈ **5 min 29 s** (`closed_at − primeiro segmento`);
`agent_time_ms` = `21 + 3916 + 10` = **3 947 ms** (o `queue` de 2 620 ms **fora**, por filtro). Razão ≈ **83×**.
Se as duas vierem iguais, o filtro não foi aplicado.

---

## Fase 4 — Remoção do legado ⚠️ **REESCRITA 2026-08-11: é MIGRAÇÃO, não remoção**

> ### O erro desta seção, e ele é meu
>
> A v1 dizia *"a remoção ficou barata: tudo zero, sem backfill"*. Isso vale para o **DADO** — §8.1 do ADR
> mediu `instances=0`, `webhooks=0`, `collects=0`, `workflow_events` vazia. **Eu estendi a conclusão ao
> CÓDIGO sem verificar**, e o inventário de chamadores (2026-08-11) mostra o contrário: cinco frentes com
> chamador VIVO, incluindo **um serviço de produção** e **o `skill-flow-worker` batendo nas rotas que já
> devolvem 410**.
>
> *Tabela vazia não implica rota morta.* Uma rota pode ter chamador vivo e não persistir nada — foi
> exatamente o caso do proxy de resume, que atravessa para o channel-gateway sem tocar PostgreSQL.
> Remover como pacote repetiria o erro que a Fase F desfez: tratar como pacote coisas que só estão
> adjacentes.

### Inventário de chamadores (o que decide a ordem)

| Rota | Chamador VIVO | Consequência |
|---|---|---|
| `POST /v1/workflow/resume` | **evaluation-api** (`router.py:312`, usado em `:2269`/`:2388`) | sempre manda `tenant_id` ⇒ só o ramo **proxy**; o ramo legado PG só tem e2e |
| `POST /v1/workflow/trigger` | `WorkflowEditorPage.tsx:113` (rota `/workflow/editor` viva) | UI precisa migrar antes |
| `GET /instances`, `/instances/{id}` | 4 telas + `agent-registry/skills.ts:501` + **`skill-flow-worker/worker.ts:230`** | leitura de processo; substituto = substrato de sessão |
| `GET /instances/{id}/sessions` | `AnaliseProcessosPage.tsx:311` | idem |
| CRUD `/v1/workflow/webhooks*` | `WebhooksTab`, montada em `/workflow/calendar` **e** `/workflow/triggers` | ⚠️ a rota **não é órfã** — a v1 desta spec afirmava que era |
| 410: persist-suspend, complete, fail, collect/persist | **`skill-flow-worker/workflow-client.ts:79,90,104,120`** | remover troca 410 por 404 **sem eliminar a chamada** — consertar o worker ANTES |

**Genuinamente sem chamador (removível hoje):** `POST /admin/backfill-events` (`:846`) ·
`GET /campaigns/{id}/collects` (`:562`, só e2e) · `POST /v1/workflow/collect/respond` (`:542`, só e2e) ·
os componentes órfãos `WorkflowsPage.tsx` e `WorkflowMonitorPage.tsx` (não referenciados por `routes.tsx`).
`POST /v1/workflow/webhook/{id}` não tem chamador **in-repo** — mas o risco residual é **externo**
(integrador real), e isso o código não pode responder.

### Sub-fases, cada uma com gate próprio

- **4a — repontar a evaluation-api** ✅ *(feita 2026-08-11)*: passa a chamar
  `POST /v1/channels/webhook/resume/{token}` no channel-gateway, direto. É serviço INTERNO, então usa a
  porta interna — a externa (Fase 1) é para terceiros. Depois disto `/v1/workflow/resume` não tem mais
  chamador de produção.
- **4b — desacoplar o `skill-flow-worker`** das 4 rotas 410 (`workflow-client.ts`). Enquanto ele as
  chamar, removê-las só troca o código de erro.
- **4c — migrar a UI de processos** (`/instances*`) para o substrato de sessão (`channel='webhook'` +
  `session_transitions`). É a fatia CARA e é trabalho de produto, não de remoção.
- **4d — remover** o que sobrar, incluindo `WebhooksTab` e as duas rotas que a montam.

⚠️ **`installation_id` / `organization_id`** da `WorkflowInstance` seguem sem decisão (ADR §9) — não
portar automaticamente.

**Remover:** `workflow.webhooks` + CRUD + `POST /v1/workflow/webhook/{id}`; `workflow.instances` e o
lifecycle 410; `POST /v1/workflow/resume` (substituído pela Fase 1) e `POST /v1/workflow/trigger`;
`WebhooksTab` + rota órfã `/workflow/calendar`.

⚠️ **Não remover junto, e a razão é a mesma que a Fase F já pagou:** o tópico `workflow.events` e o
`skill-flow-worker`. O e2e **18** depende do worker, e a evaluation-api **consome** `workflow.events`
(motor de review legado, reactive-only). Adjacência não é pacote.

**Gate:** `probe_webhook_endpoint_inventory.sh` já reprova com `F3 > 0`; depois da remoção `F3` é
estruturalmente `0` e a checagem vira testemunha.

📄 **Doc que sai junto:** `docs/guias/webhook-patterns.md` § "Padrão 1" ainda ensina o caminho legado a
integradores. Reescrever para o `ChannelEndpoint` **é parte da remoção**, não follow-up.

---

## 5. O que esta spec NÃO decide

- **Construir a borda** (Fase 0 opção (b)) — arco próprio.
- **`source` asserido na rota INTERNA** — a Fase 1 fecha só a porta que abre.
- **Mudar a regra de atribuição de agente** para usar a transição. A Fase 2 dá o substrato; usá-lo é fatia
  da lente de qualidade, com números de qualidade a medir antes.
- **`installation_id` / `organization_id`** da `WorkflowInstance` — sem equivalente em sessão, suspeita de
  vestígio de multi-tenancy. Decisão explícita, nunca porte automático.
- **União de intervalos** para uma duração "sem sobreposição". Nomeado como não-objetivo em §3.2.

---

## 6. Ordem, e por que ela não é negociável

```
Fase 0 (borda: declarar) → Fase 1 (porta + resume_meta) → Fase 2 (transição) → Fase 3 (duração) → Fase 4 (remoção)
```

- **1 antes de 4** — D8: remover a workflow-api sem a porta tira a única via de resume externa.
- **1 antes de 2** — o escritor do resume só cumpre o invariante RMT (linha inteira) se `resume_meta`
  existir. Sem ela, a Fase 2 nasce com o defeito que o `CLAUDE.md` mais catalogou.
- **2 antes de 3** — sem a transição nomeada, "tempo suspenso" continua sendo derivação implícita, e a D9
  voltaria a ser um terceiro comportamento de um nome já sobrecarregado.
- **3 antes de 4** — a remoção apaga `workflow_events`, que é o único portador atual de `suspend_reason`.
  Apagá-lo antes de a Fase 2 escrever o substituto perde o campo (hoje **vazio**, então o custo é do
  desenho, não do dado).
