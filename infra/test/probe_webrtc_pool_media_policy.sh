#!/usr/bin/env bash
# probe_webrtc_pool_media_policy.sh — 2026-09-14  (VOZ-10 · ADR adr-voice-media-plane V11)
#
# PERGUNTA: as mídias que o WebRTC oferece são config do POOL — editável na tela, obrigatória
# onde importa, lida FRESCA — e é essa config que chega ao token do cliente e do atendente?
#
# O DEFEITO QUE O ORIGINOU (medido antes do conserto)
#   O teto vinha de `PLATFORM_DEFAULT`, uma tabela em `media_policy.py`: todo pool, de todo
#   tenant, oferecia áudio e vídeo ao humano — config de negócio morando em código, sem tela
#   e sem dono. Ao lado, o adapter lia `pool.webrtc_recording` para gravar a chamada, campo
#   que não existia em schema, tabela, tela nem produtor: leitor sem produtor. Censo ao vivo
#   no mesmo dia: 0 pools webrtc no registry, 0 políticas — nada a migrar.
#
# QUATRO RAMOS
#   A  CONTRATO — as chaves e os tipos são os mesmos no zod, na tabela, na rota, na tela, no
#      produtor (bridge) e no leitor (gateway); o leitor não tem mais default de plataforma
#      nem o gatilho de gravação sem produtor; os três chamadores do bridge passam o campo.
#   B  REGISTRY AO VIVO — pool de contato com webrtc sem política é recusado (POST, PUT que
#      limpa, PUT que acrescenta o canal), nomeando o campo; política declarada persiste e
#      volta no GET. Fixtures fixas e idempotentes (a API não tem DELETE de pool).
#   C  PRODUTOR → LEITOR, imagens reais — o bridge lê o registry e escreve o routing.assigned;
#      o gateway o consome e assina tokens com o provider real: teto do cliente = política ∩
#      consumo; teto do atendente = política; troca de política vale sem reiniciar; registry
#      fora recusa e diz. Mutações: leitor que ignora o pool TEM de derrubar C1; produtor com
#      cache TEM de derrubar C4.
#   D  PRODUTOR VIVO — todo routing.assigned escrito depois que o bridge subiu carrega
#      `media_policy_source`. Sem entrada nova, INCONCLUSIVO.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
BRIDGE="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
REDIS_C="${REDIS_CONTAINER:-plughub-demo-redis-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_voz10_webrtc"          # pool webrtc de contato (fixture fixa)
POOL_TXT="probe_voz10_texto"       # pool sem webrtc (fixture fixa)
# Id NOVO a cada rodada: no verde ele nunca passa a existir; um id fixo, criado uma vez por
# um registry antigo, faria o B1 reprovar para sempre por resíduo, não por comportamento.
POOL_NUNCA="probe_voz10_sem_politica_$(date +%s)"
# Valores distintivos: nenhum default produz "cliente só vídeo, atendente só áudio".
P1='{"customer_publish":["video"],"agent_publish":["audio"]}'
P2='{"customer_publish":["audio"],"agent_publish":[]}'
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
tally() {
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      *) [ -n "$l" ] && incon "saida inesperada: $l";; esac
  done <<< "$1"
}

