#!/usr/bin/env bash
# probe_operational_tools_reach.sh — 2026-08-21
#   TODO § "Capacidade × PAUSA", item (3)
#
# PERGUNTA, e ela vem ANTES do conserto: **quem sofre?**
#
# Os três tools do grupo `operational` decidem oferta de canal AO CLIENTE sobre
# `snapshot.available`. Dois ramos são suspeitos de publicar veredicto sobre
# número que o próprio código declara não confiar:
#
#   (a) `system_availability_check`, sem o rollup do tenant, cai em
#       `pools_available > 0 ? "available" : "unknown"` — a soma das linhas, que o
#       comentário três linhas acima proíbe usar como fallback.
#   (b) `pool_status_get`/`queue_context_get` leem `snapshot.available` sem tratar
#       a AUSÊNCIA do campo. O bootstrap OMITE `available` de propósito quando a
#       soma seria parcial (`capacity_unknown: "unmanaged_members"`), e
#       `undefined > 0` é `false` — a omissão vira "não há agente".
#
# Antes de consertar qualquer um dos dois é preciso saber se o ramo é ALCANÇÁVEL
# e se alguém o percorre. Um "fix" num caminho sem tráfego fica verde sem mudar
# nada, e o verde é creditado a ele (lição de 2026-08-20, trilha de sentimento).
#
# USO:   bash infra/test/probe_operational_tools_reach.sh
# SAÍDA: 0 = mediu · 2 = INCONCLUSIVO (leitor mudo)

set -u

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
CH_DB="${CH_DB:-plughub_demo}"

DC="docker compose -f $COMPOSE"
r()  { $DC exec -T redis redis-cli "$@" < /dev/null; }
# O `2>/dev/null` saiu: um erro do ClickHouse engolido vira "não respondeu", e
# "não respondeu" é indistinguível de "não há linha". Degradação nunca é muda.
ch() { $DC exec -T clickhouse clickhouse-client -q "$1" < /dev/null; }

[ "$(r PING)" = "PONG" ] || { echo "INCONCLUSIVO: redis mudo"; exit 2; }

echo "== quem sofre com os tools do grupo \`operational\`? (tenant=$TENANT) =="
echo

# ── 1) O ramo (a) é alcançável? Só se o rollup do tenant faltar ─────────────
echo "── (a) fallback de \`system_availability_check\` ──"
EX="$(r EXISTS "${TENANT}:capacity:snapshot" | tr -d '\r')"
TTL="$(r TTL   "${TENANT}:capacity:snapshot" | tr -d '\r')"
echo "   rollup {t}:capacity:snapshot ..... EXISTS=$EX  TTL=${TTL}s"
if [ "$EX" = "1" ]; then
  echo "   → o ramo do fallback NÃO roda enquanto o rollup existir. Alcançável"
  echo "     quando ele expira (TTL 1 h) sem que nada recompute — janela real,"
  echo "     mas não é o estado corrente."
else
  echo "   → rollup AUSENTE agora: o fallback está VIVO neste instante, e o"
  echo "     veredicto 'available' está saindo da soma das linhas de pool."
fi
echo

# ── 2) O ramo (b) é alcançável? Contar snapshots com capacidade OMITIDA ─────
echo "── (b) snapshots com capacidade OMITIDA (bootstrap, soma parcial) ──"
SNAPS="$(r --scan --pattern "${TENANT}:pool:*:snapshot" 2>/dev/null | tr -d '\r' | sort -u)"

# ── UMA leitura, não N ──────────────────────────────────────────────────────
# A 1ª versão fazia um `docker compose exec` por chave (36 deles) e contou
# **6 snapshots "sem `available`"**. Não eram: com o contador de leitura-vazia
# separado do contador de campo-ausente, a execução seguinte deu 0 e 0. Seis
# leituras falharam — corrida SCAN→GET ou exec intermitente, dá no mesmo: era o
# INSTRUMENTO, e a contagem virou achado sobre o produto por um passo.
# `MGET` numa chamada só remove a classe inteira, e a ordem do retorno casa com
# a ordem das chaves (linha vazia = chave ausente, sem ambiguidade).
VALS="$(r MGET $SNAPS)"
TOT=0; SEM_AVAIL=0; CAPUNK=0; BOOTSTRAP=0; VAZIO=0
OFENSORES=""; MODELOS=""
_i=0
for k in $SNAPS; do
  TOT=$((TOT+1)); _i=$((_i+1))
  V="$(printf '%s\n' "$VALS" | sed -n "${_i}p" | tr -d '\r')"
  MODELOS="$MODELOS
$(printf '%s' "$V" | sed -n 's/.*"model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  if [ -z "$V" ]; then VAZIO=$((VAZIO+1)); continue; fi
  # SEM o `"model":` colado: `json.dumps` escreve `"model": "…"` COM espaço, e a
  # 1ª versão deste teste devolvia 0 enquanto o histograma logo abaixo contava
  # 30. Duas contagens da mesma coisa discordando na MESMA tela — só não passou
  # porque o histograma estava do lado. Casar o valor, não a pontuação.
  case "$V" in *'bootstrap_placeholder'*) BOOTSTRAP=$((BOOTSTRAP+1)) ;; esac
  case "$V" in *'"capacity_unknown"'*)              CAPUNK=$((CAPUNK+1)) ;; esac
  case "$V" in
    *'"available"'*) : ;;
    *) SEM_AVAIL=$((SEM_AVAIL+1))
       OFENSORES="$OFENSORES
     $k
       ${V}" ;;
  esac
