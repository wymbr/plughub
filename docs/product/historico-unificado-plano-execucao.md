# Histórico unificado — análise de estado e plano de execução

> Levantado em 2026-08-14, **por leitura de código**, antes de qualquer sessão de trabalho.
> Desenho: [`../adr/adr-historico-unificado-duas-visoes.md`](../adr/adr-historico-unificado-duas-visoes.md) ·
> Telas: [`historico-unificado-telas-design.md`](historico-unificado-telas-design.md) ·
> Kickoff de F0: [`historico-unificado-kickoff.md`](historico-unificado-kickoff.md).
>
> Este documento **não substitui** o ADR. Ele registra (a) que o estado do repositório diverge do que
> ADR/TODO/kickoff afirmam, (b) o que essa divergência muda no escopo, e (c) o plano por sessão.

---

## 1. O achado que reordena o arco

**F0 e F1 estão implementados no repositório, e nenhum dos três documentos sabe disso.** O `TODO.md`
diz *"nada implementado"*, o ADR está *"proposto"*, o kickoff manda medir o defeito antes de construir
— e o defeito não está mais lá.

| Fase | Estado declarado | Estado no código | Evidência |
|---|---|---|---|
| **F0.1** — gate do `collect` | "não honra `customer_resumable`" | **implementado** | `channel-gateway/adapters/webhook.py:1956-2010` (dual-write gated) + `main.py:998-1004` (o endpoint parou de descartar os campos) |
| **F0.2** — `parquear_resultado` → `collect` | "usa `delegate`" | **migrado**, datado 2026-08-12 | `skill_limite_entrega_v1.yaml:34-80` — `type: collect`, `customer_resumable: true`, `resume_policy: auto`, `timeout_hours: 168` |
| **F1** — `journey_merge` no intake | "pendente" | **implementado** no cenário de referência | `skill_limite_entrada_v1.yaml:362-372` (`unificar_journey`), no ramo `policy == "auto"` |
| **F1** — no intake de portabilidade | pendente | **ausente** | `agente_portabilidade_intake_v1.yaml` — zero ocorrências de `journey_merge`; vai de `avaliar_politica_retomada` direto a `retomar_processo` |

Nada disso aparece no `CHANGELOG.md` (grep de `handle_collect` / `customer_resumable` /
`parquear_resultado` não retorna entrada de agosto). A leitura mais provável é que a sessão de F0 rodou
em 2026-08-12, escreveu o código e **acabou antes de registrar e validar**.

> **Consequência de método, não de escopo:** o arco não começa em F0, começa numa **medição**. Código
> escrito não é código que roda — o `collect` só entra em vigor se o snapshot do slot `current` do pool
> `limite_entrega` tiver sido re-tirado, e o serviço só executa o gate se a imagem tiver sido
> reconstruída. Os dois são aplicadores separados da fonte, e os dois falham por **sucesso pelo caminho
> antigo**.

### 1.1 A emenda que o as-built força no ADR

O `collect` como construído é **LAZY** (`webhook.py:1818-1838`): entrega o convite, suspende, e **não
cria sessão nem aloca recurso**. A sessão-filha só nasce em `handle_collect_engage`, e é lá — e só lá —
que `spawn_reason='collect'` é escrito (`webhook.py:2076`).

Três coisas mudam em relação ao que ADR e telas-design assumem:

1. **Decisão aberta #1 está respondida por ausência.** *"`collect` que expira sem engajamento conta como
   contato?"* — não conta, porque **não existe**. Não há linha, não há sessão, não há o que excluir. O
   próprio YAML já registra isso (`skill_limite_entrega_v1.yaml:46-50`). Falta transportar a resposta
   para o ADR §4 e para o cabeçalho da visão 2 (telas-design §2, linha *"saída ativa (`collect`,
   expirada)"* — essa linha **não vai existir**).
