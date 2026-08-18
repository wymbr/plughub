# Handoff — segmento que nunca fecha (causa raiz CONSERTADA) + o que sobrou

> Sucede [`kickoff-segmento-aberto-e-volume-sessoes.md`](kickoff-segmento-aberto-e-volume-sessoes.md),
> que está **encerrado**: os dois itens dele foram respondidos. Este arquivo existe para a próxima
> sessão não recomeçar pelo que já caiu.

---

## O que ficou fechado (não reabrir sem dado novo)

| | |
|---|---|
| **Causa raiz do segmento aberto** | `conversations.participants` publicado **sem `key`** em tópico de 3 partições ⇒ `joined`/`left` do mesmo segmento invertiam ⇒ o `joined`, inserido depois, vencia a dedup nas DUAS tabelas |
| **Conserto** | `key=session_id` no bridge **+** `ReplacingMergeTree(row_version)` (versão do EVENTO) em `segments` e `participation_intervals`. Uma parte sozinha não bastaria |
| **Gate** | 3/3 reproduções fecharam, na forma que antes era moeda (2/4). Confirmado por segundo instrumento: `queue` com `ultimo_dia` = dia do teste e abertos ainda 2 (os históricos) |
| **Volume de sessões (118 × 285)** | Mesmo dado, principais diferentes: o ABAC `accessible_pools` custa 166 linhas; sem header `Authorization` o endpoint devolve 285 (falha ABERTA, documentada em `pool_auth.py:130-135`) |

**Hipóteses eliminadas por medição — não refazer:** LPUSH `__agent_available__` ausente · marcador
`queue:agent_active` sumindo · despejo do Redis · wipe do e2e · empate de RMT como *causa* (é o
mecanismo da perda, não a origem) · `asyncio.create_task` sem referência forte · os 3 tiers do
`/reports/sessions` · degradação do `pools_client` · rajada de e2e · produtor fantasma de sessões.

---

## Pendência 1 — o passado NÃO foi reparado *(decisão, não implementação)*

Seguem **9** segmentos abertos em sessão fechada: `primary` 5 · `queue` 2 · `specialist` 2, todos
anteriores a 2026-08-15. O `DEFAULT` do `row_version` só repara onde as duas linhas ainda coexistem; nos
antigos o merge já apagou fisicamente a perdedora.

**Os eventos continuam no tópico** `conversations.participants` (verificado: o `left` do
`queue-dce98532…` está lá). Um reprocessamento gated repararia — mas mexe em substrato de qualidade e
**pede decisão**, não código: reprocessar re-emite eventos que o consumer trata como `live`, e o
discriminador `origin` existe justamente para essa fronteira. Ver `adr-quality-substrate-isolation.md`.

**Se decidir NÃO reparar:** a baseline 5·2·2 precisa virar constante declarada no
`probe_open_segments_closed_sessions.sh` (hoje ela está no texto do veredicto), senão a próxima pessoa
lê 9 como defeito vivo.

---

## Pendência 2 — `queue_config.skill_id` é decorativo *(defeito vivo, separado)*

Não era a causa do segmento aberto, e continua de pé. `resolve_flow_for_agent`
(`orchestrator-bridge/main.py:494-497`) resolve produção pelo **slot `current` do POOL**, e
`_activate_queue_agent` passa o **pool de destino** ⇒ o `queue_config.skill_id` **nunca é consultado**.

Medido: `retencao_humano` declara `skill_fila_v1` (existe, `published`, com flow), não tem slot, e o
agente de fila **nunca roda** — `activate_native_agent` devolve `{}` em 3 ms, visível como `ERROR`
*"NENHUM slot `current`"* em toda reprodução. Os 12 segmentos `queue` que completam com `handoff` são de
pools **sem** `queue_config`: rodaram o skill do próprio pool sob `role='queue'`.

**Três consequências, e a terceira é a que decide o desenho:**

1. a config aparece na UI e não executa nada;
2. o relatório de Fila/SLA registra "espera atendida" onde não houve agente de fila algum
   (`docs/arcos/queue-attended-model.md` mede um agente que, neste pool, nunca rodou);
3. **o segmento de fila nasce antes de se saber se o agente pode rodar** — o marcador (`:5504`) e o
   `participant_joined` (`:5527`) são escritos antes da resolução do flow (`:5546`). Daí existir
   segmento `queue` de 3 ms que não enfileirou ninguém.