echo "════════════════════════════════════════════════════════════════════"
echo " as mídias do WebRTC são config do pool — e é ela que chega ao token?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CONTRATO ───────────────────────────────────────────────────────"
A_OUT=$(python3 - <<'PYEOF'
import ast, io, json, re
def r(ok, txt): print(("OK " if ok else "FALHA ") + txt)
def src(p): return io.open(p, encoding="utf-8").read()
def nocomment_ts(s): return re.sub(r"(//[^\n]*|/\*.*?\*/)", "", s, flags=re.S)
def docless_strings(tree):
    doc = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            doc.add(id(n.body[0].value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc]

GWP = "packages/channel-gateway/src/plughub_channel_gateway/adapters/"
ZOD = nocomment_ts(src("packages/schemas/src/agent-registry.ts"))

# A1 — zod × leitor
m_kind = re.search(r"MediaKindSchema\s*=\s*z\.enum\(\[([^\]]*)\]\)", ZOD)
zod_kinds = tuple(re.findall(r'"(\w+)"', m_kind.group(1))) if m_kind else ()
m_pol = re.search(r"PoolMediaPolicySchema\s*=\s*z\.object\(\{(.*?)\}\)\.strict\(\)", ZOD, re.S)
zod_keys = tuple(re.findall(r"(\w+)\s*:", m_pol.group(1))) if m_pol else ()
pt = ast.parse(src(GWP + "media_policy.py"))
def const(name):
    for n in pt.body:
        tgt = n.target if isinstance(n, ast.AnnAssign) else (n.targets[0] if isinstance(n, ast.Assign) else None)
        if getattr(tgt, "id", "") == name:
            return n.value
names = {n.targets[0].id: n.value.value for n in pt.body if isinstance(n, ast.Assign)
         and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
kinds = tuple(names.get(e.id) for e in const("KINDS").elts) if const("KINDS") is not None else ()
pkeys = tuple(e.value for e in const("POLICY_KEYS").elts) if const("POLICY_KEYS") is not None else ()
r(bool(zod_keys) and set(zod_keys) == set(pkeys), "A1 chaves da politica: zod=%s leitor=%s" % (list(zod_keys), list(pkeys)))
r(bool(zod_kinds) and set(zod_kinds) == set(kinds), "A1 tipos de midia: zod=%s leitor=%s" % (list(zod_kinds), list(kinds)))
r(re.search(r"media_policy:\s*PoolMediaPolicySchema\.nullable\(\)\.optional\(\)", ZOD) is not None,
  "A1 PoolRegistrationSchema declara `media_policy`")
leitor_usa = {k for k in pkeys if re.search(r'get\("%s"\)' % k, src(GWP + "media_policy.py"))}
r(leitor_usa == set(pkeys), "A1 o leitor LE as chaves que declara (%s)" % sorted(leitor_usa))

# A2 — tabela
prisma = src("packages/agent-registry/prisma/schema.prisma")
r(re.search(r"^\s*media_policy\s+Json\?", prisma, re.M) is not None, "A2 schema.prisma tem `media_policy Json?`")
import glob
mig = [p for p in glob.glob("packages/agent-registry/prisma/migrations/*/migration.sql")
       if re.search(r'ADD COLUMN[^;]*"media_policy"', src(p))]
r(len(mig) == 1, "A2 migracao versionada adiciona a coluna (%s)" % [p.split("/")[-2] for p in mig])

# A3 — rota: valida e grava nos dois verbos
route = nocomment_ts(src("packages/agent-registry/src/routes/pools.ts"))
r(route.count("mediaPolicyViolation(") >= 2, "A3 POST e PUT validam a politica (%d chamadas)" % route.count("mediaPolicyViolation("))
r(len(re.findall(r"media_policy:\s*body\.media_policy", route)) >= 2, "A3 POST e PUT gravam `body.media_policy`")

# A4 — tela
pp = nocomment_ts(src("packages/platform-ui/src/modules/config-recursos/PoolsPage.tsx"))
types = nocomment_ts(src("packages/platform-ui/src/types/index.ts"))
r(types.count("media_policy?: PoolMediaPolicy | null") >= 2, "A4 tipos Pool e input de create/update tem `media_policy`")
r(re.search(r"media_policy:\s*formData\.media_policy", pp) is not None and "MediaPolicyEditor" in pp,
  "A4 PoolsPage edita e ENVIA a politica")
tui = set(re.findall(r"pools\.mediaPolicy\.([\w.]+)", pp)) | {"kind.audio", "kind.video", "customer_publish", "agent_publish"}
def flat(d, p=""):
    out = set()
    for k, v in d.items():
        out |= flat(v, p + k + ".") if isinstance(v, dict) else {p + k}
    return out
loc = {}
for lg in ("en", "pt-BR"):
    j = json.loads(src("packages/platform-ui/src/i18n/locales/%s/configRecursos.json" % lg))
    loc[lg] = flat(j.get("pools", {}).get("mediaPolicy", {}))
usadas = {k for k in tui if not k.endswith(".")}   # `kind.${kind}` fica `kind.`: expandido acima
r(usadas <= loc["en"] and usadas <= loc["pt-BR"] and loc["en"] == loc["pt-BR"],
  "A4 i18n: chaves usadas presentes e iguais nos dois locales (faltam en=%s pt=%s)" % (sorted(usadas - loc["en"]), sorted(usadas - loc["pt-BR"])))

# A5 — produtor: o campo sai do registry nos três chamadores
bt = ast.parse(src("packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py"))
helper = next((n for n in ast.walk(bt) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_routing_assigned_pool_field"), None)
hs = set(docless_strings(helper)) if helper else set()
r(helper is not None and {"media_policy", "media_policy_source", "registry", "registry_unavailable", "not_webrtc"} <= hs,
  "A5 bridge produz `media_policy` + `media_policy_source` (registry|registry_unavailable|not_webrtc)")
calls = [c for c in ast.walk(bt) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_write_routing_assigned_to_stream"]
def via_helper(c):
    kw = next((k.value for k in c.keywords if k.arg == "pool_config"), None)
    return isinstance(kw, ast.Await) and getattr(kw.value.func, "id", "") == "_routing_assigned_pool_field"
r(len(calls) >= 3 and all(via_helper(c) for c in calls),
  "A5 %d chamador(es) do routing.assigned passam o campo do registry" % len(calls))

# A6 — leitor: procedências que o produtor emite são as que o leitor distingue; sem default nem gatilho morto
ls = set(docless_strings(pt))
r({"registry", "registry_unavailable"} <= ls, "A6 leitor distingue `registry` e `registry_unavailable`")
r(const("PLATFORM_DEFAULT") is None and "PLATFORM_DEFAULT_SOURCE" not in names,
  "A6 leitor sem teto default de plataforma")
wt = ast.parse(src(GWP + "webrtc.py"))
r("webrtc_recording" not in docless_strings(wt), "A6 adapter nao LE `webrtc_recording` (leitor sem produtor)")
r(any(isinstance(n, ast.Attribute) and n.attr == "attendant_from_pool_field" for n in ast.walk(wt)),
  "A6 adapter monta o atendente a partir do campo `pool`")
PYEOF
)
tally "$A_OUT"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · REGISTRY AO VIVO ───────────────────────────────────────────────"
C="curl -s --max-time 20"
TOKEN=$($C -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$TOKEN" ]; then
  incon "B/C: login do admin falhou em $AUTH — registry e produtor nao medidos"
else
  BODY=$(mktemp)
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  req() {
    local m=$1 p=$2 extra=()
    [ -n "${3:-}" ] && extra=(-d "$3")
    $C -o "$BODY" -w '%{http_code}' -X "$m" "${H[@]}" "${extra[@]}" "$REG$p"
  }
  body() { cat "$BODY"; }

  WEBRTC_BODY="{\"pool_id\":\"$POOL\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\""
  # B1 — POST sem política: recusado nomeando o campo (e o pool NÃO passa a existir)
  st=$(req POST /v1/pools "{\"pool_id\":\"$POOL_NUNCA\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000}")
  campo=$(body | jq -r '.details.field // empty' 2>/dev/null)
  gst=$(req GET "/v1/pools/$POOL_NUNCA")
  [ "$st" = 422 ] && [ "$campo" = media_policy ] && [ "$gst" = 404 ] \
    && ok "B1 POST webrtc sem politica -> 422 details.field=media_policy; pool nao criado" \
    || falha "B1 POST webrtc sem politica -> $st campo='$campo'; GET do pool -> $gst"

  # B2 — fixture com política: cria ou atualiza, e o GET devolve o que se gravou
  st=$(req GET "/v1/pools/$POOL")
  if [ "$st" = 404 ]; then st=$(req POST /v1/pools "$WEBRTC_BODY,\"media_policy\":$P1}"); want=201
  else st=$(req PUT "/v1/pools/$POOL" "{\"media_policy\":$P1}"); want=200; fi
  req GET "/v1/pools/$POOL" >/dev/null; lido=$(body | jq -cS '.media_policy')
  [ "$st" = "$want" ] && [ "$lido" = "$(echo "$P1" | jq -cS .)" ] \
    && ok "B2 politica declarada persiste e volta no GET ($lido)" \
    || falha "B2 gravar politica -> $st (esperado $want); GET devolveu $lido"

  # B3 — PUT que limpa a política de pool webrtc: recusado, e nada muda
  st=$(req PUT "/v1/pools/$POOL" '{"media_policy":null}'); campo=$(body | jq -r '.details.field // empty' 2>/dev/null)
  req GET "/v1/pools/$POOL" >/dev/null; lido=$(body | jq -cS '.media_policy')
  [ "$st" = 422 ] && [ "$campo" = media_policy ] && [ "$lido" = "$(echo "$P1" | jq -cS .)" ] \
    && ok "B3 PUT media_policy=null em pool webrtc -> 422; politica intacta" \
    || falha "B3 PUT limpando -> $st campo='$campo'; GET devolveu $lido"

  # B4 — PUT que acrescenta webrtc a pool sem política: recusado, e o canal não entra
  gst=$(req GET "/v1/pools/$POOL_TXT")
  # a fixture volta a só webchat antes de medir (um registry antigo pode ter aceitado o webrtc)
  [ "$gst" = 200 ] && req PUT "/v1/pools/$POOL_TXT" '{"channel_types":["webchat"]}' >/dev/null
  [ "$gst" = 404 ] && req POST /v1/pools "{\"pool_id\":\"$POOL_TXT\",\"channel_types\":[\"webchat\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\"}" >/dev/null
  st=$(req PUT "/v1/pools/$POOL_TXT" '{"channel_types":["webchat","webrtc"]}')
  req GET "/v1/pools/$POOL_TXT" >/dev/null; canais=$(body | jq -c '.channel_types')
  [ "$st" = 422 ] && [ "$canais" = '["webchat"]' ] \
    && ok "B4 PUT acrescentando webrtc a pool sem politica -> 422; canais intactos" \
    || falha "B4 PUT acrescentando webrtc -> $st; canais=$canais"

  # B5 — tipo desconhecido e chave a mais: o schema recusa (não é aceito e ignorado)
  s1=$(req PUT "/v1/pools/$POOL" '{"media_policy":{"customer_publish":["tela"],"agent_publish":[]}}')
  s2=$(req PUT "/v1/pools/$POOL" '{"media_policy":{"customer_publish":[],"agent_publish":[],"gravar":true}}')
  if [ "${s1:0:1}" = 4 ] && [ "${s2:0:1}" = 4 ]; then
    ok "B5 politica invalida recusada: tipo desconhecido -> $s1; chave extra -> $s2"
  else
    falha "B5 politica invalida ACEITA: tipo desconhecido -> $s1; chave extra -> $s2"
  fi

  # B0 — censo (informativo): pools webrtc de contato sem política ficam SEM mídia, e o gateway avisa
  CENSO=$($C -H "x-tenant-id: $TENANT" "$REG/v1/pools?limit=1000" | jq -r '[.pools[] | select((.channel_types|index("webrtc")) and ((.purpose // "contact")=="contact"))] | "\(length) \([.[]|select(.media_policy==null)|.pool_id]|join(","))"' 2>/dev/null)
  echo "  INFO    B0 pools webrtc de contato no tenant: ${CENSO%% *}; sem politica: [${CENSO#* }]"
  rm -f "$BODY"
fi

# ── C ────────────────────────────────────────────────────────────────────────
echo ""
echo "── C · PRODUTOR (bridge) → LEITOR (gateway), imagens reais + mutações ──"
GIMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
BIMG=$(docker inspect -f '{{.Image}}' "$BRIDGE" 2>/dev/null)
if [ -z "$GIMG" ] || [ -z "$BIMG" ] || [ -z "${TOKEN:-}" ]; then
  incon "C: gateway/bridge fora do ar ou sem login — nao medido"
else
  LKENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep '^PLUGHUB_WEBRTC_LIVEKIT_' | sed 's/^/-e /' | tr '\n' ' ')
  BRENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BRIDGE" | grep -E '^(AGENT_REGISTRY_URL|REDIS_URL)=' | sed 's/^/-e /' | tr '\n' ' ')
  # `timeout` mata o CLI do docker, não o container: nome próprio, kill explícito, FALHA contada.
  run_img() {
    local name="probe_voz10_$$_$RANDOM" rc
    timeout "${EXERCISE_TIMEOUT_S:-240}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python "$@"
    rc=$?
    if [ "$rc" -eq 124 ]; then docker kill "$name" >/dev/null 2>&1; echo "FALHA TIMEOUT exercicio morto apos ${EXERCISE_TIMEOUT_S:-240}s"; fi
    return 0
  }
  cenario() {   # $1 = MODE do produtor, $2 = MODE do leitor
    local sid="probe_voz10_$(date +%s)_$RANDOM" out
    out=$(run_img $BRENV -e MODE="$1" -e SID="$sid" -e POOL="$POOL" -e TENANT="$TENANT" -e ADMIN_TOKEN="$TOKEN" \
          -e P1="$P1" -e P2="$P2" "$BIMG" - < infra/test/_webrtc_pool_media_policy_bridge.py 2>&1)
    if ! printf '%s\n' "$out" | grep -q '^PASSO 6 escrito'; then
      echo "FALHA C-produtor bridge nao completou a sequencia: $(printf '%s' "$out" | tail -3 | tr '\n' ' ' | cut -c1-240)"
      docker exec "$REDIS_C" redis-cli DEL "session:$sid:meta" "session:$sid:stream" >/dev/null 2>&1
      return
    fi
    printf '%s\n' "$out" | grep -E '^PASSO (1|4) ' | grep -vqE ' 200$' && echo "FALHA C-produtor PUT de politica nao deu 200: $(printf '%s' "$out" | grep -E '^PASSO (1|4) ' | tr '\n' ' ')"
    run_img $LKENV -e MODE="$2" -e SID="$sid" -e POOL="$POOL" -e P1="$P1" -e P2="$P2" "$GIMG" - \
      < infra/test/_webrtc_pool_media_policy_exercise.py 2>/dev/null
  }
  C_OUT=$(cenario full full)
  tally "$C_OUT"
  N=$(printf '%s\n' "$C_OUT" | grep -cE '^(OK|FALHA) (C[0-5]|LIMPEZA) ')
  [ "$N" -ge 7 ] || falha "exercicio emitiu $N de 7 veredictos — morreu no meio"

  for mut in "mut_cache:full:C4" "full:mut_ignore_pool:C1"; do
    IFS=: read -r mb mg must <<< "$mut"
    M_OUT=$(cenario "$mb" "$mg")
    if printf '%s\n' "$M_OUT" | grep -q "^FALHA $must "; then
      ok "M produtor=$mb leitor=$mg derruba $must (o ramo reprova quando o defeito volta)"
    else
      falha "M produtor=$mb leitor=$mg NAO derrubou $must: $(printf '%s' "$M_OUT" | grep -E " $must |C-produtor" | head -1)"
    fi
  done
  # a fixture volta a P1 (a sequência a deixa em P2)
  $C -o /dev/null -X PUT -H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json' \
     -d "{\"media_policy\":$P1}" "$REG/v1/pools/$POOL"
fi

# ── D ────────────────────────────────────────────────────────────────────────
echo ""
echo "── D · PRODUTOR VIVO (bridge) ─────────────────────────────────────────"
STARTED=$(docker inspect -f '{{.State.StartedAt}}' "$BRIDGE" 2>/dev/null)
if [ -z "$STARTED" ]; then
  incon "D: container $BRIDGE fora do ar"
else
  START_MS=$(( $(date -d "$STARTED" +%s) * 1000 ))
  NOVAS=0; SEM=0; DESC=""
  while IFS='|' read -r k eid src; do
    [ -z "$eid" ] && continue
    case "$k" in *probe_voz10_*) continue;; esac     # as do cenário C não são amostra do produtor vivo
    NOVAS=$((NOVAS+1))
    case "$src" in registry|registry_unavailable|not_webrtc) ;; *) SEM=$((SEM+1)); DESC="$k $eid source='$src'";; esac
  done < <(docker exec "$REDIS_C" sh -c '
    for k in $(redis-cli --scan --pattern "session:*:stream"); do
      redis-cli --raw XRANGE "$k" '"$START_MS"' + | awk -v k="$k" "
        /^[0-9]+-[0-9]+\$/ { if (id!=\"\" && t==\"routing.assigned\") print k \"|\" id \"|\" s; id=\$0; t=\"\"; s=\"\"; prev=\"\"; next }
        { if (prev==\"type\") t=\$0; if (prev==\"pool\") { s=\$0; sub(/.*\"media_policy_source\": *\"/, \"\", s); sub(/\".*/, \"\", s); if (\$0 !~ /media_policy_source/) s=\"\" } prev=\$0 }
        END { if (id!=\"\" && t==\"routing.assigned\") print k \"|\" id \"|\" s }"
    done' 2>/dev/null)
  if [ "$NOVAS" -eq 0 ]; then
    incon "D: nenhum routing.assigned escrito depois que o bridge subiu ($STARTED) — sem amostra"
  elif [ "$SEM" -eq 0 ]; then
    ok "D1 $NOVAS routing.assigned novo(s), todos com media_policy_source"
  else
    falha "D1 $SEM de $NOVAS routing.assigned novo(s) sem media_policy_source (ex.: $DESC)"
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
