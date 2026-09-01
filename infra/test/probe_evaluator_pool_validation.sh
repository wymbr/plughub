#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# probe — CAP-04: o `evaluator_pool` de uma campanha roda mesmo um flow de avaliação?
#
# POR QUE ESTE PROBE EXISTE
# =========================
# A remoção do gate de `agent_role` (CAP-01) teve UM efeito real, e só um: perdeu-se
# a detecção de avaliador MAL CONFIGURADO. Ela voltou onde o erro nasce — a campanha
# que declara `evaluator_pool` — e este probe é o que impede a mitigação de virar
# promessa sem mecanismo (`docs/adr/adr-remove-agent-role-axis.md` § Riscos 1).
#
# O discriminador é DERIVADO DO ARTEFATO (o flow deployado invoca
# `evaluation_context_get`/`evaluation_submit`?), nunca um campo declarado — um campo
# novo seria o `agent_role` renascendo com outro nome, que é a alternativa que o ADR
# refuta.
#
# OS RAMOS, e de qual proposição cada um é evidência
# ==================================================
#   P1  CONTROLE POSITIVO — pool avaliador de verdade é ACEITO.
#       Sem ele, um guard que recusasse TUDO passaria neste arquivo inteiro.
#   P2  pool com flow real que NÃO avalia é RECUSADO (400), e a recusa NOMEIA o motivo
#   P3  `evaluator_pool` ausente é aceito — NULL é "default global", não é declaração
#   P4  pool inexistente no registry é RECUSADO
#   P5  o UPDATE também gateia — a tela EDITA campanha, não recria; gatear só o
#       create deixaria a porta aberta pelo caminho mais usado
#   P6  `unverifiable` ≠ `not_evaluator` — pool SEM slot `current` promovido é ACEITO
#       (com log), porque "não consegui ver o artefato" e "vi e ele não avalia" são
#       fatos diferentes. Colapsá-los recriaria o filtro que filtra ESVAZIANDO (o
#       defeito da F2 do arco de relatórios)
#
# P1 e P2 são o MESMO caminho com um bit diferente (qual pool), e é a assimetria
# entre eles que carrega a prova. P6 é o ramo que mais tenta ser "simplificado" —
# recusar o não-verificável parece mais seguro e é o que transforma indisponibilidade
# do agent-registry em campanha impossível de salvar.
#
# Pré: stack demo no ar; evaluation-api REBUILDADA; agent-registry no ar; jq.
#      ⚠️ Rodar de dentro do WSL — o Git Bash do Windows não tem `jq`.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
AREG="${AREG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
FAIL=0

# Pools usados. Não são inventados: medidos no tenant demo antes de escrever o probe.
POOL_EVALUATOR="${POOL_EVALUATOR:-avaliacao_ia}"     # slot current invoca as duas tools
POOL_NOT_EVAL="${POOL_NOT_EVAL:-sac_ia}"             # slot current, 14 steps, zero invoke
POOL_NO_SLOT="${POOL_NO_SLOT:-retencao_humano}"      # sem slot current promovido
POOL_ABSENT="pool_que_nao_existe_$$"

assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (=$2)"; FAIL=1; fi; }

# Ramo que não RODOU não é ramo que passou. A 1ª versão deste probe pulou o P5
# (bug de truncamento no corpo do POST) e ainda assim imprimiu ✅ — o modo de falha
# exato que a § Postura descreve: "prefira que o teste se declare INCONCLUSIVO a
# passar por ausência de amostra". Aqui ele nem se declara: ele REPROVA.
SKIPPED=0
skip() { echo "  ⊘ $1 — RAMO NÃO EXERCIDO"; SKIPPED=$((SKIPPED+1)); }

# ── Bearer com evaluation.formularios:read_write ──────────────────────────────
BEARER=$(python3 -c "
import jwt, time
print(jwt.encode({'sub':'u_probe_cap04','tenant_id':'$TENANT','roles':['admin'],
  'module_config':{'evaluation':{'formularios':{'access':'read_write'}}},
  'accessible_pools':[],'iat':int(time.time()),'exp':int(time.time())+3600},
  '$JWT_SECRET', algorithm='HS256'))" 2>/dev/null)
[ -n "$BEARER" ] || { echo "✗ não consegui mintar o Bearer (pyjwt instalado?)"; exit 1; }
BH="Authorization: Bearer $BEARER"

# ── Pré-condição: o mundo que o probe assume ainda é o mundo real ─────────────
# Sem isto, um pool que mudasse de deploy faria o probe medir outra coisa e ficar
# verde/vermelho pelo motivo errado.
pool_tools() {
  $CURL "$AREG/v1/pools/$1/slots" -H "x-tenant-id: $TENANT" \
    | jq -r 'if .slots.current.set then
               ([.slots.current.yaml_snapshot.steps[]?.tool] | map(select(.)) | unique | join(","))
             else "SEM-SLOT" end'
}
echo "══ pré-condições · o parque ainda é o que o probe assume ══"
T_EVAL="$(pool_tools "$POOL_EVALUATOR")"
T_NOT="$(pool_tools "$POOL_NOT_EVAL")"
T_NOSLOT="$(pool_tools "$POOL_NO_SLOT")"
assert_true "$POOL_EVALUATOR invoca tools de avaliação (=$T_EVAL)" \
  "$(echo "$T_EVAL" | grep -q evaluation_context_get && echo true || echo false)"
assert_true "$POOL_NOT_EVAL tem slot e NÃO invoca tools de avaliação (=${T_NOT:-<vazio>})" \
  "$([ "$T_NOT" != "SEM-SLOT" ] && ! echo "$T_NOT" | grep -q evaluation_ && echo true || echo false)"
assert_true "$POOL_NO_SLOT está sem slot current (=$T_NOSLOT)" \
  "$([ "$T_NOSLOT" = "SEM-SLOT" ] && echo true || echo false)"
[ "$FAIL" = 0 ] || { echo "❌ pré-condições falharam — o probe mediria outra coisa"; exit 1; }

# ── Form (a campanha exige um) ────────────────────────────────────────────────
FORM=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -H "$BH" -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"cap04_probe_$$\",\"min_passing_score\":7.0,\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D1\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$FORM" ] || { echo "✗ criação de form falhou — probe não pode continuar"; exit 1; }

