# Pricing Module — Capacity-Based Billing

> Última atualização: 2026-05-25 · Estado: Arc 16
>
> Full reference for the pricing-api billing model, endpoints, invoice structure, and Config API integration.
> See CLAUDE.md for architectural summary.

---

## Design Principle

**Billing by configured capacity, not by consumption.**

A tenant pays for the capacity they configure (e.g. "10 human agent slots in the retention pool") regardless of whether those slots are fully utilized. This model makes costs predictable and avoids penalizing efficient operations.

Consumption metering still happens (see `docs/arcos/usage-metering.md`) but is not used for billing — it is used for operational analytics and future overage alerts.

---

## Billing Components

### 1. Base Capacity

Monthly capacity billing, pro-rated by actual days in the billing period.

Calculation:
```
base_invoice = sum(
    unit_price[resource_type] × quantity × billing_days / days_in_month
    for each resource in tenant plan
)
```

`billing_days` excludes days where the tenant had zero resources configured (e.g. mid-month provisioning).

### 2. Reserve Pools

Reserve pools are on-demand capacity that can be activated and deactivated at any time. Billing is per activation day (calendar day, not 24h window).

```
reserve_invoice = sum(
    unit_price[pool_id] × days_active × (1 + reserve_markup_pct/100)
    for each reserve pool
)
```

`reserve_markup_pct` default = 0%. Can be configured globally or per pool via Config API.

---

## Configuration

Config API namespace `pricing`:

| Key | Default | Description |
|---|---|---|
| `unit_prices` | `{}` | Map of resource_type/pool_id → price per unit per month |
| `reserve_markup_pct` | `0` | Global markup for reserve pool billing |
| `billing_cycle_day` | `1` | Day of month the billing cycle resets |
| `currency` | `"BRL"` | ISO-4217 currency code |

Example `unit_prices`:
```json
{
  "human_agent_slot":    49.90,
  "ai_agent_slot":       9.90,
  "retencao_reserva":    299.00
}
```

---

## REST Endpoints

All endpoints require `X-Admin-Token` header (same pattern as other admin endpoints).

### `GET /v1/pricing/invoice/{tenant_id}`

Returns current period invoice breakdown.

Query params:
- `?format=xlsx` — returns Excel workbook instead of JSON

JSON response:
```json
{
  "tenant_id":     "...",
  "period_start":  "2026-05-01",
  "period_end":    "2026-05-31",
  "billing_days":  31,
  "currency":      "BRL",
  "line_items": [
    { "type": "base", "resource": "human_agent_slot", "qty": 10, "unit_price": 49.90, "subtotal": 499.00 },
    { "type": "reserve", "pool_id": "retencao_reserva", "days": 5, "unit_price": 299.00, "subtotal": 1495.00 }
  ],
  "total": 1994.00
}
```

### `POST /v1/pricing/resources/{tenant_id}`

Set the tenant's base resource plan. Replaces existing plan.

```json
{
  "resources": [
    { "type": "human_agent_slot", "quantity": 10 },
    { "type": "ai_agent_slot",    "quantity": 5 }
  ]
}
```

This also writes quota limits to Redis: `{tenant}:quota:limit:{dimension}`.

### `POST /v1/pricing/reserve/{tenant_id}/{pool_id}/activate`

Activates a reserve pool. Records `activated_at` timestamp.

### `POST /v1/pricing/reserve/{tenant_id}/{pool_id}/deactivate`

Deactivates a reserve pool. Records `deactivated_at` and computes `days_active`.

---

## Quota Side Effects (implementado 2026-06-04 — capacity-governance item 1)

> Histórico: esta seção descrevia uma integração que **não existia** (descoberto na
> validação da Fase 2 Pools, 2026-06-04 — pricing-api não tinha código Redis e as
> chaves documentadas nem batiam com os leitores). Reescrita com o comportamento real.

Toda mutação de resources — `POST /v1/pricing/resources` (upsert), `DELETE`
(remoção), `activate`/`deactivate` de reserva — dispara o **quota sync**
(`quota_sync.py`): recalcula **C** do tenant (capacidade contratada de agentes =
`ai_agent` + `human_agent`, base + reservas comerciais ativas, todas as
instalações) e grava:

```
{tenant}:quota:max_concurrent_sessions = C     (C > 0)
                                          DEL  (C == 0 — sem resources → sem limite)
```

Leitores: `AdmissionController._shared_limit` no routing-engine (admissão híbrida:
`shared = C − Σ session_reservation`; rejeição vira outage `shared_full`) e
`checkConcurrentSessions` no mcp-server. Recompute completo e idempotente; no boot
o `sync_all` re-deriva a quota de todos os tenants com resources (auto-cura após
flush do Redis). `PLUGHUB_PRICING_REDIS_URL` vazia → sync desabilitado; Redis fora
→ loga warning e o billing segue (quota nunca quebra o pricing).

As dimensões de **uso** (`{tenant}:quota:limit:{dimension}` — messages,
llm_tokens, voice_minutes — lidas pelo `assertQuota()`) seguem **sem produtor**:
fazem parte de plano/consumo, não de capacidade — fora do escopo do
capacity-governance (ver TODO). O Config API seed não grava nenhuma dessas chaves.

---

## Platform-UI

`BillingPage.tsx` at `/config/billing` (role: `admin`).

Shows:
- Current period invoice preview
- Resource plan configuration form
- Reserve pool activation/deactivation controls
- Download invoice as XLSX
