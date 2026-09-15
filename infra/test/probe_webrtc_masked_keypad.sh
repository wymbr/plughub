#!/usr/bin/env bash
# probe_webrtc_masked_keypad.sh — 2026-09-15  (VOZ-05, fatia A da coleta mascarada)
#
# PERGUNTA: um cliente WebRTC consegue entregar um valor PROTEGIDO a um fluxo — pelo campo
# protegido do widget —, sem que o valor, o texto livre ou a fala dele durante a coleta
# apareçam em superfície de leitura da plataforma?
#
# O QUE HAVIA (medido em 2026-09-15)
#   · `set-next` de `skill_auth_form_v1` num pool só-WebRTC: 422 `masked_sem_canal_capaz` —
#     webrtc não declarava `masked_input`, e por isso nenhum vazamento: RECUSA;
#   · o menu do WebRTC não funcionava nem sem máscara: o adapter mandava o menu aninhado em
#     `payload` e gravava a resposta como envelope JSON em `menu:result:{sid}` — chave e formato
#     que o motor não lê; o widget lia e mandava outras chaves;
#   · o bridge entrega ao menu que espera QUALQUER resposta do cliente, e só redige campo a campo
#     o `menu_result`: uma fala transcrita (`text`) durante a coleta viraria o valor do form, em
#     claro no histórico — o buraco que abriria no dia da declaração.
#
# RAMOS
#   A  a capacidade é declarada nas DUAS casas (TS canônico e gêmeo Python)
#   M0 o deploy do skill mascarado num pool só-WebRTC é ACEITO (antes: 422)
#   K1..K4, V1, V2 — exercício ao vivo (ver `_webrtc_masked_keypad_exercise.py`)
#   H1 (no exercício, antes do fechamento) histórico (`session:{sid}:messages`): as duas submissões REDIGIDAS (e-mail visível,
#      `••••••` nos mascarados) e NENHUMA outra linha do cliente (nem texto livre, nem fala)
#   H2 stream canônico e H3 logs de cinco serviços sem nenhum dos valores
#   V1 o gateway TRANSCREVEU a fala e a DESCARTOU — a linha prova que o bot ouviu; sem ela, a
#      ausência da fala no histórico seria só o bot surdo
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz05_masked"
SKILL="skill_probe_masked_keypad_v1"
FIXTURE="infra/test/fixtures/skill_probe_masked_keypad_v1.json"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

# valores distintivos por rodada — procurados depois em toda superfície
rnd() { tr -dc '0-9' < /dev/urandom | head -c "$1"; }
EMAIL="probe.voz05.$(rnd 6)@exemplo.com"
SENHA_ERRADA="7$(rnd 5)"
SENHA_CERTA="1$(rnd 5)"
CODIGO_2FA="8$(rnd 5)"
TEXTO_LIVRE="minha senha e 9$(rnd 8)"
FRASE_FALADA="A minha senha secreta é quatro sete nove dois."

echo "════════════════════════════════════════════════════════════════════"
echo " valor protegido pelo WebRTC: chega ao fluxo e a nenhuma superficie?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · declaracao ─────────────────────────────────────────────────────"
grep -qE '^\s*webrtc:\s*\[.*"masked_input"' packages/schemas/src/channel-capabilities.ts \
  && ok "A1 TS canonico: webrtc declara masked_input" || falha "A1 TS canonico: webrtc NAO declara masked_input"
grep -qE '"webrtc":\s*frozenset\(\{.*"masked_input"' packages/channel-gateway/src/plughub_channel_gateway/channel_capability_registry.py \
  && ok "A2 gemeo Python: webrtc declara masked_input" || falha "A2 gemeo Python: webrtc NAO declara masked_input"