2. **"A perna do output como sessão" só se materializa se o cliente engajar.** O ADR lista isso como
   entrega de F0 sem ressalva; o as-built entrega *condicionalmente*. Para o cenário de referência, cujo
   retorno real é o **espontâneo por identidade**, a perna outbound nunca aparece.
3. **A "prova de saída" para o caso não-engajado deixa de ter linha própria.** O que resta é o par
   honesto de D9 — `(emitiu?, close_reason)` no contato anterior — mais o `suspend` do workflow chamador,
   que vive em `session_transitions` e não tem superfície. Isso é decisão de exibição de F4, e precisa
   estar escrita antes, senão vira "bug" na revisão da tela.

Nenhum dos três exige código novo. Os três exigem **texto** — e é mais barato escrevê-los agora do que
descobri-los renderizando.

---

## 2. Escopo remanescente

| Fase | O que é | Depende de | Tamanho |
|---|---|---|---|
| **S0** | medir/validar/registrar F0+F1 · fechar decisão aberta #1 | — | ½ sessão |
| **F1b** | `entrou por`: first-write-wins em `sessions.pool_id` | independente | 1 sessão (o inventário é o custo) |
| **F2** | `root_session_id` em `/reports/segments` + achado 6 (ABAC) | independente | ½ sessão |
| **F3** | visão 1 — lista de contatos, chip, direção | F1b | 1–2 sessões |
| **F4** | visão 2 — pivô, lente A/B, internas dobradas | F2, F3 | 2 sessões |
| **F5** | `ContextStorePersister` | nenhuma | fase própria, adiável |
| — | `journey_merge` no intake de portabilidade | — | fatia pequena, **fora do caminho crítico** |

**Ordem recomendada:** S0 → F2 → F1b → F3 → F4 → F5.

F2 sobe na frente de F1b (o ADR os lista ao contrário) por três razões: é a menor, é backend puro sem
consumidor a quebrar, e é a **única** que destrava F4 — enquanto F1b só destrava um filtro de F3. Trocar
a ordem custa zero, porque não há dependência entre as duas.

---

## 3. Plano por sessão

### S0 — ✅ CONCLUÍDA 2026-08-14

Resultado, contra as previsões escritas antes de rodar:

| Medição | Previsto | Medido | |
|---|---|---|---|
| git | F0 na árvore | **commitado** (`774b257`, `43ab761`) | ✅ |
| gate no container | `1` + testemunha `3` | `1` + `3` | ✅ *(depois de consertar o caminho)* |
| slot `current` | `type: collect` | `type: collect`, 13/08 15:50Z | ✅ *(depois de consertar o `jq`)* |
| `probe_journey_limite` | `5/0` | `5/0` | ✅ |
| `smoke_limite_tres_acessos` | `16/0` | **`18/0`** | ⚠️ o registro estava velho |
| `session_count` | **3** | **4** | ❌ previsão errada — a 4ª é o **alias**, não filha do collect |
| `spawn_reason='collect'` | 0 | 0 *(e `delegate` também 0)* | ✅ + achado |

A previsão de `session_count` errou por eu ter esquecido o merge: o `collect` de fato não materializa
sessão (o lazy se confirmou), mas F1 já estava trazendo o acesso espontâneo por `journey_aliases`. As
duas afirmações eram verdadeiras ao mesmo tempo, e só a soma estava errada.

Registro aplicado em `CHANGELOG.md`, `TODO.md`, ADR (§4, §7, status), telas-design §2 e kickoff.

<details>
<summary>Procedimento original de S0 (mantido para reuso)</summary>

Objetivo: transformar "o código está lá" em "o comportamento está em vigor, e está escrito onde se
procura". Sem isto, toda medição posterior herda um ambiente cuja versão ninguém conhece.

**S0.1 — o repositório**

```bash
git -C ~/projects/plughub status --short
```

```bash
git -C ~/projects/plughub log --oneline -8
```

