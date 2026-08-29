/**
 * masking-policy.ts
 * Política de mascaramento para menu steps — fonte única da regra de precedência.
 *
 * Regra de precedência (field-level > step-level), sobre a DECLARAÇÃO `masked`,
 * que desde a T2 é `boolean | string` (string = id de tipo do catálogo):
 *   field.masked = "<tipo>"  → mascarado com aquele tipo, independente do step
 *   field.masked = true      → mascarado com o tipo `opaque` (o mais restritivo)
 *   field.masked === false   → campo NÃO mascarado, mesmo que o step mascare
 *   field.masked undefined   → herda o step (tipo inclusive)
 *
 * ⚠️ A regra vive em UMA função — `maskedFieldType`. `isFieldMasked` e
 * `computeMaskedFieldIds` são derivações dela. Reaplicar a precedência noutro
 * lugar cria duas respostas para "este campo é mascarado?", e a permissiva vence.
 *
 * Usada em dois momentos do ciclo de vida do menu step:
 *   1. Ao ENVIAR ao canal  → computeMaskedFieldIds() → masked_fields[] para o webchat
 *   2. Ao RECEBER resposta → isFieldMasked() → routing para maskedScope vs pipeline_state
 *
 * Nota: para interações text (sem fields[]), o campo implícito é step.output_as ?? step.id.
 * Passe-o como implicitFieldId para que masked_fields contenha ao menos um ID, sinalizando
 * ao channel-gateway que deve renderizar <input type="password"> no webchat.
 */

import { OPAQUE_DATA_TYPE_ID } from "@plughub/schemas"

/**
 * A declaração `masked` — booleano OU id de tipo do catálogo (T2 do ADR
 * `adr-masked-typed-declaration.md`).
 *
 *   `"cpf"`  → mascarado, tipo `cpf`
 *   `true`   → mascarado, tipo `opaque` (o mais restritivo). **Não é "sem tipo"**
 *   `false`  → override explícito: não mascarado
 *   ausente  → herda o step
 */
export type MaskedDecl = boolean | string | undefined

export type MaskedFieldDef = {
  id:      string
  masked?: MaskedDecl   // undefined permitido para compatibilidade com exactOptionalPropertyTypes
}

/**
 * Normaliza UMA declaração, sem olhar herança.
 *   `undefined` → herdar   ·   `false` → não mascarar   ·   demais → id do tipo
 *
 * String vazia resolve para `opaque`, **não** para "não mascarar": declaração
 * malformada é caso de recusa no deploy (D3), e em runtime o lado seguro é o
 * restritivo. Tratá-la como `false` seria fail-OPEN no único ponto do produto em
 * que degradar significa vazar.
 */
function normalizeDecl(d: MaskedDecl): string | false | undefined {
  if (d === undefined) return undefined
  if (d === false)     return false
  // ⚠️ NÃO REMOVER. Assimetria DELIBERADA com o schema (T7-A, 2026-08-29): a
  // ESCRITA já não aceita `true` (`MaskedDeclarationSchema`), mas o RUNTIME
  // continua tolerando — e a razão é medida, não conservadorismo.
  //
  // O snapshot do slot NÃO é validado por Zod na execução (o skill-flow-service é
  // wrapper fino sobre o engine) e o `POST /v1/pools/:id/rollback` apenas troca
  // linhas de slot, sem revalidar. Um slot `previous` anterior à T6 portanto
  // continua executável — e tem de continuar. Sem esta linha, `true` cairia no
  // `d.trim()` abaixo: TypeError sobre um booleano, no meio de um atendimento.
  //
  // Sai junto da metade (B) da T7, quando nenhum snapshot ALCANÇÁVEL (current,
  // next E previous, por tenant) tiver a forma anônima — o eixo 1b do
  // `q_masked_declaration_census.sh` reporta os dois números separados.
  if (d === true)      return OPAQUE_DATA_TYPE_ID
  const trimmed = d.trim()
  return trimmed.length ? trimmed : OPAQUE_DATA_TYPE_ID
}

