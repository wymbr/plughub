#!/usr/bin/env bash
# probe_context_map_seed.sh — GATE da ALW-12: o mapa de contexto do TENANT tem
# caminho de provisionamento, e ele não pode danificar o da plataforma.
#
# POR QUE ESTE GATE EXISTE
# ------------------------
# A ALW-04 encolheu a SEMENTE da plataforma para só-plataforma, e isso está certo.
# O que não existia era a contraparte: os 20 campos de domínio de tenant do demo
# viviam no store apenas porque alguém os escreveu pela API, uma vez. Medido em
# 2026-09-02, contra a semente — que É o estado pós-`--wipe` — **17 das 52** escritas
# de skill voltavam a NÃO DECLARADAS.
#
# O QUE ESTE GATE JULGA, E O QUE ELE DELIBERADAMENTE NÃO JULGA
# -------------------------------------------------------------
# A proposição central é de REPRODUTIBILIDADE: *o mapa vivo é derivável de arquivos
# versionados?* O ramo C a responde por reconstrução determinística (semente +
# arquivo == vivo), o que é mais forte que rodar o seed e ver se ele não reclama —
# um seed que não faz nada também não reclama.
#
# Os ramos E e F são as testemunhas NEGATIVAS, e a F existe por um defeito REAL:
# a primeira versão do seed mesclava no mapa EFETIVO (resolvido por tenant) e
# escrevia no GLOBAL. Enquanto os dois coincidem — o caso normal, garantido pela
# CNS-08 — nada acontece. Divergindo, o `PUT` grava o conteúdo do TENANT por cima
# da plataforma: medido, o global caiu de 97 canônicas para **20**, e a metade
# `core.*` desapareceu. A conferência do efeito pegou o sintoma e reprovou DEPOIS
# de já ter gravado. **Conferir o efeito não substitui escolher a base certa**, e é
# por isso que o ramo F testa a recusa PRÉVIA e confere que o global não foi tocado.
#
# ⚠️ O ramo F escreve DIRETO no banco, de propósito: a API recusa override de tenant
# nesta chave desde a CNS-08 (422), então a única forma de montar o estado é por
# fora dela — que é exatamente a classe de causa que a guarda cobre. Ele **nunca
# toca a linha `__global__`**; só insere e remove uma linha de tenant, e o trap roda
# em EXIT/INT/TERM.
#
# ⚠️ E o config-api CACHEIA **em Redis** (`plughub:cfg:{tenant}:{ns}:{key}`), não em
# processo. Escrita direta no banco não invalida nada, e **reiniciar o config-api NÃO
# resolve** — a chave sobrevive ao container. A invalidação é o `DEL` da chave.
#
# Isto custou três diagnósticos errados em 2026-09-02, e o motivo é instrutivo: o ramo
# ficava INCONCLUSIVO ou VERDE conforme o cache estivesse quente, e o que o esquentava
# era um ramo ANTERIOR do próprio gate (o D lê o mapa). Rodando o cenário isolado ele
# funcionava; dentro do gate, não. **Estado compartilhado entre ramos é entrada não
# declarada do ramo seguinte** — a mesma família do ambiente que só sobe porque já
# subiu antes.
#
# NÃO julgado aqui: a restauração pós-`--wipe` de verdade (encolher o global e ver o
# seed recompor). Ela foi validada à mão em 2026-09-02 — 17 não declaradas → 1, e o
# resultado byte-idêntico ao estado anterior — e fica FORA do gate porque exige
# escrever na linha `__global__`: uma interrupção no meio deixaria a instalação com
# o mapa da plataforma pela metade. Receita no `CHANGELOG.md` de 2026-09-02.
#
# Três estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
set -u

cd "$(dirname "$0")/../.." || exit 2

