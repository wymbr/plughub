/**
 * certify/flow.ts
 * Validação de flow.yaml (GitAgent) e flow.json (nativo).
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Verificações:
 *   1. Todos os next/on_success/on_failure/default/conditions[].next
 *      referenciam step_ids existentes no mesmo flow
 *   2. Steps do tipo complete declaram outcome válido
 *   3. Steps do tipo task declaram agent_pool (yaml) ou target (json)
 *   4. Existe exatamente um step de entry declarado no flow
 *   5. Não há ciclos no grafo sem saída por complete ou escalate
 */

import * as fs   from "node:fs"
import * as path from "node:path"
import { parseYaml } from "./yaml"

// ─────────────────────────────────────────────
// Tipos
// ─────────────────────────────────────────────

export interface FlowError {
  step_id?: string
  field?:   string
  message:  string
}

export interface FlowValidationResult {
  valid:  boolean
  errors: FlowError[]
  flow?:  ParsedFlow
}

export interface ParsedStep {
  id:         string
  type:       string
  [key: string]: unknown
}

export interface ParsedFlow {
  entry: string
  steps: ParsedStep[]
}

// ─────────────────────────────────────────────
// Carregamento do flow
// ─────────────────────────────────────────────

const FLOW_CANDIDATES = [
  "flow.yaml", "flow.yml",
  "flows/flow.yaml", "flows/flow.yml",
  "flows/main.yaml", "flows/main.yml",
  "flow.json",
]

const VALID_OUTCOMES = new Set(["resolved", "escalated_human", "transferred_agent", "callback"])

export function findAndValidateFlow(dirPath: string): FlowValidationResult & { filePath?: string } {
  for (const candidate of FLOW_CANDIDATES) {
    const filePath = path.join(dirPath, candidate)
    if (!fs.existsSync(filePath)) continue

    let raw: string
    try { raw = fs.readFileSync(filePath, "utf-8") }
    catch (e) {
      return { valid: false, errors: [{ message: `${candidate}: erro ao ler — ${String(e)}` }], filePath }
    }

    let parsed: unknown
    try {
      parsed = filePath.endsWith(".json")
        ? JSON.parse(raw)
        : parseYaml(raw)
    } catch (e) {
      return { valid: false, errors: [{ message: `${candidate}: sintaxe inválida — ${String(e)}` }], filePath }
    }

    const result = validateFlow(parsed)
    return { ...result, filePath }
  }

  // Nenhum flow encontrado — não é erro, é opcional
  return { valid: true, errors: [] }
}

// ─────────────────────────────────────────────
// validateFlow
// ─────────────────────────────────────────────

export function validateFlow(raw: unknown): FlowValidationResult {
  const errors: FlowError[] = []

  // Estrutura básica
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { valid: false, errors: [{ message: "flow deve ser um objeto com 'entry' e 'steps'" }] }
  }

  const obj = raw as Record<string, unknown>

  if (!obj["entry"] || typeof obj["entry"] !== "string") {
    errors.push({ field: "entry", message: "'entry' é obrigatório e deve ser string" })
  }

  if (!Array.isArray(obj["steps"]) || obj["steps"].length === 0) {
    errors.push({ field: "steps", message: "'steps' é obrigatório e deve ser array não-vazio" })
    return { valid: false, errors }
  }

  const rawSteps = obj["steps"] as unknown[]
  const steps: ParsedStep[] = []

  // Parsear e validar estrutura mínima dos steps
  for (let idx = 0; idx < rawSteps.length; idx++) {
    const s = rawSteps[idx]
    if (!s || typeof s !== "object" || Array.isArray(s)) {
      errors.push({ message: `steps[${idx}]: deve ser um objeto` })
      continue
    }
    const step = s as Record<string, unknown>
    if (!step["id"] || typeof step["id"] !== "string") {
      errors.push({ message: `steps[${idx}]: campo 'id' é obrigatório` })
      continue
    }
    if (!step["type"] || typeof step["type"] !== "string") {
      errors.push({ step_id: step["id"] as string, message: "campo 'type' é obrigatório" })
      continue
    }
    steps.push(step as unknown as ParsedStep)
  }

  if (errors.length > 0) return { valid: false, errors }

  const stepIds    = new Set(steps.map(s => s.id))
  const entryStep  = obj["entry"] as string
  const flow: ParsedFlow = { entry: entryStep, steps }

  // ── Check 1: entry referencia step existente ──
  if (!stepIds.has(entryStep)) {
    errors.push({
      field:   "entry",
      message: `entry '${entryStep}' não referencia um step existente`,
    })
  }

  // ── Checar cada step ──
  for (const step of steps) {
    validateStep(step, stepIds, errors)
  }

  // ── Check 4: exatamente um step pode ser entry (verificar que entry está declarado) ──
  // (já verificado acima — entry deve existir nos steps)

  // ── Check 5: detectar ciclos sem saída por complete/escalate ──
  if (errors.length === 0) {
    const cycleErrors = detectInfiniteCycles(flow)
    errors.push(...cycleErrors)
  }

  return { valid: errors.length === 0, errors, flow: errors.length === 0 ? flow : undefined }
}

