#!/usr/bin/env bash
# gate_webhook_registry_resolution.sh — Fases C/D/E do ADR
#                                       adr-webhook-endpoint-single-registry
#
# A PERGUNTA: `/v1/channels/webhook/{identifier}` resolve pelo REGISTRO, e SÓ por
# ele — recusando o que não tem linha, sem confundir "não existe" com "não
# consegui perguntar"?
#
# ─── O QUE ESTE GATE PROVA, E POR QUE PRECISA DE TRÊS METADES ─────────────────
#
# Um gate que só dispara um endereço REGISTRADO e vê "via REGISTRO" não pode
# reprovar: se o código logasse "via REGISTRO" incondicionalmente, continuaria
# verde. Por isso há um controle para cada afirmação:
#
#   POSITIVO  endereço COM linha       → resolve pelo REGISTRO, HTTP 201
#   NEGATIVO  endereço SEM linha       → HTTP **404** nomeado (Fase E)
#   EXTERNA   endereço interno na slug → HTTP 404 + recusa por ORIGEM (§7.6.3)
#
# ⚠️ **A metade NEGATIVA inverteu na Fase E** (2026-08-07). Até a Fase D ela
# esperava **201**, porque o fallback por `webhook_skill_id` ainda atendia e a D7
# exigia que ele saísse por ÚLTIMO. Agora ela espera **404**: o registro é o único
# resolvedor. A inversão é o registro de que a fase aconteceu — um gate cujo
# resultado esperado nunca muda quando o comportamento muda não estava medindo o
# comportamento.
#
# ─── POR QUE O DISPARO POSITIVO É `skill_formfill_demo_v1`, E NÃO OS 10 ───────
# Disparar os dez endereços internos NÃO é mais cobertura, é dano: entre eles há
# `skill_deploy_promote_v1` (promove um pool de verdade) e os dispatchers de
# outbound (drenam campanha e contatam gente). Um gate não pode ter efeito
# colateral de produção. `formfill_demo` suspende esperando preenchimento no
# Console — é o mesmo alvo que `smoke_formfill_renderer.sh` já exercita.
# A cobertura dos outros nove é do `probe_webhook_endpoint_inventory.sh`
# (existência da linha); o que ESTE gate acrescenta é que o gateway a CONSULTA.
#
# ─── O QUE ESTE GATE **NÃO** MEDE POR PADRÃO ──────────────────────────────────
# O ramo `unavailable` → **503** (registro inalcançável). Exercê-lo exige derrubar
# o agent-registry, o que um gate de rotina não deve fazer sozinho. Fica opt-in:
#
#     GATE_TEST_UNAVAILABLE=1 bash infra/test/gate_webhook_registry_resolution.sh
#
# Sem a flag o gate **declara** que não mediu, em vez de omitir — um ramo não
# exercido que aparece como verde é a forma mais barata de comprar confiança sem
# dar nada. E é o ramo que mais importa depois da Fase E: se ele responder 404, um
# soluço de rede do registry vira "endereço não existe" para todo disparo interno.
#
# ─── PREVISÕES (registradas antes de rodar) ───────────────────────────────────
#   P1  POSITIVO  → HTTP 201 · log "via REGISTRO" · pool=formfill_demo_ia
#   P2  NEGATIVO  → HTTP **404** · log "SEM linha no registro"   (era 201 até a D)
#   P3  EXTERNA   → HTTP 404 · log de recusa por procedência
#   P4  (opt-in)  registry parado → HTTP **503**, nunca 404
#
# Uso:  bash infra/test/gate_webhook_registry_resolution.sh [tenant]
# Pré:  channel-gateway (8010), agent-registry (3300), routing-engine.
# Saída: 0 = passou · 1 = mediu e reprovou · 2 = INCONCLUSIVO (não mediu).

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
GW="http://localhost:8010"

REGISTERED="skill_formfill_demo_v1"          # tem linha (Fase B) → resolve pelo registro
EXPECTED_POOL="formfill_demo_ia"
UNREGISTERED="skill_gate_c_controle_negativo" # sem linha → 404 nomeado (Fase E)

