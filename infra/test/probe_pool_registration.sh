#!/usr/bin/env bash
# probe_pool_registration.sh — 2026-08-05
#   TODO § "`agent_ready` não inscreve instância em pool nenhum — e o SET que ele
#   escreve não tem leitor"
#
# PERGUNTA (uma só, e é de MEDIÇÃO, não de código):
#   Quem inscreve uma instância de IA num pool HOJE, no demo — e em QUAL chave?
#
# POR QUE ELA DECIDE O ITEM:
#   O item tem duas saídas de sentidos OPOSTOS e o diff é quase o mesmo nas duas:
#     (a) VESTÍGIO — a inscrição migrou para o slot de deploy + bootstrap, e o
#         `sadd` de `agent_ready` + o SET `:available` + a asserção do cenário 01
#         + o stub do `runtime.test.ts` são resíduo do modelo anterior.
#         Conserto = REMOVER (e aceitar os vermelhos que isso produz).
#     (b) VIVO — algum consumidor real depende de `:available`, e o defeito é o
#         `pools: []` FIXO do `infra/registry-client.ts` §71.
#         Conserto = no cliente HTTP.
#   Apagar o SET certo pelo motivo errado dá o mesmo diff com a dívida escondida.
#   Por isso: medir primeiro. A lição de 2026-08-05 (3 de 7 achados vieram de
#   `MONITOR`, não de leitura de fonte) é literalmente esta.
#
# O QUE O CÓDIGO JÁ AFIRMA (e que este probe NÃO repete — ele mede a consequência):
#   · `{t}:pool:{p}:available`  — 1 `sadd` (runtime.ts §274, dentro do `agent_ready`)
#                                 4 `srem`, ZERO leituras fora de teste no mcp-server;
#                                 zero ocorrências de `:available` em `.py`.
#   · `{t}:pool:{p}:instances`  — o ready_set DE VERDADE: escrito pelo
#                                 instance_bootstrap (`_create_instance` §805,
#                                 `_sync_pool_sets` §879), pelo routing-engine
#                                 (`set_instance` §1872) e, para HUMANO, pelo
#                                 mcp-server (`registerHumanAgent` server.ts §554).
#   · `agent_ready` (runtime.ts §271) itera o campo `pools` do hash
#     `{t}:agent:instance:{id}`, escrito pelo `agent_login` a partir do cliente HTTP
#     que devolve `pools: []` sem ramo algum. Laço vazio ⇒ nada é escrito.
#
# ═══ PREVISÃO, ESCRITA ANTES DE RODAR (contar, não estimar) ═══
#   P1. Chaves `{T}:pool:*:instances` existentes  → ≥ 1  (TESTEMUNHA de presença:
#       sem ela, a ausência de P2 seria indistinguível de "prefixo errado").
#   P2. Chaves `{T}:pool:*:available` existentes  → 0.
#   P3. Probe SADD injetado durante o MONITOR aparece no log → SIM
#       (TESTEMUNHA do instrumento: sem ela, "nenhum SADD" mede o grep, não o Redis).
#   P4. SADD em `:instances` durante o boot do bridge → ≥ 1, vindo do IP do
#       orchestrator-bridge e/ou do routing-engine.
#   P5. SADD em `:available` durante a janela → 0.
#   P6. QUALQUER comando (leitura inclusive) sobre `:available` na janela → 0.
#
#   Um contador de AUSÊNCIA (P2/P5/P6) só vale ao lado dos de PRESENÇA (P1/P3/P4).
#   Os quatro estão no mesmo veredicto de propósito.
#
# LIMITE HONESTO DO INSTRUMENTO:
#   a janela do MONITOR é de ~2 min sobre um demo em boot. Ela responde
#   "quem ESCREVE hoje"; ela NÃO prova "não existe leitor jamais" — quem prova
#   isso é o grep de fonte acima (zero leituras em todo o repo fora de teste).
#   Não confundir os dois na hora de escrever o veredicto.
#
# USO:   bash infra/test/probe_pool_registration.sh
# SAÍDA: 0 = VESTÍGIO (saída (a))  ·  1 = VIVO (saída (b))  ·  2 = INCONCLUSIVO

set -u   # sem -e de propósito: ramo AUSENTE imprime INCONCLUSIVO, não morre

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
WINDOW="${WINDOW:-150}"        # segundos de MONITOR
BOOT_WAIT="${BOOT_WAIT:-100}"  # espera após o restart do bridge

DC="docker compose -f $COMPOSE"
LOG=/tmp/monitor_sadd_$$.log
PROBE_KEY="${TENANT}:pool:__probe_witness__:instances"

