#!/usr/bin/env bash
# q_analytics_authless_inventory.sh — MEDIÇÃO (não gate) do passo 1/2 do plano
# `accessible_pools`: quem chama a analytics-api SEM `Authorization`, e quantos
# principals dependem hoje da convenção `[] = irrestrito`.
#
# Por que existe: `pool_auth.py:133-135` devolve IRRESTRITO quando o header está
# ausente, sem 401. Inverter isso (passo 1 do plano) quebra todo chamador
# header-less. Este script CONTA esses chamadores antes de inverter.
#
# Regra da casa aplicada: contar por DERIVAÇÃO, nunca por menção. O alvo não é o
# nome `ANALYTICS_API_URL` — é qualquer coisa que enderece a porta 3500 ou um dos
# prefixos que o proxy do front manda para lá. Contador de ausência SEMPRE com
# testemunha de presença ao lado.
#
# Roda do HOST. Não precisa de build. Não altera nada.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
DC="${DC:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"
S=packages/platform-ui/src

hr() { printf '\n── %s %s\n' "$1" "$(printf '─%.0s' $(seq 1 $((70 - ${#1}))))"; }

hr "1. BLAST RADIUS — endpoints que dependem do pool_auth"
DEPS=$(grep -rn "Depends(optional_pool_principal)" packages/analytics-api/src \
       --include=*.py | grep -vc "/tests/")
# testemunha de presença: o símbolo existe (senão 0 significaria "grep errado")
SYM=$(grep -c "^def optional_pool_principal\|^async def optional_pool_principal" \
      packages/analytics-api/src/plughub_analytics_api/pool_auth.py)
echo "call sites com Depends(optional_pool_principal) : $DEPS"
echo "testemunha — definição do símbolo encontrada    : $SYM  (0 = grep quebrado, não 'não usa')"
[ "$SYM" -eq 0 ] && echo "  !! INCONCLUSIVO: símbolo não encontrado; o contador acima não vale"

hr "2. CHAMADORES SERVER-SIDE header-less (derivação: alvo :3500)"
echo "Cada linha é um fetch/httpx SEM header de Authorization."
echo
printf '%-22s %-34s %s\n' "SERVIÇO" "ENDPOINT" "GATEADO?"
printf '%-22s %-34s %s\n' "mcp-server-plughub" "/v1/audit/mcp-calls" "gate de AUDIT (não pool)"
printf '%-22s %-34s %s\n' "evaluation-api" "/reports/segments" "SIM — pool_auth"
printf '%-22s %-34s %s\n' "evaluation-api" "/v1/transcript/sessions/{id}" "não (só tenant)"
printf '%-22s %-34s %s\n' "agent-registry" "/reports/sessions" "SIM — pool_auth (INERTE*)"
echo
echo "* agent-registry: o compose NÃO define ANALYTICS_API_URL, então a URL cai no"
echo "  default 'localhost:3500' = ele mesmo. A chamada falha e morre num 'catch {}'"
echo "  vazio (skills.ts:598) => handoff-status reporta activeSessionCount=0 SEMPRE."
echo
echo "-- verificação viva da derivação (alvos :3500 no fonte) --"
grep -rn "localhost:3500\|analytics-api:3500" packages/*/src 2>/dev/null \
  | grep -v "/tests/\|__pycache__\|/dist/" | sed 's|packages/||;s/:[0-9]*:/ : /' | sort -u
echo
echo "-- testemunha: ANALYTICS_API_URL declarada no compose (quem FOI fiado) --"
grep -n "ANALYTICS_API_URL" docker-compose.demo.yml | sed 's/^ *//' || echo "  AUSENTE"

hr "3. CHAMADORES BROWSER-SIDE sem token (platform-ui)"
echo "NÃO há choke point único: são TRÊS mecanismos de token + uma classe sem nenhum."
for M in "apiFetch(" "getAccessToken()"; do
  N=$(grep -rn --include=*.ts --include=*.tsx -e "$M" $S | grep -vc "api/apiFetch.ts")
  printf '  mecanismo %-18s : %s call sites\n' "$M" "$N"