Três ramos, e nenhum é falha: **committed** → só falta CHANGELOG/TODO · **modificado não commitado** →
a sessão de F0 morreu antes do commit; conferir se compila antes de qualquer medida · **ausente do
`git status` e do log** → estou lendo outra árvore, **inconclusivo**, parar.

**S0.2 — o que RODA (não o que está no repo)**

Preflight com contador-testemunha ao lado do contador de ausência — casar o **token da chamada**, nunca
o identificador (o comentário que documenta a mudança reescreve a palavra):

```bash
DC="docker compose -f docker-compose.demo.yml"
```

```bash
$DC exec -T channel-gateway grep -c "collect dual-write" \
  /app/src/plughub_channel_gateway/adapters/webhook.py
```

```bash
$DC exec -T channel-gateway grep -c "write_pending" \
  /app/src/plughub_channel_gateway/adapters/webhook.py
```

**Previsão (escrita antes de rodar):** 1º comando → **1**; 2º comando → **3** (delegate, collect,
conference). `0` no primeiro com `2` no segundo = a imagem é anterior a F0.1 ⇒ `build` + `up -d`
channel-gateway. `0` nos dois = o leitor está errado (caminho), inconclusivo.

**S0.3 — o slot, que é o aplicador separado**

Editar o YAML do skill **não publica nada**, e republicar `skill.flow` não re-snapshota o slot que o
bridge executa.

```bash
curl -s -H "x-tenant-id: tenant_demo" \
  http://localhost:3300/v1/pools/limite_entrega/slots | jq -r '.current.yaml_snapshot' \
  | grep -c "type: collect"
```

**Previsão: 1.** Se `0`, o pool ainda roda o `delegate` — aplicar com
`infra/scripts/deploy_skill_to_slot.sh` antes de medir qualquer coisa. Se o campo vier nulo, o slot
nunca foi preenchido: **inconclusivo**, não "zero".

**S0.4 — os dois gates do arco**

```bash
bash infra/test/probe_journey_limite.sh
```

```bash
bash infra/test/smoke_limite_tres_acessos.sh
```

**Previsão: 5/0 e 16/0.** F0 não pode regredir o cenário. Vermelho aqui **antes** de qualquer trabalho
novo é o resultado mais valioso da sessão — significa que F0 entrou incompleto.

**S0.5 — a asserção nova, e a decisão aberta #1**

```bash
curl -s "http://localhost:3500/reports/journeys?tenant_id=tenant_demo&root_session_id=<RAIZ>" \
  | jq '{session_count, internal_session_count}'
```

**Previsão revisada pelo as-built: `session_count: 3`**, não 4 como o kickoff previa. O kickoff foi
escrito supondo que o `collect` materializa sessão-filha ao disparar; ele é lazy, e sem clique não há
sessão. Ramos:

- **3** → confirma o lazy e **fecha a decisão aberta #1 por ausência**. Registrar no ADR §4 e podar a
  linha da saída expirada do telas-design §2.
- **4** → a sessão-filha materializou; **medir o que a criou** antes de tratar como certo, porque
  contradiz `webhook.py:1818-1838`.
- **5+** → o retorno espontâneo está criando contato **além** do filho do collect. É o caso que decide
  se F1 cobre tudo — investigar antes de seguir.

**Entregáveis de S0** (o registro é entregável tanto quanto o código):
`CHANGELOG.md` com a entrada de F0+F1 · `TODO.md` com as fases marcadas e o cabeçalho corrigido ·
ADR §4 decisão #1 fechada · ADR status de *proposto* para *parcialmente implementado* ·
telas-design §2 sem a linha da saída expirada.

</details>

**Dívida que S0 abriu e não fechou** (nenhuma bloqueia F2):

1. **`probe_journey_limite.sh` não pode reprovar na dimensão de F1** — conta por proveniência. Um gate
   de merge é query própria sobre `journey_aliases`, não este arquivo.
