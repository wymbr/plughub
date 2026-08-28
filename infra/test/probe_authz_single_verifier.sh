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
# O agravante é o que dá nome ao instrumento: `channel-gateway/auth.py` PROMETIA no
# docstring ser o ponto compartilhado — *"outros módulos devem reusar estas funções em
# vez de reimplementar"* — e cinco serviços reimplementaram. Promessa sem mecanismo é a
# mesma família do DDL de `participation_intervals`, que afirmava em prosa a ordenação
# que nenhum produtor impunha. Este probe é o MECANISMO.
#
# (Passado desde 2026-08-28: aquele arquivo migrou no passo 3 e hoje é camada fina.
# O verbo mudou de tempo aqui de propósito — um instrumento que descreve um repositório
# que já não existe é a mesma classe de erro que ele foi criado para pegar.)
#
# O QUE ELE PODE REPROVAR
# =======================
#   C1  aparecer uma implementação NOVA (a sétima)                    → VERMELHO
#       Quando o probe nasceu, ele NÃO exigia que as seis existentes migrassem — era
#       decisão de arco próprio. **Esse arco foi feito em 2026-08-28 (passos 0–6)** e a
#       linha de base caiu de 7 arquivos/6 serviços para **1**, que é o EMISSOR e fica
#       por decisão. Hoje o C1 protege um estado alcançado, não uma dívida.
#   C2  um dos CONSUMIDORES deixar de importar `plughub_authz`         → VERMELHO
#       (a cópia entrando pela porta dos fundos, nos casos em que já se decidiu que
#       não entra). A lista tinha 2 nomes e passou a ter 7 em 2026-08-28.
#   C3  o pacote canônico sumir do Dockerfile de um consumidor         → VERMELHO
#       O import passa no `grep` e falha no CONTAINER — "existe" ≠ "está pronto".
#   C4  reimplementar o resolvedor de ESCOPO de pool                   → VERMELHO
#       Acrescentado em 2026-08-28. Este eixo NÃO era coberto: o C1 conta quem
#       decodifica JWT, e o resolvedor só consome claims já decodificados — por isso
#       as três cópias existentes passavam por baixo dele. Ver `_scope_resolver_census.py`.
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
#
# ⚠️ A linha de base ENCOLHE conforme a migração anda, e cada saída é datada aqui — se
# ela ficasse parada, o probe passaria a proteger um repositório que já não existe.
#   2026-08-28 · saíram `config-api/router.py` e `pricing-api/router.py` (passo 2:
#                ambos passaram a usar `plughub_authz.enforce_write`).
#   2026-08-28 · saiu `channel-gateway/auth.py` (passo 3: `verify_user_jwt`, `abac_can`
#                e `bearer_from_header` migraram; o arquivo virou camada fina com o
#                wrapper de origem do escopo). É o arquivo que PROMETIA no docstring ser
#                o ponto compartilhado — deixou de ser cópia antes de deixar de mentir.
#   2026-08-28 · saiu `analytics-api/audit.py` (passo 4: `_ACCESS_LEVELS`+`_has_abac` —
#                a LISTA indexada, divergência 2 — e o decode local). Compõe os
#                PRIMITIVOS, não `enforce_write`: aqui a recusa grava trilha antes de
#                virar resposta, e um portão que já responde não deixa isso acontecer.
#   2026-08-28 · saiu `auth-api/router.py` (passo 5: `_ACCESS_RANK` — QUARTA cópia da
#                tabela — e `_check_config_field` viraram `abac_can`). Exigiu afinar o
#                critério: ver a nota sobre `from jose` no `grep` abaixo.
#   2026-08-28 · saiu `evaluation-api/router.py` (passo 6, o ÚLTIMO: decode canônico,
#                escopo de pool promovido ao `abac_can` (D2) e o RAMO LEGADO fechado —
#                `module_config` vazio deixou de liberar revisão e contestação).
#
# ✅ **A MIGRAÇÃO ACABOU.** O que sobra na lista não é dívida: é o EMISSOR do token.
# `auth-api/jwt_utils.py` assina com `python-jose` e o canônico verifica com PyJWT;
# quem assina e quem confere têm de ser a mesma biblioteca no seu lado, e trocar o
# emissor por simetria mexeria na assinatura de toda a plataforma para arrumar a
# estética de um import (D4). A linha de base **não deve ir a zero** — se um dia for,
# alguém migrou o emissor sem decidir isso.
BASELINE="packages/auth-api/src/plughub_auth_api/jwt_utils.py"

