#!/usr/bin/env bash
# gate_external_resume.sh — Fase 1 do arco de workflow.
#
# Porta EXTERNA de resume: POST /channel/webhook/resume/{token}
# Simétrica ao trigger em prefixo; o token é a credencial (capability de uso único).
#
# P1 resume externo funciona            P4 `source` asserido é DESCARTADO
# P2 segundo resume → 409 terminal      P5 concorrência → um 200, um 409 in_flight
# P3 token inexistente → 404            P6 TTL do hash NÃO encurta token de outra sessão
#
# ⚠️ P6 é o único que reprovava ANTES do conserto. Se ele passar numa base sem o
#    fix, o teste não alcançou a condição — não comemore, investigue.
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
GW="${GW:-http://localhost:8010}"
TENANT="${TENANT:-tenant_demo}"
POOL="${POOL:-formfill_demo}"
TRIGGER_POOL="${TRIGGER_POOL:-formfill_demo_ia}"

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
body() { curl -s --max-time 15 "$@"; }

PASS=0; FAIL=0
ok()   { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
info() { echo "      $1"; }

echo "══ porta externa de resume — gate Fase 1 ══"
echo "   gw=$GW tenant=$TENANT"

# ── Preflight ─────────────────────────────────────────────────────────────────
[ "$(code "$GW/health")" = "200" ] || {
  echo "   ⛔ INCONCLUSIVO — channel-gateway fora do ar"; exit 3; }
[ "$(R PING)" = "PONG" ] || {
  echo "   ⛔ INCONCLUSIVO — redis inalcançável pelo compose"; exit 3; }
# A rota tem de EXISTIR. 404 do FastAPI (rota ausente) e 404 do handler (token
# ausente) têm o mesmo código — sem esta distinção, um gate inteiro passaria
# medindo a ausência da rota.
if ! body "$GW/openapi.json" | grep -q '/channel/webhook/resume/{resume_token}'; then
  echo "   ⛔ INCONCLUSIVO — a rota /channel/webhook/resume/{token} NÃO está no schema."
  echo "      Rebuild do channel-gateway pendente? Sem a rota, P1–P5 mediriam o 404"
  echo "      do FastAPI e passariam por engano."; exit 3
fi
echo "   preflight: health=200 · redis=PONG · rota presente no schema"

# ── Helper: cria uma workflow suspensa e devolve o resume_token ───────────────
#
# ⚠️ O DISPARO usa a rota INTERNA por pool (`/v1/channels/webhook/pool/{id}`), a
# mesma do `smoke_formfill_renderer.sh`. É montagem, não alvo: o gate julga a porta
# EXTERNA de RESUME, e amarrá-lo ao registro de `ChannelEndpoint` faria um endpoint
# não registrado reprovar a porta de resume — portão julgando a própria montagem.
#
# ⚠️ E o que este token É importa para ler o P1: `skill_formfill_demo_v1` suspende
# num `delegate` para uma fila de PULL, então existe um item de trabalho humano
# associado. Não há, neste tenant, pool webhook com `suspend` puro
# (`skill_reembolso_demo_v1`/`skill_portabilidade_demo_v1` têm o step, nenhum pool os
# deploya). Consequência medida ao escrever o gate: a conferência de posse do A5 é
# gateada em `approver is not None` (`webhook.py:1148`), e a porta externa passa
# `approver=None` — logo ela NÃO confere posse. Ver `TODO.md` § "porta externa de
# resume × posse do item de pull".
new_token() {
  local sid tok
  sid="$(body -X POST "$GW/v1/channels/webhook/pool/$TRIGGER_POOL" \
          -H 'content-type: application/json' \
          -d "{\"tenant_id\":\"$TENANT\",\"payload\":{}}" \
        | sed -E 's/.*"session_id":"([^"]+)".*/\1/')"
  [ -n "$sid" ] || return 1
  for _ in $(seq 1 20); do
    tok="$(R HGETALL "$TENANT:resume_tokens" | paste - - \
           | grep -F "$sid" | head -1 | cut -f1)"
    [ -n "$tok" ] && { echo "$tok"; return 0; }
    sleep 1
  done
  return 1
}

TOKEN="$(new_token)" || {
  echo "   ⛔ INCONCLUSIVO — não consegui parquear uma workflow suspensa no pool"
  echo "      '$TRIGGER_POOL'. Ajuste TRIGGER_POOL= para um pool webhook cujo skill suspenda."
  exit 3; }
info "token de trabalho: $TOKEN"

# ── P1 — resume pela porta externa ────────────────────────────────────────────
echo
echo "── P1 · resume externo ───────────────────────────────────────────────────"
P1="$(body -w '\n%{http_code}' -X POST "$GW/channel/webhook/resume/$TOKEN" \
        -H 'content-type: application/json' \
        -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"answers\":{\"ok\":true}}}")"
