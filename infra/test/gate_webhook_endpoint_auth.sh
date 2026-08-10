#!/usr/bin/env bash
# gate_webhook_endpoint_auth.sh — arco webhook-endpoint-auth
#
# A PERGUNTA: um endpoint com `auth_required` recusa quem não apresenta a credencial
# certa — **e** um endpoint sem `auth_required` continua aceitando quem não apresenta
# nada?
#
# ─── AS DUAS METADES, E POR QUE A SEGUNDA É A QUE IMPORTA MAIS ────────────────
#
#   PROTEGIDO   endpoint com token → sem header: 401 · token errado: 401 · certo: 201
#   ANÔNIMO     endpoint sem token → sem header: **201**
#
# A metade ANÔNIMA é o controle de NÃO-REGRESSÃO, e é a que decide se este arco pode
# ser entregue: `auth_required` nasce `false` exatamente para não converter os onze
# endpoints em uso num 401 retroativo. Um bug que ligasse a exigência para todo mundo
# passaria despercebido num gate que só testasse o caminho protegido — e o sintoma em
# produção seria todo disparo interno parando de uma vez.
#
# ─── O ENDPOINT PROTEGIDO É CRIADO E DESTRUÍDO AQUI ──────────────────────────
# Não se liga auth num endpoint de produção para testar: `skill_formfill_demo_v1` é
# usado por outros gates, e deixá-lo exigindo token quebraria todos eles. O gate cria
# o seu próprio endpoint efêmero, aponta para o MESMO pool do formfill (um alvo já
# exercitado, que suspende esperando preenchimento) e o remove no `trap` — inclusive
# se estourar no meio. Gate que suja o ambiente é gate que alguém desliga.
#
# ─── PREVISÕES (registradas antes de rodar) ───────────────────────────────────
#   P1  criar endpoint com auth_required=true devolve `token` UMA vez (plughub_wh_…)
#   P2  GET do endpoint **não** devolve `token_hash` (é material de credencial)
#   P3  disparo sem header               → 401
#   P4  disparo com token errado         → 401
#   P5  disparo com o token certo        → 201
#   P6  endpoint ANÔNIMO sem header      → 201   (não-regressão — o default é OFF)
#   P7  após ROTACIONAR, o token antigo  → 401 em poucos segundos (« TTL de 30 s)
#   P8  após rotacionar, o token novo    → 201
#
# ─── P9/P10 — o guard da fatia 3 (ADR §7.9), acrescentados 2026-08-10 ─────────
#   P9   create com auth_required=true e origin=internal      → **422**
#   P10  POST /v1/channel-endpoints/{id}/token em linha internal → **422**
#
# Não é sobre segurança do endereço interno (esse se protege pela topologia — é a
# própria decisão do §7.9). É sobre a decisão não depender de alguém lembrar dela:
# ligar auth ali silencia disparo interno e não fecha nada, porque o mesmo pool segue
# acionável por /v1/channels/webhook/pool/{id}, fora do registro.
#
# ─── P11/P12 — o guard da fatia 4 (ADR §7.10) ────────────────────────────────
#   P11  create webhook `external` SEM `auth_required` no corpo   → **422**
#   P12  create com `auth_required` em canal NÃO-webhook          → **422**
#
# P11 guarda a ausência de default: o route serve a UI (que RECEBE o token) e o
# RegistrySyncer (que descarta o corpo), e qualquer default é errado para um dos dois
# — ON faz instalação limpa nascer exigindo token que ninguém viu; OFF é o opt-in que
# a fatia 1 já diagnosticou. P12 guarda o escopo: a flag só é lida nas rotas de webhook.
#
# ⚠️ P10 é o ÚNICO passo deste arquivo com efeito colateral quando REPROVA: sem o
# guard no ar a chamada sucede e protege um endpoint interno REAL. O bloco reverte
# imediatamente (DELETE /token) e confere o `auth_required` de volta em false ANTES de
# declarar a falha — testar o guard sem o guard causaria justamente o dano que ele
# impede. Se a reversão falhar, o gate imprime o comando de conserto manual.
#
# Uso:  bash infra/test/gate_webhook_endpoint_auth.sh [tenant]
# Pré:  channel-gateway (8010), agent-registry (3300).
# Saída: 0 = passou · 1 = mediu e reprovou · 2 = INCONCLUSIVO (não mediu).

