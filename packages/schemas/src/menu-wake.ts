/**
 * menu-wake.ts
 * Aviso de que chegou algo para um `menu` que pode estar ESTACIONADO (DUR-01 F3).
 *
 * Tópico Kafka: menu.wake — produtores mcp-server-plughub (`menu_submit` e a resposta do
 * atendente a um agente de conferência) e routing-engine (sinais do agente de fila:
 * `__agent_available__`, `__queue_timeout__`); consumidor ÚNICO orchestrator-bridge,
 * que chama `wake_parked_run`. Chave de partição = `session_id`.
 *
 * Por que um tópico e não uma chamada: o bridge é o único que chama o executor
 * (`/execute`), e os escritores de `menu:result` fora dele não podem acordar a conversa
 * sozinhos. Não é `conversations.inbound` porque o routing-engine também o consome e
 * leria o aviso como contato novo.
 *
 * O aviso é SEMPRE posterior ao `LPUSH` da resposta — é essa ordem que o estacionamento
 * atômico protege (ou o acordar acha o lock livre, ou quem o segura acha a resposta).
 * Aviso para menu que NÃO está estacionado é inofensivo: o bridge não acha registro e
 * não faz nada.
 */

import { z } from "zod"

export const MENU_WAKE_REASONS = ["menu_submit", "agent_reply", "agent_available", "queue_timeout"] as const

export const MenuWakeEventSchema = z.object({
  event_type: z.literal("menu_wake"),
  tenant_id:  z.string().min(1),
  session_id: z.string().min(1),
  /** Campo do `menu:waiting:{sid}` — o instance_id do agente, ou `_default_` (agente de fila). */
  field:      z.string().min(1),
  reason:     z.enum(MENU_WAKE_REASONS),
  timestamp:  z.string().datetime({ offset: true }),
}).strict()

export type MenuWakeEvent = z.infer<typeof MenuWakeEventSchema>
