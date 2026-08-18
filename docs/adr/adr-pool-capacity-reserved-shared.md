# ADR — Capacidade de IA por pool: `reserved` × `shared`

- **Status:** proposto
- **Data:** 2026-08-18
- **Escopo:** provisionamento e admissão de sessões em pools `agent_kind: ai`.
  **Não** toca licença humana, roteamento, scoring nem o modelo de conferência.
- **Compõe com:** [`adr-pool-no-resource-policy.md`](adr-pool-no-resource-policy.md) — este
  decide *quanto* o pool alcança; aquele decide *o que fazer com o "não"*.

---

## 1. Problema

O modelo de capacidade de IA hoje é um **híbrido cujas duas metades não se falam**:

| Camada | Comportamento | Natureza |
|---|---|---|
| **Provisionamento** | `deployed_max_concurrent_sessions` → o bootstrap cria **N instâncias** de 1 vaga, com `pools: [pool_id]` (`instance_bootstrap.py:1122-1141`) | **reservado** — ninguém de outro pool aloca aquelas vagas |
| **Admissão** | `SCARD({t}:admission:kind:ai) ≤ C_ai` — SET único **do tenant**, que não sabe de qual pool a sessão veio (`admission.py:121-141`) | **compartilhado** — ordem de chegada |

Consequências, nenhuma delas desejada:

1. **Um pool pode esgotar o direito de admissão de outro.** Não rouba a instância — rouba a
   entrada. O contato do pool B é recusado na porta **com instâncias do B ociosas**, porque
   `admit()` roda antes de `route()` (`main.py:256-261` × `:295`).
2. **O modo estatístico não é configurável.** Para compartilhar de verdade seria preciso
   sobre-assinar (Σ declarada > `C_ai`) e deixar o pote arbitrar — mas `deployViolation`
   devolve 422 exatamente nesse caso. Declarar **menos** não cria pote dinâmico: encalha
   capacidade contratada, porque nada cria a instância que ocuparia a folga.
3. **O modo determinístico depende de disciplina, não de imposição.** `deployViolation` valida
   contra `{t}:quota:max_concurrent_sessions`, que é `C_ai + C_human` — permite declarar
   concorrência de IA acima de `C_ai`, e a quebra aparece como recusa de contato, não como 422
   no deploy. É o **defeito C**, já registrado em `capacity.ts:16-19` e `quota_sync.py:26-29`.

## 2. Medição — 2026-08-18, `tenant_demo`

> Números reais. Substituem a fase P0 que a primeira versão deste documento previa.

| # | Medida | Resultado |
|---|---|---|
| M1 | Σ declarada, pools de IA com slot `current` | **329** em **30 pools** |
| M1b | pools humanos com slot | **0** — confirma que provisionamento é só de IA |
| M2 | `SCARD {t}:admission:kind:ai` | **1** — tracking vivo |
| M3 | `quota:capacity:ai_agent` / `:human_agent` / `:max_concurrent_sessions` | **as três `nil`** |
| M4 | `checkConcurrentSessions` / `{t}:quota:concurrent_sessions` | função **órfã** (0 call sites); chave **inexistente** |
| M5 | divergência `pools.max_concurrent_sessions` × `slot.config_json` | **0 linhas** — os dois estão populados e **espelhados** |

**Leitura, em ordem de consequência:**

- **Nada está sendo imposto.** Com as três quotas `nil`, `_type_limit()` devolve `None` e
  `admit()` cai no ramo sem gate (`admission.py:150-152`); `contractedCapacity()` devolve
  `null` e `deployViolation` sai fail-open (`capacity.ts:79`). Admissão, provisionamento e login
  estão **todos abertos**. Não é defeito: `quota_sync` **deleta** a chave quando o total é 0, e
  `sync_all` só percorre tenants com linhas em `pricing.installation_resources`.
- ⇒ **Isto é construção, não conserto.** Não há comportamento em vigor a regredir.
- **O defeito é latente, e 329 é o número que vai colidir.** Qualquer `C_ai < 329` liga o gate e
  começa a recusar na porta pools com instância ociosa.
- **O gate misto na borda está morto** (M4) — a falácia de aditividade não está recusando
  ninguém. Mas seis documentos o descrevem como se existisse (§8).
- **O número mora em dois lugares** (M5). Concordam hoje; nada garante que continuem.

> ⚠️ **Nota de método.** A estimativa estática a partir de `infra/registry/*.yaml` previa
> **~30** (o arquivo não declara `max_concurrent_sessions` em lugar nenhum; ausente vale 1). O
> vivo é **329** — erro de 11×. Os máximos foram definidos **pelo deploy de cada pool**, não pelo
> arquivo. É mais uma instância de *"fonte declarativa tem aplicador separado"*: dimensionar este
> arco pelo YAML teria errado uma ordem de grandeza.

## 3. Decisões

### D1 — Duas contabilidades, e elas **já existem**

`{t}:quota:capacity:ai_agent` e `:human_agent` são gravadas pelo mesmo laço do
`quota_sync.sync_tenant` (`:80-89`). Não há valor a criar. O que existe de errado é **um
consumidor lendo a soma**.

