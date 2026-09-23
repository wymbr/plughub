#!/usr/bin/env bash
# probe_sip_edge_surface.sh — 2026-09-23 (VOZ-32, ADR adr-voice-media-plane V10)
#
# PERGUNTA: o que o serviço SIP publica no host é SÓ o que está classificado, e — se está
# publicado — quem liga para dentro precisa de uma senha que NÃO está no repositório?
#
# O ESTADO QUE O ORIGINOU: a allowlist de borda (`probe_edge_surface.sh`) só conhece prefixo HTTP;
# SIP ficava fora de toda tabela. E duas travas que pareciam existir não existiam: o seed aceitava
# tronco SEM autenticação (só logava `auth=NENHUMA`), e a senha do tronco de demo está escrita no
# compose — com a porta aberta, quem tem o repositório liga para dentro. Lista de IPs também não
# serve de trava aqui: o Docker Desktop reescreve a origem de todo pacote que entra por porta
# publicada (medido em 2026-09-23: 172.17.0.1 vindo da LAN e do localhost).
#
# A borda é OPT-IN: `PLUGHUB_SIP_EDGE=true` no `.env.demo` faz o `up.sh` incluir a camada
# `docker-compose.sip-edge.yml`. Fechada é estado VÁLIDO (verde); o probe julga as duas.
#
# CLASSIFICAÇÃO DECLARADA (editar é decisão; porta fora dela reprova):
#   5060/udp            sinalização SIP do provedor — autenticada por DIGEST
#   <rtp_port>/udp      mídia RTP; a faixa vem do `rtp_port` do próprio serviço (uma casa só)
#   nada mais: sem TCP, sem TLS/5061 (TLS/SRTP é decisão do ADR §8 ainda não tomada)
#
# RAMOS (a população é conferida ANTES dos veredictos que dependem dela):
#   P  população: o serviço SIP responde (INVITE com senha errada recebe resposta final) e há tronco
#   A  porta publicada fora da classificação reprova; faixa RTP publicada pela metade reprova
#   B  publicada ⇔ `use_external_ip: true` — porta aberta com endereço interno no SDP é chamada muda
#   C  todo tronco de ENTRADA no SFU tem autenticação
#   N  CONTROLE NEGATIVO: senha errada é recusada (401/403/407) em cada tronco
#   Y  CONTROLE POSITIVO: a senha EM VIGOR passa da autenticação (senão N passaria por tudo falhar)
#   K  com a borda publicada, a senha que está no REPOSITÓRIO é recusada — no valor e ao vivo
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO
set -uo pipefail
cd "$(dirname "$0")/../.."

SIPC="${SIP_CONTAINER:-plughub-demo-livekit-sip-1}"
SEEDC="${SEED_CONTAINER:-plughub-demo-sip-seed-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
FALHA=0; INCONCL=0
ok()      { echo "  OK      $*"; }
falha()   { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
inconcl() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
info()    { echo "  INFO    $*"; }

echo "══ borda SIP — classificação (VOZ-32) ══"
command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente (rode de dentro do WSL)"; exit 2; }
docker inspect "$SIPC" >/dev/null 2>&1 || { echo "INCONCLUSIVO: $SIPC não existe"; exit 2; }
docker inspect "$SEEDC" >/dev/null 2>&1 || { echo "INCONCLUSIVO: $SEEDC não existe (o seed nunca rodou)"; exit 2; }

env_de() {  # $1 container · $2 variável → valor, ou vazio
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p" | head -1
}
CFG=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$SIPC" \
      | awk '/^SIP_CONFIG_BODY=/{f=1; sub(/^SIP_CONFIG_BODY=/,"")} f')
RTP=$(printf '%s\n' "$CFG" | sed -n 's/^ *rtp_port: *\([0-9]*-[0-9]*\).*/\1/p' | head -1)
EXT=$(printf '%s\n' "$CFG" | sed -n 's/^ *use_external_ip: *\([a-z]*\).*/\1/p' | head -1)
[ -n "$RTP" ] || { echo "INCONCLUSIVO: rtp_port não achado no SIP_CONFIG_BODY do $SIPC"; exit 2; }
RTP_LO=${RTP%-*}; RTP_HI=${RTP#*-}

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
LKENV=(-e "LK_URL=$(env_de "$GW" PLUGHUB_WEBRTC_LIVEKIT_URL)"
       -e "LK_KEY=$(env_de "$GW" PLUGHUB_WEBRTC_LIVEKIT_API_KEY)"
       -e "LK_SECRET=$(env_de "$GW" PLUGHUB_WEBRTC_LIVEKIT_API_SECRET)")
ajudante() {
  timeout 90 docker run --rm -i --network "$NET" --entrypoint python -v "$PWD/infra/test:/t:ro" -w /t \
    "${LKENV[@]}" "$IMG" /t/_sip_edge_check.py "$@" 2>&1
}
final_de() { printf '%s\n' "$1" | sed -n 's/^FINAL \([0-9a-z]*\).*/\1/p' | head -1; }
e_auth() { case "$1" in 401|403|407) return 0 ;; *) return 1 ;; esac; }

