#!/usr/bin/env bash
# probe_release_reclaim_race.sh — 2026-08-05  (TODO § "Achados de 2026-08-04", item 5)
#
# PERGUNTA (duas, e a segunda só faz sentido depois da primeira):
#   M1. Qual a LARGURA da janela entre o release do item e o desmonte da presença
#       do humano pelo bridge?
#   M2. Quando um re-claim cai DENTRO dessa janela, o guard de dedup engole o
#       `conversations.routed` (achado 2 de volta, transitório e numa sessão só)?
#
# POR QUE MEDIR A JANELA, E NÃO O CAMINHO DE USO:
#   A leitura de 2026-08-05 mostrou que NENHUM caminho de UI fecha essa janela
#   hoje. Na MESMA aba, o `refreshSignal` refaz a lista na hora após o release,
#   mas o `autoAttendedRef` já tem o id — a aba não re-reivindica o item que ela
#   devolveu. Em OUTRA aba do mesmo usuário o ref está vazio, mas o gatilho é o
#   poll de 4 s. Acelerador e cruzamento são mutuamente exclusivos, então
#   `auto_attend` **não** é o candidato rápido que o item 5 afirma.
#   Logo, reproduzir "pelo uso" devolveria NÃO-REPRODUZIU — explicado pelos 4 s, e
#   não pela inexistência da corrida. Medição inconclusiva por construção.
#   A pergunta que decide entre *vigiar* e *consertar* é a largura da janela: com
#   ela sabemos que margem existe, e o que a consumiria no futuro (auto-claim
#   server-side, `pollMs` menor, um ref que não guarde).
#
# POR QUE A JANELA É MAIOR DO QUE "propagação Kafka":
#   O bridge tem UM consumidor para os seis tópicos e despacha com
#   `asyncio.create_task(_dispatch(...))` — sem `await`. Isso descarta a ordenação
#   do Kafka INTEIRA (inclusive dentro de uma partição do mesmo tópico): dois
#   eventos viram duas corrotinas concorrentes. A janela é o prólogo do handler de
#   `contact_closed` até o `DEL session:{sid}:human_agent` (main.py §6841), que vem
#   depois do gate de idempotência, do `_publish_lifecycle_end`, do `scard` e do
#   `_has_continuation` — cada um com seus awaits.
#
# PRÉ-REQUISITO QUE O SCRIPT NÃO INVENTA:
#   Um agente humano LOGADO no Console (a instância `human-{userId}` precisa
#   existir no Redis com vaga livre) e ao menos um item claimável na fila do pool.
#   Ambos são CONFERIDOS no preflight — sem eles o veredicto é INCONCLUSIVO, nunca
#   verde.
#
# USO:
#   bash infra/test/probe_release_reclaim_race.sh <pool_id> <instance_id> [rodadas]
#   ex.: bash infra/test/probe_release_reclaim_race.sh formfill_demo human-bef14526 5
#
# SAÍDA: 0 = VERDE (nenhum re-claim engolido) · 1 = VERMELHO (guard engoliu) ·
#        2 = INCONCLUSIVO (faltou item, instância, ou o experimento não disparou).

set -u

# CAP-12 (2026-09-01): `/api/work_queue/{claim,release}` passam a exigir credencial.
# Aqui o shim de `curl` do `_auth.sh` NAO alcanca — as chamadas sao `httpx`, dentro
# de um `python -` que roda no container do routing-engine. Entao o token e obtido
# fora e INTERPOLADO no heredoc (que e nao-citado de proposito, como `${SID}` ja
# demonstra). Sem ele os quatro POSTs voltariam 401 e o probe reportaria
# "claim_failed", que aqui pareceria a corrida NAO acontecendo — um verde falso.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_auth.sh"
_TOK="$(plughub_token)"

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
POOL="${1:-}"
INSTANCE="${2:-}"
ROUNDS="${3:-5}"

DC="docker compose -f $COMPOSE"
r() { $DC exec -T redis redis-cli "$@" < /dev/null; }