done
echo
echo "  classe SEM auth nenhuma — hooks de agent-assist com 'fetch(url)' nu:"
for F in useCustomer360 useSessionTranscript useCustomerHistory \
         useCustomerSearch useSessionTrace useCustomerJourneys; do
  P=$S/modules/agent-assist/hooks/$F.ts
  [ -f "$P" ] || { printf '    %-22s ARQUIVO AUSENTE (inventário desatualizado)\n' "$F"; continue; }
  AUTH=$(grep -c "Authorization\|getAccessToken\|apiFetch" "$P")
  printf '    %-22s auth_refs=%s  %s\n' "$F" "$AUTH" \
    "$([ "$AUTH" -eq 0 ] && echo 'SEM TOKEN' || echo 'tem algum mecanismo')"
done
echo
echo "  destes, batem em endpoint GATEADO por pool_auth (=> quebram no passo 1):"
echo "    useSessionTrace     -> /reports/sessions/{id}/trace"
echo "    useCustomerJourneys -> /reports/journeys"

hr "4. QUEM DEPENDE de 'accessible_pools = [] => irrestrito'"
$DC exec -T postgres psql -U plughub -d plughub_demo -tA -F'|' -c \
"SELECT email, array_to_string(roles,','),
        coalesce(array_length(accessible_pools,1),0),
        CASE WHEN accessible_pools IS NULL THEN 'NULL=irrestrito'
             WHEN array_length(accessible_pools,1) IS NULL THEN 'VAZIO=irrestrito'
             ELSE 'escopado' END
   FROM auth.users ORDER BY 3, 1;" 2>/dev/null \
| awk -F'|' 'BEGIN{v=0;t=0}
  {t++; printf "  %-26s %-20s n_pools=%-3s %s\n",$1,$2,$3,$4; if($4 ~ /irrestrito/) v++}
  END{printf "\n  total de usuários = %d   |   dependentes do vazio = %d\n",t,v;
      if(t==0) print "  !! INCONCLUSIVO: zero usuários lidos — DB inalcançável?"}'
echo
echo "  ⚠️ 'zero dependentes' é fato DESTA BASE, não do produto. Os produtores do"
echo "     vazio continuam vivos e um install limpo nasce irrestrito:"
grep -n "accessible_pools TEXT\[\]" packages/auth-api/src/plughub_auth_api/db.py \
  | sed 's/^/     DDL   : /'
grep -n "accessible_pools=\[\]," packages/auth-api/src/plughub_auth_api/db.py \
  | sed 's/^/     seed  : /'

hr "5. ESTADO VIVO do caminho sem header (invertido em 2026-08-27)"
echo "Ate 2026-08-27 este bloco media um FURO: sem header => irrestrito, sempre."
echo "Agora o bypass e amarrado a \`analytics_open_access\` (default False no codigo),"
echo "entao o que se mede aqui e QUAL RAMO este ambiente escolheu — nao se ha defeito."
BASE="http://localhost:3500"
Q="tenant_id=$TENANT&page_size=1"
FLAG=$(grep -oE 'PLUGHUB_ANALYTICS_OPEN_ACCESS:[[:space:]]*"?[a-zA-Z]+' \n       docker-compose.demo.yml | head -1 | grep -oE '[a-zA-Z]+$')
ANON=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/reports/sessions?$Q")
BAD=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer lixo.nao.jwt"       "$BASE/reports/sessions?$Q")
echo
echo "  PLUGHUB_ANALYTICS_OPEN_ACCESS no compose = ${FLAG:-AUSENTE}"
echo "  sem Authorization       -> HTTP $ANON"
echo "  testemunha (token LIXO) -> HTTP $BAD"
echo
if [ "$BAD" != "401" ]; then
  echo "  => INCONCLUSIVO: a testemunha nao deu 401. Sem ela, o codigo do anonimo nao"
  echo "     distingue 'liberou' de 'o mecanismo de recusa sumiu'."