set -uo pipefail

TENANT="${1:-tenant_demo}"
GW="http://localhost:8010"
REG="http://localhost:3300"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"

IDENT="skill_gate_auth_probe_$$"
POOL="formfill_demo_ia"          # alvo já exercitado por smoke_formfill_renderer
ANON_IDENT="skill_formfill_demo_v1"   # endpoint REAL, anônimo — controle de não-regressão

EP_ID=""
# Os três abaixo só ganham valor se o guard correspondente estiver AUSENTE e o create
# passar. Existirem no cleanup é o que impede o gate de deixar lixo justamente quando
# reprova — que é quando ninguém está olhando para o ambiente, e sim para o erro.
INT_EP_ID=""      # §7.9  — auth em origin=internal
NOAUTH_EP_ID=""   # §7.10 — webhook external sem decisão explícita
NONWH_EP_ID=""    # §7.10 — auth em canal não-webhook
cleanup() {
  for _id in "$EP_ID" "$INT_EP_ID" "$NOAUTH_EP_ID" "$NONWH_EP_ID"; do
    [ -n "$_id" ] && curl -s -o /dev/null -X DELETE "$REG/v1/channel-endpoints/$_id" \
      -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" 2>/dev/null
  done
  return 0
}
trap cleanup EXIT INT TERM

echo "══ autenticação de endpoint webhook — tenant=$TENANT ══"
echo

# ── PREFLIGHT ────────────────────────────────────────────────────────────────
for _i in $(seq 1 60); do
  GW_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$GW/health" 2>/dev/null)
  case "$GW_CODE" in 2*) break ;; esac
  [ "$_i" = "1" ] && printf '   aguardando channel-gateway atender'
  printf '.'; sleep 1
done
[ "${_i:-1}" != "1" ] && echo
case "${GW_CODE:-000}" in
  2*) : ;;
  *)  echo "⚠️  INCONCLUSIVO — channel-gateway não atendeu (HTTP ${GW_CODE:-000})."; exit 2 ;;
esac

# O controle de não-regressão precisa MESMO estar anônimo, senão ele não controla nada.
ANON_AUTH=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook&identifier=$ANON_IDENT" \
  -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // [])[0].auth_required // false')
if [ "$ANON_AUTH" != "false" ]; then
  echo "⚠️  INCONCLUSIVO — o controle anônimo '$ANON_IDENT' está com auth_required=$ANON_AUTH."
  echo "    Ele deixaria de ser controle de não-regressão."
  exit 2
fi

# ── Criar o endpoint protegido ───────────────────────────────────────────────
echo "── criando endpoint protegido efêmero · $IDENT ──────────────────────────"
# `auth_required` explícito — obrigatório desde a fatia 4 (§7.10) em webhook external.
CREATE=$(curl -s -m 10 -X POST "$REG/v1/channel-endpoints" \
  -H 'content-type: application/json' -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
  -d "{\"channel\":\"webhook\",\"identifier\":\"$IDENT\",\"pool_id\":\"$POOL\",
       \"display_name\":\"Gate de auth (efêmero)\",\"auth_required\":true}" 2>/dev/null)
EP_ID=$(jq -r '.id // empty' <<<"$CREATE")
TOKEN=$(jq -r '.token // empty' <<<"$CREATE")
HASH_LEAKED_ON_CREATE=$(jq -r 'has("token_hash")' <<<"$CREATE")
echo "   id=${EP_ID:-«nenhum»}   token=${TOKEN:0:16}…   prefixo_guardado=$(jq -r '.token_prefix // "«nenhum»"' <<<"$CREATE")"

if [ -z "$EP_ID" ] || [ -z "$TOKEN" ]; then
  echo "⚠️  INCONCLUSIVO — não consegui criar o endpoint protegido:"
  echo "$CREATE" | head -5
  exit 2
fi