if [ -z "$POOL" ] || [ -z "$INSTANCE" ]; then
  echo "USO: bash infra/test/probe_release_reclaim_race.sh <pool_id> <instance_id> [rodadas]"
  echo "  ex.: … formfill_demo human-bef14526 5"
  echo "VEREDICTO: INCONCLUSIVO — sem alvo não há experimento."
  exit 2
fi

echo "== probe: janela release → desmonte de presença, e o guard sob corrida =="
echo "   tenant=$TENANT pool=$POOL instance=$INSTANCE rodadas=$ROUNDS"
echo

# ── Preflight 1: o leitor lê ─────────────────────────────────────────────────
if [ "$(r PING)" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING."
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê; nada abaixo vale."
  exit 2
fi

# ── Preflight 2: a instância existe e tem vaga ──────────────────────────────
# Sem isto, todo claim devolveria `no_capacity`/`instance_not_found` e o script
# sairia "sem engolidas" — VERDE por nada ter acontecido, que é o modo de falha
# que esta suíte existe para não repetir.
INST_RAW="$(r GET "${TENANT}:instance:${INSTANCE}")"
if [ -z "$INST_RAW" ]; then
  echo "PREFLIGHT FALHOU: instância ${INSTANCE} não existe em ${TENANT}:instance:*."
  echo "  Um agente humano precisa estar LOGADO no Console (a instância nasce no login)."
  echo "VEREDICTO: INCONCLUSIVO — sem instância, nenhum claim acontece."
  exit 2
fi
OCC="$(r SCARD "${TENANT}:instance:${INSTANCE}:sessions")"
echo "preflight: instância viva, ocupantes do semáforo = ${OCC}"
echo "           (se já estiver lotada, o claim recusa e o script sai INCONCLUSIVO)"

# ── Preflight 3: há item claimável ──────────────────────────────────────────
SID="$(r ZRANGE "${TENANT}:pool:${POOL}:queue" 0 0)"
if [ -z "$SID" ]; then
  echo "PREFLIGHT FALHOU: fila ${POOL} vazia — nenhum item claimável."
  echo "  Criar um: bash infra/test/smoke_formfill_renderer.sh  (1 por execução)"
  echo "VEREDICTO: INCONCLUSIVO — sem item não há release nem re-claim."
  exit 2
fi
echo "           item alvo: ${SID} (o mais antigo da fila)"
echo

# ── Linha de base do log: CONTAR ANTES, contar depois, usar a diferença ─────
# Recortar é obrigatório (o log tem "Skipping duplicate" de outras sessões e de
# execuções anteriores — e o MESMO item é reusado entre execuções, então filtrar
# por session_id não basta).
#
# *A 1ª versão recortava por `--since "$(date -u +%Y-%m-%dT%H:%M:%S)"`. O timestamp
# saía em UTC e SEM sufixo de fuso, e o Docker lê naive como hora LOCAL: numa
# máquina em UTC-3 o filtro apontava 3 h no FUTURO e nenhuma linha passava. Os dois
# `grep -c` devolviam 0 — e um deles era o contador de "a corrida aconteceu?". O
# veredicto INCONCLUSIVO pegou (por exigir o `Return to queue` como prova de que o
# transporte rodou), mas a lição é a de sempre: **antes de comparar duas leituras,
# provar que o leitor lê.** Diferença de contagem não tem fuso.*
_count_log() { $DC logs orchestrator-bridge 2>/dev/null | grep -c "$1"; }
BASE_TOTAL="$($DC logs orchestrator-bridge 2>/dev/null | grep -c .)"
if [ "$BASE_TOTAL" -eq 0 ]; then
  echo 'PREFLIGHT FALHOU: docker compose logs orchestrator-bridge não devolveu linha alguma.'
  echo "VEREDICTO: INCONCLUSIVO — o leitor de log não lê; a contagem seria 0 por cegueira."
  exit 2
fi
BASE_SWALLOWED="$(_count_log "Skipping duplicate.*${SID}")"
BASE_REQUEUED="$(_count_log "Return to queue.*${SID}")"
echo "preflight: log do bridge legível (${BASE_TOTAL} linhas); linha de base para esta"
echo "           sessão — engolidas=${BASE_SWALLOWED} requeues=${BASE_REQUEUED}"
echo

# ── Experimento: claim → release → (mede janela) → re-claim IMEDIATO ────────
# Tudo dentro de UM container, num único processo, para que o intervalo entre o
# release e o re-claim seja um round-trip HTTP e não o custo de `docker exec`.
# `docker exec` custa centenas de ms — o suficiente para o desmonte terminar e a
# corrida nunca acontecer. Medir de fora mediria o overhead do medidor.
PYOUT="$($DC exec -T routing-engine python - <<PY 2>&1
import asyncio, json, time
import httpx
import redis.asyncio as aioredis
from plughub_routing.config import get_settings

MCP     = "http://mcp-server-plughub:3100"
TENANT  = "${TENANT}"
POOL    = "${POOL}"
INST    = "${INSTANCE}"
SID     = "${SID}"
ROUNDS  = ${ROUNDS}
PRESENCE = f"session:{SID}:human_agent"

async def main():
    s = get_settings()
    print("redis_url=" + s.redis_url)
    rds = aioredis.from_url(s.redis_url, decode_responses=True)
    async with httpx.AsyncClient(
        timeout=10.0,
        headers={"Authorization": "Bearer ${_TOK}"},
    ) as http:
        for n in range(1, ROUNDS + 1):
            # 1. Claim — estabelece a posse e (via bridge) a presença.
            c = await http.post(
                f"{MCP}/api/work_queue/claim/{SID}",
                json={"pool_id": POOL, "instance_id": INST, "conference_id": ""},
            )
            claimed = (c.json() or {}).get("claimed")
            if not claimed:
                print(f"ROUND {n} ABORT claim_failed reason={(c.json() or {}).get('reason')!r}")
                break

            # Espera a presença APARECER — sem isso o release aconteceria antes de
            # haver o que desmontar, e a janela medida seria zero por vacuidade.
            t_wait = time.monotonic()
            while await rds.exists(PRESENCE) == 0:
                if time.monotonic() - t_wait > 5.0:
                    print(f"ROUND {n} ABORT presence_never_appeared")
                    return
                await asyncio.sleep(0.005)

            # 2. Release — o mcp-server devolve ao árbitro e ANUNCIA o contact_closed.
            t0 = time.monotonic()
            rel = await http.post(
                f"{MCP}/api/work_queue/release/{SID}",
                json={"pool_id": POOL, "instance_id": INST},
            )
            t_http = (time.monotonic() - t0) * 1000
            if rel.status_code >= 300:
                print(f"ROUND {n} ABORT release_http={rel.status_code}")
                break

            # 3. M2 — re-claim IMEDIATO, sem esperar nada. É a corrida.
            t1 = time.monotonic()
            still_there = await rds.exists(PRESENCE)   # a presença sobreviveu ao release?
            c2 = await http.post(
                f"{MCP}/api/work_queue/claim/{SID}",
                json={"pool_id": POOL, "instance_id": INST, "conference_id": ""},
            )
            gap = (t1 - t0) * 1000
            j2 = c2.json() or {}

            # 4. M1 — largura da janela: quanto ainda falta para o marcador sumir.
            t2 = time.monotonic()
            while await rds.exists(PRESENCE) == 1:
                if time.monotonic() - t2 > 10.0:
                    break
                await asyncio.sleep(0.002)
            window = (time.monotonic() - t0) * 1000

            print(
                f"ROUND {n} release_http_ms={t_http:.0f} gap_ms={gap:.0f} "
                f"presence_at_reclaim={still_there} window_ms={window:.0f} "
                f"reclaim={j2.get('claimed')} reason={j2.get('reason')!r} sid={SID}"
            )

            # Deixa o item de volta na fila para a próxima rodada.
            if j2.get("claimed"):
                await http.post(
                    f"{MCP}/api/work_queue/release/{SID}",
                    json={"pool_id": POOL, "instance_id": INST},
                )
                await asyncio.sleep(1.5)   # deixa o desmonte terminar antes da próxima

asyncio.run(main())
PY
)"