# mk_campaign <nome> <evaluator_pool|__omit__> → ecoa "STATUS|corpo"
# ⚠️ O corpo NÃO é truncado. A 1ª versão fazia `head -c 400` aqui, o JSON de uma
# campanha criada passa disso, e o `jq` do P1 morria em "Unfinished string" — o id
# vinha vazio e o P5 era pulado em silêncio. Truncar para exibir é legítimo; truncar
# o que vai ser PARSEADO é fabricar ausência.
mk_campaign() {
  local tag="$1" ep="$2" extra=""
  [ "$ep" != "__omit__" ] && extra=",\"evaluator_pool\":\"$ep\""
  $CURL -o /tmp/cap04_body.$$ -w '%{http_code}' \
    -X POST "$EVAL/v1/evaluation/campaigns" $JSON -H "$BH" -d "{
      \"tenant_id\":\"$TENANT\",\"name\":\"$tag\",\"form_id\":\"$FORM\",
      \"pool_id\":\"$POOL_EVALUATOR\",\"evaluation_pool_id\":\"$POOL_EVALUATOR\"$extra}"
  echo "|$(cat /tmp/cap04_body.$$ 2>/dev/null | tr -d '\n')"; rm -f /tmp/cap04_body.$$
}

# ── P1 · CONTROLE POSITIVO ────────────────────────────────────────────────────
echo
echo "══ P1 · CONTROLE POSITIVO · pool avaliador de verdade é ACEITO ══"
R=$(mk_campaign "cap04_ok_$$" "$POOL_EVALUATOR"); CODE="${R%%|*}"; BODY="${R#*|}"
assert_true "campanha com evaluator_pool=$POOL_EVALUATOR criada (HTTP $CODE)" \
  "$([ "$CODE" = "201" ] && echo true || echo false)"
CAMP_OK=$(echo "$BODY" | jq -r '.campaign_id // .id // empty')

# ── P2 · pool que não avalia é RECUSADO, e a recusa NOMEIA ────────────────────
echo
echo "══ P2 · pool com flow que NÃO avalia é RECUSADO ══"
R=$(mk_campaign "cap04_bad_$$" "$POOL_NOT_EVAL"); CODE="${R%%|*}"; BODY="${R#*|}"
assert_true "recusado com 400 (HTTP $CODE)" \
  "$([ "$CODE" = "400" ] && echo true || echo false)"
# Recusa muda é quase tão ruim quanto recusa nenhuma: quem lê tem de saber O QUE
# consertar (qual pool, qual skill, qual tool falta).
assert_true "a recusa nomeia o pool e as tools esperadas" \
  "$(echo "$BODY" | grep -q "$POOL_NOT_EVAL" && echo "$BODY" | grep -q "evaluation_context_get" \
     && echo true || echo false)"

# ── P3 · NULL não é declaração ────────────────────────────────────────────────
echo
echo "══ P3 · evaluator_pool ausente é aceito (NULL = default global) ══"
R=$(mk_campaign "cap04_null_$$" "__omit__"); CODE="${R%%|*}"
assert_true "campanha sem evaluator_pool criada (HTTP $CODE)" \
  "$([ "$CODE" = "201" ] && echo true || echo false)"

# ── P4 · pool inexistente ─────────────────────────────────────────────────────
echo
echo "══ P4 · pool inexistente no registry é RECUSADO ══"
R=$(mk_campaign "cap04_ghost_$$" "$POOL_ABSENT"); CODE="${R%%|*}"; BODY="${R#*|}"
assert_true "recusado com 400 (HTTP $CODE)" \
  "$([ "$CODE" = "400" ] && echo true || echo false)"
assert_true "a recusa nomeia o pool inexistente" \
  "$(echo "$BODY" | grep -q "$POOL_ABSENT" && echo true || echo false)"

