#!/usr/bin/env bash
# mut_sip_edge_surface — o probe da borda SIP (VOZ-32) reprova o que ele diz vigiar?
#
# A borda do demo nasce FECHADA, e com ela fechada os ramos A, B e K não têm população: não há
# porta publicada para classificar nem senha exposta para recusar. Por isso esta bateria ABRE a
# borda — em 127.0.0.1, nunca na rede local — planta cada defeito e exige VERMELHO:
#
#   M0  controle: publicada (5060 + RTP), use_external_ip=true, senha FORA do repositório → VERDE
#   M1  publicada com use_external_ip=false (SDP com o IP do container)            → ramo B
#   M2  uma porta a mais publicada (5062/udp), fora da classificação               → ramo A
#   M3  publicada com a senha que está no REPOSITÓRIO                              → ramo K
#   M4  um tronco SEM autenticação no SFU (o que o seed passou a recusar)          → ramo C
#
# Não medido aqui, e por quê: N e Y (senha errada recusada · senha em vigor passa) são os
# controles do próprio probe e rodam em todo M; um serviço que aceitasse tudo reprova o N de M0.
#
# Mexe no livekit-sip e no sip-seed (recria os dois) e no tronco de demo (recria com outra senha).
# O `trap` devolve a borda FECHADA com a senha do compose. Não rode junto com o probe_voz02.
# Saída: 0 = todas pegas · 1 = alguma sobreviveu · 2 = não mediu.
set -uo pipefail
cd "$(dirname "$0")/../.."

PROBE=infra/test/probe_sip_edge_surface.sh
TMP=$(mktemp --suffix=.yml)
SENHA_FORTE="mut-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
REPO_SENHA=$(sed -n 's/^ *SIP_TRUNK_PASSWORD_DEMO: *\([^ $#][^ #]*\).*/\1/p' docker-compose.demo.yml | head -1)
GW=plughub-demo-channel-gateway-1
[ -n "$REPO_SENHA" ] || { echo "INCONCLUSIVO: senha de demo não achada no compose"; exit 2; }

env_de() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p" | head -1; }
ajudante() {
  docker run --rm --network plughub-demo_plughub-demo --entrypoint python -v "$PWD/infra/test:/t:ro" -w /t \
    -e "LK_URL=$(env_de $GW PLUGHUB_WEBRTC_LIVEKIT_URL)" -e "LK_KEY=$(env_de $GW PLUGHUB_WEBRTC_LIVEKIT_API_KEY)" \
    -e "LK_SECRET=$(env_de $GW PLUGHUB_WEBRTC_LIVEKIT_API_SECRET)" \
    "$(docker inspect -f '{{.Image}}' $GW)" /t/_sip_edge_check.py "$@" 2>&1
}

borda() {  # $1 use_external_ip · $2 senha do tronco de demo · $3 porta extra (opcional)
  { echo "services:"
    echo "  livekit-sip:"
    echo "    ports:"
    echo "      - \"127.0.0.1:5060:5060/udp\""
    echo "      - \"127.0.0.1:10000-10100:10000-10100/udp\""
    [ -n "${3:-}" ] && echo "      - \"127.0.0.1:$3:$3/udp\""
    echo "  sip-seed:"
    echo "    environment:"
    echo "      SIP_TRUNK_PASSWORD_DEMO: \"$2\""
    echo "      SIP_SEED_RECONCILE: \"true\""
  } > "$TMP"
  SIP_USE_EXTERNAL_IP="$1" docker compose -f docker-compose.demo.yml -f "$TMP" \
    up -d --no-deps --force-recreate livekit-sip sip-seed >/dev/null 2>&1
  espera_seed
}
espera_seed() {
  for _ in $(seq 1 60); do
    st=$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' plughub-demo-sip-seed-1 2>/dev/null)
    [ "$st" = "exited 0" ] && { sleep 2; return 0; }
    sleep 2
  done
  echo "   (sip-seed não terminou verde: $st)"; return 1
}
fecha() {
  ajudante rm mut-open-trunk >/dev/null
  SIP_SEED_RECONCILE=true docker compose -f docker-compose.demo.yml \
    up -d --no-deps --force-recreate livekit-sip sip-seed >/dev/null 2>&1
  espera_seed >/dev/null
  rm -f "$TMP"
  echo "   borda devolvida FECHADA ($(docker port plughub-demo-livekit-sip-1 | wc -l) porta(s) publicada(s))"
}
trap fecha EXIT INT TERM
roda() { bash "$PROBE" > /tmp/mut_sip_edge.out 2>&1; echo $?; }

SOBREVIVEU=0
espera_vermelho() {  # $1 nome · $2 ramo esperado
  local r; r=$(roda)
  if [ "$r" = 1 ] && grep -q "FALHA   $2 " /tmp/mut_sip_edge.out; then echo "$1: pega (ramo $2)"
  else echo "$1: SOBREVIVEU (rc=$r)"; grep -E 'FALHA|INCONCL' /tmp/mut_sip_edge.out | sed 's/^/     /'; SOBREVIVEU=1; fi
}

borda true "$SENHA_FORTE" || { echo "INCONCLUSIVO: não abri a borda de controle"; exit 2; }
r=$(roda)
if [ "$r" != 0 ]; then
  echo "M0 controle: probe não está verde com a borda certa (rc=$r) — mutações não provariam nada"
  sed 's/^/     /' /tmp/mut_sip_edge.out; exit 2
fi
echo "M0 controle: verde (publicada em 127.0.0.1, senha fora do repositório)"

borda false "$SENHA_FORTE";            espera_vermelho "M1 sem endereço externo" B
borda true  "$SENHA_FORTE" 5062;       espera_vermelho "M2 porta fora da classificação" A
borda true  "$REPO_SENHA";             espera_vermelho "M3 senha do repositório" K
borda true  "$SENHA_FORTE"
ajudante mk-open mut-open-trunk +551140000099 >/dev/null
espera_vermelho "M4 tronco sem autenticação" C

[ "$SOBREVIVEU" = 0 ] && { echo "todas pegas"; exit 0; }
exit 1
