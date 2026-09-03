#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# gate_published_alias_census — artefato PUBLICADO que lê alias de canônica `core.*`
# CNS-22 (arco `adr-contextstore-allowlist` / CNS-11).
#
# POR QUE ESTE GATE EXISTE
# ────────────────────────
# Em 2026-09-03, três sintomas que pareciam independentes — OTP travado, contato
# que não desliga, NPS invisível — eram **o mesmo defeito em três artefatos
# publicados diferentes**. Todos congelados antes da CNS-11, todos lendo
# `@ctx.session.*` enquanto o produtor passou a escrever a canônica `core.*`.
#
# A CNS-11 migrou o CÓDIGO e os arquivos-fonte. Não alcançou:
#   · o **snapshot do slot `current`** — é ele que o bridge executa, não o YAML;
#   · o **DialogForm publicado** — `infra/dialog/*.json` é seed-if-absent, então
#     editar o arquivo de um form já publicado é NO-OP.
#
# ⚠️ **`grep` no repositório não vê nenhum dos dois.** Eles vivem no agent-registry
# e no dialog-api. Foi por isso que a migração pôde ser declarada completa com dois
# terços do parque para trás — e por isso este gate consulta os SERVIÇOS, não o
# working tree.
#
# ⚠️ E a segunda metade da lição: a CNS-11 renomeou CONSUMIDORES deixando o
# produtor; a CNS-19 renomeou o PRODUTOR deixando consumidores. Enquanto os dois
# lados falavam o nome velho, o artefato defasado funcionava **por coincidência** —
# mover o produtor cegou-o no mesmo instante. Defasagem só é inofensiva enquanto
# ninguém conserta o outro lado.
#
# O QUE ESTE GATE PODE REPROVAR
# ─────────────────────────────
#   A  snapshot de slot NOVO lendo alias de canônica `core.*`        → VERMELHO
#   B  DialogForm publicado NOVO lendo alias de canônica `core.*`    → VERMELHO
#   C  linha da tabela de DÍVIDA que já não se aplica                → VERMELHO
#      — não é rigor decorativo: tabela que não encolhe vira ficção, e uma
#        dívida quitada que continua listada esconde a próxima.
#   D  **controle positivo**: o censo tem de ALCANÇAR os artefatos    → INCONCLUSIVO
#      — sem ele, "zero achados" e "não consegui ler nada" são o mesmo número.
#
# ⚠️ A tabela de dívida existe para o gate **nascer verde**. Um gate que nasce
# vermelho ensina todo mundo a ignorá-lo — é o defeito que o gêmeo Python descreve
# em maiúsculas. O que ele guarda é o que for NOVO.
#
# ⚠️ **LIMITE DECLARADO: form ARQUIVADO sai do catálogo e some do censo.** O
# `DELETE` de DialogForm é arquivar, não apagar (`adr-dialog-form-deletion`), e o
# dialog-api continua servindo por id — mas o form some da lista, que é por onde
# este censo anda. O ponto cego é limitado de propósito: arquivado não é
# vinculável a contato novo, então um alias ali não pode estrear um defeito; ele
# só alcança contato em andamento e história já encerrada. Medido ao falsear o
# ramo B: a isca publicada ficou `deleted_at` e saiu da lista.
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# Uso:  bash infra/test/gate_published_alias_census.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AR="${AGENT_REGISTRY_URL:-http://localhost:3300}"
DA="${DIALOG_API_URL:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
echo "${BLD}gate_published_alias_census — artefato publicado lendo alias de core.*${RST}"
echo

command -v python3 >/dev/null || { echo "  ${YEL}—${RST} INCONCLUSIVO: python3 ausente"; exit 2; }
curl -sf -o /dev/null "$AR/v1/pools?tenant_id=$TENANT" -H "x-tenant-id: $TENANT" \
  || { echo "  ${YEL}—${RST} INCONCLUSIVO: agent-registry não responde em $AR"; exit 2; }
curl -sf -o /dev/null "$DA/v1/dialog/forms" -H "X-Tenant-ID: $TENANT" \
  || { echo "  ${YEL}—${RST} INCONCLUSIVO: dialog-api não responde em $DA"; exit 2; }

cd "$RAIZ" && AR="$AR" DA="$DA" TENANT="$TENANT" python3 - <<'PY'
import io, json, os, re, sys, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
AR, DA, T = os.environ["AR"], os.environ["DA"], os.environ["TENANT"]
RED, GRN, YEL, RST = "\033[31m", "\033[32m", "\033[33m", "\033[0m"

# ── DÍVIDA DECLARADA ─────────────────────────────────────────────────────────
# Artefatos que JÁ liam alias quando este gate nasceu (censo de 2026-09-03) e que
# NÃO foram re-promovidos porque cada leitura só passa a funcionar se o PRODUTOR
# daquela tag também escrever a canônica — medir produtor por produtor é a metade
# que faltou na CNS-11. `wrapup_detached_ia` em especial FUNCIONA hoje: promovê-lo
# às cegas trocaria um verde por um vermelho.
#
# Quitar uma linha = re-promover o pool e APAGAR a linha. O ramo C reprova quem
# esquecer da segunda parte.
DIVIDA = {
    ("pool", "wrapup_detached_ia"): [
        "session.origin_session_id", "session.surveyed_agent_key",
        "session.surveyed_segment_id",
    ],
    ("pool", "outbound_survey_worker"): ["session.survey_grain"],
    ("pool", "survey_multi_ia"):        ["session.pool.id"],
    ("pool", "copilot_sac"):            ["session.sentimento.categoria"],
}

falhas = 0
def bad(m):
    global falhas
    print("  %s✗%s %s" % (RED, RST, m)); falhas += 1
