# shellcheck shell=bash
# _auth.sh — helper de credencial para os scripts de `infra/test/`.
# NAO e executavel: e para ser `source`-ado.
#
#   source "$(dirname "$0")/_auth.sh"
#   acurl -s "$AN/reports/sessions?tenant_id=$TENANT"      # curl + Bearer
#
# ─────────────────────────────────────────────────────────────────────────────
# POR QUE ELE EXISTE (2026-08-27)
#
# Medido: dos 30 scripts que falam com a analytics-api, **18 chamavam SEM token**
# (3 deles gates). Ate 2026-08-27 isso "funcionava" porque `pool_auth` devolvia
# IRRESTRITO na ausencia de header — o furo que o passo 1 fechou. O bypass agora e
# declarado pelo OPERADOR (`analytics_open_access`, default `False`), e o demo ainda
# o tem ligado *por causa destes 18*.
#
# O modo de falha que torna isto urgente-quando-for-feito: um gate que passa a
# receber 401 **nao fica vermelho** — fica INCONCLUSIVO, ou conta zero linhas e le
# isso como "nao ha dado". Virar a flag sem tratar os 18 converteria boa parte da
# superficie de verificacao do repositorio em mudez, e o sintoma seria AUSENCIA.
#
# ⚠️ ADICIONAR O HEADER NAO E NO-OP. Antes, sem token, o script lia TODOS os pools do
# tenant. Com token de admin ele passa a ler os 22 pools do admin (43 com os espelhos
# `-int`). Se um script assertar contagem, o numero PODE mudar — e essa mudanca e
# informacao, nao defeito: significa que ele media uma configuracao que nenhum usuario
# real tem. Por isso a conversao vem ANTES de virar a flag: aqui a causa e inequivoca
# (escopo), enquanto virar as duas juntas deixaria a falha ambigua entre 401 e escopo.
#
# ─────────────────────────────────────────────────────────────────────────────
# CONTRATO
#   plughub_token         -> ecoa o access_token (memoizado por execucao)
#   plughub_auth_header   -> ecoa "Authorization: Bearer <tok>"
#   acurl <args de curl>  -> curl com o header ja anexado
#   plughub_scope_line    -> uma linha dizendo QUEM autenticou e com quantos pools
#
# Falha = INCONCLUSIVO (exit 2), nunca silencio: sem credencial o script nao mede
# nada, e "0 linhas" seria indistinguivel de "nao ha dado".
#
# Sobrescrever: PLUGHUB_TEST_EMAIL / PLUGHUB_TEST_PASS (ou os antigos ADMIN_EMAIL /
# ADMIN_PASS, que varios scripts ja expoem — mantidos para nao quebrar invocacao
# existente). Default = **admin**, que e o principal mais LARGO: e o que menos muda
# os numeros hoje medidos. Usar `supervisor`/`operator` de proposito quando o que se
# quer medir E o escopo.

AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}

_PH_EMAIL=${PLUGHUB_TEST_EMAIL:-${ADMIN_EMAIL:-admin@plughub.local}}
_PH_PASS=${PLUGHUB_TEST_PASS:-${ADMIN_PASS:-changeme_admin}}
_PH_TOK=""

plughub_token() {
  if [ -n "$_PH_TOK" ]; then printf '%s' "$_PH_TOK"; return 0; fi
  command -v jq >/dev/null 2>&1 || {
    echo "INCONCLUSIVO: jq ausente — sem ele nao da para extrair o access_token" >&2
    exit 2
  }
  _PH_TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$_PH_EMAIL\",\"password\":\"$_PH_PASS\",\"tenant_id\":\"$TENANT\"}" \
    | jq -r '.access_token // empty')
  if [ -z "$_PH_TOK" ]; then
    echo "INCONCLUSIVO: login falhou para $_PH_EMAIL em $AUTH (auth-api no ar?)." >&2
    echo "  Sem token este script nao mede nada — e '0 linhas' seria lido como" >&2
    echo "  'nao ha dado'. Recusa deliberada em vez de medicao vazia." >&2
    exit 2
  fi
  printf '%s' "$_PH_TOK"
}

plughub_auth_header() { printf 'Authorization: Bearer %s' "$(plughub_token)"; }

