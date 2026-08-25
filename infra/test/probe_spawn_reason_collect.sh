#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# `spawn_reason='collect'` tem produtor? — passo 2 da ordem de trabalho
# (TODO.md § "🧭 Ordem de trabalho PROPOSTA"). Destrava o F4.
#
# ── A PERGUNTA, e por que a contagem sozinha não a responde ────────────────────
# O TODO registra "acesso outbound tem ZERO amostras" (`spawn_reason`: NULL 349 ·
# trigger 71). Esse zero tem TRÊS mundos por trás, e nenhum contador os separa:
#   (a) o produtor está mudo (defeito);
#   (b) ninguém rodou o cenário que o exercita (ausência de população);
#   (c) rodaram o cenário e PARARAM antes do fato que produz o carimbo.
# Este probe fabrica a população de propósito e ramifica sobre O QUE ela produz —
# então o resultado distingue (a) de (b)/(c), que é o que a decisão do F4 precisa.
#
# ── O que a leitura do código já estabeleceu (não re-descobrir) ────────────────
# 1. `spawn_reason='collect'` é escrito num ÚNICO lugar: `handle_collect_engage`
#    (webhook.py:2118) — o ENGAJAMENTO, não o parqueamento. `handle_collect` é
#    LAZY: entrega o convite e suspende sem criar sessão (webhook.py:1860).
#    ⇒ **rodar o cenário `limite_entrega` até parquear produz ZERO por construção.**
# 2. O YAML do cenário declara que a entrega real do link é trilha NÃO construída
#    (`skill_limite_entrega_v1.yaml:65-67`): ninguém clica, logo ninguém engaja.
#    ⇒ o clique tem de ser dado por este probe. É o único passo sintético, e é
#    exatamente o passo cuja infraestrutura não existe.
# 3. `GET /survey/{token}` NÃO publica inbound — só semeia o ctx e cunha o JWT.
#    Quem publica é a PÁGINA, ao conectar o WS (webchat.py:304). ⇒ um curl mede a
#    metade errada; por isso o `_ws_engage.py`.
# 4. A linha de `analytics.sessions` só recebe `spawn_reason` no FECHAMENTO
#    (orchestrator-bridge/main.py:2809-2918, `contact_closed`). Sessão aberta ⇒
#    `NULL` legítimo. ⇒ há uma quarta ausência a distinguir, e o probe a nomeia.
#
# ── Previsões escritas ANTES de rodar (2026-08-25) ─────────────────────────────
#   P1 baseline: exatamente DOIS valores de spawn_reason (NULL, trigger);
#                collect = 0 e delegate = 0.
#   P2 slot: o snapshot `current` de `limite_entrega` contém `type: collect`.
#   P3 smoke `limite_tres_acessos`: sai 0.
#   P4 o `resume_token` da pendência é uma chave `{t}:collect:*` (⇒ é COLLECT, e
#      não delegate — discriminador que o passo 8 do smoke NÃO faz).
#   P5 o clique marca a pendência `engaged` e cria `survey_session_id`.
#   P6 o ctx da sessão-filha traz `session.spawn_reason = "collect"`.
#   P7 depois do WS + fechamento, a linha em `sessions` traz `collect`
#      ⇒ a contagem de `collect` vai de 0 para 1.
#   ⚠️ Os frágeis são P2 (ambiente é DB-owned; o YAML não manda) e P7 (depende de
#      o flow fechar dentro da janela de poll).
#
# NÃO julga: entrega real do link (não existe), UX da página, nem `delegate`, que
# é 0 por DESENHO — `delegate()` sempre roda como conferência na sessão do
# chamador (skill-flow-service/index.ts:509-514; webhook.py:1669), então
# `handle_delegate` nunca é chamado e nenhuma sessão-filha nasce com esse rótulo.
#
# Veredicto: 0 = verde (há produtor) · 1 = DEFEITO (produtor mudo) · 2 = INCONCLUSIVO.
# Uso: bash infra/test/probe_spawn_reason_collect.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
CG="${CG:-http://localhost:8010}"
AR="${AR:-http://localhost:3300}"
CURL="curl -s --max-time 20"

redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }
chq()   { $COMPOSE exec -T clickhouse clickhouse-client -d plughub_demo --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⚠️  INCONCLUSIVO: $1"; exit 2; }

dist() {
  chq "SELECT ifNull(spawn_reason,'<NULL>') AS sr, count() AS n
       FROM sessions AS s FINAL
       WHERE tenant_id='$TENANT'
       GROUP BY sr ORDER BY n DESC
       FORMAT TSV"
}

echo "══ 0) PREFLIGHT DO LEITOR — o CH responde e o tenant tem população? ══"
# Sem isto, um leitor quebrado devolveria zero para TUDO e o veredicto acusaria o
# produtor. A testemunha é o total: se ele for 0, nada abaixo se conclui.
TOTAL=$(chq "SELECT count() FROM sessions AS s FINAL WHERE tenant_id='$TENANT'" | tr -d '\r' | head -1)
case "$TOTAL" in
  ''|*[!0-9]*) die "não consegui LER analytics.sessions — resposta: ${TOTAL:0:200}" ;;
esac
[ "$TOTAL" -gt 0 ] || die "tenant '$TENANT' tem 0 sessões — sem população não há linha de base."
echo "   ✓ leitor OK · total de sessões no tenant = $TOTAL"

echo "══ 1) BASELINE — distribuição de spawn_reason ANTES ══"
BEFORE=$(dist)
echo "$BEFORE" | sed 's/^/      /'
N_COLLECT_0=$(echo "$BEFORE" | awk -F'\t' '$1=="collect"  {print $2}'); N_COLLECT_0=${N_COLLECT_0:-0}
N_DELEG_0=$(echo   "$BEFORE" | awk -F'\t' '$1=="delegate" {print $2}'); N_DELEG_0=${N_DELEG_0:-0}
echo "   → collect=$N_COLLECT_0  delegate=$N_DELEG_0"

echo "══ 2) PREFLIGHT DO SLOT — o pool limite_entrega executa um step collect? ══"
# O bridge executa o SNAPSHOT do slot `current`, não o arquivo YAML (seed-if-absent).
# Julgar o produtor sem conferir isto é julgar código que não está rodando.
# A conferência é ESTRUTURAL (acha o step pelo id e lê o TIPO dele); um `grep` por
# "collect" casaria com o comentário do YAML que documenta a mudança.
SLOTS=$($CURL "$AR/v1/pools/limite_entrega/slots" -H "x-tenant-id: $TENANT" 2>/dev/null)
printf '%s' "$SLOTS" | grep -q 'parquear_resultado' \
  || die "o snapshot do slot 'current' de limite_entrega não contém 'parquear_resultado'
        (ou o leitor do registry falhou). Resposta: ${SLOTS:0:200}"
STEPTYPE=$(printf '%s' "$SLOTS" | python3 -c '
import json, re, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    print("READER_FAIL"); raise SystemExit

# Dois formatos possiveis de snapshot, e o probe cobre os dois sem parser de YAML:
#   (1) objeto/dict com steps[] -> acha o step por id e le a chave "type"
#   (2) texto YAML embutido      -> acha "- id: parquear_resultado" e a linha "type:"
found = []
def walk(node):
    if isinstance(node, dict):
        if node.get("id") == "parquear_resultado" and node.get("type"):
            found.append(node["type"])
        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)
    elif isinstance(node, str) and "parquear_resultado" in node:
        m = re.search(r"id:\s*parquear_resultado\s*\n\s*type:\s*(\w+)", node)
        if m:
            found.append(m.group(1))
walk(d)
print(found[0] if found else "NOT_FOUND")
' 2>/dev/null)
echo "   → tipo do step parquear_resultado no snapshot: ${STEPTYPE:-<vazio>}"
if [ "$STEPTYPE" = "collect" ]; then
  ok "slot 'current' executa um step 'collect' (F0 está DEPLOYADA neste ambiente)"
elif [ "$STEPTYPE" = "READER_FAIL" ] || [ -z "$STEPTYPE" ]; then
  die "não consegui LER o snapshot do slot (resposta não é JSON, ou o python falhou).
        Isto é falha do LEITOR — nada se conclui sobre o slot nem sobre o produtor.
        Resposta: ${SLOTS:0:200}"