def ok(m):  print("  %s✓%s %s" % (GRN, RST, m))
def inc(m):
    print("  %s—%s INCONCLUSIVO: %s" % (YEL, RST, m)); sys.exit(2)

def get(url, hdr):
    r = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())

H_AR = {"x-tenant-id": T}
H_DA = {"X-Tenant-ID": T}

# ── alias → canônica `core.*`, do mapa declarado ─────────────────────────────
try:
    t = io.open("packages/schemas/src/context-map.ts", encoding="utf-8").read()
except Exception as e:
    inc("não li o context-map (%s)" % e)

alias_core, escopo, dominio = {}, None, None
for l in t.split("\n"):
    m = re.match(r'^\s{4}(\w+):\s*\{', l)
    if m: escopo, dominio = m.group(1), None; continue
    m = re.match(r'^\s{6}(\w+):\s*\{', l)
    if m: dominio = m.group(1); continue
    m = re.match(r'^\s{8}(\w+):\s*\{(.*)', l)
    if m and escopo and dominio:
        canon = "%s.%s.%s" % (escopo, dominio, m.group(1))
        if canon.startswith("core."):
            for a in re.findall(r'"([a-z0-9_.]+)"', m.group(2)):
                if a != canon and "." in a:
                    alias_core[a] = canon
if len(alias_core) < 20:
    inc("o mapa rendeu só %d aliases de `core.*` — parse suspeito, e censo com "
        "índice incompleto acha menos do que existe" % len(alias_core))

def aliases_em(texto):
    return sorted({a for a in alias_core if ("@ctx." + a) in texto})

# ── censo A — snapshots de slot `current` ────────────────────────────────────
achados, vistos_pools, vistos_forms = {}, 0, 0
try:
    pools = get("%s/v1/pools?tenant_id=%s" % (AR, T), H_AR)["pools"]
except Exception as e:
    inc("não listei pools (%s)" % e)

for p in pools:
    pid = p["pool_id"]
    try:
        sl = get("%s/v1/pools/%s/slots" % (AR, pid), H_AR)
    except Exception:
        continue
    sl = sl.get("slots", sl)
    cur = sl.get("current") if isinstance(sl, dict) else None
    if not cur or not cur.get("skill_id"):
        continue
    vistos_pools += 1
    lidos = aliases_em(json.dumps(cur.get("yaml_snapshot") or {}, ensure_ascii=False))
    if lidos:
        achados[("pool", pid)] = lidos

# ── censo B — DialogForms publicados ─────────────────────────────────────────
try:
    lista = get("%s/v1/dialog/forms" % DA, H_DA)
    itens = lista if isinstance(lista, list) else lista.get("forms", lista.get("items", []))
except Exception as e:
    inc("não listei os DialogForms (%s)" % e)

for f in itens:
    fid = f.get("form_id")
    if not fid:
        continue
    try:
        pub = get("%s/v1/dialog/forms/%s?status=published" % (DA, fid), H_DA)
    except Exception:
        continue
    vistos_forms += 1
    lidos = aliases_em(json.dumps(pub.get("nodes") or [], ensure_ascii=False))
    if lidos:
        achados[("form", fid)] = lidos

# ── D — controle positivo ────────────────────────────────────────────────────
# Sem alcançar os artefatos, "zero achados" e "não li nada" sao o mesmo numero.
if vistos_pools == 0 or vistos_forms == 0:
    inc("censo não alcançou os artefatos (pools com slot=%d, forms publicados=%d) — "
        "zero achado aqui não seria evidência" % (vistos_pools, vistos_forms))
ok("D — censo alcançou %d snapshot(s) de slot e %d form(s) publicado(s)"
   % (vistos_pools, vistos_forms))

# ── A/B — achados NOVOS ──────────────────────────────────────────────────────
novos = {k: v for k, v in achados.items() if k not in DIVIDA}
for (kind, nome), lidos in sorted(novos.items()):
    bad("%s — %s '%s' PUBLICADO lê alias de canônica `core.*`: %s. O bridge/runner "
        "executa o artefato publicado, não o arquivo — re-promova (pool) ou "
        "republique (form) DEPOIS de conferir que o produtor de cada tag escreve a "
        "canônica"
        % ("A" if kind == "pool" else "B", "pool" if kind == "pool" else "form",
           nome, ", ".join("%s→%s" % (a, alias_core[a]) for a in lidos)))
if not novos:
    ok("A/B — nenhum artefato publicado NOVO lendo alias (%d na dívida declarada)"
       % len(DIVIDA))

# ── C — a tabela de dívida encolhe ───────────────────────────────────────────
for chave, esperados in sorted(DIVIDA.items()):
    kind, nome = chave
    lidos = achados.get(chave)
    if lidos is None:
        bad("C — a dívida declarada para o %s '%s' JÁ NÃO SE APLICA (nenhum alias "
            "lido). Apague a linha da tabela: dívida quitada que continua listada "
            "esconde a próxima" % (kind, nome))
    else:
        sobra = sorted(set(esperados) - set(lidos))
        if sobra:
            bad("C — a dívida do %s '%s' encolheu (%s já não é lido). Atualize a "
                "linha" % (kind, nome, ", ".join(sobra)))

if not falhas:
    print()
    print("  %s·%s dívida declarada, por artefato:" % (YEL, RST))
    for (kind, nome), a in sorted(DIVIDA.items()):
        print("      %-24s %s" % (nome, ", ".join(a)))

sys.exit(1 if falhas else 0)
PY
RC=$?

echo
if [ "$RC" -eq 2 ]; then exit 2; fi
if [ "$RC" -ne 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — artefato publicado fora do mapa, ou tabela de dívida desatualizada"
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — nada novo lendo alias; a dívida está declarada e é a mesma"
