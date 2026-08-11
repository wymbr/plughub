#!/usr/bin/env bash
# probe_edge_surface.sh — Fase 0 do arco de workflow.
#
# NÃO existe borda no repositório. `/channel/webhook/{slug}` (main.py:1302) e
# `/v1/channels/webhook/{skill_id}` (main.py:1387) são rotas do MESMO app FastAPI na
# MESMA porta (docker-compose.demo.yml:1185); não há proxy para `/channel` no
# vite.config.ts nem no Dockerfile do platform-ui, e não há nenhum nginx.conf
# versionado. A separação entre "externo" e "interno" é o filtro
# `allowed_origins={"external"}` (main.py:1347) — de CÓDIGO, não de topologia.
#
# Este probe NÃO constrói a borda e NÃO testa produto. Ele faz uma coisa só:
# **enumera a superfície e exige que todo prefixo esteja CLASSIFICADO**, para que
# quem publicar este serviço saiba o que pode publicar — e para que um prefixo novo
# não entre em silêncio. É testemunha, não portão de produto.
#
# Uso:  bash infra/test/probe_edge_surface.sh
# Env:  GW=http://localhost:8010
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

GW="${GW:-http://localhost:8010}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/packages/channel-gateway/src/plughub_channel_gateway"

echo "══ superfície de borda — channel-gateway ══"
echo "   gw=$GW"

# ── CLASSIFICAÇÃO DECLARADA ────────────────────────────────────────────────────
# Prefixo → classe|motivo. Editar isto é uma DECISÃO, e é o ponto do probe:
# prefixo novo sem linha aqui reprova, em vez de entrar calado.
#
# ⚠️ "externo" NÃO significa "sem auth" — significa "tem de ser alcançável de fora
#    para a função existir". Metade desta lista é callback de provedor ou tráfego de
#    browser, e nenhuma delas é opcional.
declare -A CLASS=(
  [channel]="externo|trigger de webhook por identificador opaco; a porta que a Fase 1 espelha"
  [survey]="externo|página pública de pesquisa, autenticada pela posse do token (survey_web.py:563)"
  [webhooks]="externo|callback de PROVEDOR (whatsapp/email/sms/voice) — Meta e Twilio batem aqui"
  [voice]="externo|áudio TTS buscado pelo provedor + stream de mídia (main.py:680,697,714)"
  [webrtc]="externo|emissão de token LiveKit para o cliente na webapp (main.py:754)"
  [ws]="externo|WebSocket do webchat e do webrtc, aberto pelo BROWSER (main.py:478,729)"
  [webchat]="externo|upload/download de anexo pelo browser (upload_router.py:41)"
  [v1]="INTERNO|rede interna. Abriga o POST /v1/channels/webhook/pool/{id}, ANÔNIMO por construção (main.py:1004)"
  [health]="INTERNO|liveness do orquestrador; nada a ganhar publicando"
)
# Caminhos implícitos do FastAPI — não aparecem em rota nenhuma e por isso escapam de
# toda enumeração. Publicá-los publica o MAPA das rotas internas.
IMPLICIT=(openapi.json docs redoc)

# ── Preflight — duas leituras quebradas são iguais entre si ────────────────────
HEALTH="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$GW/health" 2>/dev/null)"
[ "$HEALTH" = "200" ] || {
  echo "   ⛔ INCONCLUSIVO — $GW/health devolveu '$HEALTH'. O serviço não está de pé;"
  echo "      sem ele a metade RUNTIME deste probe não mede nada."; exit 3; }

OPENAPI="$(curl -s --max-time 10 "$GW/openapi.json" 2>/dev/null)"
N_PATHS="$(printf '%s' "$OPENAPI" | jq -r '.paths | keys | length' 2>/dev/null || echo 0)"
if [ "${N_PATHS:-0}" -lt 5 ]; then
  echo "   ⛔ INCONCLUSIVO — /openapi.json devolveu $N_PATHS caminhos (esperado ≥ 5)."
  echo "      Ou o schema foi desabilitado, ou o jq não está presente. Um 0 aqui NÃO"
  echo "      é 'superfície limpa' — é instrumento cego."; exit 3
fi
echo "   preflight: health=200 · openapi.paths=$N_PATHS"

# ── Lado RUNTIME — o que o serviço realmente atende (HTTP) ─────────────────────
# ⚠️ WebSocket NÃO aparece no OpenAPI. Por isso o lado estático existe: sozinho, o
#    runtime declararia que /ws não existe.
RUNTIME="$(printf '%s' "$OPENAPI" | jq -r '.paths | keys[]' \
           | sed -E 's#^/+##; s#/.*##' | grep -v '^$' | sort -u)"

