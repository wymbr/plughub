#!/usr/bin/env bash
#
# probe_authz_single_verifier.sh — quantas implementações de "verificar JWT de usuário
# + ler `module_config`" existem, e se as novas usam o verificador canônico.
#
# POR QUE ESTE PROBE EXISTE
# =========================
# Em 2026-08-27, antes de fechar as portas de escrita da calendar-api e da dialog-api,
# contou-se quantas cópias independentes desse verificador já havia: **seis**. E elas
# JÁ HAVIAM DIVERGIDO, em seis pontos medidos (biblioteca, ordem de acesso,
# `module_config` vazio, `min_access` desconhecido, código de recusa, segredo ausente).
# A tabela completa está no cabeçalho de `packages/py-authz/src/plughub_authz/__init__.py`.
#
# O agravante é o que dá nome ao instrumento: `channel-gateway/auth.py` PROMETE no
# docstring ser o ponto compartilhado — *"outros módulos devem reusar estas funções em
# vez de reimplementar"* — e cinco serviços reimplementaram. Promessa sem mecanismo é a
# mesma família do DDL de `participation_intervals`, que afirmava em prosa a ordenação
# que nenhum produtor impunha. Este probe é o MECANISMO.
#
# O QUE ELE PODE REPROVAR
# =======================
#   C1  aparecer uma implementação NOVA (a sétima)                    → VERMELHO
#       É a única coisa que o probe realmente protege. Ele NÃO exige que as seis
#       existentes migrem — migrar seis serviços com posturas deliberadamente
#       diferentes é decisão de arco próprio, registrada em `TODO.md`.
#   C2  calendar-api ou dialog-api deixarem de importar `plughub_authz` → VERMELHO
#       (seria a cópia entrando pela porta dos fundos, nos dois casos em que já se
#       decidiu que não entra)
#   C3  o pacote canônico sumir do Dockerfile de um consumidor         → VERMELHO
#       O import passa no `grep` e falha no CONTAINER — "existe" ≠ "está pronto".
#
# A linha de base é uma LISTA NOMEADA, não um número. Um contador sozinho não sabe
# dizer se a sétima entrou e uma das seis saiu no mesmo commit; a lista sabe.
#
# Uso:  bash infra/test/probe_authz_single_verifier.sh
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 1

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
info() { echo "    $*"; }

# ── linha de base, medida em 2026-08-27 ───────────────────────────────────────
# Critério de "é uma implementação": o arquivo lê `module_config` E decodifica/verifica
# um JWT por conta própria (PyJWT, python-jose, ou HMAC em stdlib). Deliberadamente NÃO
# conta quem só CONSOME claims já decodificados por outro — esses não podem divergir.
BASELINE="packages/analytics-api/src/plughub_analytics_api/audit.py
packages/auth-api/src/plughub_auth_api/jwt_utils.py
packages/auth-api/src/plughub_auth_api/router.py
packages/channel-gateway/src/plughub_channel_gateway/auth.py
packages/config-api/src/plughub_config_api/router.py
packages/evaluation-api/src/plughub_evaluation_api/router.py
packages/pricing-api/src/plughub_pricing_api/router.py"

echo "${BLD}C1 — implementações independentes do verificador${RST}"

FOUND=$(
  grep -rl "module_config" --include='*.py' packages/*/src 2>/dev/null \
    | grep -v '/tests/' | grep -v 'py-authz' \
    | while read -r f; do
        # `^[^#]*` exclui COMENTÁRIOS sem precisar de pipe — três vezes neste arco um
        # grep confundiu código com comentário (o `roles:` do Sidebar, o `strict` de um
        # gate, o `is_elevated` da própria linha que documentava sua remoção).
        #
        # ⚠️ A v1 fazia `sed 's/#.*//' "$f" | grep -qE …` e SUBCONTAVA, de forma NÃO
        # DETERMINÍSTICA: com `set -o pipefail`, o `grep -q` sai no primeiro casamento,
        # o `sed` morre de SIGPIPE (141) e o `if` lê a falha do pipeline como "não
        # casou". Quanto MAIOR o arquivo, mais provável a corrida — então justamente os
        # dois routers grandes (auth-api, evaluation-api) sumiam da conta, e duas
        # execuções seguidas deram 6 e 5. Um contador que perde linha sozinho é pior que
        # contador nenhum: ele deixaria a sétima cópia entrar debaixo do verde.
        if grep -qE '^[^#]*(jwt\.decode|pyjwt\.decode|hmac\.new|from jose)' "$f"; then
          echo "$f"
        fi
      done | sort
)

BASE_SORTED=$(echo "$BASELINE" | sort)
NOVOS=$(comm -13 <(echo "$BASE_SORTED") <(echo "$FOUND"))
SUMIRAM=$(comm -23 <(echo "$BASE_SORTED") <(echo "$FOUND"))

N=$(echo "$FOUND" | grep -c . )
info "encontradas: $N (linha de base: 7 arquivos em 6 serviços)"
echo "$FOUND" | while read -r f; do [ -n "$f" ] && info "  · $f"; done

if [ -n "$NOVOS" ]; then
  bad "implementação NOVA — copiar em vez de usar packages/py-authz:"
  echo "$NOVOS" | while read -r f; do [ -n "$f" ] && info "  + $f"; done
  info "Se a cópia é deliberada, a linha de base deste probe tem de mudar JUNTO,"
  info "com o motivo — senão a sétima divergência nasce muda como as seis anteriores."
else
  ok "nenhuma implementação nova além da linha de base"
fi

if [ -n "$SUMIRAM" ]; then
  # Não é falha: sumir é o alvo. Mas tem de aparecer, para o probe não ficar
  # protegendo uma linha de base que já não descreve o repositório.
  echo "  ${YLW}·${RST} migradas/removidas desde a linha de base (atualize-a):"
  echo "$SUMIRAM" | while read -r f; do [ -n "$f" ] && info "  − $f"; done
fi

# ── C2/C3 — os dois consumidores novos usam o canônico, e no container ────────
echo
echo "${BLD}C2 — calendar-api e dialog-api usam o verificador canônico${RST}"
for svc in calendar-api dialog-api; do
  pkg="plughub_$(echo "$svc" | tr '-' '_')"
  if grep -rq "from plughub_authz import" "packages/$svc/src/$pkg/" 2>/dev/null; then
    ok "$svc importa plughub_authz"
  else
    bad "$svc NÃO importa plughub_authz — cópia entrando pela porta dos fundos"
  fi
done

echo
echo "${BLD}C3 — o pacote viaja no container (import que passa no grep e falha no runtime)${RST}"
for svc in calendar-api dialog-api; do
  if grep -q "packages/py-authz" "packages/$svc/Dockerfile" 2>/dev/null; then
    ok "$svc: Dockerfile instala packages/py-authz"
  else
    bad "$svc: Dockerfile NÃO instala packages/py-authz — a imagem sobe sem a dependência"
  fi
done

echo
if [ "$FAIL" -gt 0 ]; then echo "${RED}${BLD}GATE VERMELHO${RST} — $FAIL falha(s)"; exit 1; fi
echo "${GRN}${BLD}GATE VERDE${RST} — sem cópia nova; os dois consumidores usam o canônico"
