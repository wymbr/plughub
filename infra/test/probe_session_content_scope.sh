#!/usr/bin/env bash
# probe_session_content_scope.sh — as rotas de CONTEÚDO recortam por pool?
#
# Peça 1 da proposta (d) / decisão #6 do dono (2026-08-30). Raciocínio no
# `TODO.md` § "(d) ABAC no conteúdo" e no `CHANGELOG.md` da data.
#
# ── A proposição, e por que ela precisa de DOIS lados ────────────────────────
# Quatro rotas da analytics-api servem o CONTEÚDO de um contato — transcrição,
# stream ao vivo, trajetória, pipeline_state. Desde 2026-08-29 elas exigem
# credencial; até 2026-08-30 usavam o principal **só para resolver `tenant_id`**,
# então qualquer token válido do tenant lia o diálogo de qualquer contato cujo
# `session_id` conhecesse.
#
# O verde deste probe só vale com os dois lados, e o motivo é medido: a suíte de
# 747 testes da analytics-api passou IGUAL antes e depois do portão — ela exercita
# essas rotas com principal irrestrito, então o gate ficava INERTE nela. Um probe
# que só afirmasse "ainda responde 200" teria a mesma cegueira.
#
#   RAMO A  token escopado AO pool da sessão      → 200   (não quebrei a leitura)
#   RAMO B  token escopado a OUTRO pool           → 403 pool_scope_denied
#   RAMO C  token irrestrito                      → 200   (sem regressão p/ admin)
#   RAMO D  sessão INDETERMINÁVEL + token escopado→ 403 session_pools_undeterminable
#   RAMO E  sem token                             → 401   (o 403 do B não é 401 disfarçado)
#   RAMO F  a delegação do supervisor preserva o veredicto (meta sintético,
#           porque com sessão fechada o /join responde 404 ANTES do portão)
#
# O ramo E existe porque 403 e 401 são fáceis de confundir num script: se a camada
# de credencial voltasse a recusar tudo, o ramo B ficaria verde pelo motivo errado.
#
# Uso:  bash infra/test/probe_session_content_scope.sh
# Exige: stack demo de pé (analytics-api, clickhouse, config-api).

set -uo pipefail

AN="${AN:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CH="${CH:-plughub-demo-clickhouse-1}"
REDIS="${REDIS:-plughub-demo-redis-1}"
CURL="curl -s --max-time 10"
FAIL=0

ok()   { echo "  ✓ $1"; }
bad()  { echo "  ✗ $1"; FAIL=1; }
assert(){ if [ "$2" = "$3" ]; then ok "$1 = $3"; else bad "$1: esperado [$2], veio [$3]"; fi; }

mint() {  # $1 = JSON dos claims de escopo, ex: '{"accessible_pools":["sac_ia"]}'
  $DC exec -T config-api python - "$JWT_SECRET" "$1" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, json, time, hmac, hashlib, base64
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
secret, extra = sys.argv[1], json.loads(sys.argv[2])
now = int(time.time())
claims = {"sub": "probe_scope", "tenant_id": "tenant_demo", "iat": now, "exp": now + 3600}
claims.update(extra)
h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
p = b64(json.dumps(claims, separators=(",", ":")).encode())
sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
print(f"{h}.{p}.{b64(sig)}")
PY
}

chq() { docker exec -i "$CH" clickhouse-client -q "$1" 2>/dev/null | head -1; }

# `%{http_code}` sobrevive ao timeout do SSE: a rota de stream não fecha sozinha,
# então um 200 legítimo só se observa deixando o curl estourar o `--max-time`.
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$@"; }
body() { curl -s --max-time 4 "$@"; }

echo "══ probe_session_content_scope — escopo de pool nas rotas de conteúdo ══"
echo

# ── Amostras vindas do DADO, nunca hardcoded ────────────────────────────────
SID=$(chq "SELECT session_id FROM plughub_demo.messages AS m FINAL
           WHERE tenant_id='${TENANT}' AND session_id IN (
             SELECT session_id FROM plughub_demo.sessions FINAL
             WHERE tenant_id='${TENANT}' AND pool_id!='' )
           GROUP BY session_id ORDER BY count() DESC LIMIT 1")
POOL=$(chq "SELECT pool_id FROM plughub_demo.sessions FINAL
            WHERE tenant_id='${TENANT}' AND session_id='${SID}' LIMIT 1")
SID_INDET=$(chq "SELECT session_id FROM plughub_demo.sessions FINAL
                 WHERE tenant_id='${TENANT}' AND pool_id='' AND session_id NOT IN (
                   SELECT session_id FROM plughub_demo.segments FINAL
                   WHERE tenant_id='${TENANT}') LIMIT 1")

if [ -z "$SID" ] || [ -z "$POOL" ]; then
  echo "INCONCLUSIVO: não há sessão com pool e mensagens em ${TENANT}."
  echo "O probe não pode reprovar sem amostra — isto NÃO é verde."
  exit 2
fi
echo "  amostra: session=${SID} pool=${POOL}"
echo "  indeterminável: ${SID_INDET:-<nenhuma na base>}"
echo

# `OUTRO` tem de existir e ser != POOL, senão o ramo B passa por acidente.
OUTRO=$(chq "SELECT pool_id FROM plughub_demo.segments FINAL
             WHERE tenant_id='${TENANT}' AND pool_id!='' AND pool_id!='${POOL}'
             GROUP BY pool_id ORDER BY count() DESC LIMIT 1")
if [ -z "$OUTRO" ]; then
  echo "INCONCLUSIVO: não há um segundo pool para o ramo negativo."; exit 2
fi
echo "  pool alheio (ramo B): ${OUTRO}"
echo

TOK_A=$(mint "{\"accessible_pools\":[\"${POOL}\"]}")
TOK_B=$(mint "{\"accessible_pools\":[\"${OUTRO}\"]}")
TOK_C=$(mint '{"unrestricted":true}')
for t in A B C; do
  eval "v=\$TOK_$t"
  case "$v" in *.*.*) ;; *) echo "FALHA: token $t não foi cunhado ([$v])"; exit 2;; esac
done

R_STREAM="/sessions/${SID}/stream?tenant_id=${TENANT}"
R_TRACE="/sessions/${SID}/workflow-trace?tenant_id=${TENANT}"
R_PIPE="/sessions/${SID}/pipeline-state?tenant_id=${TENANT}"
R_TRAN="/v1/transcript/sessions/${SID}?tenant_id=${TENANT}&scope=contact"

echo "── RAMO A — token escopado AO pool da sessão ⇒ 200 ─────────────────────"
for r in "$R_STREAM" "$R_TRACE" "$R_PIPE" "$R_TRAN"; do
  assert "A ${r%%\?*}" "200" "$(code -H "Authorization: Bearer $TOK_A" "${AN}${r}")"
done
echo
echo "── RAMO B — token escopado a OUTRO pool ⇒ 403 pool_scope_denied ────────"
for r in "$R_STREAM" "$R_TRACE" "$R_PIPE" "$R_TRAN"; do
  assert "B ${r%%\?*}" "403" "$(code -H "Authorization: Bearer $TOK_B" "${AN}${r}")"
done
DET=$(body -H "Authorization: Bearer $TOK_B" "${AN}${R_TRAN}")
case "$DET" in
  *pool_scope_denied*) ok "B nomeia a recusa: pool_scope_denied" ;;
  *) bad "B deveria dizer pool_scope_denied, disse: $(echo "$DET" | head -c 90)" ;;