P1CODE="$(printf '%s' "$P1" | tail -1)"
if [ "$P1CODE" = "200" ] && printf '%s' "$P1" | grep -q '"session_id"'; then
  ok "200 com session_id"
else
  bad "esperado 200+session_id, veio: $(printf '%s' "$P1" | tr '\n' ' ')"
fi

# ── P4 — o `source` asserido foi descartado ───────────────────────────────────
# Medido no MESMO token do P1 (já consumido): o registro terminal é durável 25 h.
echo
# NB: sem crases nesta linha. Dentro de aspas duplas o bash as trata como
# substituição de comando — `source` virou o builtin `source` sem argumento, e o
# erro saiu no meio do relatório (2026-08-10, 1ª execução).
echo '── P4 · campo `source` asserido pelo chamador ────────────────────────────'
TERM_RAW="$(R GET "$TENANT:resume_terminal:$TOKEN")"
if [ -z "$TERM_RAW" ]; then
  bad "registro terminal ausente — sem ele P4 não pode julgar (INCONCLUSIVO parcial)"
  info "o P1 pode ter falhado antes de escrever o terminal"
else
  info "terminal: $TERM_RAW"
  # O JSON vem do `json.dumps` do Python, que separa com ", " e ": ". Casar sem
  # tolerar espaço reprovou um dado CORRETO na 1ª execução (2026-08-10) — falso
  # vermelho é tão caro quanto falso verde: gasta a confiança no portão.
  if printf '%s' "$TERM_RAW" | grep -Eq '"by"[[:space:]]*:[[:space:]]*"external"'; then
    ok "actor = external (o chamador não escolheu quem é)"
  else
    bad "actor NÃO é external: $TERM_RAW"
  fi
fi

# Agora o caso adversarial explícito, em token novo.
TOKEN_ADV="$(new_token)" || { echo "   ⛔ INCONCLUSIVO — sem token para P4b"; exit 3; }
body -X POST "$GW/channel/webhook/resume/$TOKEN_ADV" \
     -H 'content-type: application/json' \
     -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"source\":\"supervisor:mallory\",\"decision\":\"timeout\"}}" \
     >/dev/null
ADV="$(R GET "$TENANT:resume_terminal:$TOKEN_ADV")"
info "terminal adversarial: $ADV"
if printf '%s' "$ADV" | grep -q 'acw_supervisor_closed\|supervisor:mallory'; then
  bad "o chamador CONSEGUIU se declarar supervisor — §0.2 da spec segue aberto"
else
  ok "carimbo de supervisor recusado a chamador sem principal"
fi

# ── P2 — segundo resume do mesmo token ────────────────────────────────────────
echo
echo "── P2 · reenvio do token consumido ───────────────────────────────────────"
P2="$(body -w '\n%{http_code}' -X POST "$GW/channel/webhook/resume/$TOKEN" \
        -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\"}")"
P2CODE="$(printf '%s' "$P2" | tail -1)"
if [ "$P2CODE" = "409" ] && printf '%s' "$P2" | grep -q 'resume_already_terminal'; then
  ok "409 resume_already_terminal"
else
  bad "esperado 409 terminal, veio $P2CODE: $(printf '%s' "$P2" | head -1)"
fi

# ── P3 — token inexistente ────────────────────────────────────────────────────
echo
echo "── P3 · token inexistente ────────────────────────────────────────────────"
P3CODE="$(code -X POST "$GW/channel/webhook/resume/tok_nao_existe_$$" \
            -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\"}")"
[ "$P3CODE" = "404" ] && ok "404" || bad "esperado 404, veio $P3CODE"

# ── P5 — dois resumes concorrentes ────────────────────────────────────────────
echo
echo "── P5 · concorrência ─────────────────────────────────────────────────────"
TOKEN_C="$(new_token)" || { echo "   ⛔ INCONCLUSIVO — sem token para P5"; exit 3; }
C1="$(mktemp)"; C2="$(mktemp)"
code -X POST "$GW/channel/webhook/resume/$TOKEN_C" -H 'content-type: application/json' \
     -d "{\"tenant_id\":\"$TENANT\"}" > "$C1" &
code -X POST "$GW/channel/webhook/resume/$TOKEN_C" -H 'content-type: application/json' \
     -d "{\"tenant_id\":\"$TENANT\"}" > "$C2" &
wait
RC1="$(cat "$C1")"; RC2="$(cat "$C2")"; rm -f "$C1" "$C2"
info "códigos: $RC1 e $RC2"
N200="$(printf '%s\n%s\n' "$RC1" "$RC2" | grep -c '^200$' || true)"
N409="$(printf '%s\n%s\n' "$RC1" "$RC2" | grep -c '^409$' || true)"
if [ "$N200" = "1" ] && [ "$N409" = "1" ]; then
  ok "exatamente um 200 e um 409"