echo "${BLD}C1 — implementações independentes do verificador${RST}"

FOUND=$(
  grep -rl "module_config" --include='*.py' packages/*/src 2>/dev/null \
    | grep -v '/tests/' | grep -v 'py-authz' \
    | while read -r f; do
        # `^[^#]*` exclui COMENTÁRIOS sem precisar de pipe — três vezes neste arco um
        # grep confundiu código com comentário (o `roles:` do Sidebar, o `strict` de um
        # gate, o `is_elevated` da própria linha que documentava sua remoção).
        #
        # ⚠️ LIMITE CONHECIDO (medido no passo 4): ele exclui comentário `#`, **não
        # DOCSTRING**. Um arquivo migrado que descreva em prosa o `pyjwt.decode` que
        # deixou de ter volta a casar aqui — e só não vira falso positivo porque o
        # primeiro filtro exige `module_config`, que sai junto. É armadilha latente,
        # registrada em vez de remendada: excluir docstring exige parser, e o C4 já
        # mostra que a hora de trocar `grep` por AST é quando o falso positivo aparece,
        # não antes.
        #
        # ⚠️ A v1 fazia `sed 's/#.*//' "$f" | grep -qE …` e SUBCONTAVA, de forma NÃO
        # DETERMINÍSTICA: com `set -o pipefail`, o `grep -q` sai no primeiro casamento,
        # o `sed` morre de SIGPIPE (141) e o `if` lê a falha do pipeline como "não
        # casou". Quanto MAIOR o arquivo, mais provável a corrida — então justamente os
        # dois routers grandes (auth-api, evaluation-api) sumiam da conta, e duas
        # execuções seguidas deram 6 e 5. Um contador que perde linha sozinho é pior que
        # contador nenhum: ele deixaria a sétima cópia entrar debaixo do verde.
        # ⚠️ `from jose import .*jwt`, NÃO `from jose` (afinado no passo 5).
        # `auth-api/router.py` importa `JWTError` — o TIPO DE EXCEÇÃO que o emissor
        # levanta — e o critério largo o contava como decodificador. Depois que a parte
        # ABAC dele migrou, ele teria ficado na linha de base pelo motivo ERRADO: um
        # `import` de exceção não pode divergir de nada. É o mesmo defeito que o C4
        # corrigiu do outro lado — instrumento medindo a proposição ADJACENTE.
        # A precisão é falseável: `jwt_utils.py` faz `from jose import JWTError, jwt` e
        # CONTINUA sendo contado, que é o controle positivo desta linha.
        if grep -qE '^[^#]*(jwt\.decode|pyjwt\.decode|hmac\.new|from jose import .*jwt)' "$f"; then
          echo "$f"
        fi
      done | sort
)

BASE_SORTED=$(echo "$BASELINE" | sort)
NOVOS=$(comm -13 <(echo "$BASE_SORTED") <(echo "$FOUND"))
SUMIRAM=$(comm -23 <(echo "$BASE_SORTED") <(echo "$FOUND"))

N=$(echo "$FOUND" | grep -c . )
info "encontradas: $N (linha de base: 1 — o EMISSOR, que fica; eram 7 em 6 antes do passo 2)"
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