esac
echo
echo "── RAMO C — token irrestrito ⇒ 200 ─────────────────────────────────────"
for r in "$R_TRACE" "$R_PIPE" "$R_TRAN"; do
  assert "C ${r%%\?*}" "200" "$(code -H "Authorization: Bearer $TOK_C" "${AN}${r}")"
done
echo
echo "── RAMO D — sessão indeterminável ⇒ 403 session_pools_undeterminable ───"
if [ -n "$SID_INDET" ]; then
  D_URL="${AN}/v1/transcript/sessions/${SID_INDET}?tenant_id=${TENANT}&scope=contact"
  assert "D status" "403" "$(code -H "Authorization: Bearer $TOK_A" "$D_URL")"
  DET=$(body -H "Authorization: Bearer $TOK_A" "$D_URL")
  case "$DET" in
    *session_pools_undeterminable*) ok "D distingue indeterminável de negado" ;;
    *) bad "D deveria dizer session_pools_undeterminable, disse: $(echo "$DET" | head -c 90)" ;;
  esac
else
  echo "  — sem sessão indeterminável na base: ramo não exercido (não é verde)"
fi
echo
echo "── RAMO E — sem token ⇒ 401 (o 403 do B não é 401 disfarçado) ──────────"
for r in "$R_TRACE" "$R_PIPE" "$R_TRAN"; do
  assert "E ${r%%\?*}" "401" "$(code "${AN}${r}")"
done
echo
echo "── RAMO F — a DELEGAÇÃO do supervisor preserva o veredicto ─────────────"
# `_authorize_live_session` passou a delegar ao decisor único. Provar isso exige
# ALCANÇAR o portão: com uma sessão fechada o `/join` responde 404 antes dele
# (`session:{id}:meta` ausente), e a 1ª versão deste ramo aceitava esse 404 como
# "não afrouxou" — verde pela proposição errada, que é o defeito que este arquivo
# existe para não ter. Semeamos um meta SINTÉTICO, com id que não colide com
# sessão real, e apagamos no fim.
FAKE="probe_scope_fake_$$"
docker exec -i "$REDIS" redis-cli SET "session:${FAKE}:meta" \
  "{\"tenant_id\":\"${TENANT}\",\"pool_id\":\"${POOL}\"}" >/dev/null 2>&1
join() {  # $1 = token
  code -X POST -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
       -d "{\"session_id\":\"${FAKE}\",\"tenant_id\":\"${TENANT}\",\"participant_id\":\"probe\"}" \
       "${AN}/supervisor/join"
}
J_FORA=$(join "$TOK_B")
J_DENTRO=$(join "$TOK_A")
assert "F fora do escopo" "403" "$J_FORA"
DET=$(curl -s --max-time 4 -X POST -H "Authorization: Bearer $TOK_B" \
      -H 'Content-Type: application/json' \
      -d "{\"session_id\":\"${FAKE}\",\"tenant_id\":\"${TENANT}\",\"participant_id\":\"probe\"}" \
      "${AN}/supervisor/join")
case "$DET" in
  *pool_scope_denied*) ok "F nomeia a recusa: pool_scope_denied" ;;
  *) bad "F deveria dizer pool_scope_denied, disse: $(echo "$DET" | head -c 90)" ;;
esac
# Testemunha POSITIVA: dentro do escopo o portão NÃO recusa. O que vem depois dele
# (200, 404, 500 — a sessão é sintética) não é assunto deste ramo; 403 é.
if [ "$J_DENTRO" = "403" ]; then
  bad "F dentro do escopo veio 403 — o portão recusa quem deveria passar"
else
  ok "F dentro do escopo não é 403 (veio $J_DENTRO)"
fi
docker exec -i "$REDIS" redis-cli DEL "session:${FAKE}:meta" >/dev/null 2>&1

echo
if [ "$FAIL" -eq 0 ]; then echo "══ VERDE ══"; else echo "══ VERMELHO ══"; fi
exit "$FAIL"
