#!/usr/bin/env bash
# probe_webrtc_speech_tuning.sh — 2026-09-17  (VOZ-18)
#
# PERGUNTA: numa chamada WebRTC atendida por IA, os parâmetros de FALA que o menu declara na coleta
# por voz valem — `min_confidence` contra uma confiança MEDIDA, e `end_silence_ms` mudando onde a
# fala do cliente termina, só enquanto aquele menu espera?
#
# O ESTADO QUE O ORIGINOU (VOZ-05 fatia 5b, medido): o speaches era chamado com `response_format=json`,
# que só traz o texto — a confiança ficava no default 1,0, e `min_confidence` nunca reprovava nada;
# `end_silence_ms`/`max_speech_s` eram lidos e ignorados (o log dizia "NAO aplica").
#
# E (VOZ-19) RUÍDO NÃO É FALA? Sem o VAD do speaches, o Whisper transcrevia ruído como "Obrigado." (44 de 44
# trechos de não-fala medidos), e a transcrição gastava a tentativa do menu.
#
# RAMOS (exercício: UMA chamada, participante LiveKit real, fala sintetizada pelo speaches):
#   N1 2 s de ruído branco (RMS 1 500) e depois "Cancelar." num menu de UMA tentativa → valor ·
#   C1 "Atendente." com min_confidence 0.99 → inválido · C2 CONTROLE a mesma fala com 0.3 → valor ·
#   S2 "Cancelar." + 1,2 s + "Atendente." sem ajuste, logo depois do m3 → a primeira responde sozinha
#   (o S1, a mesma pausa com end_silence_ms 2500, só informa o desfecho — ver W1)
# RAMOS (aqui, sobre log e stream da sessão):
#   N0 o ruído chegou ao STT e o VAD o descartou (linha do gateway desde o início da chamada) ·
#   N2 nenhuma fala registrada antes do "Cancelar." do m0 ·
#   L1 o inválido do C1 foi por CONFIANÇA (linha do gateway com a medida) · L2 CONTROLE só UMA recusa
#   por confiança na sessão (o m2 não recusou) · M1 a confiança chega ao registro da fala MEDIDA
#   (0 < c < 1), nunca o 1,0 de antes · W1 a fala do m3 é UMA, com janela >= 3 s (as duas palavras e a
#   pausa) · W2 CONTROLE a primeira fala do m4 fecha em < 2,5 s — o ajuste desligou com o menu.
#   A janela, não o texto: pela chamada o Whisper pode descartar uma das palavras (medido 2026-09-17).
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
POOL="probe_voz18_speech"
SKILL="skill_probe_speech_tuning_v1"
FIXTURE="infra/test/fixtures/skill_probe_speech_tuning_v1.json"
FALHA=0
INCONCL=0

