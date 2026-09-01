#!/usr/bin/env bash
# sweep_ctx_tags.sh — inventário de TAGS do ContextStore vivo, por frequência.
#
# Por que existe (2026-08-26): a F5 transformou o ctx de EFÊMERO (Redis, TTL 24h) em
# DURÁVEL (Postgres). O hash inteiro passou a ser retido, e ninguém o revisou sob
# essa luz — o primeiro olhar no dado gravado já achou um `core.workflow.delegate_resume_token`
# (uma CAPACIDADE) em claro, porque `default_unmatched_operator` é `plain` e não há
# regra para ele.
#
# Este script não julga: ele LISTA. A pergunta que ele serve é *"que tags existem, e
# quais delas não deveriam durar?"* — e responder isso por amostra de uma sessão é
# como a suposição de "uma sessão tem uma passagem pela fila" entrou no repositório.
#
# Colunas: nº de sessões em que a tag aparece · nome da tag.
# `--values <tag>` mostra os valores distintos de UMA tag (para decidir se é segredo).
set -u

DC=${DC:-docker compose -f docker-compose.demo.yml}
TENANT=${TENANT:-tenant_demo}

redis() { $DC exec -T redis redis-cli "$@"; }

MODE=${1:-}
TAG=${2:-}

# Chaves de ctx de SESSÃO (o ctx de PROCESSO é `:ctx:journey:` e vai à parte).
mapfile -t KEYS < <(redis --raw KEYS "$TENANT:ctx:*" | grep -v ':ctx:journey:')
mapfile -t JKEYS < <(redis --raw KEYS "$TENANT:ctx:journey:*")

if [ "${#KEYS[@]}" -eq 0 ]; then
  echo "INCONCLUSIVO: nenhum ctx de sessão vivo no tenant $TENANT"
  echo "  (TTL de 24h — rode depois de exercitar contatos)"
  exit 2
fi

if [ "$MODE" = "--values" ] && [ -n "$TAG" ]; then
  echo "valores distintos de '$TAG' (${#KEYS[@]} sessões):"
  for k in "${KEYS[@]}"; do
    v=$(redis --raw HGET "$k" "$TAG")
    [ -n "$v" ] && echo "  $v"
  done | sort -u
  exit 0
fi

echo "ContextStore vivo: ${#KEYS[@]} sessões · ${#JKEYS[@]} processos · tenant $TENANT"
echo
echo "TAGS DE SESSÃO (nº de sessões · tag):"
for k in "${KEYS[@]}"; do
  redis --raw HKEYS "$k"
done | sort | uniq -c | sort -rn

if [ "${#JKEYS[@]}" -gt 0 ]; then
  echo
  echo "TAGS DE PROCESSO:"
  for k in "${JKEYS[@]}"; do
    redis --raw HKEYS "$k"
  done | sort | uniq -c | sort -rn
fi

echo
echo "Para inspecionar uma tag:  bash $0 --values <tag>"