done
echo "   snapshots vivos ....................... $TOT"
echo "   …lidos VAZIOS (corrida SCAN→GET) ...... $VAZIO   ← instrumento, não produto"
echo "   …com model=bootstrap_placeholder ...... $BOOTSTRAP"
echo "   …com capacity_unknown ................. $CAPUNK"
echo "   …SEM o campo \`available\` .............. $SEM_AVAIL   ← o que os tools leem como 0"
if [ -n "$OFENSORES" ]; then
  echo "   linhas SEM \`available\` (íntegras — quem as escreve é o achado):"
  printf '%s\n' "$OFENSORES"
fi
echo "   quem ESCREVEU as linhas (campo \`model\`) — responde 'que produtor é esse?':"
printf '%s\n' "$MODELOS" | grep -v '^$' | sort | uniq -c | sed 's/^/     /'
VAZIO_MODEL=$(printf '%s\n' "$MODELOS" | grep -c '^$')
echo "     (linhas sem \`model\` legível: $((VAZIO_MODEL - 1)))"
if [ "$SEM_AVAIL" -gt 0 ]; then
  echo "   → ramo (b) VIVO: $SEM_AVAIL pool(s) dizendo 'não sei' e sendo lidos como"
  echo "     'não há agente'."
else
  echo "   → ramo (b) DORMENTE agora. A omissão só aparece quando há membro que o"
  echo "     bootstrap não gerencia (humano logado) E a linha boa do routing-engine"
  echo "     já expirou (TTL 3600 s sem transição no pool)."
fi
echo

# ── 3) TRÁFEGO: alguém chamou os três tools? ────────────────────────────────
# `mcp_audit_log` é o registro não-optável de toda chamada MCP. Se os três tools
# têm zero linhas, o conserto abaixo não muda nada no comportamento observável —
# e isso precisa estar escrito, não descoberto depois.
echo "── tráfego real: auditoria MCP ──"
# A 1ª execução morreu com UNKNOWN_TABLE em `plughub_demo.mcp_audit_log` — a
# tabela que o CLAUDE.md descreve não existe neste ambiente. Descobrir ONDE ela
# está (ou que não está) vem ANTES de concluir qualquer coisa sobre tráfego:
# tabela ausente e zero chamadas produzem a mesma tela.
echo "   tabelas de auditoria existentes:"
ch "SELECT database, name, total_rows FROM system.tables
    WHERE name LIKE '%audit%' OR name LIKE '%mcp%' FORMAT TSV" | sed 's/^/     /'
echo
Q="SELECT tool_name, count() FROM ${CH_DB}.mcp_audit_log
   WHERE tenant_id='${TENANT}'
     AND tool_name IN ('system_availability_check','pool_status_get','queue_context_get')
   GROUP BY tool_name ORDER BY tool_name FORMAT TSV"
OUT="$(ch "$Q")"
TOTAL_AUDIT="$(ch "SELECT count() FROM ${CH_DB}.mcp_audit_log WHERE tenant_id='${TENANT}' FORMAT TSV")"
if [ -z "${TOTAL_AUDIT:-}" ]; then
  echo "   ⚠ ClickHouse não respondeu — INCONCLUSIVO para esta seção (o instrumento"
  echo "     não leu; ausência de linha não é ausência de chamada)."
else
  echo "   linhas de auditoria MCP no tenant (testemunha) ... $TOTAL_AUDIT"
  # ⚠ Medido 2026-08-21: a consulta a `system.tables` por %audit%/%mcp% voltou
  # VAZIA — `mcp_audit_log` e `audit_access_log` não existem em banco nenhum
  # deste ambiente. Logo a pergunta de tráfego fica INCONCLUSIVA por falta de
  # instrumento, e o invariante "toda chamada MCP é auditada" não tem store
  # aqui. É achado de outro arco (Audit LGPD), registrado no TODO.
  if [ -z "$OUT" ]; then
    echo "   chamadas aos três tools .......................... 0"
    echo "   → nenhum consumidor de produção. O conserto é de CONTRATO (impedir que"
    echo "     o primeiro consumidor herde o número desconfiável), não de sintoma."
  else
    echo "   chamadas por tool:"; printf '%s\n' "$OUT" | sed 's/^/     /'
  fi
fi
echo

# ── 4) Quem DECLARA usar os tools, no repositório ───────────────────────────
echo "── consumidores declarados (skills/YAML) ──"
echo "   (grep feito fora deste script; registrado aqui para não se perder:"
echo "    nenhum skill de \`skill-flow-engine/skills/\` referencia os três tools —"
echo "    os únicos chamadores são o cenário e2e 07 e a lib de teste.)"
echo
echo "(probe read-only: nada foi escrito)"