2. **`spawn_reason='delegate'` tem zero linhas** e o carimbo existe (`webhook.py:1604`). Não explicado.
   Medir quando F4 precisar da classe "interno" — antes disso é curiosidade.
3. **`set_at` idêntico entre `current` e `previous`** no `limite_entrega`: dois deploys
   indistinguíveis na lente `deploy`. Item próprio, fora deste arco.

---

### F2 — ✅ CONCLUÍDA 2026-08-14

Entregue como planejado, com duas emendas medidas: o filtro é **subconsulta** (a coluna vive em
`sessions`, não em `segments`) e o **achado 6 foi descartado por ausência de amostra** (723 segmentos
com pool, 0 vazios) — `_apply_pool_scope` não foi tocado. Gate novo:
`infra/test/probe_segments_journey_window.sh` (6/0). Placar das previsões: `A=4·B=4·C=0·D=3` certos;
errei a raiz (o probe escolhe a mais recente, que passou a ser a do smoke) e o total de asserções
(previ 7, são 6). A primeira corrida saiu VERMELHA por defeito do **leitor** — `jq //` convertendo o
`false` legítimo de `window_applied` em "ausente".

**Dívida aberta:** `/reports/journeys` publica `from_dt`/`to_dt` que não filtram quando há
`root_session_id` e não tem marcador `window_applied`. Mesma mentira, não corrigida de carona.

<details>
<summary>Plano original de F2 (mantido)</summary>

Menor fase do arco e a única que F4 não pode contornar.

**Estado medido:** `query_segments_report` (`reports_query.py:2004-2021`) **não aceita**
`root_session_id`; `_fetch_segments` aplica a janela `started_at` **incondicionalmente**
(`:2051-2055`), e `session_id` é só mais um filtro, sem isenção (`:2058-2060`). O router
(`reports.py:453-469`) idem.

**Trabalho:**

1. Aceitar `root_session_id` no router e em `query_segments_report`, replicando o padrão **parametrizado**
   de `_fetch_journeys` (`:965-973`: `s.root_session_id IN {jroots:Array(String)}` + `params["jroots"]`).
   **Preferir esse ao inline de `_fetch_sessions`** (`:544-548`, que interpola os membros na string) —
   mesmo resultado, sem concatenação.
2. **Isentar a janela** quando `root_session_id` estiver presente, exatamente como `_fetch_journeys`
   faz no `else` (`:974-976`). Sem isso a journey que atravessa semanas volta truncada em silêncio
   (achado 5).
3. **Achado 6, no mesmo passe:** `_fetch_segments` chama `_apply_pool_scope` (`:2073`), que emite
   `pool_id IN (...)` sem o `OR pool_id = ''` que `_fetch_sessions` usa (`:601`). Segmento não roteado
   some para supervisor restrito, e as duas lentes sobre o mesmo processo devolveriam árvores
   diferentes conforme o usuário. **Julgar antes de corrigir**: `_apply_pool_scope` é compartilhado —
   ver quem mais o chama (`_fetch_pools_volume:5247`, `_fetch_session_complexity:4788`) antes de mudar
   a função; pode ser que a correção certa seja um parâmetro, não uma mudança global.

**Gate:** uma journey conhecida com contato fora da janela de 7 dias tem de devolver **todos** os
segmentos com `root_session_id`, e o mesmo número com e sem `from_dt`/`to_dt`. Prever o total contado,
não "mais que antes".

</details>

---

### F1b — `entrou por`: first-write-wins em `sessions.pool_id` *(próxima — sessão própria)*

> **Kickoff pronto:** [`historico-unificado-f1b-kickoff.md`](historico-unificado-f1b-kickoff.md).
> Achado que entrou no kickoff e não estava aqui: **o conserto é forward-only** — age no ingest, e as
> linhas históricas divergentes não saram (RMT conserva a última versão escrita). O gate tem de medir
> sessão NOVA; contar a população histórica sairia vermelho para sempre e pareceria fix quebrado.