printf '%s\n' "$PYOUT" | sed 's/^/   /'
echo

# ── Leitura do log do bridge: DELTA sobre a linha de base ───────────────────
# O bridge processa em `create_task`; as últimas linhas podem não ter saído ainda
# quando o experimento termina. Ler agora mediria menos do que aconteceu.
sleep 3
SWALLOWED=$(( $(_count_log "Skipping duplicate.*${SID}") - BASE_SWALLOWED ))
REQUEUED=$((  $(_count_log "Return to queue.*${SID}")    - BASE_REQUEUED  ))
echo "── bridge: guard de dedup nesta sessão (delta sobre a linha de base) ──"
echo "   'Skipping duplicate … ${SID}' : ${SWALLOWED}"
echo "   'Return to queue … ${SID}'    : ${REQUEUED}"
echo

# ── Veredicto ───────────────────────────────────────────────────────────────
echo "== veredicto =="
ROUNDS_RUN="$(printf '%s\n' "$PYOUT" | grep -c '^ROUND [0-9]* release_http_ms')"
if [ "$ROUNDS_RUN" -eq 0 ]; then
  echo "INCONCLUSIVO — nenhuma rodada completou (ver ABORT acima)."
  echo "  Nada foi exercitado; um VERDE aqui seria ausência de medição."
  exit 2
