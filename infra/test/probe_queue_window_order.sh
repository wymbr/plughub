#!/usr/bin/env bash
# probe_queue_window_order.sh — 2026-08-05  (TODO § "Achados de 2026-08-04", item 6)
#
# PERGUNTA (uma só): quando a fila é MAIOR que a janela de leitura, quais itens
# entram na janela — os mais ANTIGOS ou os mais NOVOS?
#
# POR QUE ELA DECIDE SOZINHA, sem tela e sem tráfego real:
#   O score do ZSET de fila tem UM ÚNICO escritor em todo o routing-engine
#   (`add_queued_contact`, registry.py §2509: `zadd(key, {session_id: queued_at_ms})`).
#   É epoch-ms de chegada, sempre. Logo "menor score = mais antigo" é fato do
#   escritor, não convenção de leitor — e a pergunta acima vira aritmética
#   verificável sobre uma fila de composição CONHECIDA.
#
#   O docstring de `add_queued_contact` diz que "queue_scorer may override with
#   priority". Não existe esse caminho: o `queue_scorer` é `score_contact_in_queue`
#   (scorer.py §160), função PURA chamada em `Router.dequeue` sobre o JSON do
#   contato, e ela não escreve no ZSET. Nem poderia: a prioridade depende de
#   `now_ms` (aging + breach crescem com a espera), então um score ARMAZENADO
#   estaria velho no instante seguinte. Prioridade é derivada na leitura, por
#   construção. Este probe não pergunta qual das duas semânticas se quer — o
#   código já respondeu; ele mede a CONSEQUÊNCIA.
#
# DUAS SUPERFÍCIES, mesma premissa falsa ("maior score = maior prioridade"):
#   A) `listQueue`             (mcp-server, work-queue.ts §61) — ZREVRANGE, topN 20.
#      É a inbox pull do Console. Consequência: VISIBILIDADE.
#   B) `get_queued_contacts`   (routing-engine, registry.py §2720) — ZREVRANGE,
#      top_n 10, única consumidora = `Router.dequeue` (router.py §466), que só roda
#      em pool PUSH. Consequência: ATENDIMENTO. É a grave — o `score_contact_in_queue`
#      existe para que "nenhum contato espere para sempre" (aging + breach), e a
#      janela o impede de sequer PONTUAR os contatos que ele existe para proteger.
#
# ─────────────────────────────────────────────────────────────────────────────
# ATENÇÃO — O POOL DE RASCUNHO **NÃO** É INERTE. (achado da 1ª execução, 2026-08-05)
#
#   A 1ª versão semeava com base fixa de nov/2023 e afirmava "blast radius zero".
#   Errado, e caro: o sweep de retenção do routing-engine (main.py §1250) faz
#   `--scan` por `*:pool:*:queue` e **não pergunta se o pool existe no registry**.
#   Sem `pool_config`, o rascunho cai no ramo de FILA MUDA (teto default 1800 s);
#   itens com 2,7 anos de "espera" foram varridos e fechados como
#   `max_wait_exceeded`/`abandoned` via `_emit_queue_timeout`, que **emite segmento
#   sintético ao Kafka**. O preflight de ZCARD pegou a fila mutilada (10 de 25) e
#   barrou a medição — mas os eventos já tinham saído.
#
#   Daí as duas regras que este arquivo agora obedece:
#     1. SCORES RECENTES — os itens ficam dentro da janela de retenção (últimos
#        N segundos), então o sweep não tem o que expirar.
#     2. SEMEADURA ATÔMICA — um único EVAL Lua, em vez de 50 `docker compose exec`
#        sequenciais. Não é otimização: cada segundo de semeadura é uma janela em
#        que o sweep pode agir sobre uma fila pela metade.
#   E a verificação de ZCARD é repetida DEPOIS de medir: se o sweep agiu no meio,
#   a leitura não vale e o veredicto é INCONCLUSIVO, não VERDE.
# ─────────────────────────────────────────────────────────────────────────────
#
# USO:   bash infra/test/probe_queue_window_order.sh
# SAÍDA: 0 = VERDE (janela pega os mais antigos) · 1 = VERMELHO (pega os mais
#        novos = starvation) · 2 = INCONCLUSIVO (leitor não leu / janela não cortou).

set -u   # sem -e de propósito: ramo AUSENTE imprime INCONCLUSIVO, não morre

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
MCP_URL="${MCP_URL:-http://localhost:3100}"