**Não é adicionar campo; é parar de apagar um que já existe.** `parse_inbound` já escreve o pool de
entrada (`models.py:124`), e `pool_id` já está em `_IDENTITY_FIELDS` (`consumer.py:112-114`). O que o
destrói: `_learn_session_identity` faz `if value: entry[field] = value` (`:133-136` — a cache segue o
último) e `_inject_session_identity` só preenche ausência (`:152-154`). O padrão correto está no mesmo
arquivo, aplicado a `opened_at` (`:137-139` e `:155-160`), sob *"A abertura é imutável"*.

**O custo desta fase é o inventário, não a mudança.** Dar significado a uma coluna que hoje não tem
nenhum quebra em silêncio quem dependia do acidente. Levantado por leitura (2026-08-14), há **~40
pontos de leitura**; os que exigem julgamento explícito antes de virar a chave:

| Leitor | Por que exige decisão |
|---|---|
| `reports_query.py:707-714` + `:779` | `_fetch_sessions` já **não confia** em `sessions.pool_id`: recupera `argMin(pool_id, started_at)` de `segments` e devolve `COALESCE(NULLIF(s.pool_id,''), _pool.pool_v)`. Com first-write-wins, esse fallback vira redundante — ou concorrente. **Decidir qual dos dois é a fonte**, não deixar os dois. |
| `reports_query.py:5408` | `_fetch_pools_queue` faz `if(ss.pool_id != '', ss.pool_id, segs.q_pool)` — mesmo padrão, mesma pergunta. |
| `timeseries_query.py:26` | `pool_id` é **dimensão de breakdown** (`_VALID_BREAKDOWN`) — a série temporal muda de significado; é o efeito desejado, mas tem de ser declarado, não descoberto. |
| `models.py:319` | `contact_open` escreve `"pool_id": ""` literal. Vazio não ensina (`if value`), então hoje é inócuo — confirmar que continua inócuo com a regra nova. |
| `models.py:184-186` | comentário existente já nomeia o defeito (*"overwrites the contact's real pool_id with the specialist pool"*) — é a mesma causa, vista do outro lado. |
| `display_formatters.py`, `admin_query.py`, `sessions.py`, `dashboard.py` | agregam/filtram por pool; **é aqui que a atribuição de volume por pool passa a mudar de valor** (`limite_processo` aparece com 1 sessão tendo sido porta de entrada de 20). |

**Medição obrigatória antes e depois** — as duas leituras lado a lado, afirmando divergência, e o número
previsto por extenso: hoje **46 sessões em 314 (14,6%)** divergem, em 5 pares que somam exato
(`limite_processo→aprovacao_credito` 19 · `wrapup_detached_ia→retencao_humano-int` 12 ·
`sac_ia→retencao_humano` 12 · `formfill_demo_ia→formfill_demo` 2 · `gate_promocao_ia→aprovacao_deploy` 1).
Depois do fix, a divergência esperada é **0**, e `sac_ia` ganha **12 contatos de cliente** que hoje somem
do filtro.

**Não derivar do primeiro segmento como alternativa:** 5 sessões do ambiente têm pool e **nenhum**
segmento (abandono antes de qualquer agente entrar) — exatamente o caso que um relatório de fila
precisa ver.

---

### F3 — Visão 1 (lista de contatos)

Desenho em telas-design §1. `/analise/sessions` absorve `/analise/processos`;
`AnaliseProcessosPage.tsx` é código morto (achado 7) e sai; `AnaliseJourneysPage` deixa de ser página e
vira o nível 2 da mesma rota.

- **Filtros:** `período · canal · entrou por · atendido por`. Os dois pools **nunca** se chamam "Pool"
  na tela. `atendido por` = subconsulta em `segments` (D12); `entrou por` = a coluna que F1b define.
