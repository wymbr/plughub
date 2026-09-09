#!/usr/bin/env bash
# ==============================================================================
# probe_resume_approver_authz.sh — quem aprova, aprova por GRANT (nao por papel)
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# O `/v1/channels/webhook/resume/{token}` do channel-gateway tinha, ate 2026-08-27:
#
#     is_elevated = ("admin" in roles) or ("supervisor" in roles)
#     if not is_elevated and required_abac is not None:  ...ABAC + pool-scope...
#
# ou seja, papel elevado bypassava ABAC **e** recorte de pool numa rota que decide
# APROVACAO. As duas premissas do comentario que o justificava morreram no mesmo arco:
# desde o passo 3 todo usuario nasce com grants por campo, e no passo 5 o `passesAbac`
# da plataforma deixou de olhar papel.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   S1  o corpo do verificador voltar a citar papel;
#   S2  um principal SEM `approvals.decide` ser aceito numa tarefa que o exige;
#   S3  o admin (que TEM o grant) ser barrado — a regressao na outra direcao, e a mais
#       cara: some a capacidade de aprovar e o sintoma e "o botao nao funciona".
#
# ⚠️ S2/S3 exigem uma workflow SUSPENSA de aprovacao. Sem ela o probe diz INCONCLUSIVO
# em vez de sair verde: token inexistente devolve 404 para todo mundo, e ler isso como
# "recusou o nao-autorizado" seria medir o proprio 404.
#
# ⚠️⚠️ O S2 MEDIA A PROPOSICAO ERRADA, e foi corrigido na AUT-46 (2026-09-09).
# Ele dizia *"supervisor NAO tem `approvals.decide`"* — verdade em 2026-08-27, falsa
# desde a MOD-08/G1b (2026-09-08): o guard de RANK exige `preset(operator) ⊆
# preset(supervisor)`, e como o operator tem o campo, o supervisor passou a te-lo.
# Medido: **6** portadores, todos `read_write`.
#
# O que separa a aprovacao de NEGOCIO da promocao de DEPLOY nunca foi o campo — e o
# ESCOPO DE POOL. E ele era **opcional para o chamador** ate a AUT-46: omitir
# `pool_id` no corpo pulava o `pool_in_scope`, medido ao vivo com **200** numa
# promocao real. Hoje o pool vem do SERVIDOR e a tarefa que declara capacidade exige
# credencial. Quem prova isso comportamentalmente, com controle positivo dos dois
# lados, e `gate_resume_scope_is_not_optional.sh` — este probe fica com a metade
# ESTRUTURAL (S1), que e a que ele sabe medir sem tarefa viva.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

MAIN="$ROOT/packages/channel-gateway/src/plughub_channel_gateway/main.py"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v jq >/dev/null || { inc "jq ausente"; exit 2; }
[ -f "$MAIN" ] || { inc "main.py do channel-gateway nao encontrado"; exit 2; }

printf '\033[1mprobe: aprovacao por GRANT, nao por papel\033[0m\n'

# ── S1 — estrutural: o verificador nao decide por papel ─────────────────────
sec "S1 - o verificador do aprovador nao cita papel"
# ⚠️ SEM `head -80`: a janela por CONTAGEM DE LINHAS quebrou na AUT-46
# (2026-09-09), quando a funcao cresceu — o `abac_can` saiu da janela e a
# TESTEMUNHA DE PRESENCA reprovou dizendo *"o portao sumiu, nao mudou"*, com o
# portao inteiro no lugar. Falso vermelho de INSTRUMENTO, e do tipo caro: a
# leitura obvia era *"a AUT-46 removeu o gate"* — o oposto do que ela fez.
# O limite REAL da funcao e o proximo `def` de topo, e o range do awk ja o conhece;
# o `head` era uma segunda opiniao sobre onde a funcao acaba, e as duas divergiram.
CORPO="$(awk '/def _verify_approver|required_abac: tuple/,/^def [a-z_]+\(/' "$MAIN")"
if [ -z "$CORPO" ]; then
  inc "nao consegui isolar o verificador — a funcao mudou de forma?"
  info "Verde aqui seria vacuo: o grep nao teria olhado nada."