SCRATCH="__zrevprobe_$$"
QKEY="${TENANT}:pool:${SCRATCH}:queue"
N=25                 # itens semeados
LIMIT_A=20           # janela de `listQueue`            (default do Console)
LIMIT_B=10           # janela de `get_queued_contacts`  (default de `Router.dequeue`)

DC="docker compose -f $COMPOSE"
r() { $DC exec -T redis redis-cli "$@" < /dev/null; }

cleanup() {
  r DEL "$QKEY" > /dev/null 2>&1
  for i in $(seq 1 "$N"); do
    r DEL "${TENANT}:queue_contact:$(printf 'zrevprobe-%02d' "$i")" > /dev/null 2>&1
  done
}
trap cleanup EXIT

echo "== probe: qual ponta da fila entra na janela de leitura? (tenant=$TENANT) =="
echo "   pool de rascunho: ${SCRATCH}"
echo "   (o sweep de retenção ENXERGA este pool — por isso os scores são recentes)"
echo

# ── Preflight 1: PROVAR QUE O LEITOR LÊ ──────────────────────────────────────
PING="$(r PING)"
if [ "$PING" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING (obtido: '$PING')."
  echo "  compose usado: $COMPOSE  (rodar da RAIZ do repo, ou COMPOSE_FILE=/caminho/…)"
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê; nada abaixo vale."
  exit 2
fi

# ── Semeadura ATÔMICA: N itens, score ASCENDENTE, identidade no nome ────────
# `zrevprobe-01` é o MAIS ANTIGO (menor score) e `zrevprobe-25` o MAIS NOVO. O
# número no id É a ordem de chegada, então nenhum passo abaixo precisa de tabela
# de tradução: ler o id já diz de que ponta ele veio.
#
# O JSON é obrigatório: `get_queued_contacts` PULA membro sem JSON
# (`if not raw: continue`), então semear só o ZSET devolveria lista VAZIA — e
# vazio passaria por "janela não corta", que é o veredicto OPOSTO.
NOW_MS="$(( $(date +%s) * 1000 ))"
BASE="$(( NOW_MS - N * 1000 ))"    # item 01 = N s atrás · item N = agora

LUA="$(cat <<'LUA_EOF'
local n    = tonumber(ARGV[1])
local base = tonumber(ARGV[2])
local pfx  = ARGV[3]
local pool = ARGV[4]
local ten  = ARGV[5]
redis.call('DEL', KEYS[1])
for i = 1, n do
  local sid   = string.format('zrevprobe-%02d', i)
  local score = base + i * 1000
  redis.call('ZADD', KEYS[1], score, sid)
  redis.call('SET', pfx .. sid, string.format(
    '{"session_id":"%s","tenant_id":"%s","pool_id":"%s","tier":"standard","queued_at_ms":%d,"requirements":{}}',
    sid, ten, pool, score), 'EX', 900)
end
redis.call('EXPIRE', KEYS[1], 900)
return redis.call('ZCARD', KEYS[1])
LUA_EOF
)"

SEEDED="$(r EVAL "$LUA" 1 "$QKEY" "$N" "$BASE" "${TENANT}:queue_contact:" "$SCRATCH" "$TENANT")"

# ── Preflight 2: PROVAR QUE A FILA FICOU DO TAMANHO QUE EU DIGO ─────────────
# Sem isto, "faltam 5 itens na resposta" é indistinguível de "só semeei 20".
if [ "$SEEDED" != "$N" ]; then
  echo "PREFLIGHT FALHOU: o EVAL de semeadura devolveu ZCARD '$SEEDED', esperado $N."
  echo "VEREDICTO: INCONCLUSIVO — sem a fila conhecida, nenhuma ausência prova nada."
  exit 2
fi
echo "preflight: fila semeada ATOMICAMENTE com $SEEDED itens (score ascendente, 01 = mais antigo)"
echo "           idades: $((N)) s (item 01) até 1 s (item $N) — dentro da retenção"

# ── Controle: qual ponta é qual, lido do próprio Redis ──────────────────────
ENDS_ASC="$(r ZRANGE  "$QKEY" 0 0)"
ENDS_DESC="$(r ZREVRANGE "$QKEY" 0 0)"
echo "           ZRANGE 0 0    (menor score) = ${ENDS_ASC}   <- o mais ANTIGO"
echo "           ZREVRANGE 0 0 (maior score) = ${ENDS_DESC}   <- o mais NOVO"
echo