else
  bad "esperado 1×200 + 1×409, veio 200=$N200 409=$N409"
  info "dois 200 = a exclusão mútua da Camada F não pegou nesta porta"
fi

# ── P6 — o TTL do hash NÃO encurta o token de outra sessão ────────────────────
# ⚠️ ESTE É O QUE REPROVA NUMA BASE SEM O CONSERTO.
echo
echo "── P6 · TTL: hash compartilhado × token ──────────────────────────────────"
TOKEN_L="$(new_token)" || { echo "   ⛔ INCONCLUSIVO — sem token para P6"; exit 3; }
TTL_BEFORE="$(R TTL "$TENANT:resume_tokens")"
info "TTL do hash antes: ${TTL_BEFORE}s"
# Três estados, e confundi-los foi o que quase escondeu um bug: `-2` é chave
# inexistente, `-1` é chave SEM expiração, e só `n` pequeno é TTL curto. A v1 desta
# checagem dizia "TTL curto (-1s)", que é a frase errada para o estado errado — e
# `-1` aqui foi justamente o sintoma de o `_extend_hash_ttl` nunca definir TTL.
if [ "${TTL_BEFORE:-0}" = "-2" ]; then
  echo "   ⚠️  INCONCLUSIVO — o hash não existe. Nenhum token parqueado?"
  exit 3
fi
if [ "${TTL_BEFORE:-0}" = "-1" ]; then
  echo "   ❌ REPROVOU — o hash existe SEM EXPIRAÇÃO (-1)."
  echo "      Não é TTL curto: é TTL AUSENTE. Um escritor deixou de definir prazo,"
  echo "      e o hash passa a viver para sempre com os tokens órfãos dentro."
  echo "      Ver \`_extend_hash_ttl\` (channel-gateway) e o gêmeo no skill-flow-service."
  exit 1
fi
if [ "${TTL_BEFORE:-0}" -lt 120 ] 2>/dev/null; then
  echo "   ⚠️  INCONCLUSIVO — o hash está com TTL curto (${TTL_BEFORE}s);"
  echo "      não dá para demonstrar encurtamento a partir daqui."
  exit 3
fi
# Simula o escritor curto: um `collect` de 1 h fazia EXPIRE cru no hash inteiro.
R EXPIRE "$TENANT:resume_tokens" 90 >/dev/null
TTL_AFTER="$(R TTL "$TENANT:resume_tokens")"
info "TTL do hash depois de um escritor curto: ${TTL_AFTER}s"

# O que interessa não é o TTL — é o token AINDA RESUMIR depois que o hash morrer.
# Força o pior caso: apaga o hash (equivalente ao vencimento) e tenta o resume.
R DEL "$TENANT:resume_tokens" >/dev/null
META="$(R GET "$TENANT:resume_meta:$TOKEN_L")"
info "resume_meta presente: $([ -n "$META" ] && echo sim || echo NÃO)"
P6="$(body -w '\n%{http_code}' -X POST "$GW/channel/webhook/resume/$TOKEN_L" \
        -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\"}")"
P6CODE="$(printf '%s' "$P6" | tail -1)"
if [ "$P6CODE" = "200" ]; then
  ok "token sobreviveu à morte do hash compartilhado (reidratado pelo resume_meta)"
  # ⚠️ LIMITE DECLARADO: este passo prova a RECUPERAÇÃO, não a PREVENÇÃO. O
  # encurtamento foi forçado por `redis-cli EXPIRE` direto — de propósito, para
  # alcançar o pior caso —, então ele NÃO exercita o `_extend_hash_ttl` (a metade
  # que impede um escritor curto de encurtar o hash). Cobrir a prevenção pede um
  # escritor real de TTL curto (um `collect` de minutos) no caminho da aplicação;
  # enquanto não houver, o verde do P6 significa "o token sobrevive", não "o hash
  # não encurta".
  info "limite: prova a recuperação (resume_meta), NÃO a prevenção (_extend_hash_ttl)"
else
  bad "token morreu junto com o hash — veio $P6CODE"
  info "É EXATAMENTE o defeito §0.3: o prazo é do HASH, não do token."
  info "Numa base SEM o conserto, esta linha vermelha é o resultado esperado."
fi

# ── Veredicto ─────────────────────────────────────────────────────────────────
echo
echo "   passou=$PASS · reprovou=$FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "✅ OK — porta externa de resume fechada nas seis condições."
  exit 0
fi
echo "❌ REPROVOU em $FAIL condição(ões)."
exit 1