else
  # ⚠️ `grep -v '^\s*#'` NAO e detalhe: sem ele o padrao casa o COMENTARIO que
  # documenta a remocao, e o gate reprova o proprio conserto. Terceira vez neste arco
  # que um grep confunde codigo com comentario.
  CODIGO="$(printf '%s' "$CORPO" | grep -v '^[[:space:]]*#')"
  if printf '%s' "$CODIGO" | grep -qE 'is_elevated|"admin" in roles|"supervisor" in roles'; then
    bad "o bypass de papel voltou:"
    printf '%s' "$CODIGO" | grep -nE 'is_elevated|"admin" in roles|"supervisor" in roles' \
      | sed 's/^/                 /'
    info "Numa rota que decide APROVACAO, papel elevado pulava ABAC E recorte de pool."
  else
    ok "nenhuma decisao por papel no verificador"
  fi
  # testemunha de presenca: o gate ABAC continua la
  if printf '%s' "$CODIGO" | grep -q 'abac_can('; then
    ok "o gate por GRANT (\`abac_can\`) continua no caminho"
  else
    bad "o verificador nao chama mais \`abac_can\` — o portao sumiu, nao mudou"
  fi
fi

# ── S2/S3 — comportamental, se houver tarefa suspensa ───────────────────────
sec "S2/S3 - quem tem o grant aprova; quem nao tem, nao"
TOKEN_APROVACAO="${RESUME_TOKEN:-}"
nao_exercido=0
if [ -z "$TOKEN_APROVACAO" ]; then
  # NAO conta como falha — e cobertura DECLARADA, igual aos assistidos do manifesto.
  # Marcar falha aqui deixaria o gate vermelho para sempre por nao-defeito, e um gate
  # assim ensina a ignorar o vermelho. Sair VERDE afirmando que mediu seria pior.
  nao_exercido=1
  printf '  \033[33mNAO EXERCIDO\033[0m sem `RESUME_TOKEN` de uma aprovacao SUSPENSA\n'
  info "Token inexistente devolve 404 para todo mundo, e ler isso como 'recusou o"
  info "nao-autorizado' seria medir o proprio 404, nao o portao."
  info ""
  info "Para exercer: suspenda uma aprovacao e rode"
  info "  RESUME_TOKEN=<token> bash infra/test/probe_resume_approver_authz.sh"
else
  CG="${CG:-http://localhost:8010}"
  tenta() {  # $1 = email, $2 = senha -> status
    local t
    t="$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
         -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
         | jq -r '.access_token // empty')"
    [ -z "$t" ] && { printf 'LOGIN_FALHOU'; return; }
    curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST \
      "$CG/v1/channels/webhook/resume/$TOKEN_APROVACAO" \
      -H "Authorization: Bearer $t" -H 'content-type: application/json' \
      -d "{\"tenant_id\":\"$TENANT\",\"payload\":{}}"
  }
  # ⚠️ O supervisor TEM `approvals.decide` desde a MOD-08/G1b — o 403 que se espera
  # dele aqui e por ESCOPO (ele nao alcanca o pool da tarefa), nao por capacidade.
  # A distincao importa: se um dia ele ganhar o pool, este ramo passa a reprovar sem
  # defeito, e a mensagem abaixo e o que dira isso a quem o encontrar.
  C_SUP="$(tenta supervisor@plughub.local changeme_supervisor)"
  if [ "$C_SUP" = "403" ]; then
    ok "S2 supervisor recusado (403) — capacidade ele tem; o que falta e o POOL"
  else
    bad "S2 supervisor aceito (HTTP $C_SUP) — confira se ele passou a alcancar o pool desta tarefa"
  fi
  C_ADM="$(tenta admin@plughub.local changeme_admin)"
  case "$C_ADM" in
    2*) ok "S3 admin (com o grant, e sem recorte) aprovou (HTTP $C_ADM)" ;;
    403) bad "S3 o admin foi BARRADO (403) — some a capacidade de aprovar"
         info "Regressao na direcao cara: o sintoma na tela e 'o botao nao funciona'." ;;
    *)   inc "S3 admin devolveu HTTP $C_ADM (nem 2xx nem 403)" ;;
  esac
fi

printf '\n'
if [ "$fail" -eq 0 ] && [ "$nao_exercido" = "1" ]; then
  printf '\033[32mVERDE\033[0m (PARCIAL) - o verificador nao decide por papel.\n'
  printf '            A metade COMPORTAMENTAL (S2/S3) nao foi exercida — ver acima.\n'
elif [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - a aprovacao depende do grant e do recorte, nunca do papel.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