DC=${DC:-docker compose -f docker-compose.demo.yml}
TENANT=${TENANT:-tenant_demo}
CONFIG_API=${CONFIG_API:-http://localhost:3600}
ARQUIVO=infra/context-map/${TENANT}.json
PYSRC=packages/py-contextstore/src

FALHAS=0
INCONCL=0
ok()    { echo "  v $*"; }
falha() { echo "  X $*"; FALHAS=$((FALHAS+1)); }
inc()   { echo "  ? $*"; INCONCL=$((INCONCL+1)); }

psql_() { $DC exec -T postgres psql -U plughub -d plughub_demo -tAc "$1"; }

limpar_override() {
  psql_ "DELETE FROM public.platform_config
          WHERE tenant_id='${TENANT}' AND namespace='masking' AND key='context_map';" >/dev/null 2>&1
}

# Espera ATIVA. `sleep` fixo fez o ramo F sair INCONCLUSIVO na primeira execucao —
# e um ramo que nao julga por causa do relogio e a forma mais barata de um gate
# parar de gatear sem ficar vermelho.
# Invalida o cache Redis do config-api para esta chave. Reiniciar o serviço NÃO
# basta: o cache não é do processo.
invalidar_cache() {
  $DC exec -T redis redis-cli --scan --pattern 'plughub:cfg:*:masking:context_map' 2>/dev/null \
    | tr -d '\r' | while read -r k; do
        [ -n "$k" ] && $DC exec -T redis redis-cli DEL "$k" >/dev/null 2>&1
      done
}

esperar_config_api() {
  local i=0
  while [ $i -lt 60 ]; do
    if curl -fsS "${CONFIG_API}/v1/health" >/dev/null 2>&1; then return 0; fi
    i=$((i+1)); sleep 1
  done
  return 1
}

echo "=== probe_context_map_seed — ALW-12 (provisionamento do mapa do tenant) ==="

# ── pré-requisitos ───────────────────────────────────────────────────────────
if [ ! -f "$ARQUIVO" ]; then
  echo "INCONCLUSIVO: $ARQUIVO não existe"; exit 2
fi
if ! curl -fsS "${CONFIG_API}/v1/health" >/dev/null 2>&1; then
  echo "INCONCLUSIVO: config-api fora do ar em ${CONFIG_API}"; exit 2
fi

# ── A. o arquivo é legível e declara domínios ────────────────────────────────
echo
echo "-- A. o arquivo de provisionamento --"
N_DOM=$(python3 -c "
import io,json
d=json.load(io.open('$ARQUIVO',encoding='utf-8'))
print(sum(len(v) for v in d['contexto'].values()))" 2>/dev/null)
if [ -z "${N_DOM:-}" ] || [ "$N_DOM" -eq 0 ] 2>/dev/null; then
  falha "A: $ARQUIVO ilegível ou sem domínio nenhum"
else
  ok "A: $ARQUIVO declara $N_DOM domínio(s)"
fi

# ── B. todo `tipo` existe no catálogo VIVO (D8.3: catálogo antes do mapa) ────
echo
echo "-- B. catálogo antes do mapa --"
B=$(python3 - <<PY 2>&1
import io, json, urllib.request
d = json.load(io.open("$ARQUIVO", encoding="utf-8"))
u = "${CONFIG_API}/config/masking/types?tenant_id=${TENANT}"
with urllib.request.urlopen(u, timeout=8) as r:
    cat = json.loads(r.read().decode("utf-8"))["value"]["types"]
ids = {t["id"] for t in cat if isinstance(t, dict) and t.get("id")}
usados = {f.get("tipo") for doms in d["contexto"].values()
          for campos in doms.values() for f in campos.values()}
faltam = sorted(usados - ids - {None})
print("FALTAM " + ",".join(faltam) if faltam else "OK %d tipos usados, %d no catálogo"
      % (len(usados), len(ids)))
PY
)
case "$B" in
  OK*)      ok "B: $B" ;;
  FALTAM*)  falha "B: tipo(s) fora do catálogo — $B" ;;
  *)        inc "B: não foi possível conferir ($B)" ;;
esac

# ── C. A PROPOSIÇÃO CENTRAL: o mapa vivo é DERIVÁVEL dos arquivos ────────────
echo
echo "-- C. reprodutibilidade: SEMENTE + arquivo == mapa VIVO --"
C=$(PYTHONPATH=$PYSRC python3 - <<PY 2>&1
import io, json, urllib.request
from plughub_contextstore.default_map import DEFAULT_CONTEXT_MAP as SEED
from plughub_contextstore import build_context_tag_index

esperado = json.loads(json.dumps(SEED))
arq = json.load(io.open("$ARQUIVO", encoding="utf-8"))
colisao = []
for esc, doms in arq["contexto"].items():
    for dom, campos in doms.items():
        if dom in (esperado["contexto"].get(esc) or {}):
            colisao.append("%s.%s" % (esc, dom))
        esperado["contexto"].setdefault(esc, {})[dom] = campos

u = "${CONFIG_API}/config/masking/context_map?tenant_id=${TENANT}"
with urllib.request.urlopen(u, timeout=8) as r:
    vivo = json.loads(r.read().decode("utf-8"))["value"]

c = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)
i = build_context_tag_index(vivo)
if colisao:
    print("COLISAO " + ",".join(colisao))
elif c(esperado) == c(vivo):
    print("OK %d canonicas / %d aliases" % (len(i.canonical), len(i.alias)))
else:
    ie = build_context_tag_index(esperado)
    print("DIVERGE vivo=%d/%d esperado=%d/%d"
          % (len(i.canonical), len(i.alias), len(ie.canonical), len(ie.alias)))
