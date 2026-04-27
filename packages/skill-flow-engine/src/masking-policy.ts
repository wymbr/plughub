/**
 * masking-policy.ts
 * Política de mascaramento para menu steps — fonte única da regra de precedência.
 *
 * Regra de precedência (field-level > step-level):
 *   field.masked === true   → campo mascarado, independente de step.masked
 *   field.masked === false  → campo NÃO mascarado, mesmo que step.masked=true
 *   field.masked undefined  → herda step.masked
 *
 * Usada em dois momentos do ciclo de vida do menu step:
 *   1. Ao ENVIAR ao canal  → computeMaskedFieldIds() → masked_fields[] para o webchat
 *   2. Ao RECEBER resposta → isFieldMasked() → routing para maskedScope vs pipeline_state
 *
 * Nota: para interações text (sem fields[]), o campo implícito é step.output_as ?? step.id.
 * Passe-o como implicitFieldId para que masked_fields contenha ao menos um ID, sinalizando
 * ao channel-gateway que deve renderizar <input type="password"> no webchat.
 */

export type MaskedFieldDef = {
  id:      string
  masked?: boolean
}

/**
 * Verifica se um campo específico é mascarado, aplicando a regra de precedência.
 * Quando field.masked é undefined (campo não declarado), herda step.masked.
 */
export function isFieldMasked(
  field:      MaskedFieldDef,
  stepMasked: boolean | undefined,
): boolean {
  if (field.masked === true)  return true
  if (field.masked === false) return false   // override explícito: não mascarado
  return stepMasked === true                 // herda step-level
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
  stepMasked:       boolean | undefined,
  fields:           MaskedFieldDef[] | undefined,
  implicitFieldId?: string,
): string[] {
  // Interação sem fields[] declarados (ex: text, button, list)
  if (!fields?.length) {
    // Mascaramento step-level com campo implícito (ex: text + masked:true)
    if (stepMasked === true && implicitFieldId) return [implicitFieldId]
    return []
  }

  // Fast path: nenhum mascaramento ativo
  if (!stepMasked && !fields.some(f => f.masked === true)) return []

  return fields
    .filter(f => isFieldMasked(f, stepMasked))
    .map(f => f.id)
}
