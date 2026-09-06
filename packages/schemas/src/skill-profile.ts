/**
 * skill-profile.ts — quais tipos de step cada PERFIL admite (CTR-01 / G1).
 *
 * ── Por que este arquivo existe ─────────────────────────────────────────────
 *
 * O Arc 19 dividiu os fluxos em dois perfis — `workflow` (pool com canal
 * `webhook`) e `agent` (demais canais) — e declarou que cada um proíbe certos
 * tipos de step. O `CLAUDE.md` afirmava que isso era *"validado em parse do YAML
 * + guard no engine"*.
 *
 * **Medido em 2026-09-06: não existia allowlist de step por perfil em lugar
 * nenhum** — nem no validador do agent-registry, nem no `executor.ts`, nem no
 * `engine.ts`. A regra vivia em prosa e num comentário de schema. É a família do
 * DDL de `participation_intervals`: promessa sem mecanismo.
 *
 * ── A regra que a medição REFUTOU ───────────────────────────────────────────
 *
 * O comentário do `DelegateStep` dizia, em letras claras: *"Only valid in
 * workflow profile. Agents must never use delegate."* Isso está **errado**, e
 * não por descuido de quem escreveu — por evidência de uso:
 *
 *   · `limite_ia` (perfil agente) roda `skill_limite_entrada_v1`, que delega
 *     para `dialog_runner` e `limite_retorno`. **186 segmentos**, o último em
 *     2026-09-05;
 *   · `portabilidade_ia` (perfil agente) delega para `dialog_runner` e
 *     `portabilidade_confirmacao`;
 *   · o alvo `dialog_runner` tem **24 segmentos** — o caminho não é declarado,
 *     é EXERCIDO.
 *
 * Impor a regra ao pé da letra teria recusado **dois pools vivos**. Quando a
 * spec e o código discordam, desconfie dos dois — e aqui quem está errado é a
 * spec. `delegate` sai da lista de proibidos do perfil `agent`.
 *
 * ⚠️ **Isto NÃO é afrouxar por conveniência.** `suspend` e `collect` continuam
 * fora do perfil `agent`, e `menu`/`notify`/transação continuam fora do
 * `workflow` — medido: **0 violações em 31 deploys vivos** com esta lista, então
 * ela pode ser imposta hoje sem migração nenhuma. O que mudou foi um item, e
 * mudou porque havia contraprova.
 *
 * ── Onde isto é imposto, e por que não no publish ───────────────────────────
 *
 * O perfil é fato do **POOL**, não do skill: o mesmo skill pode ser deployado em
 * pools de perfis diferentes. Logo `PUT /v1/skills/:id` **não tem como saber** o
 * perfil — validar ali exigiria adivinhar onde o skill vai rodar.
 *
 * O portão é o **DEPLOY** (`set-next` e `promote`), onde o pool é conhecido. É o
 * mesmo lugar e a mesma forma da checagem de capacidade que já existe ali:
 * feedback cedo na declaração, re-checado no promote porque o pool pode mudar
 * no meio.
 */

/** Perfil de execução de um fluxo, derivado dos canais do POOL. */
export type SkillProfile = "workflow" | "agent"

/**
 * Tipos de step PROIBIDOS em cada perfil.
 *
 * `workflow` — não fala com o cliente por canal síncrono: `menu`/`notify` e o
 *   bloco de transação mascarada pressupõem uma conversa do outro lado.
 * `agent`    — não pode SUSPENDER esperando sinal externo (`suspend`) nem abrir
 *   uma sessão-filho de coleta (`collect`): há um cliente conectado esperando.
 *
 * ⚠️ `delegate` **não** está aqui, e a ausência é a decisão — ver o cabeçalho.
 */
export const SKILL_PROFILE_FORBIDDEN_STEPS: Readonly<Record<SkillProfile, ReadonlyArray<string>>> = {
  workflow: ["menu", "notify", "begin_transaction", "end_transaction"],
  agent:    ["suspend", "collect"],
}

/**
 * Deriva o perfil dos canais do pool.
 *
 * ⚠️ Ausência de `channel_types` resolve para `agent`, e isso é o lado
 * RESTRITIVO: a lista do agente proíbe suspensão, que é o que não se pode fazer
 * com um cliente esperando. Resolver para `workflow` liberaria `suspend` num
 * pool cuja config ainda não foi lida — degradação que só apareceria com o
 * cliente na linha.
 */
export function skillProfileFor(channelTypes: ReadonlyArray<string> | null | undefined): SkillProfile {
  return (channelTypes ?? []).includes("webhook") ? "workflow" : "agent"
}

/**
 * Tipos de step do fluxo que o perfil não admite. Vazio ⇒ conforme.
 *
 * Devolve os tipos, não os ids: a mensagem de recusa precisa dizer O QUE não é
 * permitido ali, e quem autorou encontra os steps pelo tipo. Devolver ids faria
 * a mensagem crescer com o tamanho do fluxo sem dizer mais nada.
 */
export function forbiddenStepsForProfile(
  profile: SkillProfile,
  steps:   ReadonlyArray<{ type?: string }>,
): string[] {
  const proibidos = new Set(SKILL_PROFILE_FORBIDDEN_STEPS[profile])
  const achados   = new Set<string>()
  for (const s of steps) {
    const t = s?.type
    if (typeof t === "string" && proibidos.has(t)) achados.add(t)
  }
  return [...achados].sort()
}