PY
)
case "$C" in
  OK*)      ok "C: o mapa vivo é semente+arquivo — $C" ;;
  COLISAO*) falha "C: o arquivo redeclara domínio DA PLATAFORMA — $C
       (o arquivo é a CONTRAPARTE da semente, não um override dela)" ;;
  DIVERGE*) falha "C: o mapa vivo NÃO se reconstrói dos arquivos — $C
       (é exatamente o defeito da ALW-12: estado que só existe porque já existia)" ;;
  *)        inc "C: não foi possível conferir ($C)" ;;
esac

# ── D. o seed é idempotente ──────────────────────────────────────────────────
echo
echo "-- D. idempotência --"
SAIDA=$(timeout 240 $DC run --rm context-map-seed 2>&1); RC=$?
if [ "$RC" -ne 0 ]; then
  falha "D: o seed saiu $RC sobre um store já semeado"
  echo "$SAIDA" | grep -v "Container " | tail -4 | sed 's/^/       /'
elif echo "$SAIDA" | grep -q "nada a acrescentar"; then
  ok "D: seed idempotente (exit 0, nada a acrescentar)"
else
  falha "D: exit 0 mas o seed ACRESCENTOU algo num store que já devia estar completo"
fi

# ── E. testemunha NEGATIVA: tipo desconhecido é recusado ─────────────────────
echo
echo "-- E. tipo fora do catálogo é RECUSADO (testemunha negativa) --"
cp "$ARQUIVO" /tmp/_cms_bak.json
python3 - <<PY >/dev/null 2>&1
import io, json
P = "$ARQUIVO"
d = json.load(io.open(P, encoding="utf-8"))
esc = sorted(d["contexto"])[0]
dom = sorted(d["contexto"][esc])[0]
campo = sorted(d["contexto"][esc][dom])[0]
d["contexto"][esc][dom][campo]["tipo"] = "__tipo_inexistente_probe__"
io.open(P, "w", encoding="utf-8", newline="").write(
    json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
SAIDA=$(timeout 240 $DC run --rm context-map-seed 2>&1); RC=$?
cp /tmp/_cms_bak.json "$ARQUIVO"
if [ "$RC" -ne 0 ] && echo "$SAIDA" | grep -q "fora do catálogo"; then
  ok "E: tipo desconhecido recusado (exit $RC), nomeando a regra D8.3"
else
  falha "E: tipo inexistente PASSOU (exit $RC) — o portão do catálogo é inerte"
fi

# ── F. testemunha NEGATIVA: divergência global×efetivo recusa ANTES de escrever
echo
echo "-- F. override fora da API: recusa PRÉVIA, global intacto --"
trap 'limpar_override; invalidar_cache' EXIT INT TERM

ANTES=$(psql_ "SELECT length(value::text) FROM public.platform_config
                WHERE tenant_id='__global__' AND namespace='masking' AND key='context_map';" \
         2>/dev/null | tr -d '[:space:]')
psql_ "INSERT INTO public.platform_config (tenant_id, namespace, key, value, description)
       VALUES ('${TENANT}','masking','context_map',
               '{\"mode\":\"audit\",\"dynamic_prefixes\":[],\"contexto\":{\"session\":{\"__probe__\":{}}}}'::jsonb,
               'probe_context_map_seed — temporario')
       ON CONFLICT (tenant_id, namespace, key) DO UPDATE SET value = EXCLUDED.value;" >/dev/null 2>&1
# Sem isto a leitura devolve o valor CACHEADO e o ramo passa por nao ter montado o
# cenario — inconclusivo parecendo verde, ou pior, verde por acaso.
invalidar_cache

MONTOU=$(curl -fsS "${CONFIG_API}/config/masking/context_map?tenant_id=${TENANT}" 2>/dev/null \
  | python3 -c "import json,sys; print('__probe__' in json.load(sys.stdin)['value']['contexto'].get('session',{}))" 2>/dev/null)
if [ "$MONTOU" != "True" ]; then
  inc "F: o cenário não montou (leitura efetiva não mostra o override) — não julga nada"
else
  SAIDA=$(timeout 240 $DC run --rm context-map-seed 2>&1); RC=$?
  DEPOIS=$(psql_ "SELECT length(value::text) FROM public.platform_config
                   WHERE tenant_id='__global__' AND namespace='masking' AND key='context_map';" \
            2>/dev/null | tr -d '[:space:]')
  if [ "$RC" -eq 0 ]; then
    falha "F: o seed PASSOU com override divergente — a guarda prévia é inerte"
  elif ! echo "$SAIDA" | grep -q "DIVERGE do escopo global"; then
    falha "F: recusou (exit $RC) mas por outro motivo — a guarda prévia não é a que agiu"
  elif [ "$ANTES" != "$DEPOIS" ]; then
    falha "F: recusou, mas o GLOBAL foi alterado ($ANTES -> $DEPOIS bytes).
       É o defeito de 2026-09-02: mesclar no efetivo e gravar no global."
  else
    ok "F: recusa PRÉVIA (exit $RC) e global intacto ($ANTES bytes)"
  fi
fi

limpar_override
invalidar_cache
trap - EXIT INT TERM

# ── G. as DUAS cópias da SEMENTE concordam ───────────────────────────────────
#
# A semente vive em dois arquivos: `packages/schemas/src/context-map.ts` (a AUTORIDADE)
# e `packages/py-contextstore/.../default_map.py` (cópia mantida à mão, que existe
# porque o carregador Python precisa do mesmo fallback que o TS tem).
#
# ⚠️ **A justificativa original deste ramo estava ERRADA, e a mutação a derrubou.** Ela
# dizia que nenhum gate pegava *alias só na Python* — que o `probe_context_map_audit`
# mediria a TS contra o vivo por mera CONTENÇÃO de chaves e deixaria passar. Medido:
# removida uma grafia só da TS, o ramo B daquele gate REPROVOU, porque ele compara o
# CONTEÚDO da folha e não só a presença (`difere: ["core.survey.agent_key"]`).
#
# As duas direções já eram cobertas, transitivamente pelo store vivo: `B` (vivo ⊇ TS, por
# conteúdo) mais `C` (vivo == Python + arquivo do tenant) fecham o triângulo.
#
# O que este ramo acrescenta é MENOR, e vale registrado como é:
#   · **diagnóstico direto** — diz "TS × Python", em vez de "a config viva não contém a
#     declaração", que manda procurar no lugar errado;
#   · **funciona com a stack DE PÉ OU NÃO** — B e C dependem do config-api; este não.
# Manter o texto anterior seria prosa afirmando uma lacuna inexistente: a família do DDL
# de `participation_intervals`, e do lado que este arco menos pode se permitir.
echo
echo "-- G. as duas cópias da SEMENTE (TS × Python) --"
G=$(python3 - <<'PY' 2>&1
import io, json, re, subprocess, sys
sys.path.insert(0, "packages/py-contextstore/src")
from plughub_contextstore.default_map import DEFAULT_CONTEXT_MAP as PY
from plughub_contextstore import build_context_tag_index

# A TS é lida pelo MESMO caminho que o oráculo do outro gate usa: o fonte, via node.
# Ler o `dist/` responderia sobre um artefato que pode estar atrasado.
js = subprocess.run(
    ["docker", "run", "--rm", "-v", "%s:/repo" % subprocess.run(
        ["pwd"], capture_output=True, text=True).stdout.strip(),
     "node:20-alpine", "sh", "-c",
     "cd /repo/packages/schemas && ./node_modules/.bin/esbuild --bundle --platform=node "
     "--format=cjs --log-level=error --outfile=/tmp/m.cjs src/context-map.ts >/dev/null 2>&1 "
     "&& node -e \"const m=require('/tmp/m.cjs');console.log(JSON.stringify(m.DEFAULT_CONTEXT_MAP))\""],
    capture_output=True, text=True)
if js.returncode != 0 or not js.stdout.strip():
    print("INCONCLUSIVO %s" % (js.stderr or "")[:120]); sys.exit(0)
TS = json.loads(js.stdout)

its, ipy = build_context_tag_index(TS), build_context_tag_index(PY)
so_ts = sorted(set(its.alias) - set(ipy.alias))
so_py = sorted(set(ipy.alias) - set(its.alias))
c_ts  = sorted(set(its.canonical) - set(ipy.canonical))
c_py  = sorted(set(ipy.canonical) - set(its.canonical))
if so_ts or so_py or c_ts or c_py:
    print("DIVERGE alias_so_TS=%s alias_so_PY=%s canon_so_TS=%s canon_so_PY=%s"
          % (so_ts[:4], so_py[:4], c_ts[:4], c_py[:4]))
else:
    print("OK %d canonicas / %d aliases nas duas" % (len(its.canonical), len(its.alias)))
PY
)
case "$G" in
  OK*)           ok "G: TS e Python declaram a MESMA semente — $G" ;;
  DIVERGE*)      falha "G: as duas cópias da semente DIVERGEM — $G
       Editar uma e esquecer a outra é a operação da V5. A direção 'só na Python'
       passava nos outros dois gates." ;;
  *)             inc "G: não foi possível comparar ($G)" ;;
esac

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then
  echo "FALHA — $FALHAS ramo(s) reprovado(s)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo "INCONCLUSIVO — $INCONCL ramo(s) sem julgar (nunca OK com ramo inconclusivo)"; exit 2
fi
echo "OK — o mapa do tenant se reconstrói de arquivos, e o seed não danifica a plataforma"
