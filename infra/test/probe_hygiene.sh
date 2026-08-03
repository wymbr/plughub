#!/usr/bin/env bash
# probe_hygiene.sh — sonda ÚNICA da higiene de 2026-08-03 (sessão seguinte).
#
# POR QUE ISTO EXISTE. Três itens do TODO afirmam números sobre o estado do
# sistema. A regra do topo do TODO ("meça o que o item afirma ANTES de
# executá-lo, inclusive os números que ele cita") só vale se a medição existir
# como arquivo — comando colado no terminal não deixa rastro de qual foi a
# pergunta, e a resposta vira lembrança.
#
# Cada bloco imprime a PREVISÃO antes de medir. Previsão escrita é o que separa
# "erro de previsão" de "diagnóstico": sem ela, um resultado plausível vira
# conclusão (TODO § "Erros de método", item 4).
#
# CORREÇÃO 1 (rodada 1 desta sonda). A v1 procurava os testes em `<pkg>/tests`
# e imprimiu `no tests ran in 0.00s` com **rc=0** — a própria sonda produziu o
# verde-que-não-pode-reprovar que ela persegue. O layout do repo é
# `src/<pacote_snake>/tests`. Agora: caminho correto E veredicto INCONCLUSIVO
# explícito quando nenhum teste for coletado.
#
# CORREÇÃO 2 (rodada 1). O TTL cru do Redis NÃO responde à pergunta: `ttl=1791`
# é indistinguível entre "TTL 1800 escrito agora" e "TTL 3600 escrito há 30
# min". O bloco 3b passou a derivar o TTL ORIGINAL somando a idade da linha
# (`ttl_agora + (agora − updated_at)`), que é o único jeito de medir isso sem
# gerar tráfego.
#
# Uso:  bash infra/test/probe_hygiene.sh
# Pré:  stack demo no ar. Dura ~2 min.
# Saída: sempre 0 — esta sonda MEDE, não julga. O veredicto é humano.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"

hr() { printf '\n%s\n' "══════════════════════════════════════════════════════════════════════"; }
sub() { printf -- '── %s\n' "$1"; }

# Descobre o diretório do pacote DENTRO do container (não supõe — a suposição
# `/app/packages/<nome>` já marcou 6 pacotes como "sem pyproject" em 2026-08-02).
pkgdir() {
  $DC exec -T "$1" sh -lc '
    for d in "/app/packages/'"$1"'" /app /app/src; do
      [ -f "$d/pyproject.toml" ] && { echo "$d"; exit 0; }
    done
    exit 1' 2>/dev/null < /dev/null | tr -d '\r'
}