fi
if [ "$REQUEUED" -eq 0 ]; then
  echo "INCONCLUSIVO — ${ROUNDS_RUN} rodada(s) rodaram, mas o bridge não registrou"
  echo "  NENHUM 'Return to queue' para esta sessão. O transporte não chegou lá, então"
  echo "  a corrida não chegou a existir — e 'nenhuma engolida' não diz nada."
  exit 2
fi
if [ "$SWALLOWED" -gt 0 ]; then
  echo "VERMELHO — o guard engoliu ${SWALLOWED} re-claim(s) em ${ROUNDS_RUN} rodada(s)."
  echo "  A janela é ALCANÇÁVEL: um re-claim dentro dela gasta a vaga sem gerar cartão."
  echo "  O conserto NÃO é afrouxar o guard (trocaria um caso mudo por outro, o spam de"
  echo "  participant_joined do drain) — é ordenar o desmonte antes do routed, ou dar ao"
  echo "  routed de CLAIM um discriminador que o drain não tem."
  exit 1
fi
echo "VERDE — ${ROUNDS_RUN} rodada(s), nenhum re-claim engolido."
echo
echo "  O guard ESTAVA em jogo, não isento: o claim manda conference_id=\"\" e o"
echo "  \`work_task_claim\` monta o routed com \`conference_id or None\` (router.py §816),"
echo "  então o routed saiu sem conferência — a condição \`if not conference_id\` do"
echo "  guard (main.py §3517) foi avaliada. Sem esta conferência, um VERDE aqui seria"
echo "  a isenção do guard passando por vitória do desmonte."
echo
echo "  CUIDADO AO LER \`presence_at_reclaim\`: ele é medido quando o re-claim é"
echo "  DISPARADO, e o guard avalia quando o bridge PROCESSA o routed — depois do"
echo "  publish e do fetch do consumidor. \`presence_at_reclaim=1\` com 0 engolidas não"
echo "  é contradição: é a distância entre os dois instantes. O proxy SUPERESTIMA o"
echo "  acerto, e por isso não vale como veredicto — quem decide é a contagem no log."
echo
echo "  A MARGEM REAL não é (window_ms − gap_ms). Os dois eventos atravessam o Kafka;"
echo "  o que protege é o \`contact_closed\` ter sido publicado ANTES (no início do"
echo "  release) e o routed depender de todo o resto — release responder, claim ir e"
echo "  voltar. A margem é esse OFFSET DE PUBLICAÇÃO menos a diferença de latência dos"
echo "  dois handlers. Ela é incidental, não projetada: o bridge despacha com"
echo "  \`create_task\` e não ordena nada. Sob carga, o handler de contact_closed pode"
echo "  atrasar arbitrariamente enquanto o de routed corre — é aí que o risco mora,"
echo "  e é isso que este probe NÃO mede (uma rodada ociosa não é uma rodada sob carga)."
exit 0