r()  { $DC exec -T redis redis-cli "$@" < /dev/null; }
rsh() { $DC exec -T redis sh -c "$1" < /dev/null; }

cleanup() {
  r DEL "$PROBE_KEY" > /dev/null 2>&1
}
trap cleanup EXIT

echo "== probe: quem inscreve instância de IA num pool, e em qual chave? =="
echo "   tenant=$TENANT  compose=$COMPOSE  janela=${WINDOW}s"
echo

# ── Preflight 1: PROVAR QUE O LEITOR LÊ ──────────────────────────────────────
PING="$(r PING)"
if [ "$PING" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING (obtido: '$PING')."
  echo "  rodar da RAIZ do repo, ou COMPOSE_FILE=/caminho/docker-compose.demo.yml"
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê; nada abaixo vale."
  exit 2
fi

# ── Preflight 2: TESTEMUNHA DE PRESENÇA + conferência do PREFIXO (P1) ────────
echo "── P1 · chaves ':instances' (testemunha de presença; confere o prefixo) ──"
INST_KEYS="$(rsh "redis-cli --scan --pattern '${TENANT}:pool:*:instances'" | tr -d '\r')"
N_INST="$(printf '%s\n' "$INST_KEYS" | grep -c ':instances' )"
echo "$INST_KEYS"
echo "   → $N_INST chave(s)"
if [ "$N_INST" -eq 0 ]; then
  echo
  echo "VEREDICTO: INCONCLUSIVO — nenhum ready_set no prefixo '$TENANT'."
  echo "  Ou o tenant está errado, ou NINGUÉM está logado: nos dois casos a"
  echo "  ausência de ':available' não significa nada."
  exit 2
fi

echo
echo "── membros de cada ready_set (SMEMBERS: contar não é identificar) ──"
rsh "for k in \$(redis-cli --scan --pattern '${TENANT}:pool:*:instances'); do echo \"== \$k\"; redis-cli smembers \$k; done"

# ── P2 · o SET sob suspeita EXISTE? ─────────────────────────────────────────
echo
echo "── P2 · chaves ':available' (previsão: NENHUMA) ──"
AVAIL_KEYS="$(rsh "redis-cli --scan --pattern '${TENANT}:pool:*:available'" | tr -d '\r')"
N_AVAIL="$(printf '%s\n' "$AVAIL_KEYS" | grep -c ':available' )"
if [ "$N_AVAIL" -gt 0 ]; then
  echo "$AVAIL_KEYS"
  echo "   → $N_AVAIL chave(s) — e os membros:"
  rsh "for k in \$(redis-cli --scan --pattern '${TENANT}:pool:*:available'); do echo \"== \$k\"; redis-cli smembers \$k; done"
else
  echo "   → 0 chaves"
fi

# ── Fase 2: MONITOR armado, testemunha do instrumento, boot do bridge ───────
echo
echo "── armando MONITOR por ${WINDOW}s (detached, dentro do container redis) ──"
$DC exec -d redis sh -c "timeout ${WINDOW} redis-cli monitor > $LOG 2>&1"
sleep 3

echo "── P3 · testemunha do INSTRUMENTO: injetando um SADD conhecido ──"
r SADD "$PROBE_KEY" probe_witness > /dev/null
sleep 1
N_PROBE="$(rsh "grep -c '__probe_witness__' $LOG 2>/dev/null || true" | tr -d '\r')"
echo "   → ocorrências do probe no log: ${N_PROBE:-0}"
if [ "${N_PROBE:-0}" -eq 0 ]; then
  echo
  echo "VEREDICTO: INCONCLUSIVO — o MONITOR não capturou nem o SADD que EU emiti."
  echo "  Qualquer contagem de ausência abaixo mediria o instrumento, não o Redis."
  exit 2
fi

echo
echo "── reiniciando o orchestrator-bridge (é ele que reconcilia as instâncias) ──"
$DC restart orchestrator-bridge
echo "── esperando ${BOOT_WAIT}s de boot + reconciliação ──"
sleep "$BOOT_WAIT"

# ── Coleta ──────────────────────────────────────────────────────────────────
echo
echo "── P4 · SADD em ':instances' na janela (previsão: ≥1) ──"
rsh "grep '\"SADD\"' $LOG | grep ':instances' | grep -v __probe_witness__ | head -40"
N_SADD_INST="$(rsh "grep '\"SADD\"' $LOG | grep ':instances' | grep -vc __probe_witness__ || true" | tr -d '\r')"
echo "   → $N_SADD_INST SADD(s)"

echo
echo "── quem os emitiu (IP do cliente no MONITOR) ──"
rsh "grep '\"SADD\"' $LOG | grep ':instances' | grep -v __probe_witness__ | sed 's/.*\[\([0-9]* [0-9.]*\):.*/\1/' | sort | uniq -c"
echo
echo "── tradução IP → serviço ──"
for svc in orchestrator-bridge routing-engine mcp-server-plughub; do
  IP="$($DC exec -T "$svc" hostname -i < /dev/null 2>/dev/null | tr -d '\r' | awk '{print $1}')"
  echo "   $svc = ${IP:-<não respondeu>}"
done

echo
echo "── P5 · SADD em ':available' na janela (previsão: 0) ──"
N_SADD_AVAIL="$(rsh "grep '\"SADD\"' $LOG | grep -c ':available' || true" | tr -d '\r')"
echo "   → ${N_SADD_AVAIL:-0}"
[ "${N_SADD_AVAIL:-0}" -gt 0 ] && rsh "grep '\"SADD\"' $LOG | grep ':available' | head -20"

echo
echo "── P6 · QUALQUER comando sobre ':available' na janela (previsão: 0) ──"
N_ANY_AVAIL="$(rsh "grep -c ':available' $LOG || true" | tr -d '\r')"
echo "   → ${N_ANY_AVAIL:-0}"
[ "${N_ANY_AVAIL:-0}" -gt 0 ] && rsh "grep ':available' $LOG | head -20"

echo
echo "── retrato DEPOIS: ready_sets ──"
rsh "for k in \$(redis-cli --scan --pattern '${TENANT}:pool:*:instances'); do echo \"== \$k\"; redis-cli smembers \$k; done"

# ── Veredicto RAMIFICADO sobre o valor medido — três ramos ──────────────────
echo
echo "════════════════════ VEREDICTO ════════════════════"
echo "  P1 ready_sets existentes ....... $N_INST"
echo "  P2 ':available' existentes ..... $N_AVAIL"
echo "  P3 testemunha do instrumento ... ${N_PROBE:-0}"
echo "  P4 SADD ':instances' na janela . $N_SADD_INST"
echo "  P5 SADD ':available' na janela . ${N_SADD_AVAIL:-0}"
echo "  P6 qualquer ':available' ....... ${N_ANY_AVAIL:-0}"
echo

if [ "${N_AVAIL:-0}" -gt 0 ] || [ "${N_SADD_AVAIL:-0}" -gt 0 ] || [ "${N_ANY_AVAIL:-0}" -gt 0 ]; then
  echo "VIVO (saída (b)) — o SET ':available' tem escritor e/ou tráfego no demo."
  echo "  Então o defeito é o 'pools: []' fixo do registry-client, e o conserto é"
  echo "  NO CLIENTE. Antes de codar: ver ACIMA quem tocou a chave (linhas do P5/P6)"
  echo "  — 'existe tráfego' não é o mesmo que 'existe LEITOR que decide algo'."
  exit 1
fi

if [ "${N_SADD_INST:-0}" -eq 0 ]; then
  echo "INCONCLUSIVO — nenhum SADD em ':instances' na janela."
  echo "  A ausência dos DOIS lados não distingue 'vestígio' de 'janela curta'."
  echo "  Repetir com WINDOW maior, ou confirmar que o bridge reconcilia mesmo"
  echo "  (docker compose -f $COMPOSE logs --tail 50 orchestrator-bridge)."
  exit 2
fi

echo "VESTÍGIO (saída (a)) — quem inscreve hoje é OUTRO caminho, em OUTRA chave:"
echo "  ':instances' recebeu $N_SADD_INST SADD(s) na janela; ':available' recebeu"
echo "  0, e a chave sequer existe. Some-se o grep de fonte (zero leitores fora de"
echo "  teste, em todo o repo) e a conclusão é: o 'sadd' do agent_ready escreve num"
echo "  SET que ninguém lê, a partir de uma lista que nunca é não-vazia."
echo
echo "  CONSERTO (nesta ordem, e o vermelho é o RESULTADO, não o obstáculo):"
echo "    1. remover o sadd/srem de ':available' (runtime.ts §274/349/572/613,"
echo "       server.ts §2519) e o helper keys.poolAvailable;"
echo "    2. trocar o stub de runtime.test.ts por um que respeite a FORMA do"
echo "       cliente real (pools: [], max_concurrent_sessions: 1, permissions: []) —"
echo "       é o §7: um dublê que devolve o que a produção não consegue devolver"
echo "       não simplifica, contradiz;"
echo "    3. remover a asserção 'Pool … contains instance after agent_ready' do"
echo "       cenário 01 e o getPoolAvailable do e2e/lib/redis-client.ts;"
echo "    4. NÃO tocar em ':instances', 'pool_roster' nem 'busy_instances'."
exit 0
