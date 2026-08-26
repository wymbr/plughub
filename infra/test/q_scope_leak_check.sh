#!/usr/bin/env bash
# q_scope_leak_check.sh — o usuário está vendo linha de pool que NÃO lhe foi liberado?
#
# ── A pergunta, e por que ela não se responde "no olho" ──────────────────────
#
# A queixa natural é *"vejo contatos de pools que não liberei"*, e ela quase sempre
# esbarra na terceira cláusula do predicado, que ninguém tem na cabeça ao olhar a
# tela. `_session_scope_clause` (`reports_query.py`) autoriza por TRÊS razões, em OU:
#
#   1. a sessão ENTROU por um pool meu        → `s.pool_id IN (…)`
#   2. a sessão ainda não tem pool            → `s.pool_id = ''`
#   3. um pool meu PARTICIPOU dela            → existe segmento meu
#
# A (3) é ampliadora e deliberada (um contato que ENTROU por `sac_ia` e foi atendido
# por `retencao_humano` é do supervisor de retenção, mesmo que ele não tenha `sac_ia`).
# Então "a coluna ENTERED VIA mostra um pool que não é meu" **não é** evidência de
# furo — é o caso (3) funcionando. Só é furo a linha que falha nas TRÊS.
#
# Este script reconstrói as três cláusulas do lado do cliente, a partir da MESMA
# resposta que a tela recebe (`pool_id` = entrada; `attended_pool_ids` = projeção dos
# segmentos, que é a fonte da cláusula 3), e conta:
#
#   · SUSPEITA  — falhou nas três ⇒ furo de ABAC, com a linha impressa
#   · as três testemunhas de PRESENÇA, uma por cláusula ⇒ um "0 suspeitas" que venha
#     de parse vazio ou lista vazia fica visível em vez de passar por aprovação
#
# ⚠️ A lista de pools sai do TOKEN, não de config: é o que o backend realmente usa
# (mais os espelhos `-int`, que o `pool_auth` deriva — por isso eles são acrescentados
# aqui também, senão o script acusaria furo em cima da própria derivação).
#
# Uso:  bash infra/test/q_scope_leak_check.sh
#       ADMIN_EMAIL=operator@plughub.local ADMIN_PASS=… bash infra/test/q_scope_leak_check.sh
# Exit: 0 sem suspeita · 1 suspeita(s) · 2 INCONCLUSIVO
set -u

AN=${AN:-http://localhost:3500}
AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_admin}
FROM=${FROM:-2026-08-19T00:00:00}
TO=${TO:-2026-08-26T23:59:59}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -z "$TOK" ] && { echo "INCONCLUSIVO: login falhou para $ADMIN_EMAIL"; exit 2; }

PAY=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+')
case $(( ${#PAY} % 4 )) in 2) PAY="$PAY==";; 3) PAY="$PAY=";; esac
CLAIMS=$(echo "$PAY" | base64 -d 2>/dev/null)
[ -z "$CLAIMS" ] && { echo "INCONCLUSIVO: não decodifiquei o token"; exit 2; }

# Espelho `-int` derivado do lado do servidor (`pool_auth._with_internal_mirrors`).
POOLS=$(echo "$CLAIMS" | jq -c '[.accessible_pools[]?] | . + map(. + "-int") | unique')
N_POOLS=$(echo "$POOLS" | jq 'length')
echo "usuário=$ADMIN_EMAIL · pools no token (+espelhos)=$N_POOLS"

if [ "$N_POOLS" -eq 0 ]; then
  echo
  echo "INCONCLUSIVO: \`accessible_pools\` VAZIO. Pela convenção da auth-api"
  echo "  (\`pool_auth.py:157-161\`) isso é IRRESTRITO — logo não existe 'pool não"
  echo "  liberado' e a pergunta não se aplica a este usuário. Rode com um usuário"
  echo "  escopado, ou trate a própria convenção como o item a decidir."
  exit 2
fi

RESP=$(curl -s -H "Authorization: Bearer $TOK" \
  "$AN/reports/sessions?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO&page=1&page_size=200")
N=$(echo "$RESP" | jq '[.data[]?] | length')
[ "${N:-0}" -eq 0 ] && { echo "INCONCLUSIVO: a resposta veio sem linhas"; exit 2; }

read -r C1 C2 C3 SUSP <<<"$(echo "$RESP" | jq -r --argjson p "$POOLS" '
  [.data[]] as $rows
  | [ ($rows | map(select((.pool_id // "") as $e | $e != "" and ($p|index($e)))) | length),
      ($rows | map(select((.pool_id // "") == "")) | length),
      ($rows | map(select(((.pool_id // "") as $e | ($p|index($e))|not)
                           and ((.attended_pool_ids // []) | any(. as $a | $p|index($a))))) | length),
      ($rows | map(select(((.pool_id // "") as $e | $e != "" and (($p|index($e))|not))
                           and (((.attended_pool_ids // []) | any(. as $a | $p|index($a)))|not))) | length)
    ] | @tsv')"

echo "linhas=$N"
echo "  cláusula 1 (entrou por pool meu) ....... $C1"
echo "  cláusula 2 (sem pool ainda) ............ $C2"
echo "  cláusula 3 (pool meu ATENDEU) .......... $C3   ← a que engana o olho"
echo "  SUSPEITAS (falharam nas três) .......... $SUSP"
echo

if [ "$SUSP" -gt 0 ]; then
  echo "VERMELHO: $SUSP linha(s) sem NENHUMA razão de autorização. Furo de ABAC."
  echo "$RESP" | jq -r --argjson p "$POOLS" '.data[]
    | select(((.pool_id // "") as $e | $e != "" and (($p|index($e))|not))
             and (((.attended_pool_ids // []) | any(. as $a | $p|index($a)))|not))
    | "    \(.session_id[-14:])  entrou=\(.pool_id // "?")  atendido=\((.attended_pool_ids // [])|join(","))"' \
    | head -20
  exit 1
fi

echo "VERDE: nenhuma linha sem razão de autorização."
echo "  Se a queixa é 'vejo pool que não liberei', o responsável é a cláusula 3"
echo "  ($C3 linha(s)): o contato ENTROU por um pool alheio mas foi ATENDIDO por um"
echo "  pool seu. É desenho — e é discutível como desenho, não como defeito."
exit 0
