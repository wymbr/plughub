#!/usr/bin/env bash
# probe_process_chip_scoped_marker.sh — o chip volta para o usuário ESCOPADO, e volta
# como MARCADOR DE EXISTÊNCIA (caminho (a)), não como tamanho real.
#
# ── O que este gate julga ─────────────────────────────────────────────────────
#
# `journey_session_count` é contado SOB a ABAC de propósito: contar os membros que o
# operador não alcança revelaria o tamanho de um processo que toca pools fora do
# escopo dele. A consequência não prevista era a tela AFIRMAR o contrário do que
# sabia — com contagem `1`, o front escondia o chip (`> 1`) e a linha passava a dizer
# *"este contato não pertence a processo nenhum"*.
#
# O conserto publica `journey_has_scoped_out_members` — a EXISTÊNCIA. Logo há DUAS
# proposições a provar, e elas puxam em direções opostas:
#
#   (A) o marcador ACENDE onde o processo é maior que o alcance  → o chip volta
#   (B) a contagem CONTINUA escopada                             → nada vazou
#
# ── ⚠️ POR QUE A v1 DESTE GATE ESTAVA ERRADA (conserto 2026-08-26) ───────────
#
# A v1 tomava a INTERSEÇÃO (`linhas multi visíveis ao usuário`) como sendo "processos
# com membro fora do escopo" — e daí exigia marcador `true` em todas, chamando `false`
# de MUDO e `auth_n == anon_n` de VAZAMENTO. Isso é verdade só num ambiente em que o
# escopo REALMENTE deixa membros de fora; era o caso quando o gate foi escrito (admin
# com 5 pools, 86 de 120 linhas). Com o admin em 22 pools o mesmo gate reprovou código
# CORRETO, imprimindo `4 → 4 · false` em 29 linhas e chamando as 29 de defeito.
#
# O erro não estava nos ramos, estava na POPULAÇÃO: ela não pode ser assumida do
# recorte, tem de ser DERIVADA do dado. `auth_n < anon_n` é a única evidência de que
# há membro fora do alcance — e é ela que decide qual valor do marcador é o correto:
#
#     auth_n <  anon_n  →  há membro fora  →  marcador DEVE ser `true`
#     auth_n == anon_n  →  não há          →  marcador DEVE ser `false`
#     auth_n >  anon_n  →  impossível      →  o escopado contou MAIS que o irrestrito
#
# É a mesma lição do D14.1 (um instrumento pode ser falseável, ramificado e honesto e
# ainda medir a proposição vizinha) — cometida aqui uma tela depois de tê-la
# consertado no `q_scope_delta_stage2.sh`.
#
# ── Testemunhas ──────────────────────────────────────────────────────────────
#
# · de PRESENÇA: a CHAVE existe na resposta. Ausente ⇒ imagem antiga ⇒ INCONCLUSIVO,
#   nunca vermelho — um contador de ausência sem testemunha de presença reprova o
#   ambiente e chama de defeito de código.
# · NEGATIVA: no escopo IRRESTRITO o marcador tem de vir `false` em TODAS as
#   linhas — medido, nunca `null` e nunca `true`.
# · de POPULAÇÃO: sem nenhuma linha com `auth_n < anon_n` o gate NÃO julga (exit 2).
#   Um usuário de escopo largo não refuta o defeito; ele apenas não o exerce.
#
# Uso:  bash infra/test/probe_process_chip_scoped_marker.sh
#       ADMIN_EMAIL=operator@plughub.local ADMIN_PASS=changeme_operator bash …
# Exit: 0 verde · 1 vermelho · 2 INCONCLUSIVO
set -u

AN=${AN:-http://localhost:3500}
AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_admin}
FROM=${FROM:-2026-08-19T00:00:00}
TO=${TO:-2026-08-26T23:59:59}
FIELD=journey_has_scoped_out_members

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

# ⚠️ A REFERENCIA IRRESTRITA MUDOU DE VEICULO (2026-08-27).
# Ate aqui a referencia era uma leitura SEM credencial, porque `sem header => ve
# tudo` era o comportamento do analytics. Esse ramo foi fechado (a flag
# `analytics_open_access` tem default `false`), e a leitura anonima passou a devolver
# 401 — o probe saia INCONCLUSIVO, honestamente, mas sem medir nada.
# Agora irrestrito e uma CREDENCIAL DECLARADA (`unrestricted: true` + lista vazia),
# nao a ausencia de uma. A proposicao e os tres ramos sao os mesmos; so o veiculo
# mudou. O rotulo mudou junto de proposito: chamar de "anon" uma leitura autenticada
# daria ao proximo leitor uma ideia errada do que se compara.
REF_EMAIL=${REF_EMAIL:-probe@plughub.local}
REF_PASS=${REF_PASS:-changeme_probe}

