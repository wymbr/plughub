#!/usr/bin/env bash
# seed_withheld_demo.sh — injeta o discriminador de retenção e NÃO limpa.
#
# Irmão do `probe_context_withheld.sh`, com o trap de limpeza removido de
# propósito: o probe prova o CONTRATO (e apaga o que sujou); este existe para
# **olhar a tela**, que precisa do dado vivo enquanto você navega.
#
# ⚠️ POR QUE VOCÊ PRECISA ESTAR LOGADO COMO `operator` ────────────────────────
#
# `admin` está em `supervisor_roles` (masking.context_rules): pula o portão de
# namespace E não casa regra de papel `operator`. Para o admin **nada é retido**,
# e a faixa não renderiza — corretamente. Comparar duas telas de admin não julga
# nada; é a armadilha registrada no `TODO.md` e no ADR §5.
#
# ⚠️ A INJEÇÃO NÃO CHEGA SOZINHA À TELA JÁ ABERTA (medido 2026-08-26) ─────────
#
# O `HSET` aqui é FORA DE BANDA: escreve direto no Redis sem publicar
# `supervisor_state.updated`, que é o evento que faz a aba refazer o fetch. Numa
# Console já carregada, a faixa só aparece depois de um **refresh**. Não é defeito
# da V1 — é propriedade deste atalho de teste, e o caminho real (agente/tool
# escrevendo no ctx) publica o evento.
#
# ⚠️ Refresh com o operator logado devolve o contato a ele mesmo — então o refresh
# é seguro para este teste, mas não o faça esperando ver o contato mudar de dono.
#
# Uso:  bash infra/test/seed_withheld_demo.sh <session_id>
#       bash infra/test/seed_withheld_demo.sh <session_id> --clean   (remove)
#       (sem argumento, lista as sessões com ContextStore vivo)
set -u

DC=${DC:-docker compose -f docker-compose.demo.yml}
TENANT=${TENANT:-tenant_demo}

redis() { $DC exec -T redis redis-cli "$@"; }

SID=${1:-}
if [ -z "$SID" ]; then
  echo "uso: $0 <session_id> [--clean]"
  echo
  echo "sessões com ContextStore vivo:"
  redis --raw KEYS "$TENANT:ctx:*" | grep -v ':ctx:journey:' | sed "s/^$TENANT:ctx://" | head -20
  exit 2
fi

CTX="$TENANT:ctx:$SID"
TAG_GATE="caller.nome"
TAG_RULE="session.demo.resume_token"

if [ "${2:-}" = "--clean" ]; then
  redis HDEL "$CTX" "$TAG_GATE" "$TAG_RULE" >/dev/null
  echo "removidos de $CTX: $TAG_GATE · $TAG_RULE"
  exit 0
fi

if [ "$(redis --raw EXISTS "$CTX")" != "1" ]; then
  echo "INCONCLUSIVO: $CTX não existe (a sessão está viva?)"
  exit 2
fi

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkentry() { printf '{"value":"%s","confidence":1.0,"source":"seed_withheld_demo","visibility":"agents_only","updated_at":"%s"}' "$1" "$NOW"; }

redis HSET "$CTX" "$TAG_GATE" "$(mkentry 'Maria Demo')"      >/dev/null
redis HSET "$CTX" "$TAG_RULE" "$(mkentry 'tok_demo_segredo')" >/dev/null

cat <<EOF
injetado em $CTX (NÃO será limpo automaticamente):
  $TAG_GATE          → ns fora do default  ⇒ faixa "Por visibilidade do pool"
  $TAG_RULE  → casa \`*.resume_token\`     ⇒ faixa "Por regra de mascaramento"

Agora, no navegador:
  1. Entre como operator@plughub.local (o Console só lista contatos DELE — não dá
     para "abrir" um contato que está com outro agente)
  2. Com o contato $SID já atendido por ele, vá na aba Context
  3. **DÊ REFRESH** — o HSET é fora de banda e não acorda a tela já carregada
  4. Esperado: faixa "2 campo(s) oculto(s) por política", com as duas causas separadas,
     e o cabeçalho continuando a contar só os campos VISÍVEIS (dois números, dois
     significados — nunca um contador inflado)
  5. Como admin, a MESMA tela não mostra faixa nenhuma — é o discriminador, não um bug

Para limpar:  bash $0 $SID --clean
EOF
