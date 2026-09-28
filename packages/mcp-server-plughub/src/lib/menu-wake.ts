/**
 * lib/menu-wake.ts — avisa o bridge de que chegou algo para um `menu` (DUR-01 F3).
 *
 * Quem escreve em `menu:result` por fora do bridge (o `menu_submit` do Console e a resposta
 * do atendente a um agente de conferência) não pode acordar a conversa estacionada: só o
 * bridge chama o executor. Este aviso vai no tópico `menu.wake`, SEMPRE depois do `LPUSH`,
 * com a sessão como chave de partição.
 *
 * Falhar aqui não perde a resposta — ela está na lista —, mas deixa a conversa estacionada
 * até o prazo do menu (ou até a próxima coisa que a acorde). Por isso a falha é LOGADA com
 * essa consequência, nunca engolida.
 */
import { MenuWakeEventSchema, type MenuWakeEvent } from "@plughub/schemas"
import type { KafkaProducer } from "../infra/kafka"

export async function publishMenuWake(
  kafka: KafkaProducer,
  ev: Omit<MenuWakeEvent, "event_type" | "timestamp">,
): Promise<boolean> {
  try {
    const evento = MenuWakeEventSchema.parse({
      event_type: "menu_wake",
      timestamp:  new Date().toISOString(),
      ...ev,
    })
    await kafka.publish("menu.wake", evento, evento.session_id)
    return true
  } catch (err) {
    console.warn(
      `[menu.wake] aviso NÃO publicado (session=${ev.session_id} field=${ev.field} ` +
      `reason=${ev.reason}): se o menu estiver estacionado, ele só acorda no prazo — ${String(err)}`,
    )
    return false
  }
}
