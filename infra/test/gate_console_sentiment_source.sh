#!/usr/bin/env bash
# gate_console_sentiment_source.sh — o número que a Console EXIBE ao operador.
#
# ── O que este gate julga ────────────────────────────────────────────────────
# Os dois gates de 2026-08-24 provam que a plataforma MEDE sentimento
# (`probe_sentiment_producer.sh` = metade gateway; `gate_sentiment_engine_half.sh`
# = metade engine). Nenhum dos dois toca a terceira perna: **MEDIR não é EXIBIR**.
# Em 08-24 mediu-se -0.50 no ContextStore e a barra da Console dizia "Neutral".
#
# Este gate julga a leitura, sobre o endpoint que a Console de fato consome —
# `GET /api/supervisor_state/:sessionId` no mcp-server (porta 3100), NÃO a tool
# MCP homônima. Eram duas implementações independentes do mesmo cálculo, e a que
# a passagem apontava (`tools/supervisor.ts:118`) não é a que desenha a tela.
# Hoje as duas compartilham `lib/session-sentiment.ts`.
#
# ── As DUAS metades, e por que a testemunha é obrigatória ────────────────────
# Um gate que só verificasse "score medido chega à tela" passaria com o código
# ANTIGO no dia em que `partial_params` tivesse valor. O discriminador é o outro
# lado: **sessão SEM medição tem de devolver `null`, nunca `0`** — porque `0` é
# um ponto legítimo da escala ("neutro"), e era exatamente isso que o `?? 0`
# publicava para toda sessão da plataforma.
#
# A testemunha é deliberadamente severa: o ctx da sessão EXISTE e tem outra tag;
# só a tag de sentimento está ausente. Isso separa "não há contexto nenhum" de
# "há contexto e não houve medição", que é o caso real de toda sessão que não
# passou pela fila.
#
# ── Uso ──────────────────────────────────────────────────────────────────────
#   bash infra/test/gate_console_sentiment_source.sh
#
# Não precisa de contato real: semeia duas sessões sintéticas no Redis e as
# apaga no fim. Por isso é re-executável, ao contrário do gate da metade engine.
#
# Sai: 0 = VERDE · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

DC="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"
MCP="${MCP:-http://localhost:3100}"
AUTH="${AUTH:-http://localhost:3202}"   # 3200 no host é o ai-gateway, não a auth-api
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"
AD_PASS="${AD_PASS:-changeme_admin}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'

STAMP="$(date +%s)"
SID_M="gate-sentsrc-measured-$STAMP"
SID_W="gate-sentsrc-witness-$STAMP"

R() { $DC exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }
die() { echo "   ⛔ INCONCLUSIVO: $1"; cleanup; exit 3; }
cleanup() {
  R DEL "session:$SID_M:meta" >/dev/null
  R DEL "session:$SID_W:meta" >/dev/null
  R DEL "$TENANT:ctx:$SID_M"  >/dev/null
  R DEL "$TENANT:ctx:$SID_W"  >/dev/null
}

echo "══ Console · fonte do sentimento exibido ══"
echo

# ── Preflight 1 · a imagem em execução tem o helper? ─────────────────────────
# "Saída byte-idêntica é sinal de que nada mudou": nenhum serviço monta o fonte,
# então medir sem rebuild julga a imagem ANTIGA — e o vermelho seria sobre código
# que não está mais lá. Confere o ARTEFATO, não o fonte no repo.
echo "── preflight 1 · lib/session-sentiment no bundle em execução ─────────────"
HELPER="$($DC exec -T mcp-server-plughub sh -c \
  'ls dist/lib/session-sentiment.js 2>/dev/null || true' < /dev/null 2>/dev/null | tr -d '\r')"
if [ -z "$HELPER" ]; then
  die "o container do mcp-server-plughub não tem \`dist/lib/session-sentiment.js\`.
        A imagem é anterior ao conserto. Rebuild + recriação (build verde NÃO
        recria container):
          $DC build mcp-server-plughub && $DC up -d mcp-server-plughub"
fi
echo "      ✅ $HELPER"

# ── Preflight 2 · token ──────────────────────────────────────────────────────
echo "── preflight 2 · login (o endpoint exige Bearer com role) ────────────────"
TOK="$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')"
[ -n "$TOK" ] || die "login falhou em $AUTH (3202 é auth-api; 3200 é o ai-gateway)"
echo "      ✅ token obtido"

# ── Semeadura ────────────────────────────────────────────────────────────────
# `session:{id}:meta` é String (JSON), NUNCA hash — HSET aqui faria o endpoint ler
# vazio, o tenant sair "" e as DUAS metades devolverem null por igual, o que
# passaria por verde sem julgar nada.
echo
echo "── semeadura · duas sessões sintéticas ───────────────────────────────────"
META="{\"tenant_id\":\"$TENANT\",\"pool_id\":\"gate_sentsrc\",\"started_at\":\"$(date -u +%FT%TZ)\"}"
ENTRY='{"value":-0.5,"confidence":0.8,"source":"ai_inferred:gate","visibility":"agents_only","updated_at":"1970-01-01T00:00:00Z"}'