# ── P2 · o hash vaza na leitura? ─────────────────────────────────────────────
# Sem `x-service-token` (o caso da UI) o hash NÃO pode aparecer. Com ele, deve —
# é assim que o gateway verifica localmente. As duas metades importam: a primeira é
# a que protege a credencial, a segunda é a que prova que o canal de serviço existe
# (sem ele o gateway falha fechado e o P5 reprovaria por outro motivo).
HASH_AS_UI=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook&identifier=$IDENT" \
  -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // [])[0] | has("token_hash")')
HASH_AS_SVC=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook&identifier=$IDENT" \
  -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" 2>/dev/null \
  | jq -r '(.endpoints // [])[0] | has("token_hash")')
echo "   token_hash visível p/ UI: $HASH_AS_UI (esperado false) · p/ serviço: $HASH_AS_SVC (esperado true)"
echo

# O gateway cacheia a resolução por `endpoint_cache_ttl_s` (30 s). O endpoint é NOVO,
# então não há entrada velha — mas o cache é a razão de os disparos virem DEPOIS da
# criação, e de a revogação levar até 30 s para valer (limitação declarada).
echo "── disparos ─────────────────────────────────────────────────────────────"
fire() {  # $1 = descrição, $2... = headers extras
  local desc="$1"; shift
  curl -s -o /dev/null -w '%{http_code}' -m 20 -X POST "$GW/v1/channels/webhook/$IDENT" \
    -H 'content-type: application/json' "$@" \
    -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"api\"}" 2>/dev/null
}
NOHDR=$(fire "sem header")
echo "   sem X-Webhook-Token .......... HTTP $NOHDR   (esperado 401)"
BADTOK=$(fire "token errado" -H "X-Webhook-Token: plughub_wh_definitivamente_errado")
echo "   token ERRADO ................. HTTP $BADTOK   (esperado 401)"
GOODTOK=$(fire "token certo" -H "X-Webhook-Token: $TOKEN")
echo "   token CERTO .................. HTTP $GOODTOK   (esperado 201)"

