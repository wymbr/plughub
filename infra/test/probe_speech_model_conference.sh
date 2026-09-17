#!/usr/bin/env bash
# probe_speech_model_conference.sh — 2026-09-17  (VOZ-17 · ADR adr-voice-media-plane.md V13)
#
# PERGUNTA: uma config de fala que o serviço NÃO serve é recusada na GRAVAÇÃO — e a que ele serve
# continua passando?
#
# O ESTADO QUE O ORIGINOU (medido em 2026-09-17)
#   · perfil e default eram gravados DIRETO no config-api pela tela, e a única conferência era o
#     PADRÃO do nome (`speech_config.VOICE_PARAMS`), que diz a forma e nada sobre existir;
#   · um `stt_model` plausível e não instalado era aceito e só falhava na CHAMADA: 404 do serviço a
#     cada frase, `fala PERDIDA` no log do gateway, e nada vermelho em lugar nenhum;
#   · modelo, língua e voz sem perfil vinham do ENV do gateway — config de negócio em env, contra a
#     regra da casa, e sem tela.
#
# RAMOS
#   K0  gateway no ar com a rota na IMAGEM, e serviço de fala com catálogo
#   A1  `GET /v1/speech-models` devolve o que está INSTALADO, por tarefa, com as vozes
#   A2  PORTÃO: anônimo 401 · quem não tem `config.channels` em escrita 403, e nada é gravado
#   B1  CONTROLE POSITIVO: perfil que o serviço serve é gravado e aparece no config-api
#   B2  modelo que o serviço não tem: 422 e o perfil de B1 continua intacto
#   B3  tarefa trocada (modelo de TTS no campo de STT): 422
#   B4  voz que não é daquele modelo: 422
#   B5  campo desconhecido: 422 (forma, sem sequer consultar o serviço)
#   C1  a porta ANTIGA (config-api direto) aceita o MESMO perfil que a nova recusou — a diferença é
#       decisão, está nomeada em `VOZ-29`, e este ramo existe para ela não virar surpresa
#   D1  default do tenant: voz inválida 422 e o namespace `webrtc` não muda
#   D2  default válido é gravado com escopo de TENANT; D2b a leitura separa as três camadas (env ×
#       tenant × efetivo) com a procedência de cada campo; D3 `""` REMOVE o override (volta ao env)
#   E1  a rota apaga o perfil, e ele some do config-api
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
GW_URL="${GW_URL:-http://localhost:8010}"
AUTH="${AUTH:-http://localhost:3202}"
CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
PERFIL="probe_voz17"
SONDA="probe-voz17@plughub.local"
SENHA="Probe#Voz17!"

FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
fim() {
  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
  echo " VERDE — a porta de config de fala confere contra o que o servico TEM"; exit 0
}

echo "════════════════════════════════════════════════════════════════════"
echo " config de fala que o servico nao serve chega a ser gravada?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -n "$TOKEN" ] || { incon "login do admin falhou — nada medido"; fim; }
HS=(-H "Content-Type: application/json" -H "X-Tenant-ID: $TENANT" -H "Authorization: Bearer $TOKEN")