else
  die "o slot 'current' de limite_entrega tem 'parquear_resultado' mas NÃO como 'collect'
        — o snapshot é anterior à F0.2 e o cenário parquearia com 'delegate'. Nada se
        conclui sobre o produtor de collect. Re-snapshote:
          H='-H x-tenant-id:$TENANT -H x-service-token:changeme_agent_registry_service_token_demo -H content-type:application/json'
          curl -s -X PUT  $AR/v1/pools/limite_entrega/slots/next \$H -d '{\"skill_id\":\"skill_limite_entrega_v1\"}'
          curl -s -X POST $AR/v1/pools/limite_entrega/promote \$H"
fi

echo "══ 3) roda o cenário de ponta a ponta (fonte da população) ══"
CPF="529$(date +%s | tail -c 9)"
echo "   → CPF do caso: $CPF   (o smoke leva ~1–2 min)"
CPF="$CPF" bash infra/test/smoke_limite_tres_acessos.sh > /tmp/_probe_collect_smoke.log 2>&1
SMOKE_RC=$?
tail -8 /tmp/_probe_collect_smoke.log | sed 's/^/      │ /'
[ "$SMOKE_RC" -eq 0 ] \
  || die "o smoke saiu $SMOKE_RC — sem cenário completo não há collect parqueado.
        Log inteiro em /tmp/_probe_collect_smoke.log"
ok "cenário completo (smoke verde)"

CUST=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"provision\":true,\"anchors\":[{\"kind\":\"cpf\",\"value\":\"$CPF\"}]}" \
  | jq -r '.customer_id // empty')
[ -n "$CUST" ] || die "não consegui reresolver o customer_id do CPF $CPF"
echo "   ✓ customer_id = $CUST"

echo "══ 4) a pendência é de COLLECT (e não de delegate)? ══"
TOKEN=""; POL=""
for _ in $(seq 1 20); do
  PEND=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CUST?tenant_id=$TENANT")
  POL=$(echo "$PEND" | jq -r '.policy // empty')
  TOKEN=$(echo "$PEND" | jq -r '.resume_token // empty')
  [ "$POL" = "auto" ] && [ -n "$TOKEN" ] && break
  sleep 1
done
[ -n "$TOKEN" ] || die "nenhuma pendência sob $CUST — o parking não chegou a existir."
echo "   → policy=$POL token=${TOKEN:0:24}…"
RAWCOLLECT=$(redis GET "${TENANT}:collect:${TOKEN}" | tr -d '\r')
if [ -n "$RAWCOLLECT" ]; then
  ok "o resume_token É um collect pending ({t}:collect:*) — parking por collect, confirmado"
else
  die "o token da pendência NÃO tem chave {t}:collect:* — este parking é DELEGATE, não
        collect. O passo 8 do smoke fica verde nos dois casos (ele só olha policy=auto),
        então este é o discriminador que faltava. Nada se conclui sobre o produtor."
fi
[ -z "$(echo "$RAWCOLLECT" | jq -r '.survey_session_id // empty')" ] \
  && ok "pré-estado: pendência ainda NÃO engajada (sem survey_session_id)" \
  || bad "a pendência já vinha engajada — o diferencial do passo 5 perde o antes"

echo "══ 5) o CLIQUE (engajamento) — única parte sintética, e é a que não tem trilha ══"
PAGE=$($CURL "$CG/survey/$TOKEN")
BOOT=$(printf '%s' "$PAGE" | grep -o 'var BOOT = {[^;]*}' | sed 's/^var BOOT = //')
RAW2=$(redis GET "${TENANT}:collect:${TOKEN}" | tr -d '\r')
SSID=$(echo "$RAW2" | jq -r '.survey_session_id // empty')
if [ -n "$SSID" ]; then
  ok "engajamento registrado — sessão-filha $SSID (status=$(echo "$RAW2" | jq -r '.status // "?"'))"
else
  bad "o GET /survey/\$token NÃO engajou (a página caiu no fallback anônimo). Sem sessão
        criada, o produtor de spawn_reason não chega a ser exercido."
  echo "   → resposta (200 chars): $(printf '%s' "$PAGE" | head -c 200)"
  echo "   ⚠️  INCONCLUSIVO a partir daqui."; exit 2
