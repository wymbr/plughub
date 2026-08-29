#!/usr/bin/env bash
# q_masked_declaration_census.sh — F0 do ADR do `masked` TIPADO (arco ALLOWLIST, pos-V2b)
#
# A decisao a tomar: `masked: true` deixa de ser BOOLEANO e passa a nomear um TIPO
# do catalogo (`masking.types`), para que mascara-por-papel e classe LGPD sejam
# propriedade do TIPO e nao escolha de cada formulario. Conformidade nao pode ser
# por formulario: se cada form decide, ha N politicas de CPF no tenant e vale a
# mais permissiva.
#
# Este script NAO decide nada. Ele mede as tres grandezas que dimensionam o ADR, e
# e re-executavel de proposito — medicao que so vive num paragrafo se perde.
#
#   EIXO 1  quem DECLARA e quem RESOLVE `masked`  (fonte)
#   EIXO 2  a submissao de form entra na transcricao duravel?  (ClickHouse, ao vivo)
#   EIXO 3  teto de profundidade de categoria  (regex x decompose x DDL)
#
# Cada eixo carrega testemunha de presenca: "zero" sobre populacao vazia nao e
# resposta, e o eixo 3 mede uma tabela que pode estar vazia por nao ter produtor.
#
# Roda do HOST. Nao e gate: nao tem veredicto OK/FALHA, tem NUMERO. Sai 2 se algum
# eixo nao pode ser medido.
set -u

cd "$(dirname "$0")/../.." || exit 2
CH="${CH:-plughub-demo-clickhouse-1}"
CHDB="${CHDB:-plughub_demo}"
DIALOG="${DIALOG:-http://localhost:3760}"
CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"

inconclusive=0
huh() { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

echo "=== q_masked_declaration_census — F0 do \`masked\` tipado ==="

# ── EIXO 1. fonte: declaracao x resolucao ────────────────────────────────────
echo
echo "-- EIXO 1. quem DECLARA e quem RESOLVE ----------------------"
echo "declaracao (o booleano que viraria referencia a tipo):"
grep -n "masked:\s*z\.boolean()" packages/schemas/src/skill.ts packages/schemas/src/dialog.ts 2>/dev/null | sed 's/^/  . /'
echo
echo "resolvedor CANONICO (colapsa step-level + field-level numa lista de ids):"
grep -rn "export function \(isFieldMasked\|computeMaskedFieldIds\)" packages/skill-flow-engine/src/masking-policy.ts 2>/dev/null | sed 's/^/  . /'
echo "  chamadores:"
grep -rn "computeMaskedFieldIds(" --include=*.ts packages/skill-flow-engine/src packages/mcp-server-plughub/src 2>/dev/null \
  | grep -v "__tests__" | grep -v "export {" | grep -v "^.*import" | sed 's/^/      /'
echo
# Excluir a propria masking-policy e os testes nao e higiene: sem isso o
# instrumento acusa o resolvedor CANONICO como se fosse a duplicata que ele
# procura, e o numero passa a medir a pergunta errada. Comentario tambem sai —
# a prosa que documenta a precedencia reescreve a forma textualmente.
echo "OUTROS sites que leem o booleano direto (fora da masking-policy):"
grep -rn "\.masked === true" --include=*.ts packages/mcp-server-plughub/src packages/skill-flow-engine/src 2>/dev/null \
  | grep -v "masking-policy" | grep -v "__tests__" \
  | awk '{ l=$0; sub(/^[^:]*:[0-9]+:/,"",l); sub(/^[ \t]+/,"",l); if (l !~ /^(\/\/|\*|\/\*)/) print }' \
  | sed 's/^/  . /'
echo "  (o normalizador \`form_get\` ACHATA node/field do DialogForm em"
echo "   render.fields[].masked, que o step \`menu\` consome — logo a precedencia"
echo "   e aplicada UMA vez, downstream, pelo resolvedor canonico)"
echo
echo "superficies do dialog primitive x honram \`masked\`?"
for f in \
  "packages/platform-ui/src/modules/agent-assist/components/DialogFormRenderer.tsx" \
  "packages/channel-gateway/src/plughub_channel_gateway/survey_web.py" \
  "packages/mcp-server-plughub/src/tools/dialog.ts" ; do
  if [ ! -f "$f" ]; then huh "ausente: $f"; continue; fi
  N="$(grep -c "masked" "$f" 2>/dev/null)"
  echo "  . $(basename "$f")  ocorrencias_masked=${N}"
done

# ── EIXO 2. a submissao entra na transcricao? ────────────────────────────────
echo
echo "-- EIXO 2. submissao de form na transcricao duravel ----------"
TOTAL="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM ${CHDB}.messages" 2>/dev/null | tr -d '\r')"
case "$TOTAL" in
  ''|*[!0-9]*) huh "ClickHouse nao respondeu — eixo 2 nao medido" ;;
  0)           huh "tabela messages VAZIA — zero submissao nao prova nada" ;;
  *)
    echo "  MESSAGES_TOTAL=${TOTAL}   (testemunha de presenca)"
    docker exec "$CH" clickhouse-client -q "
      SELECT multiIf(
               content LIKE '[Formulário:%',       'FORMULARIO (submissao redigida campo a campo)',
               content LIKE '[entrada mascarada%', 'SUPRIMIDO  (step inteiro mascarado)',
               content LIKE '[Seleção:%',          'SELECAO    (resposta nao-texto, nada mascarado)',
                                                   'outro'
             ) AS forma, count() AS n
      FROM ${CHDB}.messages GROUP BY forma ORDER BY n DESC" 2>/dev/null | sed 's/^/  . /'
    echo
    echo "  o que SOBREVIVE em claro dentro de uma submissao:"
    docker exec "$CH" clickhouse-client -q "
      SELECT substring(content, 1, 160) FROM ${CHDB}.messages
      WHERE content LIKE '[Formulário:%' LIMIT 3" 2>/dev/null | sed 's/^/      /'
    ;;