elif [ "${FLAG:-}" = "true" ] && [ "$ANON" = "200" ]; then
  echo "  => COERENTE: flag LIGADA e anonimo passa. E o ramo declarado do demo — os 18"
  echo "     scripts da secao 6 seguem funcionando. Producao (flag ausente=False) fecha."
elif [ "${FLAG:-}" != "true" ] && [ "$ANON" = "401" ]; then
  echo "  => COERENTE: flag desligada e anonimo RECUSADO. Este ambiente esta fechado."
else
  echo "  => DIVERGE: flag=${FLAG:-AUSENTE} mas anonimo=$ANON. Um dos dois esta errado;"
  echo "     remedir antes de concluir qualquer coisa."
fi
echo

hr "6. SCRIPTS DE infra/test/ — tres classes, nao duas"
echo "Um gate que passa a receber 401 nao 'fica vermelho': fica INCONCLUSIVO, ou conta"
echo "zero linhas e le isso como 'nao ha dado'. Por isso a conversao vem ANTES de virar"
echo "a flag \`analytics_open_access\` no demo."
tot=0; comtok=0; shim=0; bloq=0; lbloq=""
for f in infra/test/*.sh; do
  # excluir o proprio helper (DEFINE o shim) e este inventario (fala dele em prosa):
  # os dois casariam a busca e inflariam a contagem de "convertidos".
  case "$(basename "$f")" in _*|q_analytics_authless_inventory.sh) continue ;; esac
  grep -qE '3500|/reports/|/v1/audit' "$f" 2>/dev/null || continue
  tot=$((tot + 1))
  if grep -q 'plughub_auth_curl_shim' "$f" 2>/dev/null; then
    shim=$((shim + 1))
  elif grep -q 'NAO CONVERTIDO' "$f" 2>/dev/null; then
    bloq=$((bloq + 1)); lbloq="$lbloq $(basename "$f")"
  elif grep -qE 'Authorization|access_token|Bearer' "$f" 2>/dev/null; then
    comtok=$((comtok + 1))
  else
    bloq=$((bloq + 1)); lbloq="$lbloq ?$(basename "$f")"
  fi
done
echo
printf '  falam com o analytics = %s
' "$tot"
printf '    · ja autenticavam sozinhos ......... %s
' "$comtok"
printf '    · convertidos pelo shim (_auth.sh) . %s
' "$shim"
printf '    · BLOQUEADOS ...................... %s
' "$bloq"
echo "  (o contador 'ja autenticavam' e a testemunha: 0 ali significaria padrao de"
echo "   busca errado, nao 'ninguem autentica')"
if [ "$bloq" -gt 0 ]; then
  echo
  echo "  BLOQUEADOS — e o motivo NAO e esquecimento:"
  for n in $lbloq; do
    case "$n" in
      \?*) printf '    · %-38s SEM MOTIVO DECLARADO — investigar
' "${n#?}" ;;
      *)   printf '    · %-38s precisa ver o TENANT INTEIRO
' "$n" ;;
    esac
  done
  echo
  echo "  Medido 2026-08-27: o tenant tem 36 pools, o admin alcanca 22, 14 ficam fora."
  echo "  NENHUM usuario deste ambiente ve tudo, exceto pelo caminho sem header. Um"
  echo "  usuario com \`accessible_pools: []\` resolveria hoje e seria retrabalho: o"
  echo "  passo 3 inverte o significado de []."
  echo "  => ENDURECER O DEMO ESTA BLOQUEADO NO PASSO 2 (irrestrito EXPLICITO),"
  echo "     nao apenas na conversao dos scripts."
fi
echo