# Roda um arquivo de teste e CLASSIFICA em 3 estados. `no tests ran` sai com
# rc=0 no pytest; tratá-lo como sucesso foi o defeito da v1.
run_suite() {  # $1=serviço  $2=pacote_snake  $3..=arquivos
  local svc="$1" snake="$2"; shift 2
  local wd; wd="$(pkgdir "$svc")"
  echo "   [dir no container: ${wd:-NÃO ENCONTRADO}]"
  if [ -z "$wd" ]; then
    echo "   ⚠️  INCONCLUSIVO — pyproject não encontrado. NÃO leia como 'passou'."
    return
  fi
  local paths=""
  for f in "$@"; do paths="$paths src/$snake/tests/$f"; done

  local t0 out rc t1
  t0=$(date +%s)
  out="$(timeout 300 $DC exec -T "$svc" sh -lc \
        "cd '$wd' && python -m pytest -p no:cacheprovider -q $paths 2>&1 | tail -8" \
        < /dev/null 2>&1)"
  rc=$?
  t1=$(date +%s)
  printf '%s\n' "$out" | sed 's/^/   /'
  echo "   → rc=$rc  duração=$((t1 - t0))s"

  if [ "$rc" -eq 124 ]; then
    echo "   ⚠️  TIMEOUT — a trava relatada é REAL."
  elif printf '%s' "$out" | grep -qE 'no tests ran|file or directory not found'; then
    echo "   ⚠️  INCONCLUSIVO — zero testes coletados. NÃO é 'zero falha'."
  elif printf '%s' "$out" | grep -qE '[0-9]+ (failed|error)'; then
    echo "   ❌ FALHAS — o item NÃO é stale."
  elif printf '%s' "$out" | grep -qE '[0-9]+ passed'; then
    echo "   ✅ verde e mediu (linha-resumo presente)."
  else
    echo "   ⚠️  INCONCLUSIVO — sem linha-resumo do pytest."
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
hr
echo "BLOCO 1 — as duas seções que o recado suspeita de STALE"
echo
echo "PREVISÃO 1a: TODO § 'analytics-api — 23 testes vermelhos', item (c) diz que"
echo "  TestDashboardRBAC TRAVA (pendurou em 2 execuções, Ctrl+C). Previsão: NÃO"
echo "  trava e passa. Refuta a previsão: timeout, ou qualquer failed."
echo "PREVISÃO 1b: TODO § 'evaluation-api — 10 testes de test_router.py' diz 10/83"
echo "  vermelhos. Previsão: 0 failed, e o total > 83 (a suíte cresceu)."
hr

sub "1a. analytics-api :: test_dashboard.py + test_admin.py (teto de 300 s)"
run_suite analytics-api plughub_analytics_api test_dashboard.py test_admin.py

sub "1b. evaluation-api :: test_router.py"
run_suite evaluation-api plughub_evaluation_api test_router.py

# ─────────────────────────────────────────────────────────────────────────────
hr
echo "BLOCO 2 — os dois INCONCLUSIVOS do report_suite_skips.sh"
echo
echo "RODADA 1 confirmou as duas previsões:"
echo "  · broker só tem 'evaluation.events' — 'evaluation.results', único tópico"
echo "    que o clickhouse-consumer consome, NÃO EXISTE (auto-create desligado);"
echo "  · 'transcripts'/'transcript_messages' não existem em plughub_demo;"
echo "  · nenhum dos dois pacotes tem Dockerfile nem serviço no compose."
echo "Reexecutado aqui só para o registro ficar junto do resto."
hr

sub "2a. tópicos do broker que casam com 'evaluation'"
$DC exec -T kafka sh -lc \
  '/opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:29092 --list 2>/dev/null | grep -i evaluation' \
  < /dev/null || echo "   (nenhum tópico casou com 'evaluation')"

sub "2b. tabelas de transcript em plughub_demo (Postgres)"
$DC exec -T postgres psql -U plughub -d plughub_demo -tAc \
  "SELECT table_schema||'.'||table_name FROM information_schema.tables
    WHERE table_name IN ('transcripts','transcript_messages');" < /dev/null \
  || echo "   (psql falhou)"
echo "   ↑ vazio = tabelas nunca criadas = o writer nunca rodou contra este banco."

# ─────────────────────────────────────────────────────────────────────────────
hr
echo "BLOCO 3 — namespace 'routing': as duas chaves sem leitor"
echo
echo "RODADA 1: config-api devolve snapshot_ttl_s=120 e score_weights com as"
echo "  chaves {skill_match, availability, aging_factor, breach_factor} — as"
echo "  MESMAS que o scorer lê do POOL, não do tenant. O único hit de"
echo "  'snapshot_ttl_s' fora de config-api/routing_config/testes é"
echo "  NamespaceEditor.tsx:48 — texto da TELA, não leitor. 'Ninguém lê' vale."
echo
echo "PREVISÃO 3b (nova): o TTL ORIGINAL das linhas escritas pelo routing-engine"
echo "  é 3600. A rodada 1 mostrou ttl=1791/1745, que é AMBÍGUO (1800 agora ou"
echo "  3600 há 30 min). Aqui a idade da linha desfaz o empate. Refuta: original"
echo "  ≈1800 (existe call site passando outro valor) ou ≈120 (a config governa)."
hr

sub "3. Redis :: TTL original derivado (ttl_agora + idade da linha)"
# SEM `< /dev/null` aqui: o heredoc É o stdin. A v2 tinha os dois, e o
# redirecionamento posterior vencia — `python -` lia script VAZIO e saía com 0,
# imprimindo nada. Terceiro defeito silencioso desta mesma sonda.
$DC exec -T routing-engine python - <<'PY'
import json, os, sys, asyncio
from datetime import datetime, timezone

# Sem degradação silenciosa: a sonda DIZ de qual variável leu a URL. O caso
# REDIS_URL x PLUGHUB_REDIS_URL já deixou 35 testes sem rodar (2026-08-02).
src = "PLUGHUB_REDIS_URL" if os.environ.get("PLUGHUB_REDIS_URL") else "REDIS_URL"
url = os.environ.get(src)
print(f"   [url de {src}: {url}]")
if not url:
    print("   ⚠️  INCONCLUSIVO — nenhuma das duas variáveis definida.")
    sys.exit(0)

import redis.asyncio as aioredis

TENANT = os.environ.get("PLUGHUB_TENANT_ID", "tenant_demo")

async def main():
    r = aioredis.from_url(url, decode_responses=True)
    keys = [k async for k in r.scan_iter(f"{TENANT}:pool:*:snapshot", count=200)]
    if not keys:
        print("   ⚠️  INCONCLUSIVO — nenhuma chave de snapshot. Sistema ocioso;")
        print("       ausência NÃO é 'TTL correto'.")
        return
    now = datetime.now(timezone.utc)
    print(f"   {len(keys)} chave(s). ttl=restante · idade=agora−updated_at · orig=ttl+idade")
    rows = []
    for k in sorted(keys):
        ttl = await r.ttl(k)
        raw = await r.get(k)
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        model = d.get("model") or "<sem model>"
        upd = d.get("updated_at")
        age = None
        if upd:
            try:
                t = datetime.fromisoformat(str(upd).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                age = int((now - t).total_seconds())
            except Exception:
                age = None
        orig = (ttl + age) if (age is not None and ttl and ttl > 0) else None
        rows.append((k.split(":")[2], ttl, age, orig, model))
    w = max(len(x[0]) for x in rows)
    for name, ttl, age, orig, model in rows:
        o = f"{orig}" if orig is not None else "?(sem updated_at)"
        a = f"{age}" if age is not None else "?"
        print(f"   {name:<{w}}  ttl={ttl:<6} idade={a:<6} orig≈{o:<8} model={model}")
    print()
    print("   LEITURA: linhas com model='bootstrap_placeholder' são do bootstrap")
    print("   (TTL 60 s, NX) e não provam nada sobre a config. O que decide são as")
    print("   escritas do routing-engine.")

asyncio.run(main())
PY

hr
echo "FIM — a sonda não emite veredicto. Compare cada bloco com a PREVISÃO."
exit 0