# Sonda sem `config.channels` em escrita: o portão tem de recortar por CAPACIDADE, não por "tem token".
uid() { curl -s -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users?tenant_id=$TENANT&limit=500" \
          | jq -r --arg e "$SONDA" '.[] | select(.email==$e) | .id' | head -1; }
limpa() {
  curl -s -o /dev/null -X DELETE "${HS[@]}" "$CFG/config/speech_profiles/$PERFIL?tenant_id=$TENANT"
  for k in stt_model stt_language tts_model tts_voice; do
    curl -s -o /dev/null -X DELETE "${HS[@]}" "$CFG/config/webrtc/$k?tenant_id=$TENANT"
  done
  i=$(uid); [ -n "$i" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users/$i"
}
trap limpa EXIT INT TERM
limpa
curl -s -o /dev/null -X POST "$AUTH/auth/users" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$SONDA\",\"name\":\"Probe VOZ-17\",\"password\":\"$SENHA\",\"roles\":[\"supervisor\"]}"
T_SONDA=$(curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$SONDA\",\"password\":\"$SENHA\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$T_SONDA" ] || { incon "usuario-sonda nao criado — o portao nao pode ser medido"; fim; }

# ── K0 ──
docker inspect -f '{{.State.Running}}' "$GW" 2>/dev/null | grep -qx true || { incon "K0 channel-gateway fora do ar"; fim; }
docker exec "$GW" grep -c 'async def speech_defaults_put' \
  /app/packages/channel-gateway/src/plughub_channel_gateway/main.py >/dev/null 2>&1 \
  || { incon "K0 a IMAGEM do gateway nao tem as rotas da VOZ-17 — build/up antes de medir"; fim; }

perfil_no_store() { curl -s "$CFG/config/speech_profiles?tenant_id=$TENANT" | jq -r ".entries.$PERFIL // \"<ausente>\"" | tr -d '\n'; }
ns_webrtc() { curl -s "$CFG/config/webrtc?tenant_id=$TENANT" | jq -r ".entries.$1 // \"<ausente>\"" | tr -d '\n'; }
escopo() { curl -s "$CFG/config/webrtc/_provenance?tenant_id=$TENANT" | jq -r ".keys.$1.effective_scope // \"<sem>\""; }

# põe um perfil pela ROTA; imprime o http_code
rota_put() { curl -s -o /tmp/voz17_body -w '%{http_code}' -X PUT "${HS[@]}" "$GW_URL/v1/speech-profiles/$PERFIL" -d "$1"; }
motivo()   { jq -r '.detail.reasons // .detail // empty' /tmp/voz17_body 2>/dev/null | tr '\n' ' ' | cut -c1-160; }

# ── A1 · o catálogo ──
CAT=$(curl -s -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" "$GW_URL/v1/speech-models")
N_STT=$(printf '%s' "$CAT" | jq -r '.stt | length' 2>/dev/null)
N_TTS=$(printf '%s' "$CAT" | jq -r '.tts | length' 2>/dev/null)
if [ "${N_STT:-0}" -lt 1 ] || [ "${N_TTS:-0}" -lt 1 ]; then
  incon "A1 catalogo vazio ou ilegivel ($(printf '%s' "$CAT" | cut -c1-160)) — SEM AMOSTRA, nada abaixo prova nada"
  fim
fi
STT_OK=$(printf '%s' "$CAT" | jq -r '.stt[0].id')
TTS_OK=$(printf '%s' "$CAT" | jq -r '.tts[0].id')
VOZ_OK=$(printf '%s' "$CAT" | jq -r '.tts[0].voices[0].name // empty')
VOZ_DE_OUTRO=$(printf '%s' "$CAT" | jq -r '[.tts[1].voices[0].name // empty] | .[0] // empty')
ok "A1 catalogo: $N_STT modelo(s) de STT e $N_TTS de TTS instalados (stt=$STT_OK tts=$TTS_OK voz=$VOZ_OK)"

# ── A2 · portão ──
AN=$(curl -s -o /dev/null -w '%{http_code}' "$GW_URL/v1/speech-models")
SO=$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Type: application/json" \
      -H "Authorization: Bearer $T_SONDA" "$GW_URL/v1/speech-profiles/$PERFIL" -d "{\"stt_model\":\"$STT_OK\"}")
DEPOIS=$(perfil_no_store)
if [ "$AN" = 401 ] && [ "$SO" = 403 ] && [ "$DEPOIS" = "<ausente>" ]; then
  ok "A2 anonimo 401 e sonda sem config.channels em escrita 403 — e nada foi gravado"
else
  falha "A2 portao: anonimo=$AN (esperado 401) sonda=$SO (esperado 403) perfil apos as tentativas=[$DEPOIS]"
fi

# ── B1 · controle positivo ──
ST=$(rota_put "{\"description\":\"probe VOZ-17\",\"stt_model\":\"$STT_OK\",\"tts_model\":\"$TTS_OK\",\"tts_voice\":\"$VOZ_OK\",\"stt_end_silence_ms\":1500}")
GRAVADO=$(perfil_no_store)
if [ "$ST" = 200 ] && [ "$(printf '%s' "$GRAVADO" | jq -r '.stt_model' 2>/dev/null)" = "$STT_OK" ]; then
  ok "B1 CONTROLE POSITIVO: perfil que o servico serve foi gravado (http 200, store=$STT_OK)"
else
  falha "B1 a porta recusou config BOA (http $ST, motivo: $(motivo)) — store=[$GRAVADO]"
fi

recusa() {  # $1 rótulo · $2 corpo · $3 pedaço esperado no motivo
  local st; st=$(rota_put "$2"); local m; m=$(motivo)
  local agora; agora=$(perfil_no_store)
  if [ "$st" = 422 ] && printf '%s' "$m" | grep -qi "$3" && [ "$(printf '%s' "$agora" | jq -r '.stt_model' 2>/dev/null)" = "$STT_OK" ]; then
    ok "$1 recusado com 422 nomeando o motivo, e o perfil anterior segue intacto — $m"
  else
    falha "$1 http $st (esperado 422 citando '$3'), motivo=[$m], perfil agora=[$agora]"
  fi
}

recusa "B2" "{\"stt_model\":\"Systran/faster-whisper-medium\"}" "nao esta instalado"
recusa "B3" "{\"stt_model\":\"$TTS_OK\"}" "tarefa"
if [ -n "$VOZ_DE_OUTRO" ]; then
  recusa "B4" "{\"tts_model\":\"$TTS_OK\",\"tts_voice\":\"$VOZ_DE_OUTRO\"}" "nao e voz"
else
  incon "B4 o servico so tem um modelo de TTS com vozes — sem par cruzado para medir"
fi
recusa "B5" "{\"stt_modelo\":\"$STT_OK\"}" "nao e campo"

# ── C1 · a porta antiga continua aberta, e isso é decisão ──
C1=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${HS[@]}" "$CFG/config/speech_profiles/${PERFIL}_cru" \
      -d "{\"value\":{\"stt_model\":\"Systran/faster-whisper-medium\"},\"tenant_id\":\"$TENANT\"}")
curl -s -o /dev/null -X DELETE "${HS[@]}" "$CFG/config/speech_profiles/${PERFIL}_cru?tenant_id=$TENANT"
if [ "$C1" = 200 ] || [ "$C1" = 201 ]; then
  ok "C1 a porta CRUA do config-api aceitou o mesmo perfil que a rota recusou (http $C1) — decisao, nomeada em VOZ-29"
else
  incon "C1 a porta crua respondeu $C1 — se ela passou a recusar, esta linha e o VOZ-29 estao velhos"
fi

# ── D1/D2 · o default do tenant ──
def_put() { curl -s -o /tmp/voz17_body -w '%{http_code}' -X PUT "${HS[@]}" "$GW_URL/v1/speech-defaults" -d "$1"; }
D1=$(def_put "{\"tts_voice\":\"nao_existe_essa_voz\"}")
if [ "$D1" = 422 ] && [ "$(ns_webrtc tts_voice)" = "<ausente>" ]; then
  ok "D1 default com voz que o modelo nao tem: 422 e o namespace webrtc intacto — $(motivo)"
else
  falha "D1 http $D1 (esperado 422) e webrtc.tts_voice=[$(ns_webrtc tts_voice)]"
fi

D2=$(def_put "{\"stt_model\":\"$STT_OK\",\"tts_voice\":\"$VOZ_OK\"}")
V=$(ns_webrtc tts_voice); E=$(escopo tts_voice)
if [ "$D2" = 200 ] && [ "$V" = "$VOZ_OK" ] && [ "$E" = tenant ]; then
  ok "D2 default gravado com escopo de TENANT (tts_voice=$V, escopo=$E)"
else
  falha "D2 http $D2 tts_voice=[$V] escopo=[$E] — esperado 200, $VOZ_OK, tenant"
fi
# o que a TELA lê: as três camadas e a procedência de cada campo
DEF=$(curl -s -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" "$GW_URL/v1/speech-defaults")
P_TTS=$(printf '%s' "$DEF" | jq -r '.provenance.tts_voice // "<sem>"')
P_MOD=$(printf '%s' "$DEF" | jq -r '.provenance.tts_model // "<sem>"')
EF=$(printf '%s' "$DEF" | jq -r '.effective.tts_voice // "<sem>"')
ANON_DEF=$(curl -s -o /dev/null -w '%{http_code}' "$GW_URL/v1/speech-defaults")
if [ "$P_TTS" = tenant ] && [ "$P_MOD" = env ] && [ "$EF" = "$VOZ_OK" ] && [ "$ANON_DEF" = 401 ]; then
  ok "D2b a leitura das tres camadas separa o que o tenant escolheu (tts_voice=$P_TTS) do que segue no env (tts_model=$P_MOD), e exige credencial"
else
  falha "D2b procedencia tts_voice=[$P_TTS] (esperado tenant) tts_model=[$P_MOD] (esperado env) efetivo=[$EF] anonimo=[$ANON_DEF]"
fi

D3=$(def_put '{"tts_voice":""}')
if [ "$D3" = 200 ] && [ "$(ns_webrtc tts_voice)" = "<ausente>" ]; then
  ok "D3 campo vazio REMOVEU o override do tenant — a chamada volta ao env do gateway"
else
  falha "D3 http $D3 e webrtc.tts_voice=[$(ns_webrtc tts_voice)] — o vazio nao removeu"
fi

# ── E1 · apagar pela rota ──
E1=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "${HS[@]}" "$GW_URL/v1/speech-profiles/$PERFIL")
if [ "$E1" = 204 ] && [ "$(perfil_no_store)" = "<ausente>" ]; then
  ok "E1 a rota apagou o perfil (204) e ele sumiu do config-api"
else
  falha "E1 http $E1 e o perfil no store=[$(perfil_no_store)]"
fi

fim