/**
 * O step mascara? Booleano DERIVADO da declaração, para os call sites cujo contrato
 * é booleano — hoje o `waitingMeta` lido pelo bridge como `any_masked`.
 *
 * ⚠️ Nunca testar `step.masked === true` na mão: com a união, `masked: "cpf"` daria
 * `false` e o step deixaria de suprimir. É a forma exata do fail-open.
 */
export function isStepMasked(stepMasked: MaskedDecl): boolean {
  const t = normalizeDecl(stepMasked)
  return t !== undefined && t !== false
}

/**
 * O TIPO efetivo de um campo, aplicando a precedência — ou `null` se não mascarado.
 *
 * É esta a função que aplica a precedência; `isFieldMasked` e `computeMaskedFieldIds`
 * são DERIVAÇÕES dela. Duas funções que reaplicassem a regra seriam duas casas para
 * a mesma pergunta, e a mais permissiva venceria.
 */
export function maskedFieldType(
  field:      MaskedFieldDef,
  stepMasked: MaskedDecl,
): string | null {
  const own = normalizeDecl(field.masked)
  if (own === false) return null           // override explícito
  if (own !== undefined) return own        // campo declara o próprio tipo
  const inherited = normalizeDecl(stepMasked)
  return inherited === undefined || inherited === false ? null : inherited
}

/**
 * Resolve o step INTEIRO numa passada: os ids mascarados e o tipo de cada um.
 *
 * Duas perguntas, dois resultados, **uma** resolução (D2):
 *   · `ids`   — dirigem a renderização de canal (overlay de senha), que não quer
 *               saber de tipo;
 *   · `types` — dirigem redação e classificação, que querem.
 */
export function resolveMaskedFields(
  stepMasked:       MaskedDecl,
  fields:           MaskedFieldDef[] | undefined,
  implicitFieldId?: string,
): { ids: string[]; types: Record<string, string> } {
  const types: Record<string, string> = {}

  // Interação sem fields[] declarados (ex: text, button, list): o campo implícito
  // herda o step, e só existe se o step mascarar.
  if (!fields?.length) {
    const stepType = normalizeDecl(stepMasked)
    if (stepType && implicitFieldId) {
      types[implicitFieldId] = stepType
      return { ids: [implicitFieldId], types }
    }
    return { ids: [], types }
  }

  const ids: string[] = []
  for (const f of fields) {
    const t = maskedFieldType(f, stepMasked)
    if (t === null) continue
    ids.push(f.id)
    types[f.id] = t
  }
  return { ids, types }
}

/**
 * Verifica se um campo específico é mascarado, aplicando a regra de precedência.
 * Quando field.masked é undefined (campo não declarado), herda step.masked.
 */
export function isFieldMasked(
  field:      MaskedFieldDef,
  stepMasked: MaskedDecl,
): boolean {
  return maskedFieldType(field, stepMasked) !== null   // DERIVAÇÃO — a regra vive em maskedFieldType
}

/**
 * Computa a lista de IDs de campos que devem ser enviados como masked_fields[]
 * ao canal (webchat: <input type="password">).
 *
 * @param stepMasked     - Valor de step.masked
 * @param fields         - Lista de field definitions do step (pode ser undefined para text)
 * @param implicitFieldId - ID implícito para interações sem fields[] (ex: step.output_as ?? step.id)
 *
 * Retorna [] quando nenhum campo é mascarado (evita enviar array vazio desnecessário).
 */
export function computeMaskedFieldIds(
  stepMasked:       MaskedDecl,
  fields:           MaskedFieldDef[] | undefined,
  implicitFieldId?: string,
): string[] {
  // DERIVAÇÃO de `resolveMaskedFields` (D2). Mantida porque os call sites que só
  // querem os ids não precisam do mapa de tipos — e CONTADA para sair quando eles
  // migrarem, como toda dívida deste arco: por contador, nunca por decreto.
  return resolveMaskedFields(stepMasked, fields, implicitFieldId).ids
}