### D2 — Provisionamento só existe para IA. Não criar simetria

Humano não é provisionado: ele se provisiona ao **logar**, e é gateado ali contra `C_human`
(`server.ts:371-376`), com instância **por usuário** (`human-{userId}`) compartilhada entre todos
os pools em que ele está. M1b confirma: zero pools humanos com slot.

⇒ **Não existe `reserved`/`shared` para humano.** Reservar licença humana por pool seria reservar
vagas de login — isso é escala/turno, e é o WFM que a plataforma declaradamente não faz. Tentar
simetria aqui é como se reintroduz a moeda mista.

### D3 — Campo `capacity_mode: reserved | shared` no pool, default `reserved`

Default preserva o comportamento atual byte a byte. Aplica-se **apenas** a pools
`agent_kind: ai`; em pool humano o campo é rejeitado no registro (não ignorado — ignorar ensina
que existe).

### D4 — `deployViolation` passa a validar contra `C_ai`

Mudança em `capacity.ts:27`: ler `{t}:quota:capacity:ai_agent` em vez de
`{t}:quota:max_concurrent_sessions`, e somar apenas slots de pools `agent_kind: ai` **em modo
`reserved`**.

**Fecha o defeito C no caminho de IA.** Feita agora, enquanto tudo é fail-open, é **inócua**;
feita depois de ligar o pricing, é mudança de comportamento em produção. Por isso vem antes.

### D5 — Pool `shared` é **isento** do gate de provisionamento

É o ponto inteiro: poder declarar mais do que o contrato serve simultaneamente. Sobre-assinatura
é a definição do modo estatístico.

### D6 — A admissão passa a conhecer o modo

```
pool reserved  →  sem gate de admissão
                  (não pode exceder: Σ declarada(reserved) ≤ C_ai já foi validada no deploy)

pool shared    →  gate contra  C_ai − Σ declarada(reserved),  ordem de chegada
```

Isto realiza a fórmula pretendida — `shared = contratado − Σ reservado` — **na moeda certa** e
com o substantivo certo (licença/instância, não sessão). E entrega as duas propriedades:
**`reserved` é determinístico** (o pool tem X e ninguém tira), **`shared` é estatístico** (os
pools disputam por chegada, com prioridade igual).

Efeito colateral desejável: o pool reservado deixa de poder ser recusado na porta com instância
ociosa — o defeito nº 1 da §1.

### D7 — A reserva é o **provisionamento**, não um balde de sessões

Registro explícito, porque a semelhança superficial é o risco. **Não reviver:**
`pools.session_reservation` (migration `20260802000000_drop_pool_session_reservation`),
`{t}:admission:shared`, `{t}:admission:reserved:{pool}`, `{t}:admission:member:{sid}`.

Aqueles baldes fatiavam **sessões** sobre um pote **misto**. Aqui não há balde novo, não há
aritmética nova em Redis, e a moeda é única. As instâncias já provisionadas **são** a reserva.

**Por que a objeção original não transfere:** o invariante *"capacidade é do RECURSO, nunca do
pool"* nasceu do caso humano (1 pessoa, 3 vagas, 3 pools ⇒ contagem tripla). Uma instância de IA
pertence a **exatamente um pool** (`pools: [pool_id]`), então reservar IA por pool não fragmenta
recurso compartilhado — apenas nomeia o que o bootstrap já faz.

### D8 — Uma fonte para o número (M5)

O valor vive hoje na coluna `pools.max_concurrent_sessions` **e** em
`pool_skill_slots.config_json->>'max_concurrent_sessions'`, espelhados. **O slot é a
autoridade** — é o que `deployViolation` lê (`slotDeclared`) e o que o bootstrap executa
(`instance_bootstrap.py:1092,1111`). A coluna é resíduo do modelo pré-slot.

Resolver antes de acrescentar `capacity_mode`, ou o campo novo nasce ao lado de uma duplicação
que ele vai herdar.

*(Ressalva: que a coluna seja resíduo é inferência — falta ler a rota do agent-registry que compõe
`deployed_max_concurrent_sessions`. A fase P2 começa por essa leitura.)*

### D9 — `max_concurrent_sessions` (misto) é aposentado como número de capacidade

Depois do D4 sobra **zero** leitor legítimo. O outro (`checkConcurrentSessions`) é órfão e sai
junto (§8). A chave permanece escrita pelo `quota_sync` enquanto houver consumidor externo
declarado; quando não houver, sai também.

## 4. O que **não** muda

Licença humana e o gate de login · modelo de conferência, sessão, segmento e journey ·
roteamento, scoring e desempate · `claim_instance` e o semáforo de vaga · `ChannelSchema` ·
`queue_config` e a fila atendida · a isenção de `C_ai` da fila muda · o formato do SET
`admission:kind:ai` (o tracking já está correto e populado — quando o gate ligar, liga com estado
válido, sem migração).

## 5. Fases

