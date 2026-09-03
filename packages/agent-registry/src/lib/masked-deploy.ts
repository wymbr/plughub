/**
 * masked-deploy.ts — deploy de skill que MASCARA num pool que não tem canal capaz.
 *
 * NIV-03 (metade de DEPLOY). ADR `adr-agent-flow-single-authored-level.md` § F3:
 * *"skill com campo `masked` promovida a pool sem nenhum canal `masked_input` é
 * decidível estaticamente, e pega antes de haver cliente do outro lado"*.
 *
 * ── Duas perguntas, dois desfechos, e confundi-las seria o erro ─────────────
 *
 * **IMPOSSÍVEL ⇒ 422.** O pool não tem NENHUM canal que colete valor mascarado.
 * Não há contato, em canal nenhum, em que este deploy funcione: todo menu
 * mascarado será recusado em runtime pela guarda do `notification_send`. É
 * defeito de configuração, decidível sem cliente do outro lado, e o único momento
 * em que há um humano olhando é este.
 *
 * **PARCIAL ⇒ aviso, nunca recusa.** O pool tem canais capazes *e* incapazes. Aí
 * o desfecho depende de por onde o contato chega — o que não é estático, e por
 * isso não é decidível aqui. Recusar seria proibir uma configuração legítima (um
 * pool multicanal cuja coleta sensível só acontece no webchat); calar seria a
 * MSK-01 outra vez, que é literalmente esta situação (`limite_ia` com
 * `[webchat, whatsapp]`). O aviso viaja no corpo da resposta **e** no log do
 * serviço: as duas casas, porque a UI pode não mostrar o corpo e o log pode não
 * ser lido, e a informação some se depender de só uma.
 *
 * ⚠️ **O que este módulo NÃO enxerga**, e é decisão de escopo: máscara declarada
 * em `DialogForm` referenciado por id (`dialog_form_id`), que é o caso do
 * `dialog_limite_solicitacao` (`cvv`). Resolver aquilo aqui exigiria o
 * agent-registry falar com o dialog-api dentro do caminho de deploy. Não fica
 * descoberto: a guarda de RUNTIME pega, porque lá o menu já vem com
 * `masked_fields` preenchido, venha de onde vier. Aqui pega-se o que é estático.
 */

import { CHANNEL_CAPABILITIES, MASKED_INPUT, channelSatisfies } from "@plughub/schemas"

/** `masked` declarado ⇒ mascara, salvo o override explícito `false`. */
function declMasks(v: unknown): boolean {
  return v !== undefined && v !== null && v !== false
}

/**
 * Steps do snapshot que declaram `masked` — no nível do step ou de um campo.
 * Devolve rótulos legíveis (`coletar_dados.senha`), porque a mensagem de recusa
 * precisa dizer ONDE mexer; um booleano manda o autor procurar.
 */
export function maskedDeclarations(snapshot: unknown): string[] {
  const out: string[] = []
  const flow = snapshot as { steps?: unknown[] } | null
  if (!flow || !Array.isArray(flow.steps)) return out
  for (const raw of flow.steps) {
    const st = raw as Record<string, unknown> | null
    if (!st || typeof st !== "object") continue
    const id = typeof st["id"] === "string" ? st["id"] : "(sem id)"
    if (declMasks(st["masked"])) out.push(id)
    const fields = st["fields"]
    if (Array.isArray(fields)) {
      for (const f of fields as Record<string, unknown>[]) {
        if (f && typeof f === "object" && declMasks(f["masked"])) {
          out.push(`${id}.${typeof f["id"] === "string" ? f["id"] : "?"}`)
        }
      }
    }
  }
  return out
}

export type MaskedDeployVerdict =
  | { kind: "ok" }
  | { kind: "warn";  warning: string }
  | { kind: "block"; error: string; message: string }

/**
 * Julga um par (snapshot, canais do pool).
 *
 * `channelTypes` vazio é tratado como **desconhecido, não como zero**: um pool sem
 * `channel_types` declarado não afirma que não atende canal nenhum — afirma que
 * ninguém declarou. Bloquear ali converteria omissão em recusa, e o parque tem
 * pools assim (webhook, por exemplo, que nem cliente tem). O silêncio aqui é
 * coberto pela guarda de runtime.
 */
export function judgeMaskedDeploy(
  snapshot:     unknown,
  channelTypes: unknown,
  ctx:          { poolId: string; skillId: string },
): MaskedDeployVerdict {
  const decls = maskedDeclarations(snapshot)
  if (decls.length === 0) return { kind: "ok" }

  const canais = Array.isArray(channelTypes)
    ? (channelTypes as unknown[]).filter((c): c is string => typeof c === "string")
    : []
  if (canais.length === 0) return { kind: "ok" }

  const capazes   = canais.filter((c) => channelSatisfies(c, [MASKED_INPUT]))
  const incapazes = canais.filter((c) => !channelSatisfies(c, [MASKED_INPUT]))

  if (capazes.length === 0) {
    const sabem = Object.keys(CHANNEL_CAPABILITIES).filter(
      (c) => (CHANNEL_CAPABILITIES as Record<string, readonly string[]>)[c]!.includes(MASKED_INPUT),
    )
    return {
      kind:  "block",
      error: "masked_sem_canal_capaz",
      message:
        `O skill '${ctx.skillId}' coleta valor mascarado (${decls.join(", ")}), e o pool ` +
        `'${ctx.poolId}' não declara nenhum canal capaz disso — canais do pool: ` +
        `[${canais.join(", ")}]; canais que sabem mascarar: [${sabem.join(", ")}]. ` +
        `Em runtime TODO menu mascarado seria recusado, então este deploy não funciona ` +
        `em contato nenhum. Conserto: acrescentar um canal capaz ao pool, ou remover a ` +
        `declaração 'masked' do fluxo.`,
    }
  }

  if (incapazes.length > 0) {
    return {
      kind: "warn",
      warning:
        `deploy PARCIALMENTE mascarável: o skill '${ctx.skillId}' coleta valor mascarado ` +
        `(${decls.join(", ")}) e o pool '${ctx.poolId}' também atende [${incapazes.join(", ")}], ` +
        `que não sabem mascarar. Contato que chegar por esses canais terá o menu RECUSADO ` +
        `em runtime (on_failure), não entregue em claro. Se isso não é o desejado, tire ` +
        `esses canais do pool.`,
    }
  }

  return { kind: "ok" }
}