- **Colunas:** direção (derivada de `spawn_reason` + canal, D8) · contato · `entrou por → atendido por` ·
  início · **`elapsed_time_ms`** (nunca Σ segmentos) · desfecho · chip.
- **Saem:** `origin (ANI)` e `destination (DNIS)` — permanentemente vazias nas 314 sessões (achados 1 e
  3). **Não voltam.**
- **Chip:** conta o processo inteiro, não a fatia filtrada; só quando `N > 1`; computado sobre a página
  retornada. Exige o rótulo do rodapé — decisão aberta #2, texto ainda não escrito.
- i18n namespace `contacts` (blocos `lista.*`, `journeys.*`, `segments.*`), **dois locales antes do PR**.

---

### F4 — Visão 2 (processo)

Desenho em telas-design §2. Consome F2.

- **Pivô**, nunca lista (D2). Cabeçalho com `acessos do cliente` × `contatos` × `decorrido`, e o toggle
  de internas reconciliando cabeçalho×tabela (D11 — hoje `_fetch_journeys` aplica `_apply_contact_scope`
  e `/reports/sessions?root_session_id=` não).
- **Duas classes de linha** (D4): acesso do cliente (expandida) × etapa interna (dobrada). O caso *em
  espera* precisa de rótulo honesto, ou lê como sessão quebrada.
- **Lentes A e B são um componente com toggle de ordenação** (D6), com offset relativo à abertura do
  processo além do timestamp absoluto.
- **Extrair, não reescrever:** `SegmentList.tsx` já funde segmentos + sessões-filhas num eixo único
  ordenado por tempo (achado 8). Herda a dívida de UI declarada em telas-design §4 (hex hardcoded,
  `text-[10px]`).
- **A sobreposição entre contatos** (o acesso 2 roda dentro da janela da análise, achado 12) só aparece
  na lente B — a barra de tempo não é decoração.

---

### F5 — `ContextStorePersister`

Fase própria, desenho fechado no ADR §3. Gatilho `session_closed` → PG `session_context_snapshot`;
**mascarado sempre**; estado final, não trajetória; foto inteira, nunca delta; ctx de processo a cada
close de membro. Entrada oculta por `visibility` é **contada, não omitida**.

---

## 4. Fora deste arco — não deixar entrar

| Item | Por quê |
|---|---|
| Achados 9/10/11 — Audit LGPD documentado e **ausente** | item próprio; um gate de compliance que a doc dá por fechado é grave demais para virar sub-tarefa de tela |
| Achados 16/17 — seeds sem `origin`, `voice.py` com métodos inexistentes | itens próprios; mesma classe estrutural (o mecanismo que impediria o defeito existe e não é usado por quem escreve) |
| `journey_merge` no intake de portabilidade | fatia pequena, não pertence ao cenário de referência |
| Transcript fundido cross-contato | D5 o exclui; exigiria ADR de masking próprio |
| "Cada perna do workflow é uma session" | rejeitado no ADR §5; inflaria `session_count`, TMA e abandono juntos |

---

## 5. Riscos de ambiente que mordem especificamente este arco

- **e2e apaga `tenant_demo:*` e `session:*` antes de CADA cenário** — não rodar e2e no meio de uma
  medição de journey; leva junto os pendings e os tokens de resume que a medição observa.
- **`docker cp` sobrevive a `restart`, não a `up -d`** — mudança em serviço = `build`.
- **`up -d <serviço>` sobe só o subgrafo**; o tell é `kafka-init Exited`.
- **`up -d --force-recreate` apaga o `pip install`** do pytest.
- ClickHouse db = **`plughub_demo`** · agent-registry em **`plughub_registry`** (`plughub_demo` tem um
  `pools` fóssil que devolve retrato velho e passa por medição).
- Portas: analytics-api **3500** · channel-gateway **8010** · agent-registry **3300** · auth-api
  **3202** · redis **6380** · postgres **5433**.