# Cresceu além da Fase C: mede a resolução por registro (C) e a recusa por
# procedência na porta externa (§7.6.3, achado na Fase D). Mesmo cuidado que o
# título do probe — nome de fase congelado vira pista falsa depois de duas fases.
echo "══ webhook · resolução por registro + filtro de procedência — tenant=$TENANT ══"
echo

# ── PREFLIGHT — com ESPERA, não só com recusa ────────────────────────────────
#
# Sem o preflight, "0 linhas de fallback" seria obtido também por um gateway fora
# do ar: ausência de log é indistinguível de ausência de serviço.
#
# A ESPERA foi acrescentada depois de o gate sair INCONCLUSIVO três vezes pelo
# mesmo motivo — rodado logo após `docker compose up -d`, que retorna quando o
# container foi CRIADO, não quando o serviço está atendendo. Recusar dar veredicto
# ali estava certo, mas era resposta incompleta: o gate depende de uma condição
# que ele mesmo pode aguardar. Um INCONCLUSIVO que 20 s de espera evitariam é
# atrito, não informação — e atrito treina a pessoa a reexecutar no reflexo, que é
# como um INCONCLUSIVO legítimo (serviço realmente quebrado) acaba ignorado.
#
# O teto existe para que a espera não vire mascaramento: passado ele, o serviço
# está de fato fora, e aí a recusa é a resposta certa.
GW_WAIT_S="${GW_WAIT_S:-60}"
GW_CODE=""
for _i in $(seq 1 "$GW_WAIT_S"); do
  GW_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$GW/health" 2>/dev/null)
  case "$GW_CODE" in
    2*) break ;;
  esac
  [ "$_i" = "1" ] && printf '   aguardando channel-gateway atender'
  printf '.'
  sleep 1
done
[ -n "${_i:-}" ] && [ "${_i:-1}" != "1" ] && echo " (${_i}s)"

case "$GW_CODE" in
  2*) : ;;
  000) echo "⚠️  INCONCLUSIVO — channel-gateway(8010) SEM CONEXÃO após ${GW_WAIT_S}s."; exit 2 ;;
  *)   echo "⚠️  INCONCLUSIVO — channel-gateway /health devolveu HTTP $GW_CODE após ${GW_WAIT_S}s."; exit 2 ;;
esac