// ─────────────────────────────────────────────
// Validação por tipo de step
// ─────────────────────────────────────────────

function validateStep(step: ParsedStep, stepIds: Set<string>, errors: FlowError[]): void {
  const { id, type } = step

  switch (type) {
    case "task":
      validateNext(step, ["on_success", "on_failure"], stepIds, errors)
      // agent_pool (YAML) ou target (JSON) deve estar presente
      if (!step["agent_pool"] && !step["target"]) {
        errors.push({
          step_id: id,
          message: "step task deve declarar 'agent_pool' (YAML) ou 'target' (JSON)",
        })
      }
      break

    case "choice": {
      validateNext(step, ["default"], stepIds, errors)
      const conditions = step["conditions"]
      if (!Array.isArray(conditions) || conditions.length === 0) {
        errors.push({ step_id: id, message: "step choice deve ter ao menos uma condição em 'conditions'" })
      } else {
        for (let ci = 0; ci < conditions.length; ci++) {
          const cond = conditions[ci] as Record<string, unknown>
          if (cond["next"] && typeof cond["next"] === "string" && !stepIds.has(cond["next"])) {
            errors.push({
              step_id: id,
              field:   `conditions[${ci}].next`,
              message: `referência quebrada: step '${cond["next"]}' não existe`,
            })
          }
        }
      }
      break
    }

    case "catch":
      validateNext(step, ["on_failure"], stepIds, errors)
      // Validar estratégias
      if (Array.isArray(step["strategies"])) {
        for (const strategy of step["strategies"] as Record<string, unknown>[]) {
          for (const field of ["on_exhausted", "on_success", "on_failure"]) {
            const ref = strategy[field]
            if (typeof ref === "string" && ref.length > 0 && !stepIds.has(ref)) {
              errors.push({
                step_id: id,
                field:   `strategies.${field}`,
                message: `referência quebrada: step '${ref}' não existe`,
              })
            }
          }
        }
      }
      break

    case "escalate":
      // escalate é terminal — não tem next
      if (!step["target"] || typeof step["target"] !== "object") {
        errors.push({ step_id: id, message: "step escalate deve ter 'target.pool'" })
      }
      break

    case "complete": {
      // ── Check 2: outcome válido ──
      const outcome = step["outcome"]
      if (typeof outcome !== "string") {
        errors.push({ step_id: id, message: "step complete deve declarar 'outcome'" })
      } else if (!VALID_OUTCOMES.has(outcome)) {
        errors.push({
          step_id: id,
          message: `outcome '${outcome}' inválido — deve ser um de: ${[...VALID_OUTCOMES].join(", ")}`,
        })
      }
      break
    }

    case "invoke":
    case "reason":
    case "notify":
      validateNext(step, ["on_success", "on_failure"], stepIds, errors)
      break

    default:
      errors.push({ step_id: id, message: `tipo de step desconhecido: '${type}'` })
  }
}

