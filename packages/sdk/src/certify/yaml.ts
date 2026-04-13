/**
 * certify/yaml.ts
 * Tiny YAML parser para o subconjunto usado em agent.yaml e flow.yaml.
 * Suporta: objetos por indentação, arrays (-), strings, números, booleanos.
 * Não requer dependência externa.
 */

export function parseYaml(content: string): unknown {
  const lines = preprocess(content)
  const root  = parseBlock(lines, 0)
  return root.value
}

// ─────────────────────────────────────────────
// Tipos internos
// ─────────────────────────────────────────────

interface Line {
  indent: number
  raw:    string  // conteúdo após remoção da indentação e de comentários
}

interface ParseResult {
  value: unknown
  next:  number   // próximo índice a ser lido
}

// ─────────────────────────────────────────────
// Pré-processamento
// ─────────────────────────────────────────────

function preprocess(content: string): Line[] {
  return content
    .split("\n")
    .map(line => {
      // Remove comentários inline (mas não dentro de strings)
      const commentIdx = findCommentIndex(line)
      const raw = (commentIdx >= 0 ? line.slice(0, commentIdx) : line).trimEnd()
      const indent = raw.length - raw.trimStart().length
      return { indent, raw: raw.trimStart() }
    })
    .filter(l => l.raw.length > 0)  // remove linhas vazias e comentários puros
}

function findCommentIndex(line: string): number {
  let inSingle = false
  let inDouble = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!
    if (ch === "'" && !inDouble) inSingle = !inSingle
    else if (ch === '"' && !inSingle) inDouble = !inDouble
    else if (ch === "#" && !inSingle && !inDouble) return i
  }
  return -1
}

// ─────────────────────────────────────────────
// Parser recursivo
// ─────────────────────────────────────────────

function parseBlock(lines: Line[], startIdx: number): ParseResult {
  if (startIdx >= lines.length) return { value: null, next: startIdx }

  const firstLine = lines[startIdx]!
  const baseIndent = firstLine.indent

  // Detectar se é array ou objeto com base na primeira linha
  if (firstLine.raw.startsWith("- ") || firstLine.raw === "-") {
    return parseArray(lines, startIdx, baseIndent)
  }

  if (firstLine.raw.includes(": ") || firstLine.raw.endsWith(":")) {
    return parseObject(lines, startIdx, baseIndent)
  }

  // Valor escalar isolado
  return { value: parseScalar(firstLine.raw), next: startIdx + 1 }
}

function parseObject(lines: Line[], startIdx: number, baseIndent: number): ParseResult {
  const obj: Record<string, unknown> = {}
  let i = startIdx

  while (i < lines.length) {
    const line = lines[i]!
    if (line.indent < baseIndent) break

    if (line.indent > baseIndent) {
      // Continuação de valor de bloco — pular (já processado)
      i++
      continue
    }

    // key: value  ou  key:
    const colonIdx = line.raw.indexOf(": ")
    if (colonIdx < 0 && !line.raw.endsWith(":")) {
      i++
      continue
    }

    const key = colonIdx >= 0
      ? line.raw.slice(0, colonIdx).trim()
      : line.raw.slice(0, -1).trim()

    if (colonIdx >= 0) {
      const inlineValue = line.raw.slice(colonIdx + 2).trim()
      if (inlineValue.length > 0) {
        obj[key] = parseScalar(inlineValue)
        i++
        continue
      }
    }

    // Valor é um bloco na próxima linha com maior indentação
    i++
    if (i < lines.length && lines[i]!.indent > baseIndent) {
      const result = parseBlock(lines, i)
      obj[key] = result.value
      i = result.next
    } else {
      obj[key] = null
    }
  }

  return { value: obj, next: i }
}

function parseArray(lines: Line[], startIdx: number, baseIndent: number): ParseResult {
  const arr: unknown[] = []
  let i = startIdx

  while (i < lines.length) {
    const line = lines[i]!
    if (line.indent < baseIndent) break
    if (line.indent > baseIndent) { i++; continue }

    if (!line.raw.startsWith("- ") && line.raw !== "-") { i++; continue }

    const afterDash = line.raw === "-" ? "" : line.raw.slice(2).trim()

    if (afterDash.length === 0) {
      // Elemento é um bloco na próxima linha
      i++
      if (i < lines.length && lines[i]!.indent > baseIndent) {
        const result = parseBlock(lines, i)
        arr.push(result.value)
        i = result.next
      } else {
        arr.push(null)
      }
      continue
    }

    // Elemento inline: pode ser escalar ou object inline (key: val key2: val2)
    if (afterDash.includes(": ") || afterDash.endsWith(":")) {
      // Inline object — criar sub-linhas virtuais com indentação +2
      const subIndent = line.indent + 2
      const subLines: Line[] = [{ indent: subIndent, raw: afterDash }]
      // Coletar linhas seguintes que pertencem a este item
      i++
      while (i < lines.length && lines[i]!.indent > baseIndent) {
        subLines.push(lines[i]!)
        i++
      }
      const result = parseBlock(subLines, 0)
      arr.push(result.value)
    } else {
      arr.push(parseScalar(afterDash))
      i++
    }
  }

  return { value: arr, next: i }
}

// ─────────────────────────────────────────────
// Scalars
// ─────────────────────────────────────────────

function parseScalar(raw: string): unknown {
  if (raw === "true")  return true
  if (raw === "false") return false
  if (raw === "null" || raw === "~") return null

  // Quoted strings
  if ((raw.startsWith("'") && raw.endsWith("'")) ||
      (raw.startsWith('"') && raw.endsWith('"'))) {
    return raw.slice(1, -1)
  }

  // Numbers
  if (/^-?\d+(\.\d+)?$/.test(raw)) {
    const n = Number(raw)
    if (!isNaN(n)) return n
  }

  // Plain string
  return raw
}