fi

echo "══ 6) TESTEMUNHA DO PRODUTOR — o ctx da sessão-filha traz o rótulo? ══"
CTXSR=$(redis HGET "${TENANT}:ctx:${SSID}" "session.spawn_reason" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
[ "$CTXSR" = "collect" ] \
  && ok "ctx: session.spawn_reason = collect" \
  || bad "ctx: session.spawn_reason = '${CTXSR:-<ausente>}' (esperado 'collect') — o
        produtor de webhook.py:2118 não escreveu. Este é o defeito (a), produtor mudo."

echo "══ 7) o inbound — conecta o WS como o cliente conectaria ══"
JWT=$(echo "$BOOT" | jq -r '.jwt // empty' 2>/dev/null)
POOL=$(echo "$BOOT" | jq -r '.pool_id // empty' 2>/dev/null)
[ -n "$JWT" ] && [ -n "$POOL" ] \
  || die "não consegui extrair jwt/pool_id do bootstrap da página (BOOT='${BOOT:0:120}')"
echo "   → pool da sessão de retorno: $POOL"
$COMPOSE cp infra/test/_ws_engage.py channel-gateway:/tmp/_ws_engage.py >/dev/null 2>&1 \
  || die "docker compose cp falhou — sem o cliente WS não há inbound a publicar"
WSOUT=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_engage.py "$JWT" "$POOL" 30 2>&1)
echo "$WSOUT" | sed 's/^/      │ /'
echo "$WSOUT" | grep -q '^AUTHENTICATED' \
  || die "o WS não autenticou — o inbound não foi publicado e nada se conclui sobre a
        linha de analytics."
ok "WS autenticado — inbound publicado pelo caminho real do webchat"

echo "══ 8) a linha de analytics carrega o rótulo? (só chega no FECHAMENTO) ══"
ROW=""; SR=""; ST=""; WAITED=0
for i in $(seq 1 60); do
  ROW=$(chq "SELECT ifNull(spawn_reason,'<NULL>'), status FROM sessions AS s FINAL
             WHERE tenant_id='$TENANT' AND session_id='$SSID' FORMAT TSV" | tr -d '\r' | head -1)
  SR=$(echo "$ROW" | awk -F'\t' '{print $1}')
  ST=$(echo "$ROW" | awk -F'\t' '{print $2}')
  [ "$SR" = "collect" ] && { WAITED=$i; break; }
  sleep 1
done
echo "   → linha: spawn_reason='${SR:-<sem linha>}' status='${ST:-}' após ${WAITED:-60}s"
if [ "$SR" = "collect" ]; then
  ok "sessions.spawn_reason = collect — a classe de linha 'acesso outbound' TEM produtor"
elif [ -z "$ROW" ]; then
  bad "nenhuma linha em sessions para $SSID em 60 s — o inbound não virou sessão
        (roteamento/ingest), e isso é outro defeito, não o do rótulo."
elif [ "$SR" = "<NULL>" ] && [ "$ST" = "active" ]; then
  echo "   ⚠️  linha existe e está ABERTA: NULL aqui é legítimo (o rótulo só é carimbado
        no contact_closed). Não é defeito do produtor — é a quarta ausência."
  echo "   ⚠️  INCONCLUSIVO no passo 8; os passos 5–6 já provaram o produtor do ctx."
else
  bad "linha fechada (status=$ST) com spawn_reason='$SR' — o bridge não repetiu o
        rótulo na linha de close (main.py:2918) e o ReplacingMergeTree o apagou."
fi

echo "══ 9) distribuição DEPOIS ══"
AFTER=$(dist)
echo "$AFTER" | sed 's/^/      /'
N_COLLECT_1=$(echo "$AFTER" | awk -F'\t' '$1=="collect" {print $2}'); N_COLLECT_1=${N_COLLECT_1:-0}
echo "   → collect: $N_COLLECT_0 → $N_COLLECT_1   (delegate segue $N_DELEG_0, por DESENHO)"

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  sessão de survey : ${SSID:-<nenhuma>}"
echo "  customer_id      : ${CUST:-}   (CPF $CPF)"
echo "  ✅ $PASS   ❌ $FAIL"
echo "═══════════════════════════════════════════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