echo ""
echo "── M0 · deploy ────────────────────────────────────────────────────────"
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then
  incon "M0: login do admin falhou ou gateway fora do ar — nada medido ao vivo"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_webrtc_masked_keypad (VOZ-05 fatia mascarada)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
  fi
  # O skill é FIXTURE do probe (não o `skill_auth_form_v1`, cujo `validate_pin` quebra quando o
  # contato chega sem identidade — ver a nota no JSON). Publicado e promovido A CADA rodada: o M0
  # é o juiz de deploy mascarado × canais do pool, e "já estava promovido" não o exerceria.
  BODY=$(mktemp)
  PUB=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
  if [ "$PUB" != 200 ] && [ "$PUB" != 201 ]; then
    incon "fixture $SKILL nao publicada (http $PUB): $(head -c 200 "$BODY")"
    PRONTO=0
  else
    SN=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$POOL/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
    if [ "$SN" = 200 ]; then
      PM=$(curl -s -o "$BODY" -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$POOL/promote" -d '{}')
      [ "$PM" = 200 ] && { ok "M0 set-next + promote de skill mascarado ($SKILL) em pool so-WebRTC aceitos"; PRONTO=1; }         || { falha "M0 promote recusado (http $PM): $(head -c 300 "$BODY")"; PRONTO=0; }
    else
      falha "M0 set-next recusado (http $SN): $(jq -r '.error // empty' "$BODY") — $(jq -r '.message // empty' "$BODY" | cut -c1-160)"
      PRONTO=0
    fi
  fi
  rm -f "$BODY"
  sleep 3

  if [ "$PRONTO" = 1 ]; then
    INST=""
    for _ in $(seq 1 40); do
      INST=$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)
      [ -n "$INST" ] && break
      sleep 1
    done
    if [ -z "$INST" ]; then
      incon "nenhuma instancia de IA em $POOL 40 s depois do promote — exercicio nao roda"
    else
      echo ""
      echo "── K · exercicio ao vivo (instancia $INST) ────────────────────────────"
      ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
      T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      name="probe_voz05m_$$_$RANDOM"
      OUT=$(timeout "${EXERCISE_TIMEOUT_S:-300}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            $ENV -e POOL="$POOL" -e EMAIL="$EMAIL" -e SENHA_ERRADA="$SENHA_ERRADA" -e SENHA_CERTA="$SENHA_CERTA" \
            -e CODIGO_2FA="$CODIGO_2FA" -e TEXTO_LIVRE="$TEXTO_LIVRE" -e FRASE_FALADA="$FRASE_FALADA" \
            "$IMG" - < infra/test/_webrtc_masked_keypad_exercise.py 2>&1)
      [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
      while IFS= read -r l; do
        case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
          INFO\ *) echo "  INFO    ${l#INFO }";; esac
      done <<< "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL|INFO) ')"
      N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) (K[1-4]|H[12]) ')
      [ "$N" -ge 7 ] || falha "exercicio emitiu $N de 7 veredictos K/H:$(printf '%s' "$OUT" | grep -vE '^(OK|FALHA|INCONCL|INFO|SID|T_FALA) ' | tail -3 | tr '\n' ' ' | cut -c1-300)"
      SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p')

      if [ -n "$SID" ]; then
        echo ""
        echo "── H · superficies da sessao $SID ─────────────────"
        VAZL=""
        for ct in "$GW" plughub-demo-orchestrator-bridge-1 plughub-demo-mcp-server-plughub-1 plughub-demo-skill-flow-service-1 plughub-demo-mcp-server-auth-1; do
          LOGS=$(docker logs --since "$T0" "$ct" 2>&1)
          for v in "$SENHA_ERRADA" "$SENHA_CERTA" "$CODIGO_2FA" "${TEXTO_LIVRE##* }"; do
            printf '%s' "$LOGS" | grep -qF "$v" && VAZL="$VAZL ${ct#plughub-demo-}:$v"
          done
        done
        [ -z "$VAZL" ] && ok "H3 logs de gateway, bridge, mcp-server, skill-flow e mcp-server-auth sem os valores" \
          || falha "H3 VAZOU em log:$VAZL"

        if printf '%s\n' "$OUT" | grep -q '^T_FALA '; then
          achou=""
          for _ in $(seq 1 10); do
            docker logs --since "$T0" "$GW" 2>&1 | grep -q "fala transcrita DESCARTADA durante coleta mascarada session=$SID" && { achou=1; break; }
            sleep 1
          done
          [ -n "$achou" ] && ok "V1 o gateway transcreveu a fala da coleta e a DESCARTOU (o bot ouviu)" \
            || falha "V1 nenhuma fala descartada para $SID — o bot nao ouviu, ou a fala foi publicada"
        fi
      fi
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