# ── Previsão, impressa ANTES dos números ────────────────────────────────────
echo "── previsão (escrita antes de medir) ──"
echo "   Se a janela usa ZREVRANGE, ela pega os mais NOVOS:"
echo "     A) listQueue(top_n=${LIMIT_A})            -> $(printf 'zrevprobe-%02d' $((N - LIMIT_A + 1)))..zrevprobe-${N}; ausentes 01..$(printf '%02d' $((N - LIMIT_A)))"
echo "     B) get_queued_contacts(top_n=${LIMIT_B})  -> $(printf 'zrevprobe-%02d' $((N - LIMIT_B + 1)))..zrevprobe-${N}; ausentes 01..$(printf '%02d' $((N - LIMIT_B)))"
echo "   Se usasse a ponta certa, seriam 01..$(printf '%02d' "$LIMIT_A") e 01..$(printf '%02d' "$LIMIT_B")."
echo

# ── Veredicto de uma superfície ─────────────────────────────────────────────
# Ramifica sobre o VALOR medido, em três estados. Cada ramo nomeia o que viu.
# $1 = rótulo · $2 = limite da janela · $3 = ids devolvidos (um por linha)
#
# COMPARAÇÃO DIRETA DE CONJUNTO, não contagem de "quantos antigos faltam".
# A 1ª versão contava ausências nas duas pontas e exigia "nenhum novo presente"
# para dar VERDE. Impossível: com N=25 e janela 20, a janela CERTA (01..20) e a
# ERRADA (06..25) compartilham 15 itens — as duas pontas só são disjuntas quando
# a janela é menor que metade da fila. Resultado: o probe sabia reprovar mas não
# sabia aprovar, e devolveu INCONCLUSIVO sobre a saída correta.
#
# *Um veredicto que só pode acertar num sentido é a versão de três estados do
# teste que não pode reprovar — e passa despercebido porque o estado inútil
# (INCONCLUSIVO) parece prudência.*
verdict_for() {
  local LABEL="$1" LIMIT="$2" GOT="$3"
  local n_got want_old want_new i
  n_got="$(printf '%s\n' "$GOT" | grep -c . )"

  if [ "$n_got" -eq 0 ]; then
    echo "   [INCONCLUSIVO] ${LABEL}: nenhum id devolvido — o leitor não leu esta fila."
    echo "                  (0 é indistinguível de 'janela vazia'; não é VERDE nem VERMELHO)"
    return 2
  fi
  if [ "$n_got" -eq "$N" ]; then
    echo "   [INCONCLUSIVO] ${LABEL}: vieram os $N — a janela não cortou nada."
    echo "                  Refazer com N > limite real; sem corte não há ponta a escolher."
    return 2
  fi

  # Os dois conjuntos ESPERADOS, montados por extenso e comparados como texto.
  want_old=""; for i in $(seq 1 "$LIMIT");             do want_old+="$(printf 'zrevprobe-%02d\n' "$i")"$'\n'; done
  want_new=""; for i in $(seq $((N - LIMIT + 1)) "$N"); do want_new+="$(printf 'zrevprobe-%02d\n' "$i")"$'\n'; done

  if [ "$(printf '%s\n' "$GOT")" = "$(printf '%s' "$want_old")" ]; then
    echo "   [VERDE]        ${LABEL}: devolveu ${n_got} itens = os ${LIMIT} MAIS ANTIGOS (01..$(printf '%02d' "$LIMIT"))."
    return 0
  fi
  if [ "$(printf '%s\n' "$GOT")" = "$(printf '%s' "$want_new")" ]; then
    echo "   [VERMELHO]     ${LABEL}: devolveu ${n_got} itens = os ${LIMIT} MAIS NOVOS."
    echo "                  Os $((N - LIMIT)) mais antigos (01..$(printf '%02d' $((N - LIMIT)))) NÃO entraram na janela."
    return 1
  fi
  echo "   [INCONCLUSIVO] ${LABEL}: ${n_got} itens, e o conjunto não é nem os ${LIMIT} mais"
  echo "                  antigos nem os ${LIMIT} mais novos. Ler os ids abaixo."
  return 2
}

RC_A=2; RC_B=2

# ── Medição A — a rota HTTP que a inbox do Console chama de verdade ─────────
echo "── A) GET /api/work_queue/list  (mcp-server → listQueue, ZREVRANGE, topN ${LIMIT_A}) ──"
RESP_A="$(curl -s --max-time 10 "${MCP_URL}/api/work_queue/list?tenant_id=${TENANT}&pools=${SCRATCH}" 2>/dev/null)"
if [ -z "$RESP_A" ]; then
  echo "   [INCONCLUSIVO] sem resposta de ${MCP_URL} — mcp-server-plughub no ar?"
