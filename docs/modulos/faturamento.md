# Módulo: Faturamento

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/config/billing` | Roles: admin, business

## O que é

O módulo de Faturamento exibe e gerencia a cobrança por capacidade configurada — o modelo diferencial do PlugHub. Mostra a fatura mensal detalhada (base + reserve pools), histórico de ativações, dados de consumo (para curadoria de qualidade, não incluídos no faturamento) e permite export em XLSX.

## Princípio de cobrança

**Cobrança por capacidade configurada, não por consumo.** O cliente compra N instâncias de agente IA e M agentes humanos. O preço é fixo independente do volume de turnos, tokens ou mensagens.

### Dois componentes

| Componente | Granularidade | Lógica |
|---|---|---|
| **Base capacity** | Mensal proporcional (dias úteis no ciclo) | Recursos sempre ativos (ai_agent, human_agent, whatsapp_number, etc.) |
| **Reserve pools** | Full-day billing por ativação | Capacidade adicional ativada/desativada manualmente; se ativado em qualquer momento do dia D, o dia D inteiro é faturável |

### Preços padrão (configuráveis via Config API namespace `pricing`)

| Recurso | Preço mensal (BRL) |
|---|---|
| `ai_agent` | 120,00 |
| `human_agent` | 50,00 |
| `whatsapp_number` | 15,00 |
| `voice_trunk_in/out` | 40,00 cada |
| `email_inbox` | 25,00 |
| `sms_number` | 10,00 |
| `webchat_instance` | 20,00 |

## Layout da página

### ResourceSidebar (220 px)

Lista de recursos configurados, agrupados por pool_type (base / reserve). Campo de admin token local (necessário para ações de ativação/desativação). Não persiste — existe apenas na sessão atual do browser.

### Invoice tab

- Tabela de base items: recurso, tipo, quantidade, preço unitário, subtotal
- Grupos de reserve pools com toggle **Ativar / Desativar** e dias ativos no ciclo
- Totais por seção e GrandTotal em destaque
- Botão **Export XLSX** — link direto para `GET /v1/pricing/invoice/{tenantId}?format=xlsx`

### Consumption tab

- Dados de `GET /reports/usage` da analytics-api
- Agrega por dimensão (sessions, messages, llm_tokens_input/output, webchat_attachments)
- **Banner explícito**: "Dados de consumo não são incluídos no faturamento — disponíveis para curadoria de qualidade"

## APIs envolvidas

| Endpoint | Descrição |
|---|---|
| `GET /v1/pricing/invoice/{tenantId}` | Fatura em JSON (ciclo atual ou explícito) |
| `GET /v1/pricing/invoice/{tenantId}?format=xlsx` | Export XLSX com layout de fatura |
| `GET /v1/pricing/resources/{tenantId}` | Lista recursos configurados |
| `POST /v1/pricing/resources/{tenantId}` | Upsert recurso (admin) |
| `DELETE /v1/pricing/resources/{tenantId}/{resource_id}` | Remove recurso (admin) |
| `POST /v1/pricing/reserve/{tenantId}/{pool_id}/activate` | Ativa reserve pool (registra data de ativação) |
| `POST /v1/pricing/reserve/{tenantId}/{pool_id}/deactivate` | Desativa reserve pool |
| `GET /v1/pricing/reserve/{tenantId}/activity` | Log de ativações |
| `GET /reports/usage` | Dados de consumo (analytics-api) |

Auth admin: `X-Admin-Token` header (verificado contra `Settings.admin_token`; vazio = sem auth em dev).

## Cálculo de fatura

```
# Base items
daily_rate = unit_price / billing_days
subtotal   = daily_rate × quantity × billing_days   (sempre billing_days para base)

# Reserve items
reserve_unit  = unit_price × (1 + reserve_markup_pct / 100)
reserve_daily = reserve_unit / billing_days
subtotal      = reserve_daily × quantity × days_active   (dias distintos do log)
```

`billing_cycle_day` (default: 1) configura o dia do mês em que o ciclo se inicia. Editável via Config API namespace `pricing`.

## Diferencial competitivo

| Plataforma | Modelo de cobrança | Previsibilidade |
|---|---|---|
| Gemini Enterprise | Seats + runtime GB-hora + tokens + storage + indexação | Muito baixa |
| Agentforce (Flex) | Actions + créditos + tokens + implementação por agente | Baixa |
| Genesys | Seats + AI tokens por consumo | Média |
| LangGraph / n8n | Seats + execuções por nó | Baixa |
| **PlugHub** | **Instâncias configuradas** | **Alta — preço fixo** |

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `pricing-api` | Cálculo de fatura, CRUD de recursos, reserve pools, export XLSX (porta 3900) |
| `analytics-api` | Dados de consumo bruto (metering, não faturamento) |
| `platform-ui` | `modules/billing/BillingPage.tsx` |

## Referências

- Backend: `packages/pricing-api/`
- Frontend: `packages/platform-ui/src/modules/billing/BillingPage.tsx`
- Config: namespace `pricing` no Config API (unit_prices, reserve_markup_pct, billing_cycle_day, currency)