# curl com credencial. USAR nos servicos que CONFEREM o JWT do auth-api — hoje a
# analytics-api e o mcp-server-plughub (`/api/*`), este ultimo desde a CAP-12
# (2026-09-01), porque o `PLUGHUB_JWT_SECRET` dele e o mesmo segredo que a auth-api
# emite. Mandar este Bearer para um servico que NAO o confere pode ser recusado, e um
# 401 vindo do servico errado e o tipo de falha que se depura pelo lado errado.
acurl() { curl -H "$(plughub_auth_header)" "$@"; }

# Uma linha declarando SOB QUAL ESCOPO a medicao foi feita. Chamar no cabecalho do
# script: sem ela, dois numeros diferentes do mesmo gate ficam inexplicaveis, e a
# regra da casa e que toda medicao de ABAC diga QUAL usuario usou.
plughub_scope_line() {
  local pay n
  pay=$(plughub_token | cut -d. -f2 | tr '_-' '/+')
  case $(( ${#pay} % 4 )) in 2) pay="$pay==";; 3) pay="$pay=";; esac
  n=$(printf '%s' "$pay" | base64 -d 2>/dev/null | jq -r '.accessible_pools | length' 2>/dev/null)
  if [ "${n:-0}" = "0" ]; then
    echo "escopo: $_PH_EMAIL · accessible_pools=[] (IRRESTRITO por convencao do auth-api)"
  else
    echo "escopo: $_PH_EMAIL · $n pools (mais os espelhos -int, derivados)"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# plughub_auth_curl_shim — anexa a credencial SO onde o JWT do auth-api e conferido
#
# Por que um shim e nao editar cada `curl`: a deteccao ESTATICA de call site e
# comprovadamente incompleta neste repositorio. Medido em 2026-08-27: seis dos 18
# scripts nao casam nenhum padrao de "curl ... 3500|/reports" e mesmo assim chamam a
# analytics-api — porque o curl e multi-linha, ou a URL vem montada numa variavel.
# Editar chamada por chamada erraria justamente esses, EM SILENCIO, e o sintoma
# seria um 401 no dia em que a flag virasse.
#
# O shim decide sobre a URL REAL, em runtime. Nao pode errar por forma do fonte.
#
# ⚠️ Ele sombreia `curl` DENTRO do script que o chama (nunca globalmente), e so
# acrescenta o header quando o alvo CONFERE este JWT — mandar um Bearer do auth-api
# para config-api/agent-registry poderia ser recusado, e um 401 vindo do servico
# errado e o tipo de falha que se depura pelo lado errado por meia hora.
#
# ── CAP-12 (2026-09-01): o mcp-server entrou na lista ────────────────────────────
# As nove rotas `/api/*` do mcp-server passaram a exigir credencial. Elas eram
# chamadas por NOVE scripts desta pasta, e sem esta linha todos passariam a receber
# 401 — que em varios deles seria lido como "a fila esta vazia", porque o teste conta
# itens. Ou seja: fechar a rota sem estender o shim trocaria um vazamento por uma
# suite que reprova pelo motivo errado, ou pior, que PASSA medindo zero.
#
# O casamento e por ORIGEM (3100 direto, 5174 pela borda) E por CAMINHO das nove
# rotas — a segunda metade cobre script que monte a URL com outro host. `*/api/*`
# sozinho seria largo demais: meia duzia de servicos da casa tem `/api`.
#
# Uso, uma linha no topo do script:
#     source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim
#
# O login e LAZY: se o script nao chamar o analytics, nenhum token e pedido.

plughub_auth_curl_shim() {
  curl() {
    local a u=""
    for a in "$@"; do
      case "$a" in http://*|https://*) u="$a" ;; esac
    done
    case "$u" in
      *:3500*|*/reports/*|*/v1/audit*|*/analytics/*|\
      *:3100/api/*|*:5174/api/*|\
      */api/work_queue/*|*/api/conversation_history/*|*/api/copilot_state/*|\
      */api/supervisor_capabilities/*|*/api/agent_done/*|*/api/menu_submit/*)
        command curl -H "$(plughub_auth_header)" "$@" ;;
      *)
        command curl "$@" ;;
    esac
  }
}