**Duas saídas, e elas não são equivalentes:** (a) fazer o queue agent resolver pelo
`queue_config.skill_id` (o pool de fila é um deploy próprio — implica dar slot a ele); (b) declarar que
fila só existe onde há deploy e **remover** `queue_config` do schema. Escolher é decisão de produto —
com o alvo n8n na mesa, (b) pode ser o caminho barato. **Não implementar sem decidir.**

---

## Pendência 3 — o conserto foi validado em UMA forma só

O gate cobriu o caso de **falha rápida** (3 ms, `role='queue'`). Continua **não medido**:

- segmento de fila **longo** (agente rodando de fato — só reproduzível com
  `ALLOW_LIVE_FLOW_FALLBACK=true`, hoje declarado no compose e vazio por defeito);
- as famílias **B** (`primary`/`specialist`, órfãos de suspend/resume). O raciocínio de que são o mesmo
  defeito é **inferência** — mesmo publish, mesma ausência nas duas tabelas —, não medição. Um resume
  reproduzido pós-conserto fecharia o caso; até lá, "as duas famílias eram um defeito só" é hipótese
  bem-sustentada, não fato.

---

## Pendência 4 — dívidas expostas, nenhuma é causa de nada conhecido

- **78 `asyncio.create_task` no bridge, zero com referência forte** (`_bg_tasks`/`add_done_callback`:
  nenhuma ocorrência). A doc do asyncio pede a referência; o loop guarda só referência fraca. Não foi a
  causa deste bug — o evento chegou ao tópico —, mas é falha silenciosa por construção. Conserto
  mecânico: um helper que retenha e logue exceção no `add_done_callback`.
- **`_migrate_sessions_row_version` segue específica** enquanto as duas novas usam
  `_migrate_row_version` genérica. Deixado assim de propósito: reescrever um caminho já validado em
  produção, sem medição, é risco sem retorno.
- **`probe_family_a_queue_signal.sh`** cumpriu o papel e hoje tem enquadramento obsoleto (procura o
  ramo do sinal como discriminador). Manter como histórico ou podar.

---

## Instrumentos novos desta sessão

| Script | Para quê |
|---|---|
| `probe_participant_event_in_kafka.sh <sid>` | **o primeiro a rodar** se aparecer órfão novo: diz se o `left` chegou ao tópico, separando produtor de consumidor |
| `watch_queue_marker.sh <T0-ISO-UTC>` | leitor da reprodução: marcador, resolução do flow, ramo do drain com TTL, segmento resultante |
| `probe_queue_segment_exit_paths.sh` | por qual porta cada segmento de fila fechou (o `outcome` é o discriminador) |
| `probe_contacts_count_funnel.sh` | funil do `WHERE` do `/reports/sessions`; auto-verificável contra o endpoint |
| `probe_open_segments_closed_sessions.sh` | o gate; rodapé já atualizado com a baseline e o próximo comando |

Instrumentação permanente no código: `marker SET` / `marker DELETE deleted=N` em INFO no bridge, e o
**TTL da chave** no ramo ELSE do drain — foi ela que separou *"o marcador sumiu"* de *"o marcador foi
apagado por quem devia"*.

---

## Armadilhas que morderam nesta sessão (a próxima herda)

- **Parte já mesclada não mostra empate.** Consultei `segments` sem `FINAL` num caso novo e declarei
  "empate refutado" — mas o `_part` era de nível 232, já mesclado. Para ver duas versões é preciso
  pegar a linha **antes** do merge, ou usar outra testemunha.
- **`< /dev/null` no lugar errado do pipe.** `cmd | grep P < /dev/null` faz o *grep* ler `/dev/null` e
  devolver vazio. A saída vazia parece medição e não é.
- **`--tail N` não alcança o boot.** Grep de log de migração com `--tail 200` volta vazio mesmo com a
  migração tendo rodado; a prova é `system.tables.engine_full`, não o log.
- **`command -v` não acha binário fora do PATH** — `apache/kafka` guarda tudo em `/opt/kafka/bin`. O
  detector devolveu "não existe" para um binário existente.
- **MV em banco Atomic prende-se por UUID**, não por nome: `RENAME` da tabela-origem deixa a MV órfã e
  ela para de receber INSERT em silêncio. Toda migração por rebuild de tabela com MV dependente tem de
  derrubar e recriar as views — e recriar em `finally`, não no caminho feliz.
