# Fase 2 — Relatório de Pools / Infraestrutura

> Estado: **spec / ADR** (não implementado). Base para a implementação.
> Complementa a Fase 1 (Agentes): Fase 1 = produtividade por pessoa; Fase 2 = saúde operacional por pool/canal.
> Ver também `docs/arcos/analytics-reports-redesign.md`.

---

## Perguntas que o relatório responde

Dimensão central: **pool × canal × endpoint × tempo**. Quatro famílias:

1. **Volume / tráfego** — quanta demanda e quando (por pool, canal e endpoint).
2. **Fila** — a fila está dando conta (espera, tamanho, abandono, disponíveis) + **SLA**.
3. **Capacidade / headroom** — super ou subdimensionado (concorrência vs capacidade contratada).
4. **SLA** — % dentro do alvo (deriva da Fila).

---

## Estrutura de UI — aba `Analytics/Pools`

Espelha a `Analytics/Agents`: barra de filtros (período + pool + canal) → sub-abas.

- **Visão geral** — tira de KPIs (contatos, espera média, abandono, SLA, pico conc./cap.) + tabela "Saúde dos pools" (uma linha por pool, drill-down ao clicar abre a visão do pool).
- **Volume** — área/linha de contatos no tempo (empilhada por canal), heatmap hora×dia, donut por canal, e **drill-down por endpoint** (DNIS) dentro do canal.
- **Fila** — espera/tamanho no tempo, abandono por pool, sobreposição disponíveis × fila.
- **Capacidade** — área de concorrência no tempo com linha de teto = capacidade; headroom = teto − pico; utilização por pool.
- **SLA** — % de atendimento dentro do alvo por pool e no tempo.

---

## Endpoints (contratos)

Convenção: `tenant_id` (obrigatório), `from_dt`, `to_dt`, `pool_id?`, `channel?`, `bucket?` (`hour`|`day`; default `hour` ≤48h, senão `day`). Escopo por `accessible_pools` do JWT aplicado no `WHERE`. Timestamps ISO no retorno.

### `GET /reports/pools/volume`
Fonte: `sessions`. Alimenta a sub-aba Volume.

```json
{
  "data": {
    "series":      [ { "bucket": "2026-06-01T09:00:00Z", "pool_id": "sac_ia", "channel": "webchat", "endpoint": "site-a.com", "contacts": 34 } ],
    "by_channel":  [ { "channel": "webchat", "contacts": 720 } ],
    "by_endpoint": [ { "channel": "whatsapp", "endpoint": "+551130000001", "contacts": 300 } ],
    "totals":      { "contacts": 1240 }
  },
  "meta": { "from_dt": "…", "to_dt": "…", "bucket": "hour" }
}
```

`endpoint` = DNIS (destino discado/acionado), já capturado pela channel-gateway no Arc 19: voice→DID, whatsapp→número WA, webchat/webrtc→widget/URL, email→endereço, webhook→`skill_id`.

### `GET /reports/pools/queue`
Fonte: `queue_events` (`queued`/`position_updated`/`abandoned`) + `sessions`. Alimenta Fila **e** SLA (SLA é métrica de espera).

```json
{
  "data": {
    "series":  [ { "bucket": "…", "pool_id": "retencao_humano", "avg_wait_ms": 70000, "max_queue_len": 5, "queued": 40, "abandoned": 4, "available_agents": 3 } ],
    "by_pool": [ { "pool_id": "retencao_humano", "queued": 412, "abandoned": 38, "abandon_rate": 0.091, "avg_wait_ms": 70000, "p95_wait_ms": 240000, "sla_target_ms": 300000, "within_sla": 374, "sla_attainment": 0.84 } ]
  },
  "meta": { … }
}
```

### `GET /reports/pools/occupancy`
Fonte: **picos persistidos** pelo Routing Engine (ver abaixo) + capacidade configurada. Alimenta Capacidade.

```json
{
  "data": {
    "series":  [ { "bucket": "…", "pool_id": "retencao_humano", "peak_concurrency": 8, "capacity": 10 } ],
    "by_pool": [ { "pool_id": "retencao_humano", "peak_concurrency": 8, "capacity": 10, "headroom": 2, "utilization": 0.80 } ],
    "total":   { "peak_concurrency": 11, "capacity": 24, "headroom": 13, "utilization": 0.46 }
  },
  "meta": { … }
}
```

