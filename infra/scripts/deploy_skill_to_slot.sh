#!/usr/bin/env bash
# deploy_skill_to_slot.sh — publica um skill YAML E promove no slot do pool, VERIFICANDO.
#
# POR QUE EXISTE. Publicar um skill editado exige três passos e cada um falha de um
# jeito diferente e silencioso:
#
#   1. `PUT /v1/skills/:id` — skills são **seed-if-absent**: editar o YAML e
#      reiniciar o bridge é NO-OP (ele só loga o DRIFT). Sem este PUT nada muda.
#   2. `PUT /pools/:id/slots/next` + `POST /promote` — o bridge executa o **snapshot
#      do slot `current`**, não o `skill.flow`. Publicar sem promover também é no-op.
#   3. **conferir o snapshot promovido** — e é aqui que a coisa morde: se o passo 1
#      falhar, os passos 2 e 3 têm SUCESSO assim mesmo e tiram foto do flow ANTIGO.
#      Em 2026-08-03 foi exatamente isso: o PUT devolveu 422 (corpo errado), o
#      promote respondeu `"action":"promoted"`, e o teste seguinte falhou apontando
#      para a feature nova — que nunca chegou a rodar. Deploy que reporta OK sobre o
#      artefato errado é a mesma família de "«foi escrito» ≠ «mudou»".
#
# Por isso este script (a) mostra o CORPO do erro em vez do código, e (b) termina
# comparando o snapshot promovido com uma âncora que só existe no flow novo.
#
# ARMADILHA DO config_json: `set-next` sem `config_json` grava `{}`, e o promote
# torna isso `current` — apagando, por exemplo, `max_concurrent_sessions`. O script
# LÊ o config_json do slot atual e o repassa por padrão.
#
# Uso:
#   bash infra/scripts/deploy_skill_to_slot.sh <skill_yaml> <pool_id> [âncora]
# Ex.:
#   bash infra/scripts/deploy_skill_to_slot.sh \
#     packages/skill-flow-engine/skills/skill_wrapup_detached_v1.yaml \
#     wrapup_detached_ia  dialog_form_id
set -uo pipefail

YAML="${1:?uso: deploy_skill_to_slot.sh <skill_yaml> <pool_id> [âncora]}"
POOL="${2:?falta o pool_id}"
ANCHOR="${3:-}"
AR="${AGENT_REGISTRY_URL:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"

H=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" -H 'content-type: application/json')

# Lê um campo do slot `current` SEM supor a forma da resposta. O GET pode devolver
# os slots na raiz (`{current:…}`) ou aninhados (`{slots:{current:…}}`) — a resposta
# do `promote` usa a segunda forma. Supor uma delas fez o script anterior reportar
# "slot current sem yaml_snapshot" quando o snapshot existia: erro de LEITURA
# publicado como erro do ALVO, que é o defeito que este script existe para impedir,
# cometido por ele mesmo.
#
# Quando não acha, imprime a resposta CRUA em stderr — ausência de campo nunca vira
# `{}` em silêncio.
#
# O JSON entra por ARGUMENTO, não por stdin. `python3 - <<'PY'` já usa o stdin para
# o PRÓPRIO PROGRAMA, então `sys.stdin.read()` lá dentro lê vazio — e o resultado
# foi "RESPOSTA NÃO-JSON:" com a resposta em branco, apontando para o servidor
# quando o defeito era o canal. Segunda vez que a mesma confusão heredoc×stdin
# aparece hoje (a 1ª foi na probe_hygiene, com `python - <<PY < /dev/null`).
slot_field() {  # $1 = nome do campo · $2 = JSON cru
  # Programa entre aspas SIMPLES no shell ⇒ nada de aspas simples no Python.
  python3 -c '
import json, sys
field, raw = sys.argv[1], sys.argv[2]
if not raw.strip():
    print("{}")
    print("RESPOSTA VAZIA do endpoint (nem JSON, nem erro) — confira URL e auth.",
          file=sys.stderr)
    raise SystemExit(0)
try:
    d = json.loads(raw)
except Exception:
    print("{}")
    print("RESPOSTA NAO-JSON:", raw[:400], file=sys.stderr)
    raise SystemExit(0)
cur = (d.get("slots") or d).get("current") or {}
val = cur.get(field)
if val is None:
    print("{}")
    print("campo ausente no slot current:", field, "| resposta crua:", raw[:800],
          file=sys.stderr)
else:
    print(json.dumps(val))
' "$1" "$2"
}

SKILL_ID=$(python3 -c "import yaml,sys; print(yaml.safe_load(open('$YAML'))['id'])") || {
  echo "❌ não consegui ler o id de $YAML"; exit 2; }
echo "── skill=$SKILL_ID  pool=$POOL"

