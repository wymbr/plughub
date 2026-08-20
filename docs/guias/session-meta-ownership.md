# `session:{id}:meta` — partição de propriedade e regra de prazo

> Estado: **fatia A concluída (2026-08-22)** — regra de prazo em vigor nos três sites do
> bridge, partição declarada e medida. Rejeição de campo alheio (fatia B) e separação
> `entry_pool_id` × `pool_id` (fatia C) seguem em aberto; ver § Fatias pendentes.

A chave `session:{id}:meta` é um **string JSON** com TTL, escrita por seis sites em quatro
pacotes. Até 2026-08-22 não havia schema, helper nem dono declarado: a chave era interpolada
à mão em ~50 lugares e cada escritor decidia sozinho o conjunto de campos, o modo de criação
e o prazo. Este documento declara a partição, registra como ela foi **medida** e fixa a
única regra que hoje é enforçada.

---

## 1. Os seis escritores (as-built)

| # | Site | Modo | Prazo pedido | Campos |
|---|---|---|---|---|
| W1 | `channel-gateway/adapters/webchat.py:196` | `SETEX` cego | 14 400 | contact_id · session_id · tenant_id · customer_id · channel · **pool_id** · started_at · customer_participant_id |
| W2 | `channel-gateway/adapters/webrtc.py:473` | `SETEX` cego | 14 400 | idem, sem `started_at` |
| W2b | `channel-gateway/adapters/webrtc.py:872` | `EXPIRE` (keepalive) | 14 400 | — só prazo |
| W3 | `channel-gateway/adapters/webhook.py:596` | `SETEX` cego | **86 400** | tenant_id · channel · contact_id · customer_id? — **abstém-se de `pool_id`** |
| W4 | `mcp-server-plughub/tools/bpm.ts:217` (`conversation_start`) | `SETEX` cego | 14 400 | + `process_context` |
| W5 | `orchestrator-bridge/main.py` (`activate_human_agent`) | **helper**, `mode=merge` | `_stl()` | instance_id · pool_id · agent_type_id · user_login |
| W6 | `orchestrator-bridge/main.py` (`process_routed`, pool webhook) | **helper**, `mode=create` | `_stl()` | agent_type_id · pool_id · instance_id (+ campos da porta como `soft`) |
| W7 | `orchestrator-bridge/main.py` (`process_routed`, primary) | **helper**, `mode=upsert` | `_stl()` | agent_type_id · instance_id (+ pool_id/tenant_id como `soft`) |

As três semânticas do bridge (exige-existir · só-cria · cria-ou-funde) existiam espalhadas e
implícitas; agora estão enumeradas uma vez, no parâmetro `mode` de `session_meta_merge`.

---

## 2. A partição — medida, não suposta

Obtida com `infra/test/probe_meta_ttl_bridge_off.sh`, que usa **o bridge como única
variável**: dispara um trigger com o serviço parado (só a porta escreve), religa (o evento de
alocação estava esperando no Kafka) e compara os conjuntos de campos da MESMA chave.

```
bridge parado : channel · contact_id · customer_id · tenant_id
bridge dentro : + agent_type_id · instance_id · pool_id      (perdeu: nenhum)
```

| Dono | Campos | Significado |
|---|---|---|
| **PORTA** (canal / trigger) | `tenant_id` `channel` `contact_id` `customer_id` `session_id` `started_at` `customer_participant_id` | fatos do CONTATO, conhecidos na entrada |
| **BRIDGE** (alocação) | `agent_type_id` `instance_id` `pool_id` `user_login` | fatos do ATENDIMENTO, só existem depois de alocar |

Declarada em código em `orchestrator-bridge/main.py`, constantes `_META_FIELDS_GATEWAY` /
`_META_FIELDS_BRIDGE`.

**Nenhum dos seis pode virar dono único**, e isso é desenho, não acidente: os adapters de
canal cobrem apenas webchat/webrtc; o bridge só chega depois da alocação e não conhece o
contato; o trigger webhook se abstém de `pool_id` de propósito.

### 2.1 O caso sujo — `pool_id` carrega dois fatos

`webhook.py` **não** escreve `pool_id`, e o comentário no site diz por quê: *"ele é reescrito
pelo bridge na alocação; semeá-lo aqui gravaria o pool de ENTRADA num campo que os leitores
tomam por 'pool que está atendendo'"*. Mas `webchat.py:210` e `webrtc.py:484` gravam
exatamente isso — o pool de entrada — no mesmo nome.

Um nome carregando dois fatos é a mesma família de `elapsed_time_ms` × `agent_time_ms` (D9,
Arc 19): **os dois valores são legítimos e não são intercambiáveis**. O leitor que fecha o
caso é `webhook.py:1330` (`resume_pool = _meta_r.get("pool_id")`), que roda no resume — logo
depois da alocação — e portanto quer a acepção do bridge.

A correção de raiz é separar `entry_pool_id` (canal) de `pool_id` (bridge). Está **fora da
fatia A** porque muda contrato lido por vários componentes; ver § Fatias pendentes.

---

## 3. A regra em vigor: o prazo só ESTENDE

### O defeito, medido

`infra/test/probe_meta_ttl_bridge_off.sh`, contra a imagem anterior:

```
TTL 86397 ──(alocação)──> 14398     mesma chave, 6 s
```

A porta webhook escreve 24 h porque a workflow que o meta descreve fica suspensa por
`timeout_hours*3600 + 3600` (48 h de default). O bridge reescrevia com `_stl()` (4 h), e o
meta passava a morrer **antes** da sessão que ele descreve.

### Quem sofria

`infra/test/probe_resume_outlives_meta.sh`, no ambiente demo:

```
condenados (meta morre antes do token) : 7
cobertos   (meta cobre o token)        : 0
```