# ── ROTAÇÃO · o token ANTIGO tem de morrer NA HORA ───────────────────────────
#
# POR QUE ROTAÇÃO E NÃO REVOGAÇÃO. `DELETE /:id/token` desliga `auth_required` junto
# (para não deixar o endpoint num estado impossível de satisfazer), então depois dele
# o endereço fica ANÔNIMO e aceita sem header — não dá para distinguir "a revogação
# valeu" de "a revogação não valeu". Rotacionar mantém a exigência e troca o segredo:
# o token ANTIGO tem de passar a ser recusado, e isso é falseável.
#
# O QUE ESTE TESTE MEDE DE VERDADE. O disparo do bloco anterior (token CERTO → 201)
# **populou o cache** do resolver com o hash antigo. Se a invalidação por
# `registry.changed` não estivesse ligada, o gateway seguiria aceitando o token
# antigo até o TTL (`endpoint_cache_ttl_s`, 30 s). Logo: 401 em poucos segundos só
# pode vir da invalidação — não pode ser expiração natural. É por isso que o teto de
# espera abaixo precisa ficar MUITO abaixo do TTL; igualá-lo tornaria o teste incapaz
# de distinguir as duas causas, e ele passaria mesmo com o consumidor desligado.
ROT_MAX_WAIT_S=10        # << endpoint_cache_ttl_s (30s) — a folga é o que dá sentido
echo "── ROTAÇÃO · token antigo deve morrer antes do TTL ──────────────────────"
ROT=$(curl -s -m 10 -X POST "$REG/v1/channel-endpoints/$EP_ID/token" \
  -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" 2>/dev/null)
NEWTOKEN=$(jq -r '.token // empty' <<<"$ROT")
echo "   token novo: ${NEWTOKEN:0:16}…"

OLD_DEAD_AFTER=""
_t0=$(date +%s)
for _s in $(seq 1 "$ROT_MAX_WAIT_S"); do
  CODE=$(fire "antigo pós-rotação" -H "X-Webhook-Token: $TOKEN")
  if [ "$CODE" = "401" ]; then OLD_DEAD_AFTER=$(( $(date +%s) - _t0 )); break; fi
  sleep 1
done
if [ -n "$OLD_DEAD_AFTER" ]; then
  echo "   token ANTIGO recusado após ${OLD_DEAD_AFTER}s   (esperado « 30s do TTL)"
else
  echo "   token ANTIGO AINDA ACEITO após ${ROT_MAX_WAIT_S}s   (esperado recusa)"
fi
NEW_CODE=$(fire "novo" -H "X-Webhook-Token: $NEWTOKEN")
echo "   token NOVO ................... HTTP $NEW_CODE   (esperado 201)"
echo

# ── Controle de NÃO-REGRESSÃO ────────────────────────────────────────────────
ANONCODE=$(curl -s -o /dev/null -w '%{http_code}' -m 20 -X POST "$GW/v1/channels/webhook/$ANON_IDENT" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"api\"}" 2>/dev/null)
echo "   endpoint ANÔNIMO sem header .. HTTP $ANONCODE   (esperado 201 — default OFF)"
echo

# ── Guard: auth_required é RECUSADO em linha origin=internal (ADR §7.9) ──────
#
# A decisão da fatia 3 é que endereço interno se protege pela TOPOLOGIA, não por
# token: ligar auth ali silencia o disparo interno e não fecha nada, porque o mesmo
# pool segue acionável por /v1/channels/webhook/pool/{id}, que não passa pelo
# registro. O guard existe para que essa decisão não dependa de alguém lembrar dela.
#
# ⚠️ AS DUAS METADES, e a segunda é a que importa. A tela já não oferece o botão em
# linha `internal` desde a Fase D — mas read-only da TELA não é read-only da API
# (§7.6.4), e todo disparo deste arco foi por curl. Testar só o create deixaria
# passar exatamente o caminho que estava aberto.
echo "── guard · auth_required recusado em origin=internal (§7.9) ──────────────"
INT_ID=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook&identifier=$ANON_IDENT" \
  -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // [])[0].id // empty')
INT_ORIGIN=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook&identifier=$ANON_IDENT" \
  -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // [])[0].origin // "«AUSENTE»"')

# Testemunha: sem uma linha REALMENTE `internal` este bloco não julga nada, e
# "0 falhas sobre 0 amostras" já passou por aprovação neste mesmo arco (F4c).
GUARD_CHECKED=0
CREATE_INT=""; TOKEN_INT=""
if [ -z "$INT_ID" ] || [ "$INT_ORIGIN" != "internal" ]; then
  echo "   ⚠️  NÃO CHECADO — '$ANON_IDENT' tem origin=$INT_ORIGIN (esperado internal)."
  echo "       Sem amostra interna o guard não é exercido. Isto NÃO é aprovação."
else
  GUARD_CHECKED=1
  # (i) create com auth_required=true numa linha que NASCE internal.
  # Guarda o corpo, não só o código: se o guard estiver ausente a linha É criada, e
  # ela precisa ser removida no trap — senão o gate deixa para trás um endpoint
  # interno protegido, que é lixo com aparência de configuração.
  CREATE_INT_BODY=$(curl -s -m 10 -w $'\n%{http_code}' -X POST "$REG/v1/channel-endpoints" \
    -H 'content-type: application/json' -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
    -d "{\"channel\":\"webhook\",\"identifier\":\"skill_gate_int_probe_$$\",\"pool_id\":\"$POOL\",
         \"display_name\":\"Guard interno (efêmero)\",\"origin\":\"internal\",\"auth_required\":true}" 2>/dev/null)
  CREATE_INT=$(tail -n1 <<<"$CREATE_INT_BODY")
  INT_EP_ID=$(jq -r '.id // empty' <<<"$(sed '$d' <<<"$CREATE_INT_BODY")" 2>/dev/null)
  echo "   create internal + auth ....... HTTP $CREATE_INT   (esperado 422)"
  # (ii) gerar token sobre a linha interna REAL — o furo que a tela escondia
  #
  # ⚠️ ESTE PASSO TEM EFEITO COLATERAL QUANDO REPROVA, e é o único do arquivo que tem.
  # Se o guard NÃO estiver no ar (imagem antiga do agent-registry), a chamada SUCEDE e
  # liga `auth_required` num endpoint interno de verdade — silenciando disparo interno
  # e destruindo o controle de não-regressão das próximas execuções. Ou seja: o teste
  # do guard, sem o guard, causa exatamente o dano que o guard existe para impedir.
  # Por isso a reversão é IMEDIATA e incondicional no ramo de sucesso (DELETE /token
  # revoga e desliga `auth_required` junto, por desenho da fatia 1), e o gate reprova
  # DEPOIS de restaurar — nunca antes.
  TOKEN_INT=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "$REG/v1/channel-endpoints/$INT_ID/token" \
    -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" 2>/dev/null)
  echo "   POST {id}/token em internal .. HTTP $TOKEN_INT   (esperado 422)"
  case "$TOKEN_INT" in
    2*)
      echo "   ⚠️  o guard NÃO recusou — revertendo AGORA para não deixar '$ANON_IDENT'"
      echo "       protegido (silenciaria disparo interno e quebraria o controle anônimo)."
      REVERT=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X DELETE \
        "$REG/v1/channel-endpoints/$INT_ID/token" \
        -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" 2>/dev/null)
      BACK=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook&identifier=$ANON_IDENT" \
        -H "x-tenant-id: $TENANT" 2>/dev/null | jq -r '(.endpoints // [])[0].auth_required // false')
      echo "       revert HTTP $REVERT · auth_required agora = $BACK   (esperado false)"
      [ "$BACK" = "false" ] || {
        echo "   ‼️  REVERSÃO FALHOU — '$ANON_IDENT' ficou com auth_required=$BACK."
        echo "       CONSERTE À MÃO antes de qualquer outra coisa:"
        echo "         curl -X DELETE $REG/v1/channel-endpoints/$INT_ID/token \\"
        echo "              -H 'x-tenant-id: $TENANT' -H \"x-service-token: \$SVC\""
      }
      ;;
  esac