# A linha do endereço positivo TEM de existir — senão o teste positivo mediria a
# ausência dela, não a resolução, e reprovaria pelo motivo errado.
HAS_ROW=$(curl -sf -m 10 \
  "http://localhost:3300/v1/channel-endpoints?channel=webhook&identifier=$REGISTERED" \
  -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // []) | length' 2>/dev/null)
if [ "${HAS_ROW:-0}" != "1" ]; then
  echo "⚠️  INCONCLUSIVO — o endereço positivo '$REGISTERED' não tem linha no registro"
  echo "    (encontrado: ${HAS_ROW:-erro}). Rode a Fase B antes: o gate mediria a"
  echo "    ausência da linha, não a troca de resolução."
  exit 2
fi

# O controle negativo precisa MESMO não existir, senão ele deixa de ser controle.
HAS_NEG=$(curl -sf -m 10 \
  "http://localhost:3300/v1/channel-endpoints?channel=webhook&identifier=$UNREGISTERED" \
  -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // []) | length' 2>/dev/null)
if [ "${HAS_NEG:-0}" != "0" ]; then
  echo "⚠️  INCONCLUSIVO — o controle negativo '$UNREGISTERED' TEM linha no registro."
  echo "    Ele resolveria pelo registro e o controle viraria um segundo positivo."
  exit 2
fi

# ── Contagem por DELTA (antes × depois), não por janela de log ───────────────
#
# ⚠️ DUAS tentativas de janela falharam antes desta, e as duas do mesmo jeito:
# contando execuções anteriores como se fossem esta.
#
#   v1  `--since "${elapsed}s"` (duração) — duas execuções seguidas ficam a
#       segundos uma da outra, e a duração da 2ª alcançava o log da 1ª.
#   v2  `--since "$T0_ISO"` (instante absoluto) — continuou contando a linha do
#       gate de AUTH, que dispara o MESMO `skill_formfill_demo_v1` como controle
#       de não-regressão: 2 onde havia 1.
#
# ⚠️ **A causa da v2 NÃO foi diagnosticada.** A hipótese registrada aqui antes era
# "`docker compose logs` ignora o timestamp" — e ela foi **REFUTADA na medição**:
# `--since` com um instante de agora devolve 0 linhas, logo o filtro É honrado.
# Continua em aberto por que a linha vizinha entrou na janela (candidatos vivos:
# granularidade de segundo, fuso na comparação, buffering do log do container).
#
# A correção não é uma 3ª tentativa de janela — é **parar de depender de recorte
# temporal**, o que dispensa a resposta. Contar ANTES, disparar, contar DEPOIS,
# afirmar sobre a DIFERENÇA. Imune à semântica de `--since`, e continua
# diagnóstico: se o serviço passar a logar duas vezes de verdade, o delta acusa.
#
# *Nota de método: a v2 nasceu de uma explicação plausível que eu não verifiquei, e
# "passou a funcionar" não é "eu entendi". Quando existe um conserto que não depende
# do diagnóstico, ele é preferível — mas o diagnóstico não deve ser dado por feito.*
#
# `--tail` limita o custo de ler o log inteiro. Precisa ser MAIOR que o volume
# gerado entre as duas leituras, senão a base desliza e o delta mente — 5000 é
# ordem de grandeza acima dos poucos disparos deste gate.
TAIL=5000
count_gw() { $DC logs --tail "$TAIL" channel-gateway 2>/dev/null | grep -c "$1"; }
count_rt() { $DC logs --tail "$TAIL" routing-engine   2>/dev/null | grep -c "$1"; }

_PAT_POS_REG="identifier=$REGISTERED → pool=$EXPECTED_POOL (via REGISTRO"
_PAT_NEG_REF="identifier=$UNREGISTERED SEM linha no registro"
_PAT_EXT_REF="webhook externo: '$REGISTERED' EXISTE mas é de procedência interna"
_PAT_GW_FB="FASE C · FALLBACK webhook"
_PAT_RT_FB="FASE C · FALLBACK ROTEOU"

B_POS_REG=$(count_gw "$_PAT_POS_REG")
B_NEG_REF=$(count_gw "$_PAT_NEG_REF")
B_EXT_REF=$(count_gw "$_PAT_EXT_REF")
B_GW_FB=$(  count_gw "$_PAT_GW_FB")
B_RT_FB=$(  count_rt "$_PAT_RT_FB")

sleep 1   # separa do preflight

# ── POSITIVO ─────────────────────────────────────────────────────────────────
echo "── POSITIVO · $REGISTERED (tem linha) ───────────────────────────────────"
POS_CODE=$(curl -s -o /tmp/_gc_pos.json -w '%{http_code}' -m 20 \
  -X POST "$GW/v1/channels/webhook/$REGISTERED" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"api\"}" 2>/dev/null)
POS_SID=$(jq -r '.session_id // empty' /tmp/_gc_pos.json 2>/dev/null)
echo "   HTTP $POS_CODE   session=${POS_SID:-«nenhuma»}"

# ── NEGATIVO ─────────────────────────────────────────────────────────────────
# Fase E: espera 404. Até a Fase D esperava 201 (o fallback atendia).
echo "── NEGATIVO · $UNREGISTERED (sem linha — deve recusar) ──────────────────"
NEG_CODE=$(curl -s -o /tmp/_gc_neg.json -w '%{http_code}' -m 20 \
  -X POST "$GW/v1/channels/webhook/$UNREGISTERED" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"api\"}" 2>/dev/null)
NEG_SID=$(jq -r '.session_id // empty' /tmp/_gc_neg.json 2>/dev/null)
echo "   HTTP $NEG_CODE   session=${NEG_SID:-«nenhuma»}"

# ── PORTA EXTERNA · recusa por procedência (ADR §7.6.3) ──────────────────────
#
# A Fase B semeou os endereços internos, e como `/channel/webhook/{slug}` sempre
# resolveu pelo registro, os dez passaram a responder ali também (404 → 201,
# medido em 2026-08-07). Filtramos: a porta externa serve só `origin='external'`.
#
# POR QUE NÃO HÁ CONTROLE POSITIVO AQUI. Seria disparar `crm-callback`, o único
# endpoint externo — e ele aponta para `retencao_humano`, ou seja, criaria um
# CONTATO HUMANO enfileirado a cada execução do gate. Em vez disso, a asserção é
# dupla e discrimina do mesmo jeito: 404 **e** o log dizendo `procedência interna`.
# Se a porta externa quebrasse por inteiro, o 404 continuaria vindo mas o log NÃO
# diria isso — o teste distingue "recusado por origem" de "parou de resolver".
echo "── PORTA EXTERNA · $REGISTERED via /channel/webhook (deve recusar) ──────"
EXT_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 20 \
  -X POST "$GW/channel/webhook/$REGISTERED" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"api\"}" 2>/dev/null)
echo "   HTTP $EXT_CODE   (esperado 404 — era 201 antes do filtro)"
echo

sleep 3   # deixa o routing consumir o evento antes de ler o log dele

# DELTA: o que ESTA execução acrescentou. Ver o bloco de comentário acima para as
# duas tentativas de janela que falharam antes desta.
POS_VIA_REG=$(( $(count_gw "$_PAT_POS_REG") - B_POS_REG ))
NEG_REFUSED=$(( $(count_gw "$_PAT_NEG_REF") - B_NEG_REF ))
EXT_REFUSED=$(( $(count_gw "$_PAT_EXT_REF") - B_EXT_REF ))

# `FASE C · FALLBACK` (gateway) e `FALLBACK ROTEOU` (routing) — as duas medidas da
# Fase C. Os produtores dos dois logs foram REMOVIDOS na Fase E, então estes deltas
# são estruturalmente 0. **Não viram asserção**: uma verificação que só pode dar o
# valor esperado não distingue nada — seria comprar verde a troco de nada, que é o
# que este arquivo passou a sessão inteira evitando. Ficam como TESTEMUNHA impressa:
# se aparecerem, alguém restaurou o fallback.
GW_FALLBACK=$(( $(count_gw "$_PAT_GW_FB") - B_GW_FB ))
RT_FALLBACK=$(( $(count_rt "$_PAT_RT_FB") - B_RT_FB ))

echo "── evidência no log (DELTA desta execução) ──────────────────────────────"
echo "   positivo via REGISTRO ....... $POS_VIA_REG   (esperado 1)"
echo "   negativo recusado nomeado ... $NEG_REFUSED   (esperado 1)"
echo "   externa recusou por origem .. $EXT_REFUSED   (esperado 1)"
echo "   [testemunha] fallback gateway $GW_FALLBACK   · routing $RT_FALLBACK"
echo "                (produtores removidos na Fase E — se >0, o fallback voltou)"
echo

# ── UNAVAILABLE (opt-in) · registro fora do ar deve dar 503, NUNCA 404 ───────
#
# Este é o ramo que mais importa depois da Fase E, e o único que não dá para
# exercer sem quebrar algo de propósito. Um 404 aqui afirmaria que o endereço não
# existe por causa de um soluço de rede — e o chamador (fire-and-forget, na
# maioria) desistiria de um disparo legítimo, em silêncio.
#
# O `trap` religa o agent-registry mesmo se o curl estourar ou o script morrer no
# meio: um gate que pode deixar a stack pela metade é pior que um gate a menos.
UNAV_CODE="skipped"
if [ "${GATE_TEST_UNAVAILABLE:-0}" = "1" ]; then
  echo "── UNAVAILABLE · agent-registry parado (opt-in) ─────────────────────────"
  trap '$DC start agent-registry >/dev/null 2>&1 || true' EXIT INT TERM
  $DC stop agent-registry >/dev/null 2>&1
  # O cache do resolver tem TTL — e ele guarda a resolução POSITIVA do positivo
  # acima. Usar um identificador NUNCA consultado evita medir o cache em vez do
  # registro (seria um 404/201 vindo de memória, não da indisponibilidade).
  UNAV_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 20 \
    -X POST "$GW/v1/channels/webhook/skill_gate_e_unavailable_probe" \
    -H 'content-type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"api\"}" 2>/dev/null)
  $DC start agent-registry >/dev/null 2>&1
  trap - EXIT INT TERM
  echo "   HTTP $UNAV_CODE   (esperado 503 — 404 seria mentir sobre a existência)"
  echo
fi

# ── Veredicto ────────────────────────────────────────────────────────────────
FAIL=0
[ "$POS_CODE" = "201" ] || { echo "❌ positivo não devolveu 201 (veio $POS_CODE) — endereço COM linha parou de resolver"; FAIL=1; }
# Asserções de contagem são `-eq 1`, não `-ge 1`: o delta mede exatamente o que
# ESTA execução acrescentou, e `≥1` toleraria justamente o defeito que já apareceu
# duas vezes aqui (contagem herdada de execução anterior). Se um dia o serviço
# legitimamente logar duas vezes, o vermelho é informação, não ruído.
[ "${POS_VIA_REG:-0}" -eq 1 ] || { echo "❌ 'via REGISTRO' teve delta ${POS_VIA_REG} (esperado 1) — 0: a resolução pelo registro não está no ar; >1: o serviço logou mais de uma vez por disparo"; FAIL=1; }
[ "$NEG_CODE" = "404" ] || { echo "❌ NEGATIVO não devolveu 404 (veio $NEG_CODE) — endereço SEM linha ainda é atendido; o fallback não saiu (Fase E)"; FAIL=1; }
[ "${NEG_REFUSED:-0}" -eq 1 ] || { echo "❌ recusa nomeada do negativo teve delta ${NEG_REFUSED} (esperado 1) — 0: 404 MUDO, a falha que a D7 alertava"; FAIL=1; }
[ "$EXT_CODE" = "404" ]       || { echo "❌ a porta EXTERNA aceitou um endereço interno (HTTP $EXT_CODE) — o filtro por procedência não está no ar (ADR §7.6.3)"; FAIL=1; }
[ "${EXT_REFUSED:-0}" -eq 1 ] || { echo "❌ recusa por origem teve delta ${EXT_REFUSED} (esperado 1) — 0: pode ter parado de resolver em vez de filtrar (404 sozinho não distingue)"; FAIL=1; }
if [ "$UNAV_CODE" != "skipped" ]; then
  [ "$UNAV_CODE" = "503" ] || { echo "❌ registro fora do ar devolveu $UNAV_CODE — deveria ser 503. Com 404, um soluço de rede vira 'endereço não existe' para todo disparo interno"; FAIL=1; }
fi

if [ "$FAIL" -eq 0 ]; then
  echo "✅ Fase E OK — o registro é o ÚNICO resolvedor; sem linha, sem sessão."
  if [ "$UNAV_CODE" = "skipped" ]; then
    echo "   ⚠️  o ramo 503 (registro fora do ar) NÃO foi exercido — rode com"
    echo "       GATE_TEST_UNAVAILABLE=1 para medi-lo. Verde aqui não o cobre."
  fi
  exit 0
fi
echo
echo "❌ reprovou. Se a causa for endereço sem linha, o conserto é SEMEAR"
echo "   (declarar em infra/registry/*.yaml ou cadastrar na tela) — nunca reviver o"
echo "   fallback, que era a segunda fonte que este arco existe para eliminar."
exit 1