# ── Lado ESTÁTICO — decoradores no fonte (pega WebSocket) ─────────────────────
[ -d "$SRC" ] || { echo "   ⛔ INCONCLUSIVO — fonte não encontrado em $SRC"; exit 3; }
STATIC="$( { grep -rhoE '^@app\.(get|post|put|patch|delete|websocket)\("/[^"{]*' "$SRC/main.py" \
              | sed -E 's#.*\("/##; s#/.*##'
            grep -rhoE 'APIRouter\(prefix="/[^"]*"' "$SRC" \
              | sed -E 's#.*prefix="/##; s#".*##; s#/.*##'
          } | grep -v '^$' | sort -u )"

ALL="$(printf '%s\n%s\n' "$RUNTIME" "$STATIC" | grep -v '^$' | sort -u)"
N_ALL="$(printf '%s\n' "$ALL" | grep -c . || true)"

echo
echo "── prefixos enumerados (runtime ∪ estático): $N_ALL ──────────────────────"

RC=0
UNCLASSIFIED=""
N_EXT=0; N_INT=0
while read -r p; do
  [ -n "$p" ] || continue
  entry="${CLASS[$p]:-}"
  if [ -z "$entry" ]; then
    printf '   ❓ /%-10s SEM CLASSIFICAÇÃO\n' "$p"
    UNCLASSIFIED="$UNCLASSIFIED /$p"
    RC=1
    continue
  fi
  klass="${entry%%|*}"; why="${entry#*|}"
  [ "$klass" = "externo" ] && N_EXT=$((N_EXT + 1)) || N_INT=$((N_INT + 1))
  printf '   %-8s /%-10s %s\n' "$klass" "$p" "$why"
done <<< "$ALL"

# Classificado que sumiu: avisa, não reprova — remover rota é legítimo, deixar a
# linha órfã é só sujeira.
while read -r k; do
  printf '%s\n' "$ALL" | grep -qx "$k" || echo "   ⚠️  /$k classificado mas AUSENTE — linha órfã na tabela"
done <<< "$(printf '%s\n' "${!CLASS[@]}" | sort)"

# Runtime que o estático não viu ⇒ o parser estático mente, e o probe com ele.
while read -r r; do
  [ -n "$r" ] || continue
  printf '%s\n' "$STATIC" | grep -qx "$r" || {
    echo "   ❌ /$r existe em runtime e NÃO foi achado no fonte — o parser estático está incompleto"
    RC=1; }
done <<< "$RUNTIME"

# ── Caminhos implícitos do FastAPI ────────────────────────────────────────────
echo
echo "── caminhos implícitos (invisíveis a qualquer enumeração) ────────────────"
for ip in "${IMPLICIT[@]}"; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$GW/$ip" 2>/dev/null)"
  if [ "$code" = "200" ]; then
    echo "   INTERNO  /$ip  responde 200 — publicá-lo publica o MAPA das rotas internas"
  else
    echo "   /$ip → $code (não exposto)"
  fi
done

# ── Testemunhas: por que a classificação de /v1 é load-bearing ────────────────
echo
echo "── testemunhas ───────────────────────────────────────────────────────────"
if printf '%s' "$OPENAPI" | jq -e '.paths | has("/v1/channels/webhook/pool/{pool_id}")' >/dev/null 2>&1; then
  echo "   ✓ /v1/channels/webhook/pool/{pool_id} está no schema — a rota ANÔNIMA (main.py:1004)"
  echo "     existe e é descobrível. É ela que torna 'v1=INTERNO' uma exigência, não um gosto."
else
  echo "   ⚠️  rota anônima de pool NÃO encontrada no schema — verificar se mudou de forma"
fi

EDGECFG=0
grep -qE "'\^/channel|\"/channel|location .*\^/channel" \
     "$ROOT/packages/platform-ui/vite.config.ts" "$ROOT/packages/platform-ui/Dockerfile" 2>/dev/null && EDGECFG=1
if [ "$EDGECFG" -eq 0 ]; then
  echo "   ✓ nenhuma config de borda publica /channel (vite.config.ts, Dockerfile)."
  echo "     Confirma: a distinção externo×interno NÃO é topológica hoje. Este probe"
  echo "     documenta o requisito; ele não o aplica — nada aqui impede um deploy errado."
else
  echo "   ⚠️  ACHADA config de borda para /channel — a premissa da Fase 0 mudou, reler a spec"
fi

# ── Veredicto ─────────────────────────────────────────────────────────────────
echo
echo "   externos=$N_EXT · internos=$N_INT · total=$N_ALL"
if [ "$RC" -ne 0 ]; then
  echo "❌ REPROVOU —${UNCLASSIFIED:+ prefixo(s) sem classificação:$UNCLASSIFIED}"
  echo "   Classificar é DECIDIR o que um deploy pode publicar. Não silencie editando"
  echo "   a tabela sem antes responder: esta rota precisa ser alcançável de fora?"
else
  echo "✅ OK — toda a superfície está classificada."
  echo "   Lembrete honesto: isto é uma DECLARAÇÃO. Nenhum teste no repositório verifica"
  echo "   o que o deploy realmente publica."
fi
exit "$RC"