# `grep -c`, nunca `grep -q`: sob pipefail o `-q` mata o `docker logs` de SIGPIPE e reprova achando
log_conta() { docker logs --since 20m "$GW" 2>&1 | grep -c "$1"; }

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " a coleta por voz aplica confianca medida e silencio de fim por menu?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then
  incon "login do admin falhou ou gateway fora do ar — nada medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_webrtc_speech_tuning (VOZ-18)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
  fi
  BODY=$(mktemp)
  PUB=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
  PUBMSG=$(head -c 300 "$BODY")
  SN=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$POOL/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  PM=$(curl -s -o "$BODY" -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$POOL/promote" -d '{}')
  rm -f "$BODY"
  if [ "$PUB" != 200 ] && [ "$PUB" != 201 ] || [ "$SN" != 200 ] || [ "$PM" != 200 ]; then
    incon "fixture nao implantada (publish=$PUB set-next=$SN promote=$PM) $PUBMSG"
  else
    INST=""
    for _ in $(seq 1 40); do
      INST=$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)
      [ -n "$INST" ] && break
      sleep 1
    done
    if [ -z "$INST" ]; then
      incon "nenhuma instancia de IA em $POOL 40 s depois do promote"
    else
      sleep 3
      ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
      name="probe_voz18_$$_$RANDOM"
      INICIO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      OUT=$(timeout "${EXERCISE_TIMEOUT_S:-400}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            $ENV -e POOL="$POOL" "$IMG" - < infra/test/_webrtc_speech_tuning_exercise.py 2>&1)
      [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
      while IFS= read -r l; do
        case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";; esac
      done <<< "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL) ')"
      printf '%s\n' "$OUT" | grep -E '^INFO ' | sed 's/^INFO /  INFO    /'
      N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA|INCONCL) (N1|C1|C2|S2) ')
      [ "$N" -ge 4 ] || falha "exercicio emitiu $N de 4 veredictos: $(printf '%s' "$OUT" | grep -vE '^(OK|FALHA|INCONCL) ' | tail -3 | tr '\n' ' ' | cut -c1-300)"

      SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
      if [ -z "$SID" ]; then
        incon "L1/L2/M1/W1/W2 a chamada nao abriu sessao — nada a conferir no log e no stream"
      else
        sleep 3
        # o menu_id no log é o do motor (uuid), não o id do passo: conta-se na sessão, e só o m1 recusa
        n1=$(log_conta "abaixo da confianca minima no menu .*session=$SID")
        if [ "${n1:-0}" -ge 1 ]; then
          ok "L1 um invalido por confianca medida: $(docker logs --since 20m "$GW" 2>&1 | grep "confianca minima no menu .*session=$SID" | head -1 | grep -oE '\([0-9.]+ < [0-9.]+\)')"
        else
          falha "L1 nenhuma recusa por confianca na sessao $SID — o C1 pode ter sido invalido por outro motivo"
        fi
        [ "${n1:-0}" -le 1 ] && ok "L2 CONTROLE so uma recusa por confianca na sessao (o m2, min 0.3, nao recusou)" \
                             || falha "L2 $n1 recusas por confianca na sessao — o m2 (min 0.3) tambem recusou"

        # N0 — o N1 passaria com um ruído que nunca chegou ao STT: o VAD tem de tê-lo descartado nesta chamada
        # (o provedor não conhece a sessão; a janela é o tempo da chamada, num pool só do probe)
        nv=$(docker logs --since "$INICIO" "$GW" 2>&1 | grep -c "sem fala pelo VAD")
        [ "${nv:-0}" -ge 1 ] && ok "N0 o ruido chegou ao STT e o VAD o descartou ($nv trecho(s) desde $INICIO)" \
                             || falha "N0 nenhum trecho descartado pelo VAD desde $INICIO — o N1 nao prova que o ruido chegou"

        # as falas do cliente no stream, na ordem: confiança, janela (ms) e texto de cada uma
        FALAS=$(docker exec "$REDIS" redis-cli --raw XRANGE "session:$SID:stream" - + 2>/dev/null \
                | grep '"speech"' | jq -r '"\(.content.speech.confidence // "ausente") \((.content.speech.end_ms // 0) - (.content.speech.start_ms // 0)) \(.text // "")"' 2>/dev/null)
        NF=$(printf '%s\n' "$FALAS" | grep -c .)
        echo "  INFO    falas do cliente (confianca janela_ms texto): $(printf '%s\n' "$FALAS" | paste -sd '|' -)"
        prim=$(printf '%s\n' "$FALAS" | sed -n 1p | cut -d' ' -f3-)
        case "$prim" in
          *ancel*) ok "N2 a primeira fala registrada e o 'Cancelar.' do m0 ($prim) — o ruido nao deixou registro" ;;
          *) falha "N2 a primeira fala registrada e '$prim', nao o 'Cancelar.' do m0 — o ruido virou fala" ;;
        esac
        if [ "$NF" -lt 5 ]; then
          incon "M1/W1/W2 $NF falas do cliente no stream session:$SID:stream, esperadas >= 5 (m0 m1 m2 m3 m4)"
        else
          fora=$(printf '%s\n' "$FALAS" | awk '!($1 ~ /^[0-9.]+$/ && $1 > 0 && $1 < 1)' | grep -c .)
          [ "$fora" -eq 0 ] && ok "M1 as $NF falas registradas trazem confianca medida em (0,1)" \
                            || falha "M1 $fora de $NF falas sem confianca medida em (0,1)"
          w3=$(printf '%s\n' "$FALAS" | sed -n 4p | awk '{print $2}')
          w4=$(printf '%s\n' "$FALAS" | sed -n 5p | awk '{print $2}')
          [ "${w3:-0}" -ge 3000 ] && ok "W1 a fala do m3 (end_silence_ms 2500) juntou as duas palavras: janela ${w3} ms" \
                                  || falha "W1 a fala do m3 fechou em ${w3} ms — a pausa de 1,2 s a cortou, o ajuste nao valeu"
          [ "${w4:-99999}" -lt 2500 ] && ok "W2 CONTROLE a primeira fala do m4 (sem ajuste) fechou em ${w4} ms" \
                                      || falha "W2 a primeira fala do m4 durou ${w4} ms — o ajuste do m3 continuou ligado"
        fi
      fi
    fi
  fi
fi

echo "────────────────────────────────────────────────────────────────────"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
echo " VERDE"; exit 0