---

## Modelo de concorrência — contadores no Routing Engine (decisão central)

A concorrência **não** é reconstruída por varredura de `participation_intervals` como fonte primária (query pesada; o bucket corrente, com sessões em andamento sem `left_at`, fica impreciso). O Routing Engine é o **árbitro único da alocação** — o único que conhece a concorrência instantânea a cada `+1`/`−1` — então rastreia o pico ali. A varredura fica como fallback/reconciliação.

### Invariantes

- **Contador vivo de concorrência** (número de sessões ativas): persistente entre buckets, só muda em alocação (`+1`) e liberação (`−1`). **Nunca zerado na virada do bucket** — uma sessão não termina porque o tempo virou.
- **Dois conjuntos**: um **por pool** (`{tenant}:pool:{id}:concurrency`) e um **total** (`{tenant}:concurrency_total`), ambos via `INCR`/`DECR` atômico no Redis (compartilhado entre instâncias de routing, sobrevive a restart).
- **`peak_total` é instantâneo e independente** — **NÃO é a soma dos max por pool**. Os picos por pool ocorrem em tempos diferentes (ex.: A pica 8 às 10:00 com B em 3; B pica 6 às 10:30 com A em 5 → soma = 14, mas o total nunca passou de 11). Dimensionar pelo somatório super-provisiona.
- **Max por bucket com carry-over**: o max do bucket N+1 **começa no valor do contador vivo no instante do fechamento de N** (carry-over), não em zero. Uma sessão que atravessa 3 buckets conta no max dos 3.

### Mecanismo

- Em cada `+1`/`−1`: atualiza o contador vivo e `peak_bucket = max(peak_bucket, contador)` — por pool e total.
- No **fechamento do bucket N** (flush): (1) emite `peak_N` (pool + total) → Kafka → ClickHouse; (2) **não toca** no contador vivo; (3) semeia `peak_{N+1} = contador atual` (carry-over).
- O carry-over no flush cobre o **bucket sem eventos** (steady-state com N sessões em curso): sem ele registraria max=0; com ele registra N corretamente.
- UI: barra "ao vivo" do bucket corrente lê o running-max do Redis; histórico vem do ClickHouse.

### Persistência

- Redis (corrente): `{tenant}:pool:{id}:concurrency`, `{tenant}:concurrency_total`, `{tenant}:pool:{id}:peak:{bucket}`, `{tenant}:peak_total:{bucket}`.
- ClickHouse (histórico): nova tabela `pool_occupancy_peaks(tenant_id, pool_id, bucket, peak_concurrency, ingested_at, date)`; o total como `pool_id = '__total__'` (ou coluna dedicada). Kafka topic `pool.occupancy` (producer: Routing Engine; consumer: analytics-api).

---

## Fonte de capacidade (decisão)

Capacidade = **configurada no módulo de pricing** (capacidade contratada), **não** o snapshot de tempo real:

- O **snapshot operacional** (`{tenant}:pool:{id}:snapshot`, disponíveis agora) é staffing instantâneo → fica na sub-aba **Fila** (disponíveis × fila), volátil, não é o teto.
- A **capacidade do pricing** (base + reserve por pool; capacidade-base do tenant para o total) é o teto contratado → denominador de headroom e a ponte para o **custo**.

Par: por pool → `peak_concurrency` vs capacidade do pool; total → `peak_total` (instantâneo) vs capacidade-base do tenant. Headroom = teto − pico nos dois níveis.

---

## Fontes de dados (já coletadas)

| Família | Fonte |
|---|---|
| Volume | `sessions` (channel_type, DNIS/ANI do Arc 19) |
| Fila / SLA | `queue_events` + `sessions` |
| Capacidade | `pool_occupancy_peaks` (novo, do Routing Engine) + capacidade do pricing |

---

## Pendente (implementação)

1. Routing Engine: contadores de concorrência (pool + total) no Redis + flush de pico por bucket → Kafka `pool.occupancy`.
2. analytics-api: consumer `pool.occupancy` → `pool_occupancy_peaks`; três endpoints `/reports/pools/{volume,queue,occupancy}`.
3. pricing-api: expor capacidade configurada por pool + capacidade-base do tenant (para o denominador).
4. platform-ui: aba `Analytics/Pools` (Visão geral + Volume + Fila + Capacidade + SLA), i18n en + pt-BR.
