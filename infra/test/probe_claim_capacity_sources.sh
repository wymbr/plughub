#!/usr/bin/env bash
# probe_claim_capacity_sources.sh — 2026-08-04
#
# PERGUNTA (uma só): o cliente e o árbitro contam a MESMA capacidade?
#
# O Console decide se o botão "Atender (Pull)" fica habilitado com
#   atCapacity = contacts.size >= (JWT.maxConcurrentSessions ?? 3)
# ou seja, conta CARTÕES na tela. O árbitro (`work_task_claim` → `claim_instance`)
# decide com
#   SCARD {t}:instance:{iid}:sessions  >=  max_concurrent do registro da instância
# ou seja, conta OCUPANTES DO SEMÁFORO — que incluem holds de wrap-up e qualquer
# sessão que ocupe vaga sem ter virado cartão.
#
# São duas fontes diferentes para o mesmo número. Quando discordam, o botão fica
# habilitado e o servidor recusa com `no_capacity` — e a recusa é hoje efêmera.
#
# Este probe NÃO conserta nada. Ele só coloca os dois números lado a lado e
# IDENTIFICA cada ocupante (`SMEMBERS`, não `SCARD`: contar não diz QUAL).
#
# USO:
#   1. logar no Console, ativar o(s) pool(s) de pull, atender/reivindicar o que quiser
#   2. bash infra/test/probe_claim_capacity_sources.sh
#   3. contar os CARTÕES na coluna esquerda do Console e comparar com o que sai aqui
#
# Sem argumento, varre toda instância `human-*`. Com argumento, só a informada:
#   bash infra/test/probe_claim_capacity_sources.sh human-usr_demo_op

set -u   # sem -e de propósito: um ramo AUSENTE deve imprimir INCONCLUSIVO, não morrer

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
FILTER="${1:-}"

# O serviço `redis` do compose não declara `container_name`, então o container real
# carrega o prefixo do projeto (`plughub-redis-1`, etc.). `compose exec` resolve o
# serviço pelo nome e não depende desse prefixo.
r() { docker compose -f "$COMPOSE" exec -T redis redis-cli "$@" < /dev/null; }

echo "== probe: fontes de capacidade (tenant=$TENANT) =="
echo