R SET "session:$SID_M:meta" "$META" >/dev/null
R SET "session:$SID_W:meta" "$META" >/dev/null
R HSET "$TENANT:ctx:$SID_M" 'core.sentiment.current' "$ENTRY" >/dev/null
# Testemunha: ctx EXISTE, com outra tag — só a de sentimento falta.
R HSET "$TENANT:ctx:$SID_W" 'caller.customer_id' '{"value":"cus_gate","confidence":1.0,"source":"gate","visibility":"agents_only","updated_at":"1970-01-01T00:00:00Z"}' >/dev/null
echo "      medida     : $SID_M  (ctx com core.sentiment.current = -0.5)"
echo "      testemunha : $SID_W  (ctx presente, SEM a tag de sentimento)"

echo
echo "── previsão, antes de medir ──────────────────────────────────────────────"
echo "      medida.current     esperado: -0.5     (o score que está no ctx)"
echo "      testemunha.current esperado: null     (ausência, NUNCA 0)"
echo "      trend  nas duas    esperado: null     (não há produtor de histórico)"

ask() { # sid -> corpo JSON
  $CURL -H "Authorization: Bearer $TOK" "$MCP/api/supervisor_state/$1"
}
code() {
  $CURL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" \
    "$MCP/api/supervisor_state/$1"
}

echo
echo "── medida ────────────────────────────────────────────────────────────────"
C_M="$(code "$SID_M")"
[ "$C_M" = "200" ] || die "endpoint devolveu HTTP $C_M para a sessão medida — nada a julgar."
B_M="$(ask "$SID_M")"
V_M="$(printf '%s' "$B_M" | jq -r 'if .sentiment.current == null then "NULL" else (.sentiment.current|tostring) end' 2>/dev/null)"
T_M="$(printf '%s' "$B_M" | jq -r 'if .sentiment.trend   == null then "NULL" else (.sentiment.trend|tostring)   end' 2>/dev/null)"
B_W="$(ask "$SID_W")"
V_W="$(printf '%s' "$B_W" | jq -r 'if .sentiment.current == null then "NULL" else (.sentiment.current|tostring) end' 2>/dev/null)"

echo "      medida.current     : ${V_M:-<ilegível>}"
echo "      medida.trend       : ${T_M:-<ilegível>}"
echo "      testemunha.current : ${V_W:-<ilegível>}"

# Ilegível é INCONCLUSIVO, nunca um ramo escolhido por default — o corpo pode ter
# mudado de forma, e aí o gate não sabe o que está julgando.
[ -n "$V_M" ] && [ -n "$V_W" ] || die "corpo da resposta não expõe \`.sentiment.current\`.
        A forma mudou? Ajustar o extrator antes de julgar. Corpo cru (300 chars):
        $(printf '%s' "$B_M" | head -c 300)"

FAIL=0

# ── Metade 1 · o score medido chega à tela ───────────────────────────────────
if [ "$V_M" != "-0.5" ]; then
  echo
  echo "   ❌ A tela NÃO lê a medição: ctx tem -0.5 e o endpoint devolveu '$V_M'."
  if [ "$V_M" = "0" ]; then
    echo "      '0' é a assinatura do defeito original — a fonte aposentada"
    echo "      (session:{id}:ai → partial_params.sentiment_score) com \`?? 0\` por cima."
  fi
  echo "      Fonte correta: {tenant}:ctx:{sid} → core.sentiment.current,"
  echo "      lida do hash CRU em lib/session-sentiment.ts."
  FAIL=1
fi

# ── Metade 2 · TESTEMUNHA — ausência não pode virar 0 ────────────────────────
if [ "$V_W" = "0" ]; then
  echo
  echo "   ❌ REGRESSÃO DO \`?? 0\`: sessão sem medição devolveu 0, não null."
  echo "      Isso classifica como 'neutral' na Console e desarma as guardas"
  echo "      \`!== null\` que a UI já tem. Ausência de medição tem de ser null."
  FAIL=1
elif [ "$V_W" != "NULL" ]; then
  die "testemunha devolveu '$V_W' — nem null nem 0. Fato novo, não previsto pelo
        gate; investigar antes de chamar de verde ou vermelho."
fi

# ── Metade 3 · trend não é inventado ─────────────────────────────────────────
if [ "$T_M" != "NULL" ]; then
  echo
  echo "   ❌ trend = '$T_M' sem histórico medido. O default antigo era \"stable\","
  echo "      que soa como leitura e é invenção — a mesma família do \`?? 0\`."
  echo "      Enquanto não houver produtor de histórico, trend é null."
  FAIL=1
fi

cleanup

if [ "$FAIL" -ne 0 ]; then
  echo
  echo "   (chaves sintéticas removidas)"
  exit 1
fi

echo
echo "   ✅ A LEITURA ESTÁ PROVADA, nas duas metades."
echo "      · com medição no ctx, a Console recebe -0.5 (e não 'neutral')"
echo "      · sem medição, recebe null — e nenhuma superfície renderiza"
echo "      · trend é null enquanto não houver histórico medido"
echo
echo "   ⚠️  LIMITE: este gate julga o ENDPOINT, não os pixels. Que as quatro"
echo "      superfícies (ActionBar · ContactList · ChatArea · EstadoTab) escondam"
echo "      o indicador com null é garantido por tipo (SentimentState.current:"
echo "      number | null), não por medição — só a tela prova a tela."
echo
echo "   ⚠️  LIMITE 2: exercita a implementação HTTP, que é a que a Console usa."
echo "      A tool MCP \`supervisor_state\` compartilha o mesmo helper, mas seu"
echo "      caminho de leitura não é percorrido aqui."
exit 0