| # | Fase | Entrega | Por que nesta ordem |
|---|---|---|---|
| **P0** | **Ligar o pricing** | popular `pricing.installation_resources`; confirmar que `sync_tenant` grava as três chaves | sem isso todo teste de teto passa por **ausência de amostra** — o teste que não pode reprovar |
| **P1** | `deployViolation` contra `C_ai` | `capacity.ts:27` + filtro por `agent_kind` | inócua enquanto fail-open; mudança de comportamento depois. **Antes do P0 se possível** |
| **P2** | Uma fonte para o número | ler a rota do registry, eleger o slot, remover/derivar a coluna | o campo novo não pode nascer ao lado da duplicação |
| **P3** | `capacity_mode` | schema, migration, guard (rejeita em pool humano), isenção do `deployViolation` para `shared` | — |
| **P4** | Admissão consciente do modo | `reserved` sem gate; `shared` contra `C_ai − Σ reserved` | depende de P1 e P3 |
| **P5** | Visibilidade | Monitor/Analytics: reservado × compartilhado por pool, e o saldo do pote | substrato existe: `{t}:admission:ai_pools`, `{t}:capacity:snapshot`, séries `__capacity_{kind}__` |
| **P6** | Limpeza | remover `checkConcurrentSessions` + `{t}:quota:concurrent_sessions`; corrigir os seis documentos | §8 — é defeito colateral, pode ir em paralelo |

## 6. Riscos

| Risco | Sinal de que aconteceu |
|---|---|
| **P0 nunca acontece e o arco fica decorativo** | Fases P3–P5 entregues, quotas seguem `nil`, nenhum gate dispara — e um teste verde não prova nada |
| Simetria humano/IA sendo reintroduzida | Aparece `capacity_mode` em pool humano, ou reserva de login por pool |
| `shared` virando balde | Aparece SET, contador ou aritmética por pool no Redis — é o D7 sendo violado |
| Ligar o pricing com `C_ai < 329` | Recusa imediata na porta em pools com instância ociosa. **Dimensionar `C_ai` contra a Σ declarada antes de ativar** |
| Visibilidade somando o que não soma | Tela exibindo `Σ available(pool)` como total, ou `by_channel` como partição — as duas falácias já documentadas |
| Duplicação do D8 sobrevivendo | Coluna e slot divergem; a tela mostra um, o bootstrap provisiona pelo outro, nada fica vermelho |

## 7. Fora de escopo

- **Licença humana em qualquer forma de reserva por pool** (D2).
- **Reviver fatia de sessão** (D7).
- **Cota por chamador externo** (`a2a_client`): resolvida por `allowed_pools` + pool dedicado +
  `on_no_resource: reject`; ver o ADR irmão.
- **Elasticidade** (criar instância sob demanda quando há licença livre). O bootstrap é
  reconciliador de estado desejado e **remove** instância a mais; elasticidade seria outro
  modelo, e `shared` cobre o caso de uso sem ela.
- **Unidade de licença por instância substituindo o gate de sessão.** O docstring do
  `admission.py` aponta para lá (*"quando as licenças existirem, este gate é SUBSTITUÍDO por
  elas"*). Este ADR é compatível com esse destino, mas não o antecipa.

## 8. Defeito colateral que sai junto

**`checkConcurrentSessions` é função órfã.** Definida em `quota-check.ts:89`, usada **só no
próprio teste**; zero call sites em produção. O docstring diz *"usado pelo Channel Gateway antes
de aceitar um inbound"* — mas o channel-gateway é **Python** e a função é **TypeScript no
mcp-server**: a integração descrita **não é construível como está escrita**. A chave que ela lê,
`{t}:quota:concurrent_sessions`, tem **um leitor (ela mesma) e nenhum escritor**; `EXISTS`
devolve 0 no ambiente.

Seis documentos descrevem esse gate como existente — `CLAUDE.md:865`,
`docs/modelos-de-dados.md:143`, `docs/arcos/usage-metering.md:76,98,101`,
`docs/layers/07-data-layer.md:46`, `docs/arcos/queue-attended-model.md:152`. O pior é o
`07-data-layer.md`, que atribui a escrita a *"pricing-api / Core"*, e o `usage-metering.md`, que
mostra `redis.incr`/`redis.decr` como se rodasse. Quem dimensionar concorrência lendo esses
arquivos vai contar com uma trava que não existe.

## 9. Referências

- Código: `routing-engine/…/admission.py` · `routing-engine/…/main.py:256-295, 776-778, 825-827` ·
  `agent-registry/src/lib/capacity.ts` · `pricing-api/…/quota_sync.py` ·
  `mcp-server-plughub/src/server.ts:356-380` · `mcp-server-plughub/src/lib/quota-check.ts:89-103` ·
  `orchestrator-bridge/…/instance_bootstrap.py:1085-1145`
- [`adr-pool-no-resource-policy.md`](adr-pool-no-resource-policy.md) — o desfecho
- `CLAUDE.md` § "Admissão de sessão — UM gate, na moeda certa" · § "Operational Visibility"
- `docs/product/shared-capacity-pool-as-tag-design.md`
- Migration `20260802000000_drop_pool_session_reservation`