# ── P · população ─────────────────────────────────────────────────────────────
TR=$(ajudante trunks)
TOTAL=$(printf '%s\n' "$TR" | sed -n 's/^TOTAL //p')
if [ -z "$TOTAL" ]; then
  echo "INCONCLUSIVO: não listei os troncos do SFU:"; printf '%s\n' "$TR" | tail -5 | sed 's/^/    /'; exit 2
fi
[ "$TOTAL" -gt 0 ] || { echo "INCONCLUSIVO: nenhum tronco de entrada no SFU (o sip-seed rodou?)"; exit 2; }
info "P $TOTAL tronco(s) no SFU · rtp_port=$RTP · use_external_ip=${EXT:-<ausente>}"

# Troncos DECLARADOS (arquivos), com usuário, número e o nome da env da senha.
mapfile -t ARQS < <(ls infra/sip/*.json 2>/dev/null)
[ "${#ARQS[@]}" -gt 0 ] || { echo "INCONCLUSIVO: nenhum infra/sip/*.json"; exit 2; }
PRIMEIRO=$(jq -r '.trunk.numbers[0] // empty' "${ARQS[0]}")
OUT=$(ajudante invite "$PRIMEIRO" "$(jq -r .trunk.auth_username "${ARQS[0]}")" "errada-$RANDOM$RANDOM")
F=$(final_de "$OUT")
if [ -z "$F" ] || [ "$F" = none ]; then
  echo "INCONCLUSIVO: o serviço SIP não deu resposta final a um INVITE ($PRIMEIRO) — fora do ar,"
  echo "   ou o número não está em tronco nenhum (o serviço descarta calado)."; exit 2
fi

# ── A · portas publicadas × classificação ─────────────────────────────────────
PUB=$(docker port "$SIPC" 2>/dev/null | awk '{print $1}' | sort -u)
if [ -z "$PUB" ]; then
  PUBLICADA=false
  ok "A nenhuma porta publicada — borda SIP FECHADA (estado válido; só a rede do compose liga)"
else
  PUBLICADA=true
  FORA=""; NRTP=0
  while read -r pp; do
    p=${pp%/*}; proto=${pp#*/}
    if [ "$proto" = udp ] && [ "$p" = 5060 ]; then continue; fi
    if [ "$proto" = udp ] && [ "$p" -ge "$RTP_LO" ] && [ "$p" -le "$RTP_HI" ]; then NRTP=$((NRTP + 1)); continue; fi
    FORA="$FORA $pp"
  done <<< "$PUB"
  ESPERADO=$((RTP_HI - RTP_LO + 1))
  if [ -n "$FORA" ]; then falha "A porta publicada FORA da classificação:$FORA"
  elif ! printf '%s\n' "$PUB" | grep -qx '5060/udp'; then falha "A RTP publicado sem 5060/udp — borda sem sinalização"
  elif [ "$NRTP" -ne "$ESPERADO" ]; then falha "A faixa RTP publicada pela metade: $NRTP de $ESPERADO portas de $RTP"
  else ok "A publicado exatamente 5060/udp + $RTP/udp ($ESPERADO portas)"; fi