Meta 233 min contra token 23 h — **a população inteira**. Quando a hora chega, o token é
aceito e a retomada morre em `tenant_unknown`, que é a recusa do arco P2 (2026-08-18)
funcionando perfeitamente sobre uma chave que alguém encurtou. A metade barulhenta do
conserto rodava sem a metade que faz haver o que ler.

### A regra

`session_meta_merge` calcula `eff = max(ttl_corrente, ttl_pedido)` e nunca escreve prazo
menor. `-1` (existe sem expiração) e `-2` (ausente) são **ausência de prazo** e portanto
**definem** — a mesma lição que `_extend_hash_ttl` (`webhook.py:1052`) comprou caro em
2026-08-10: tratar `-1` como "infinito a preservar" troca encurtamento por chave imortal,
que é pior por ser silencioso.

Merge e prazo acontecem num **único `EVAL`**. O motivo é o próprio propósito do helper: um
`GET`+`SETEX` pode perder o campo de outro escritor entre as duas chamadas, e perder campo é
justamente o que ele existe para impedir.

Quando um encurtamento é impedido, sai `[session_meta] prazo PRESERVADO caller=… 86397s
(pedido 14400s)` em **WARNING** — não INFO, porque vários serviços deste repo sobem com o
logger em WARNING e um alarme em INFO é um alarme que ninguém vê.

---

## 4. Contrato de `session_meta_merge`

```python
await session_meta_merge(
    redis_client, session_id, patch,
    mode="merge" | "upsert" | "create",
    owner="bridge",          # chave de _META_OWNERS
    soft={...},              # aplicado só onde o campo ainda não existe (ou é "")
    ttl_s=None,              # default _stl()
    caller="nome_do_site",   # aparece em todo log
)
```

| `mode` | Chave ausente | Chave presente |
|---|---|---|
| `merge` | **no-op** (`-2`) — reproduz o antigo `if raw_meta:` | funde `patch`, aplica `soft` nos buracos |
| `upsert` | cria com `patch ∪ soft` | idem `merge` |
| `create` | cria com `patch ∪ soft` | **não toca** conteúdo nem prazo (`-4`) — era `nx=True` |

Retornos negativos: `-2` ausente em `merge` · `-3` JSON ilegível (escrita **abortada**, nunca
sobrescreve às cegas) · `-4` já existe em `create` · `-9` erro de transporte. Todo ramo que
não escreve **loga por quê**.

**`soft` fica fora da conferência de dono de propósito.** Preencher um buraco que o dono
deixou é *backfill*, não reivindicação — é o que o bridge faz com `tenant_id` quando a porta
não escreveu. Sem essa isenção o aviso dispararia em toda alocação e viraria ruído, que é como
um alarme verdadeiro deixa de ser lido.

**String vazia conta como ausente** no `soft`: os call sites originais testavam
`if not meta.get(x)`, e trocar isso por `== nil` mudaria comportamento em silêncio num campo
que o resume lê.

---

## 5. Gate

`infra/test/probe_meta_ttl_bridge_off.sh` — 0 preservou · 1 truncou · 3 inconclusivo.
**Muta estado de serviço** (para e religa o bridge, com `trap` para religar em qualquer saída).

Duas armadilhas de instrumento já pagas, ambas registradas no cabeçalho do script:

1. **Não dá para fotografar o 86400 por fora.** Duas versões anteriores tentaram — uma lendo
   logo após o POST, outra usando `max_concurrent_sessions: 1` para deixar sessões na fila
   como controle. As duas voltaram INCONCLUSIVAS, e o motivo nunca foi o defeito: cada
   `docker exec` custa ~0,5 s e a alocação cabe nisso; e o skill suspende no `delegate`,
   devolvendo a vaga antes da primeira leitura. A variável tem de ser o **bridge**.
2. **O critério não pode ser um limiar fixo de queda.** A v1 usava "caiu mais de 120 s" e
   produziu um **falso vermelho** na primeira execução após o conserto (86397 → 86275, queda
   de 122 s) porque o laço tinha levado ~122 s de relógio. O instrumento mediu a própria
   lentidão e chamou de defeito. O critério correto compara com o decaimento natural medido
   em `date +%s`, e a saída imprime `esperado`, `observado` e a subtração.

`SELFTEST=1` injeta o `EXPIRE 14400` que o código antigo produzia e **exige vermelho** — sem
isso, o critério atual jamais teria reprovado contra um valor ruim, e o verde seria confiança
sem lastro.

Probes de apoio: `probe_session_meta_ownership.sh` (inventário + partição por rajada) e
`probe_resume_outlives_meta.sh` (conta as vítimas).

---

## 6. Fatias pendentes

**B — rejeição de campo alheio.** Hoje `session_meta_merge` **observa e loga**
(`[session_meta] DONO VIOLADO …`), não recusa. Recusar exige que os quatro escritores de canal
passem pelo helper; enquanto W1/W2/W3/W4 escreverem `SETEX` cego por fora, recusar no bridge
fecharia o caminho certo e deixaria os errados abertos — trocaria um bug quieto por uma queda
alta. O helper precisa nascer também em TypeScript (W4) e ser compartilhado pelos dois
pacotes Python, ou ganhar um gate que compare as duas listas de campos.

**C — `entry_pool_id` × `pool_id`.** A separação da § 2.1. Muda contrato lido por vários
componentes e merece fatia própria.

**Resíduo adjacente, mesma família:** `session:{id}:wf_agent` (escrito ao lado de W6) também
nasce com `_stl()` = 4 h e também descreve uma workflow que pode ficar suspensa por 48 h. Não
foi tocado nesta fatia porque não estava medido — mas o mecanismo é o mesmo e a suspeita é
específica.