fi
echo

# ── Guard fatia 4 · decisão explícita + canal (ADR §7.10) ────────────────────
#
# P11 — webhook `external` SEM `auth_required` no corpo ⇒ 422. O route não tem
#       default porque não distingue quem RECEBE o token (a UI) de quem descarta o
#       corpo (o RegistrySyncer). Sem esta asserção, um default reintroduzido em
#       qualquer dos dois sentidos passaria verde.
# P12 — `auth_required` em canal não-webhook ⇒ 422. A flag só é lida nas rotas de
#       webhook; aceitá-la ali gravaria linha afirmando proteção inexistente.
#
# Ambos são criações que DEVEM falhar, então não sujam nada quando o guard está no
# ar. Quando NÃO está, a linha nasce — e o `trap` a remove (ids capturados abaixo).
echo "── guard · decisão explícita e escopo de canal (§7.10) ───────────────────"
NOAUTH_BODY=$(curl -s -m 10 -w $'\n%{http_code}' -X POST "$REG/v1/channel-endpoints" \
  -H 'content-type: application/json' -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
  -d "{\"channel\":\"webhook\",\"identifier\":\"skill_gate_noauth_probe_$$\",\"pool_id\":\"$POOL\",
       \"display_name\":\"Sem decisão de auth (efêmero)\"}" 2>/dev/null)
NOAUTH=$(tail -n1 <<<"$NOAUTH_BODY")
NOAUTH_EP_ID=$(jq -r '.id // empty' <<<"$(sed '$d' <<<"$NOAUTH_BODY")" 2>/dev/null)
echo "   webhook external sem auth_required .. HTTP $NOAUTH   (esperado 422)"

# Canal não-webhook com a flag. `webchat` + o mesmo pool: o pool não declara webchat,
# mas isso é só AVISO (§6.1) e a recusa por canal vem antes de importar.
NONWH_BODY=$(curl -s -m 10 -w $'\n%{http_code}' -X POST "$REG/v1/channel-endpoints" \
  -H 'content-type: application/json' -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
  -d "{\"channel\":\"webchat\",\"identifier\":\"gate-nonwh-probe-$$\",\"pool_id\":\"$POOL\",
       \"display_name\":\"Auth em canal errado (efêmero)\",\"auth_required\":true}" 2>/dev/null)
NONWH=$(tail -n1 <<<"$NONWH_BODY")
NONWH_EP_ID=$(jq -r '.id // empty' <<<"$(sed '$d' <<<"$NONWH_BODY")" 2>/dev/null)
echo "   webchat com auth_required ........... HTTP $NONWH   (esperado 422)"
echo