else
  GOT_A="$(printf '%s' "$RESP_A" | grep -o 'zrevprobe-[0-9][0-9]' | sort -u)"
  verdict_for "listQueue" "$LIMIT_A" "$GOT_A"; RC_A=$?
  echo "   ids: $(printf '%s' "$GOT_A" | tr '\n' ' ')"
fi
echo

# ── Medição B — a função real do árbitro, contra o Redis real ──────────────
# Não é mock: importa `InstanceRegistry` do serviço e chama o método que
# `Router.dequeue` chama, com a MESMA config (`get_settings()` lê PLUGHUB_REDIS_URL).
# A URL resolvida é IMPRESSA: ler o env errado devolveria lista vazia, e vazio já
# é tratado como INCONCLUSIVO acima — mas o valor na tela torna o erro visível em
# vez de inferido.
echo "── B) InstanceRegistry.get_queued_contacts  (routing-engine, ZREVRANGE, top_n ${LIMIT_B}) ──"
PYOUT="$($DC exec -T routing-engine python - <<PY 2>&1
import asyncio
import redis.asyncio as aioredis
from plughub_routing.config   import get_settings
from plughub_routing.registry import InstanceRegistry

async def main():
    s = get_settings()
    print("redis_url=" + s.redis_url)
    reg = InstanceRegistry(aioredis.from_url(s.redis_url, decode_responses=True))
    got = await reg.get_queued_contacts("${TENANT}", "${SCRATCH}", ${LIMIT_B})
    for c in got:
        print("ID " + c.session_id)

asyncio.run(main())
PY
)"
echo "   $(printf '%s\n' "$PYOUT" | grep '^redis_url=' || echo 'redis_url=??? (não impresso — ver saída crua)')"
GOT_B="$(printf '%s\n' "$PYOUT" | sed -n 's/^ID //p' | sort -u)"
if [ -z "$GOT_B" ]; then
  echo "   saída crua do container:"
  printf '%s\n' "$PYOUT" | sed 's/^/     /'
fi
verdict_for "get_queued_contacts" "$LIMIT_B" "$GOT_B"; RC_B=$?
echo "   ids: $(printf '%s' "$GOT_B" | tr '\n' ' ')"
echo

# ── Preflight 3 (PÓS-medição): a fila continuou inteira? ────────────────────
# O sweep de retenção roda em paralelo. Se ele tivesse comido itens no meio das
# leituras, "faltam os antigos" seria obra dele, não da janela — e o VERMELHO
# acima seria atribuído ao réu errado. Este é o controle que separa os dois.
CARD_AFTER="$(r ZCARD "$QKEY")"
if [ "$CARD_AFTER" != "$N" ]; then
  echo "== veredicto =="
  echo "INCONCLUSIVO — a fila tinha $N itens antes e $CARD_AFTER depois das leituras."
  echo "  Algo removeu itens durante a medição (candidato: o sweep de retenção)."
  echo "  Qualquer ausência observada acima pode ser dele, não da janela de leitura."
  exit 2
fi
echo "pós-checagem: fila ainda com $CARD_AFTER itens — nada foi removido durante a medição"
echo

# ── Veredicto ───────────────────────────────────────────────────────────────
echo "== veredicto =="
if [ "$RC_A" -eq 1 ] || [ "$RC_B" -eq 1 ]; then
  echo "VERMELHO — a janela de leitura pega a ponta ERRADA do ZSET."
  echo
  echo "  O que isto afirma: numa fila maior que a janela, os contatos mais antigos"
  echo "  não entram na seleção. Em (B) eles nunca chegam a ser pontuados pelo"
  echo "  \`score_contact_in_queue\` — o aging e o breach, que existem para que nenhum"
  echo "  contato espere para sempre, ficam inertes exatamente para quem mais espera."
  echo "  Em (A) eles não aparecem na inbox: a lista parece ordenada por idade"
  echo "  (o Console ordena o que RECEBEU) e está apenas sem os mais velhos."
  echo
  echo "  O que NÃO afirma: nada sobre filas <= janela. Abaixo do limite tudo carrega"
  echo "  e a re-ordenação em Python corrige — foi por isso que nunca mordeu no demo."
  exit 1
fi
if [ "$RC_A" -eq 0 ] && [ "$RC_B" -eq 0 ]; then
  echo "VERDE — as duas janelas carregam os itens MAIS ANTIGOS da fila."
  exit 0
fi
echo "INCONCLUSIVO — pelo menos uma superfície não produziu leitura utilizável."
echo "  Um VERDE aqui seria ausência de medição travestida de aprovação."
exit 2