login_tok() {  # $1=email $2=senha
  curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | jq -r '.access_token // empty'
}

TOK=$(login_tok "$ADMIN_EMAIL" "$ADMIN_PASS")
[ -z "$TOK" ] && { echo "INCONCLUSIVO: login falhou para $ADMIN_EMAIL"; exit 2; }
REF_TOK=$(login_tok "$REF_EMAIL" "$REF_PASS")
[ -z "$REF_TOK" ] && {
  echo "INCONCLUSIVO: login falhou para a referencia irrestrita $REF_EMAIL."
  echo "  criar com: bash infra/test/mk_unrestricted_principal.sh"
  exit 2
}

URL="$AN/reports/sessions?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO&page=1&page_size=200"
ANON=$(curl -s "$URL" -H "Authorization: Bearer $REF_TOK")   # referencia irrestrita
AUTHD=$(curl -s "$URL" -H "Authorization: Bearer $TOK")

N_ANON=$(echo "$ANON"  | jq '[.data[]?] | length')
N_AUTHD=$(echo "$AUTHD" | jq '[.data[]?] | length')
if [ "${N_ANON:-0}" -eq 0 ] || [ "${N_AUTHD:-0}" -eq 0 ]; then
  echo "INCONCLUSIVO: uma das duas leituras veio vazia (irrestrito=$N_ANON escopado=$N_AUTHD)."
  exit 2
fi
echo "escopado=$ADMIN_EMAIL · linhas irrestrito=$N_ANON · escopado=$N_AUTHD"

# ── 0. testemunha de PRESENÇA — a chave existe? ──────────────────────────────
# `has()`, não valor: chave AUSENTE é imagem antiga (INCONCLUSIVO); chave presente
# com `null` é "não medi", que tem julgamento próprio mais abaixo.
KEY_ANON=$(echo "$ANON"  | jq "[.data[] | select(has(\"$FIELD\"))] | length")
KEY_AUTH=$(echo "$AUTHD" | jq "[.data[] | select(has(\"$FIELD\"))] | length")
echo "0 · presença da chave \`$FIELD\`: $KEY_ANON/$N_ANON (irrestrito) · $KEY_AUTH/$N_AUTHD (escopado)"
if [ "$KEY_ANON" -eq 0 ] && [ "$KEY_AUTH" -eq 0 ]; then
  echo
  echo "INCONCLUSIVO: o campo não existe em NENHUMA linha — a analytics-api está"
  echo "  rodando a imagem anterior ao conserto. \`build\` + \`up -d analytics-api\`."
  exit 2
fi

# ── 1. testemunha NEGATIVA — no irrestrito o marcador é medido `false` ───────
FALSE_ANON=$(echo "$ANON" | jq "[.data[] | select(.$FIELD == false)] | length")
TRUE_ANON=$(echo  "$ANON" | jq "[.data[] | select(.$FIELD == true)]  | length")
NULL_ANON=$(echo  "$ANON" | jq "[.data[] | select(has(\"$FIELD\")) | select(.$FIELD == null)] | length")
echo "1 · irrestrito ($REF_EMAIL): false=$FALSE_ANON · true=$TRUE_ANON · null=$NULL_ANON"

# ── 2. população DERIVADA — quais processos perdem membros neste escopo? ─────
# ⚠️ NADA de `// "default"`: em jq o alternativo dispara em `false` e em `0` tanto
# quanto em `null`, e as três coisas têm significados diferentes aqui.
_get() { jq -r --arg s "$2" ".data[]|select(.session_id==\$s)|if has(\"$3\") then (.$3|tostring) else \"absent\" end" <<<"$1"; }