# ── 1. PUT do skill ───────────────────────────────────────────────────────────
# `flow` = APENAS { entry, steps }. Mandar o YAML inteiro (com id/description/
# classification) faz o CreateSkillSchema recusar com 422 — foi o erro de 2026-08-03.
echo "── 1. PUT /v1/skills/$SKILL_ID"
# O corpo é o SKILL (metadados no topo) + `flow` = APENAS { entry, steps }.
# Duas tentativas erradas, ambas corrigidas pelo corpo do erro que este script
# imprime (e que na 1ª rodada, feita à mão, não foi impresso — daí o ciclo perdido):
#   · mandar o YAML inteiro como `flow` → 422 (o schema do flow não aceita
#     id/description/classification lá dentro);
#   · mandar só {skill_id,name,version,flow} → 422 pedindo `description` e
#     `classification`, que são campos do SKILL e o YAML já traz.
# `CreateSkillSchema` é NÃO-partial no PUT: campo ausente é erro, não "mantém o
# que está no banco".
BODY=$(python3 - "$YAML" <<'PY'
import json, sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
print(json.dumps({
    "skill_id":       d["id"],
    "name":           d.get("name", d["id"]),
    "version":        str(d.get("version", "1.0")),
    "description":    d.get("description", "") or d["id"],
    "classification": d.get("classification", {"type": "vertical"}),
    "flow":           {"entry": d["entry"], "steps": d["steps"]},
}))
PY
) || { echo "❌ falha ao montar o corpo"; exit 2; }

OUT=$(curl -s -w '\n%{http_code}' -X PUT "$AR/v1/skills/$SKILL_ID" "${H[@]}" -d "$BODY")
CODE=$(printf '%s' "$OUT" | tail -1)
if [ "$CODE" != "200" ] && [ "$CODE" != "201" ]; then
  echo "❌ PUT devolveu $CODE — CORPO DO ERRO (é ele que diz o que fazer):"
  printf '%s\n' "$OUT" | sed '$d' | head -c 2000 | sed 's/^/    /'
  echo
  echo "   ABORTANDO antes do promote. Promover agora tiraria foto do flow ANTIGO"
  echo "   e o próximo teste falharia apontando para o lugar errado."
  exit 1
fi
echo "   ✓ publicado ($CODE)"

# ── 2. set-next preservando o config_json ─────────────────────────────────────
SLOTS_RAW=$(curl -s -w '\n__HTTP__%{http_code}' "$AR/v1/pools/$POOL/slots" "${H[@]}")
SLOTS_CODE=$(printf '%s' "$SLOTS_RAW" | sed -n 's/.*__HTTP__\([0-9]*\)$/\1/p')
SLOTS_JSON=$(printf '%s' "$SLOTS_RAW" | sed 's/__HTTP__[0-9]*$//')
if [ "$SLOTS_CODE" != "200" ]; then
  echo "❌ GET /v1/pools/$POOL/slots devolveu $SLOTS_CODE (corpo: ${SLOTS_JSON:0:200})"
  echo "   Sem ler o slot não dá para preservar o config_json nem conferir o promote."
  exit 1
fi
CFG=$(slot_field config_json "$SLOTS_JSON")
echo "── 2. set-next (config_json preservado: $CFG)"
curl -s -X PUT "$AR/v1/pools/$POOL/slots/next" "${H[@]}" \
  -d "{\"skill_id\":\"$SKILL_ID\",\"config_json\":$CFG}" >/dev/null

echo "── 3. promote"
curl -s -X POST "$AR/v1/pools/$POOL/promote" "${H[@]}" >/dev/null

# ── 4. VERIFICA o que ficou em `current` ──────────────────────────────────────
# Sem este passo o script mente com sucesso — foi o defeito que ele existe para
# impedir. A âncora é uma string que só aparece no flow NOVO.
echo "── 4. conferindo o snapshot promovido"
AFTER_JSON=$(curl -s "$AR/v1/pools/$POOL/slots" "${H[@]}")
SNAP=$(slot_field yaml_snapshot "$AFTER_JSON")
if [ -z "$SNAP" ] || [ "$SNAP" = "{}" ]; then
  echo "❌ não consegui LER o yaml_snapshot do slot current (a resposta crua saiu acima)."
  echo "   Isto é falha de leitura OU promote sem efeito — os dois exigem olhar a"
  echo "   resposta, e nenhum autoriza rodar o teste seguinte."
  exit 1
fi
if [ -n "$ANCHOR" ]; then
  if printf '%s' "$SNAP" | grep -q -- "$ANCHOR"; then
    echo "   ✅ âncora '$ANCHOR' presente no snapshot — o flow NOVO está em produção"
  else
    echo "   ❌ âncora '$ANCHOR' AUSENTE do snapshot promovido."
    echo "      O promote teve sucesso sobre o flow ERRADO. Não rode o teste seguinte:"
    echo "      ele falharia culpando a feature."
    exit 1
  fi
else
  echo "   ⚠️  sem âncora — não dá para afirmar QUAL flow foi promovido."
fi
CFGNOW=$(slot_field config_json "$AFTER_JSON" 2>/dev/null)
echo "   config_json em current: $CFGNOW"
exit 0
