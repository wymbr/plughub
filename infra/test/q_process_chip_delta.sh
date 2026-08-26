#!/usr/bin/env bash
# q_process_chip_delta.sh — a tela diz 86 contatos e PROCESS vazio; a API sem token
# diz 120 com 29 elegíveis ao chip. Qual das duas variáveis explica a diferença?
#
# ── Por que 2×2, e não "consertar o que parece errado" ───────────────────────
#
# Duas coisas separam o `curl` do navegador, e cada uma sozinha explicaria a queda
# de contagem — o que as torna indistinguíveis enquanto não forem variadas UMA DE
# CADA VEZ:
#
#   · TOKEN     — o scoping por `accessible_pools` do analytics-api é DESACOPLADO
#                 do `analytics_open_access` (memória de sessão anterior): sem
#                 token não há recorte, com token há, mesmo com a lista vazia.
#   · PAGE_SIZE — a tela pede 50, o curl pediu 200. `meta.total_contacts` NÃO
#                 deveria depender disso; se depender, a contagem é derivada da
#                 PÁGINA e não da população — que é o defeito já medido em
#                 `/reports/segments` ("trunca em silêncio").
#
# O chip viaja junto na mesma linha porque a hipótese que importa é de CAUSA
# COMUM: se `multi` cair a zero exatamente onde `contatos` cai a 86, as duas
# anomalias são uma só, e a passagem que as tratou como independentes estava
# errada.
#
# ⚠️ `multi` é um contador de ABSENTES potenciais; `com_campo` é a testemunha de
# presença ao lado. Sem ela, "o backend não manda o campo" e "manda, e nenhuma
# linha qualifica" produzem o mesmo zero.
#
# Uso:  bash infra/test/q_process_chip_delta.sh
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
[ -z "$TOK" ] && { echo "INCONCLUSIVO: login admin falhou — sem ele metade da grade não existe"; exit 2; }

SUM='{contatos: .meta.total_contacts, listados: .meta.total, internas: .meta.total_internal,
      linhas: (.data|length),
      com_campo: ([.data[]|select(.journey_session_count != null)]|length),
      multi:     ([.data[]|select((.journey_session_count // 0) > 1)]|length),
      janela:    .meta.window_applied, pools_int: .meta.internal_pools_known}'

call() { # $1=page_size  $2=auth?
  local url="$AN/reports/sessions?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO&page=1&page_size=$1"
  if [ "$2" = "auth" ]; then
    curl -s "$url" -H "Authorization: Bearer $TOK"
  else
    curl -s "$url"
  fi
}

for ps in 50 200; do
  for a in anon auth; do
    printf '%-4s %-4s ' "$ps" "$a"
    call "$ps" "$a" | jq -c "$SUM" 2>/dev/null || echo "(resposta não-JSON)"
  done
done

echo
echo "Como ler:"
echo "  · \`contatos\` muda com PAGE_SIZE  ⇒ a contagem é da PÁGINA, não da população."
echo "  · \`contatos\` muda com AUTH       ⇒ é recorte por \`accessible_pools\`."
echo "  · \`multi\` cai a 0 na MESMA célula em que \`contatos\` cai a 86 ⇒ CAUSA COMUM:"
echo "    o chip não sumiu, sumiram as linhas que o teriam."
echo "  · \`multi\`=0 com \`com_campo\`>0 e \`contatos\` INALTERADO ⇒ aí sim são dois"
echo "    defeitos, e o do chip é próprio."