AUTH_IDS=$(echo "$AUTHD" | jq -r '.data[].session_id' | sort -u)
OUT=0; IN=0; IMPOSSIBLE=0; MUTE=0; FALSE_POS=0; UNMEASURED=0
OUT_LINES=""; BAD_LINES=""
for sid in $AUTH_IDS; do
  a=$(_get "$ANON"  "$sid" journey_session_count)
  b=$(_get "$AUTHD" "$sid" journey_session_count)
  m=$(_get "$AUTHD" "$sid" "$FIELD")
  case "$a$b" in *null*|*absent*) continue ;; esac   # sem contagem dos dois lados, não julga
  if   [ "$b" -lt "$a" ]; then
    OUT=$((OUT+1))
    OUT_LINES="$OUT_LINES    ${sid: -14}  $a → $b  · $FIELD=$m\n"
    case "$m" in
      true)  : ;;
      null)  UNMEASURED=$((UNMEASURED+1)); BAD_LINES="$BAD_LINES    ${sid: -14} perde membro e o marcador é null\n" ;;
      *)     MUTE=$((MUTE+1));             BAD_LINES="$BAD_LINES    ${sid: -14} perde membro e o marcador é $m\n" ;;
    esac
  elif [ "$b" -gt "$a" ]; then
    IMPOSSIBLE=$((IMPOSSIBLE+1))
    BAD_LINES="$BAD_LINES    ${sid: -14} escopado($b) MAIOR que aberto($a)\n"
  else
    IN=$((IN+1))
    case "$m" in
      false) : ;;
      true)  FALSE_POS=$((FALSE_POS+1)); BAD_LINES="$BAD_LINES    ${sid: -14} nada fora do alcance e o marcador é true\n" ;;
      *)     UNMEASURED=$((UNMEASURED+1)); BAD_LINES="$BAD_LINES    ${sid: -14} nada fora do alcance e o marcador é $m\n" ;;
    esac
  fi
done
echo "2 · população derivada: PERDEM membro=$OUT · escopo cobre=$IN · impossíveis=$IMPOSSIBLE"
[ "$OUT" -gt 0 ] && { echo; echo "3 · linhas que perdem membro (as que o marcador existe para nomear):";
                      printf "%b" "$(echo -e "$OUT_LINES" | head -8)"; }
echo

# ── veredicto ────────────────────────────────────────────────────────────────
fail=0
[ -n "$BAD_LINES" ] && { echo "linhas em desacordo:"; printf "%b" "$(echo -e "$BAD_LINES" | head -10)"; echo; }

if [ "$IMPOSSIBLE" -gt 0 ]; then
  echo "VERMELHO: $IMPOSSIBLE linha(s) com contagem ESCOPADA maior que a aberta."
  echo "  Não é o marcador: é o predicado de escopo somando o que deveria filtrar."
  fail=1
fi
if [ "$MUTE" -gt 0 ]; then
  echo "VERMELHO (A): $MUTE processo(s) perdem membro no escopo e o marcador NÃO acendeu."
  echo "  O chip continua ausente e a tela segue afirmando 'processo de um contato'."
  fail=1
fi
if [ "$FALSE_POS" -gt 0 ]; then
  echo "VERMELHO (A'): $FALSE_POS processo(s) inteiramente ao alcance e o marcador ACESO."
  echo "  O chip passa a prometer membros que não existem — a mentira na direção oposta."
  fail=1
fi
if [ "$UNMEASURED" -gt 0 ]; then
  echo "VERMELHO: $UNMEASURED linha(s) com o marcador \`null\` tendo contagem nos dois lados."
  echo "  \`null\` é 'não medi'; aqui havia como medir."
  fail=1
fi
if [ "$TRUE_ANON" -gt 0 ] || [ "$NULL_ANON" -gt 0 ]; then
  echo "VERMELHO (testemunha negativa): sem ABAC saíram true=$TRUE_ANON null=$NULL_ANON."
  echo "  Irrestrito é MEDIDO (\`false\`): não há membro fora do alcance de quem alcança tudo."
  fail=1
fi

[ "$fail" -eq 1 ] && exit 1

if [ "$OUT" -eq 0 ]; then
  echo "INCONCLUSIVO: nenhum processo perde membro no escopo de \`$ADMIN_EMAIL\`"
  echo "  ($IN processo(s) inteiramente ao alcance, todos com o marcador \`false\` — o"
  echo "  valor CORRETO aqui). O defeito não está refutado: não foi exercido. Rode com"
  echo "  um usuário de escopo estreito:"
  echo "    ADMIN_EMAIL=operator@plughub.local ADMIN_PASS=changeme_operator bash $0"
  exit 2
fi

echo "VERDE: $OUT processo(s) que perdem membro, todos com marcador aceso e contagem"
echo "  ainda escopada; $IN processo(s) cobertos, todos com \`false\`; $FALSE_ANON linha(s)"
echo "  medidas \`false\` sem ABAC."
exit 0