# ── Veredicto ────────────────────────────────────────────────────────────────
FAIL=0
[ "$NOAUTH" = "422" ] || { echo "❌ webhook external SEM auth_required devolveu $NOAUTH (esperado 422) — voltou a existir um DEFAULT, e um dos dois sentidos é sempre errado para um dos dois chamadores (§7.10)"; FAIL=1; }
[ "$NONWH"  = "422" ] || { echo "❌ auth_required em canal webchat devolveu $NONWH (esperado 422) — a flag não é lida fora de webhook; a linha afirmaria proteção inexistente"; FAIL=1; }
[ -n "$TOKEN" ] && [[ "$TOKEN" == plughub_wh_* ]] || { echo "❌ o create não devolveu um token no formato esperado"; FAIL=1; }
[ "$HASH_LEAKED_ON_CREATE" = "false" ] || { echo "❌ o 201 do create devolveu token_hash — material de credencial não sai na resposta"; FAIL=1; }
[ "$HASH_AS_UI"  = "false" ] || { echo "❌ token_hash VISÍVEL sem credencial de serviço — qualquer usuário da tela recebe material de credencial"; FAIL=1; }
[ "$HASH_AS_SVC" = "true"  ] || { echo "❌ token_hash NÃO chega ao chamador de serviço — o gateway não terá contra o que comparar e vai falhar fechado"; FAIL=1; }
[ "$NOHDR"    = "401" ] || { echo "❌ disparo SEM credencial devolveu $NOHDR (esperado 401) — o endpoint protegido está aberto"; FAIL=1; }
[ "$BADTOK"   = "401" ] || { echo "❌ token ERRADO devolveu $BADTOK (esperado 401)"; FAIL=1; }
[ "$GOODTOK"  = "201" ] || { echo "❌ token CERTO devolveu $GOODTOK (esperado 201) — a credencial válida está sendo recusada"; FAIL=1; }
[ "$ANONCODE" = "201" ] || { echo "❌ REGRESSÃO: endpoint anônimo devolveu $ANONCODE sem header (esperado 201). O default OFF virou ON — isto derruba os onze endpoints em uso"; FAIL=1; }
[ -n "$NEWTOKEN" ] || { echo "❌ a rotação não devolveu token novo"; FAIL=1; }
[ "$NEW_CODE" = "201" ] || { echo "❌ o token NOVO devolveu $NEW_CODE (esperado 201) — a rotação quebrou o endpoint"; FAIL=1; }
if [ "$GUARD_CHECKED" -eq 1 ]; then
  [ "$CREATE_INT" = "422" ] || { echo "❌ create com auth_required em origin=internal devolveu $CREATE_INT (esperado 422) — o guard §7.9 não está no ar (rebuild do agent-registry?)"; FAIL=1; }
  [ "$TOKEN_INT"  = "422" ] || { echo "❌ POST {id}/token em linha internal devolveu $TOKEN_INT (esperado 422) — read-only da tela não é read-only da API (§7.6.4); confira a reversão acima"; FAIL=1; }
else
  echo "⚠️  guard §7.9 NÃO exercido (sem linha internal de amostra) — o verde abaixo não o cobre."
fi
if [ -z "$OLD_DEAD_AFTER" ]; then
  echo "❌ o token ANTIGO ainda era aceito ${ROT_MAX_WAIT_S}s após a rotação."
  echo "   Causa provável: a invalidação por registry.changed não está no ar, e o"
  echo "   gateway segue servindo o hash cacheado até o TTL (30s). Confira o log:"
  echo "     docker compose -f docker-compose.demo.yml logs channel-gateway | grep -i invalidation"
  FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
  echo "✅ auth OK — protegido recusa sem/errado e aceita com; anônimo segue aberto;"
  echo "   token antigo morre em ${OLD_DEAD_AFTER}s após a rotação (TTL do cache: 30 s)."
  exit 0
fi
echo
echo "❌ reprovou. Se a falha for no token CERTO (P5), suspeite primeiro da"
echo "   credencial de serviço: sem PLUGHUB_AGENT_REGISTRY_SERVICE_TOKEN no"
echo "   channel-gateway o registry omite o hash e o gateway recusa TUDO (fail-closed)."
exit 1