fi

# ── B · publicada ⇔ endereço externo ──────────────────────────────────────────
if [ "$PUBLICADA" = true ] && [ "$EXT" != true ]; then
  falha "B porta publicada com use_external_ip=${EXT:-<ausente>}: o SDP anuncia o IP do container e o áudio não volta"
elif [ "$PUBLICADA" = false ] && [ "$EXT" = true ]; then
  falha "B use_external_ip=true sem porta publicada — as duas metades da borda divergiram (a chave é uma só: PLUGHUB_SIP_EDGE)"
else ok "B publicação e endereço externo concordam (publicada=$PUBLICADA, use_external_ip=${EXT:-false})"; fi

# ── C · todo tronco tem autenticação ─────────────────────────────────────────
SEM=$(printf '%s\n' "$TR" | grep '^TRUNK .* auth=nao' || true)
if [ -n "$SEM" ]; then falha "C tronco SEM autenticação no SFU:"; printf '%s\n' "$SEM" | sed 's/^/          /'
else ok "C os $TOTAL tronco(s) no SFU exigem digest"; fi

# ── N · Y · K — por tronco declarado ─────────────────────────────────────────
for a in "${ARQS[@]}"; do
  nome=$(jq -r .trunk.name "$a"); dnis=$(jq -r '.trunk.numbers[0] // empty' "$a")
  user=$(jq -r .trunk.auth_username "$a"); penv=$(jq -r .trunk.auth_password_env "$a")
  if [ -z "$dnis" ]; then inconcl "N/Y/K $nome sem número declarado — não há como ligar para ele"; continue; fi
  vigor=$(env_de "$SEEDC" "$penv")
  repo=$(sed -n "s/^ *$penv: *\([^ \$#][^ #]*\).*/\1/p" docker-compose.demo.yml | head -1)

  # N — controle negativo
  F=$(final_de "$(ajudante invite "$dnis" "$user" "errada-$RANDOM$RANDOM")")
  if e_auth "$F"; then ok "N $nome: senha errada recusada ($F)"
  else falha "N $nome: senha ERRADA não foi recusada na autenticação (final=$F)"; fi

  # Y — controle positivo (sem ele, N passaria com o serviço recusando tudo)
  if [ -z "$vigor" ]; then inconcl "Y $nome: $penv vazio no $SEEDC — não sei a senha em vigor"
  else
    F=$(final_de "$(ajudante invite "$dnis" "$user" "$vigor")")
    if [ "$F" = none ] || [ -z "$F" ]; then inconcl "Y $nome: sem resposta final com a senha em vigor"
    elif e_auth "$F"; then falha "Y $nome: a senha EM VIGOR foi recusada ($F) — o tronco no SFU não é o do arquivo?"
    else ok "Y $nome: a senha em vigor passa da autenticação (final=$F)"; fi
  fi

  # K — senha do repositório com a borda publicada
  if [ -z "$repo" ]; then ok "K $nome: a senha não está no repositório ($penv só no env)"; continue; fi
  if [ "$PUBLICADA" = false ]; then info "K $nome: senha do repositório em uso, borda FECHADA — aceitável só assim"; continue; fi
  if [ "$vigor" = "$repo" ]; then falha "K $nome: borda PUBLICADA com a senha que está no repositório ($penv)"; continue; fi
  F=$(final_de "$(ajudante invite "$dnis" "$user" "$repo")")
  if e_auth "$F"; then ok "K $nome: a senha do repositório é recusada ao vivo ($F)"
  else falha "K $nome: a senha do repositório PASSA com a borda publicada (final=$F)"; fi
done

echo "══════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo "VERDE"; exit 0