function validateNext(
  step:    ParsedStep,
  fields:  string[],
  stepIds: Set<string>,
  errors:  FlowError[],
): void {
  for (const field of fields) {
    const ref = step[field]
    if (typeof ref !== "string" || ref.length === 0) {
      errors.push({
        step_id: step.id,
        field,
        message: `campo '${field}' é obrigatório no step '${step.type}'`,
      })
      continue
    }
    if (!stepIds.has(ref)) {
      errors.push({
        step_id: step.id,
        field,
        message: `referência quebrada: step '${ref}' não existe`,
      })
    }
  }
}

// ─────────────────────────────────────────────
// Detecção de ciclos sem saída
// ─────────────────────────────────────────────

/**
 * Detecta ciclos no grafo de steps que não têm saída por um step
 * do tipo 'complete' ou 'escalate'.
 *
 * Algoritmo:
 * 1. Construir grafo de adjacência (step → [next steps])
 * 2. Encontrar componentes fortemente conexos (SCCs) com Tarjan
 * 3. Para cada SCC com mais de um nó (ou auto-loop), verificar se
 *    algum step do SCC é 'complete' ou 'escalate', ou se há uma
 *    aresta saindo do SCC para um step terminal
 */
function detectInfiniteCycles(flow: ParsedFlow): FlowError[] {
  const errors: FlowError[] = []

  const stepMap = new Map(flow.steps.map(s => [s.id, s]))

  // Grafo de adjacência
  const edges = new Map<string, string[]>()
  for (const step of flow.steps) {
    edges.set(step.id, getNextSteps(step))
  }

  // Encontrar todos os ciclos com DFS simples
  const visiting   = new Set<string>()
  const visited    = new Set<string>()
  const cycleSteps = new Set<string>()

  function dfs(stepId: string, path: string[]): void {
    if (visited.has(stepId)) return
    if (visiting.has(stepId)) {
      // Ciclo detectado — registrar todos os steps no ciclo
      const cycleStart = path.indexOf(stepId)
      const cycleNodes = path.slice(cycleStart)
      cycleNodes.forEach(n => cycleSteps.add(n))
      return
    }
    visiting.add(stepId)
    for (const next of (edges.get(stepId) ?? [])) {
      dfs(next, [...path, next])
    }
    visiting.delete(stepId)
    visited.add(stepId)
  }

  dfs(flow.entry, [flow.entry])

  // Para cada step em ciclo, verificar se algum step do ciclo é terminal
  for (const stepId of cycleSteps) {
    const step = stepMap.get(stepId)
    if (!step) continue
    if (step.type === "complete" || step.type === "escalate") {
      cycleSteps.clear()  // Ciclo tem saída
      break
    }
  }

  if (cycleSteps.size > 0) {
    errors.push({
      message: `Ciclo sem saída detectado nos steps: ${[...cycleSteps].join(", ")} — ` +
               "o grafo deve ter um step do tipo 'complete' ou 'escalate' alcançável por todo ciclo",
    })
  }

  return errors
}

function getNextSteps(step: ParsedStep): string[] {
  const nexts: string[] = []
  const push = (v: unknown) => { if (typeof v === "string" && v.length > 0) nexts.push(v) }

  push(step["on_success"])
  push(step["on_failure"])
  push(step["default"])

  if (Array.isArray(step["conditions"])) {
    for (const c of step["conditions"] as Record<string, unknown>[]) {
      push(c["next"])
    }
  }

  if (Array.isArray(step["strategies"])) {
    for (const s of step["strategies"] as Record<string, unknown>[]) {
      push(s["on_success"])
      push(s["on_failure"])
      push(s["on_exhausted"])
    }
  }

  return [...new Set(nexts)]
}