# ── Preflight: PROVAR QUE O LEITOR LÊ ────────────────────────────────────────
# Duas leituras quebradas são iguais entre si. Antes de comparar qualquer coisa,
# o redis-cli tem que responder a uma chave de valor conhecido.
PING="$(r PING)"
if [ "$PING" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING (obtido: '$PING')."
  echo "  compose usado: $COMPOSE  (rodar da RAIZ do repo, ou COMPOSE_FILE=/caminho/…)"
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê; nada abaixo vale."
  exit 2
fi
echo "preflight: redis-cli responde (PING=PONG)"
echo

# ── Instâncias humanas registradas ───────────────────────────────────────────
# --scan pega tanto `:instance:human-X` quanto `:instance:human-X:sessions`;
# ficamos só com o registro (sem sufixo).
ALL="$(r --scan --pattern "${TENANT}:instance:human-*" | grep -v ':sessions$' | sed "s|^${TENANT}:instance:||" | sort)"

if [ -n "$FILTER" ]; then
  ALL="$(echo "$ALL" | grep -x "$FILTER" || true)"
fi

if [ -z "$ALL" ]; then
  echo "Nenhuma instância humana registrada em ${TENANT}."
  echo "VEREDICTO: INCONCLUSIVO — sem agente logado não há o que comparar."
  echo "           (logar no Console e ativar um pool antes de rodar)"
  exit 2
fi

MISMATCH=0
TOTALINST=0

for IID in $ALL; do
  TOTALINST=$((TOTALINST + 1))
  RAW="$(r GET "${TENANT}:instance:${IID}")"
  # Só um campo numérico é extraído aqui; parser JSON de verdade roda no
  # routing-engine (o container do redis não tem python3).
  MC="$(echo "$RAW" | grep -o '"max_concurrent"[[:space:]]*:[[:space:]]*[0-9]*' | grep -o '[0-9]*$')"

  echo "── instância: ${IID}"
  if [ -z "$RAW" ]; then
    echo "   registro AUSENTE (chave sem valor) — INCONCLUSIVO para esta instância"
    echo
    continue
  fi
  if [ -z "$MC" ]; then
    echo "   max_concurrent ILEGÍVEL no registro — INCONCLUSIVO para esta instância"
    echo "   registro cru: $RAW"
    echo
    continue
  fi
  echo "   max_concurrent (árbitro, do registro): ${MC}"

  # SMEMBERS, não SCARD: quando a identidade importa, contar produz ambiguidade.
  MEMBERS="$(r SMEMBERS "${TENANT}:instance:${IID}:sessions")"
  if [ -z "$MEMBERS" ]; then
    echo "   ocupantes do semáforo: 0 (vazio)"
    echo "   >>> cartões esperados no Console para este agente: 0"
    echo
    continue
  fi

  NOW_MS="$(( $(date +%s) * 1000 ))"
  N=0; NHOLD=0; NSESS=0
  echo "   ocupantes do semáforo:"
  while IFS= read -r M; do
    [ -z "$M" ] && continue
    N=$((N + 1))
    case "$M" in
      __wrapup_hold__::*)
        NHOLD=$((NHOLD + 1))
        ORIG="$(echo "$M" | cut -d: -f5)"
        EXP="$(echo "$M" | awk -F'::' '{print $NF}')"
        STATE="vivo"
        if [ -n "$EXP" ] && [ "$EXP" -lt "$NOW_MS" ] 2>/dev/null; then STATE="EXPIRADO"; fi
        echo "     [hold ] origem=${ORIG:0:8} expira_em=${EXP} (${STATE}) — NÃO vira cartão, por desenho"
        ;;
      *)
        NSESS=$((NSESS + 1))
        SID="$(echo "$M" | awk -F'::' '{print $1}')"
        echo "     [sessão] ${SID}  (deve ter cartão no Console)"
        ;;
    esac
  done <<< "$MEMBERS"

  echo "   ---"
  echo "   ocupação total (árbitro):        ${N} de ${MC}"
  echo "   destes, holds de wrap-up:        ${NHOLD}"
  echo "   destes, sessões (=cartões):      ${NSESS}"
  echo "   >>> COMPARE: o Console deste agente deve mostrar ${NSESS} cartão(ões)."
  if [ "$N" -ge "$MC" ]; then
    echo "   >>> o árbitro já está LOTADO — todo claim novo responde no_capacity."
    if [ "$NSESS" -lt "$MC" ]; then
      MISMATCH=$((MISMATCH + 1))
      echo "   >>> E o cliente NÃO sabe: ${NSESS} cartões < ${MC} ⇒ botão HABILITADO."
    fi
  fi
  echo
done

echo "== veredicto =="
if [ "$TOTALINST" -eq 0 ]; then
  echo "INCONCLUSIVO — nenhuma instância legível."
  exit 2
fi

# CORRIGIDO 2026-08-04 — este bloco imprimia "SEM DIVERGÊNCIA" e o leitor lia VERDE.
# Era um veredicto que NÃO PODE REPROVAR o caso principal: o script só enxerga o lado
# do ÁRBITRO, então `NSESS < MC` compara o árbitro CONSIGO MESMO. Na reprodução do
# achado 2 (3 ocupantes × 0 cartões na tela) ele imprimiu verde justamente onde a
# divergência era total — porque 3 sessões com max 3 não disparam aquela condição.
# O número de CARTÕES é entrada HUMANA que o script não tem; sem ela o resultado é
# INCONCLUSIVO, nunca aprovado. Ver CLAUDE.md § Postura de Engenharia.
if [ "$MISMATCH" -gt 0 ]; then
  echo "DIVERGÊNCIA CONFIRMADA em ${MISMATCH} instância(s), sem precisar da tela:"
  echo "o árbitro está lotado e sobram vagas gastas por não-sessões, então o cliente —"
  echo "que conta cartões — acha que há espaço. Botão habilitado, servidor recusa."
  exit 1
fi

echo "INCONCLUSIVO até você conferir a tela."
echo
echo "  O script lê SÓ o lado do árbitro. Ele NÃO enxerga quantos cartões o Console"
echo "  mostra, e é essa comparação que responde a pergunta. Compare à mão, por"
echo "  instância, o número impresso em '>>> COMPARE:' com a coluna CONTACTS:"
echo
echo "    iguais            → as duas fontes concordam AGORA (não fecha a questão)"
echo "    cartões MENORES   → DIVERGÊNCIA: vaga ocupada por sessão sem cartão"
echo "                        (achado 2) — os ids acima nomeiam os suspeitos"
echo "    cartões MAIORES   → cartão sobrevivendo a vaga já devolvida"
echo
echo "  Vale repetir logo após um wrap-up inline: a janela do hold é curta e é a"
echo "  única ocupação legítima que nunca vira cartão."