# ── P5 · o UPDATE também gateia ───────────────────────────────────────────────
echo
echo "══ P5 · PUT de campanha também gateia (a tela EDITA, não recria) ══"
if [ -n "$CAMP_OK" ]; then
  # ⚠️ É PUT, não PATCH. A 1ª versão deste ramo usava PATCH e recebia 405 — que
  # NÃO é 400, então reprovou corretamente; mas se o veredicto fosse "não deu 201"
  # em vez de "deu 400", o 405 teria passado por verde. Ramo de recusa afirma o
  # CÓDIGO esperado, nunca "qualquer coisa menos sucesso".
  UCODE=$($CURL -o /tmp/cap04_u.$$ -w '%{http_code}' \
    -X PUT "$EVAL/v1/evaluation/campaigns/$CAMP_OK?tenant_id=$TENANT" $JSON -H "$BH" \
    -d "{\"evaluator_pool\":\"$POOL_NOT_EVAL\"}")
  UBODY=$(cat /tmp/cap04_u.$$ 2>/dev/null | tr -d '\n'); rm -f /tmp/cap04_u.$$
  assert_true "PUT para pool que não avalia recusado com 400 (HTTP $UCODE)" \
    "$([ "$UCODE" = "400" ] && echo true || echo false)"
  # ⚠️ Procurar o nome do pool no corpo NÃO serve como prova de recusa: o corpo de
  # SUCESSO ecoa `evaluator_pool` e casaria igual. Foi o que aconteceu na 1ª rodada
  # (200 + "✓ a recusa nomeia o pool"). A asserção tem de exigir a forma da RECUSA.
  assert_true "a recusa do PUT vem como detail nomeando as tools" \
    "$(echo "$UBODY" | grep -q '"detail"' && echo "$UBODY" | grep -q 'evaluation_context_get' \
       && echo true || echo false)"
  # Controle positivo do MESMO caminho: o PUT precisa continuar aceitando o pool
  # bom. Um guard que recusasse todo PUT passaria na asserção acima.
  UOK=$($CURL -o /dev/null -w '%{http_code}' \
    -X PUT "$EVAL/v1/evaluation/campaigns/$CAMP_OK?tenant_id=$TENANT" $JSON -H "$BH" \
    -d "{\"evaluator_pool\":\"$POOL_EVALUATOR\"}")
  assert_true "PUT para o pool avaliador segue aceito (HTTP $UOK)" \
    "$([ "$UOK" = "200" ] && echo true || echo false)"
else
  skip "P5 não rodou: o P1 não devolveu campaign_id"
fi

# ── P5b · o guard não vazou para rota vizinha ─────────────────────────────────
# Nasceu de um defeito REAL: a 1ª versão do guard foi parar em `update_form` (as
# três linhas de abertura são idênticas às de `update_campaign`), chamando
# `body.evaluator_pool` num `FormUpdate` que não tem o campo — 500 em toda edição
# de formulário. Nenhum ramo do probe atualizava form, então nada ficava vermelho.
echo
echo "══ P5b · rota vizinha (update_form) intacta — o guard não vazou ══"
FCODE=$($CURL -o /dev/null -w '%{http_code}' \
  -X PUT "$EVAL/v1/evaluation/forms/$FORM?tenant_id=$TENANT" $JSON -H "$BH" \
  -d "{\"name\":\"cap04_probe_renomeado_$$\"}")
assert_true "PUT /forms/{id} responde normalmente (HTTP $FCODE)" \
  "$([ "$FCODE" = "200" ] && echo true || echo false)"

# ── P6 · unverifiable ≠ not_evaluator ─────────────────────────────────────────
echo
echo "══ P6 · pool SEM slot current é ACEITO (não-verificável não é reprovado) ══"
R=$(mk_campaign "cap04_noslot_$$" "$POOL_NO_SLOT"); CODE="${R%%|*}"
assert_true "campanha com pool sem slot criada (HTTP $CODE)" \
  "$([ "$CODE" = "201" ] && echo true || echo false)"
echo "     (o aviso correspondente deve estar no log da evaluation-api, nomeando"
echo "      O QUE deixou de ser verificado — não basta dizer que degradou)"
LOGGED=$(docker compose -f docker-compose.demo.yml logs --since 2m evaluation-api 2>/dev/null \
  | grep -c "CAP-04.*ACEITO SEM VERIFICAÇÃO" || true)
assert_true "aviso de degradação registrado (=${LOGGED:-0})" \
  "$([ "${LOGGED:-0}" -ge 1 ] && echo true || echo false)"

echo
if [ "$SKIPPED" -gt 0 ]; then
  echo "❌ CAP-04 INCONCLUSIVO — $SKIPPED ramo(s) não exercido(s). Verde com ramo pulado"
  echo "   é confiança comprada sem contrapartida; trate como falha."
  exit 1
fi
[ "$FAIL" = 0 ] \
  && echo "✅ CAP-04 OK — pool avaliador é verificado pelo ARTEFATO; não-verificável não reprova" \
  || { echo "❌ CAP-04 com falhas"; exit 1; }