esac
echo
echo "  o catalogo TEM regra para o que sobrevive? (deteccao x declaracao)"
if command -v jq >/dev/null 2>&1; then
  curl -s --max-time 10 "${CFG}/config/masking?tenant_id=${TENANT}" \
    | jq -r '.entries.types.types[]? | "      . \(.id)  detect=\(if .formato.detect_pattern then "SIM" else "—" end)  lgpd=\(.lgpd // "—")"' 2>/dev/null
else
  huh "jq ausente — catalogo nao lido"
fi

# ── EIXO 2b. quantos DialogForms declaram campo masked? ──────────────────────
echo
echo "-- EIXO 2b. exposicao x dano no renderer do Console ----------"
if command -v jq >/dev/null 2>&1; then
  IDS="$(curl -s --max-time 10 -H "X-Tenant-ID: ${TENANT}" "${DIALOG}/v1/dialog/forms" \
         | jq -r '(if type=="object" then (.forms // .items // .data) else . end)[]? | .form_id // .id' 2>/dev/null)"
  if [ -z "$IDS" ]; then
    huh "dialog-api nao listou forms — 2b nao medido"
  else
    echo "  FORMS_TOTAL=$(echo "$IDS" | wc -l | tr -d ' ')   (testemunha de presenca)"
    for id in $IDS; do
      N="$(curl -s --max-time 10 -H "X-Tenant-ID: ${TENANT}" "${DIALOG}/v1/dialog/forms/${id}" \
           | jq '[.. | objects | select(.masked == true)] | length' 2>/dev/null)"
      [ "${N:-0}" != "0" ] && echo "  . ${id}  campos_masked=${N}"
    done
    echo "  (so os NAO-zero acima; o renderer do Console ignora \`masked\` — o dano"
    echo "   depende de algum destes chegar la, nao de o campo existir)"
  fi
else
  huh "jq ausente — 2b nao medido"
fi

# ── EIXO 3. teto de profundidade de categoria ────────────────────────────────
echo
echo "-- EIXO 3. teto de categoria (regex x decompose x DDL) -------"
echo "  regex declarada:"
grep -n "AGENT_EVENT_CATEGORY_REGEX\s*=" packages/schemas/src/agent-events.ts 2>/dev/null | sed 's/^/      /'
echo "  decompositor (o destructuring e o teto real do codigo):"
grep -n "const \[l1" packages/schemas/src/agent-events.ts 2>/dev/null | sed 's/^/      /'
NCOL="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM system.columns WHERE database='${CHDB}' AND table='agent_business_events' AND name LIKE 'category\_l%'" 2>/dev/null | tr -d '\r')"
case "$NCOL" in
  ''|*[!0-9]*) huh "ClickHouse nao respondeu — colunas de nivel nao contadas" ;;
  *)           echo "      DDL: ${NCOL} colunas category_lN" ;;
esac
NROW="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM ${CHDB}.agent_business_events" 2>/dev/null | tr -d '\r')"
case "$NROW" in
  ''|*[!0-9]*) huh "contagem de linhas falhou" ;;
  0)           echo "      linhas=0 -> teto e bloqueio LATENTE: sem exposicao e sem backfill" ;;
  *)
    echo "      linhas=${NROW}   (testemunha de presenca)"
    docker exec "$CH" clickhouse-client -q "
      SELECT length(splitByChar('.', category)) AS segmentos, count() AS n
      FROM ${CHDB}.agent_business_events GROUP BY segmentos ORDER BY segmentos" 2>/dev/null | sed 's/^/      /'
    ;;
esac

echo
echo "==============================================================="
if [ "$inconclusive" -gt 0 ]; then
  echo "${inconclusive} eixo(s)/ramo(s) NAO medido(s) — o censo esta incompleto"
  exit 2
fi
echo "censo completo"
exit 0