# ── C2/C3 — os consumidores usam o canônico, e no container ───────────────────
#
# A lista cresceu em 2026-08-28: aos dois originais somaram-se os três que passaram a
# consumir o resolvedor de ESCOPO (ver C4), os dois do passo 2 (config-api, pricing-api,
# que migraram o portão de escrita inteiro) e o auth-api no passo 5. Uma vez que um
# serviço entra aqui, sair é regressão — por isso a lista é nomeada, não contada.
#
# O auth-api só pôde entrar depois que o build context dele virou a RAIZ do monorepo
# (era `packages/auth-api`, e o `packages/py-authz` ficava fora do contexto). Essa
# mudança não precisa de critério próprio: se alguém revertê-la, o `COPY` do Dockerfile
# falha no build — recusa alta, não degradação.
CONSUMIDORES="calendar-api dialog-api analytics-api channel-gateway evaluation-api config-api pricing-api auth-api"

echo
echo "${BLD}C2 — os consumidores usam o verificador canônico${RST}"
for svc in $CONSUMIDORES; do
  pkg="plughub_$(echo "$svc" | tr '-' '_')"
  if grep -rq "from plughub_authz import" "packages/$svc/src/$pkg/" 2>/dev/null; then
    ok "$svc importa plughub_authz"
  else
    bad "$svc NÃO importa plughub_authz — cópia entrando pela porta dos fundos"
  fi
done

echo
echo "${BLD}C3 — o pacote viaja no container (import que passa no grep e falha no runtime)${RST}"
for svc in $CONSUMIDORES; do
  if grep -q "packages/py-authz" "packages/$svc/Dockerfile" 2>/dev/null; then
    ok "$svc: Dockerfile instala packages/py-authz"
  else
    bad "$svc: Dockerfile NÃO instala packages/py-authz — a imagem sobe sem a dependência"
  fi
done

# ── C4 — o SEGUNDO verificador: resolvedor de escopo de pool ──────────────────
#
# Este eixo não tinha mecanismo nenhum até 2026-08-28. O C1 acima conta quem
# DECODIFICA JWT e lê `module_config`; o resolvedor de escopo só consome claims já
# decodificados, então as TRÊS cópias que existiam (`analytics-api/pool_auth.py`,
# `channel-gateway/auth.py`, `evaluation-api/router.py`) passavam por baixo dele.
#
# O prazo não era estético: o passo 3 do plano de `accessible_pools` inverte o
# significado de `[]` (hoje "todos", depois "nenhum"). Inversão aplicada a duas das três
# cópias é vazamento de escopo que degrada MUDO — ninguém recebe erro, o relatório só
# mostra linhas a mais.
#
# O critério NÃO é um grep, e a razão está no cabeçalho de `_scope_resolver_census.py`:
# um grep por `unrestricted` acusaria SETE funções do auth-api que apenas ESCREVEM o
# claim no token, e `evaluation-api/_compute_result_scope`, que usa o claim noutro eixo
# (pessoa, não pool). Publicar isso seria publicar defeito que não existe.
echo
echo "${BLD}C4 — resolvedor de escopo de pool não reimplementado${RST}"
CENSO="$(python3 infra/test/_scope_resolver_census.py 2>/dev/null)"
if [ -n "$CENSO" ]; then
  bad "resolvedor de escopo REIMPLEMENTADO fora de packages/py-authz:"
  echo "$CENSO" | while read -r l; do [ -n "$l" ] && info "  + $l"; done
  info "Use \`from plughub_authz import resolve_scope, pool_in_scope\`. Se a cópia for"
  info "deliberada, ela tem de virar junto no passo 3 — e ninguém lembra de três lugares."
else
  ok "nenhuma reimplementação (as 3 cópias foram consolidadas em 2026-08-28)"
fi

echo
if [ "$FAIL" -gt 0 ]; then echo "${RED}${BLD}GATE VERMELHO${RST} — $FAIL falha(s)"; exit 1; fi
echo "${GRN}${BLD}GATE VERDE${RST} — sem cópia nova; os 8 consumidores usam o canônico; escopo não reimplementado"
