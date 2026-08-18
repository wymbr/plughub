#!/usr/bin/env bash
# gate_bg_task_visibility.sh — Pendência 4 do handoff de 2026-08-18.
#
# O DEFEITO. O bridge dispara 77 corrotinas em fire-and-forget. Nenhuma guardava
# referência forte (a doc do asyncio pede: o loop só guarda referência FRACA, e uma
# task pode ser coletada no meio da execução) e nenhuma tinha `add_done_callback`.
# A consequência cara não é a coleta — é a ATRIBUIÇÃO: uma exceção não recuperada
# vira, na melhor das hipóteses, um "Task exception was never retrieved" tardio e
# **sem session_id**. Num serviço com N sessões em paralelo isso diz que algo
# morreu, não QUAL contato quebrou.
#
# O que torna a dívida concreta: uma das 77 é o `participant_left` de
# `main.py:4653` — a linha que o `gate_family_b_resume_closes.sh` acabou de
# validar. Um verde em máquina ociosa não cobre o loop coletando a task antes do
# publish.
#
# COMO ESTE GATE JULGA — DIFERENCIAL, não controle negativo.
# "Teste também mora na imagem": provar o antes exigiria stash + rebuild. Não é
# preciso — os DOIS caminhos existem no mesmo binário. O gate levanta a MESMA
# exceção por `_spawn` e por `asyncio.create_task` cru, no MESMO processo e na
# MESMA janela, e afirma a DIVERGÊNCIA entre as duas leituras:
#
#   tratamento (`_spawn`)          → 1 linha ERROR no logger do bridge, com o NOME
#                                    da corrotina e o `session_id` no texto
#   controle (`create_task` cru)   → nada nomeia a sessão na mesma janela
#
# ⚠️ MEDIDO 2026-08-18, e corrige a leitura ingênua: o caminho cru NÃO é mudo. Ele
# emite "Task exception was never retrieved" e até nomeia a corrotina (via repr do
# coro). O que ele não consegue dizer é QUAL SESSÃO — o repr não carrega os
# argumentos —, e ele chega ~300 ms depois, porque espera o GC. A diferença é
# atribuição e pontualidade, não presença. Descrever como "mudo × falante" faria o
# gate parecer provar mais do que prova.
#
# Se as duas leituras vierem IGUAIS, o gate reprova — inclusive se as duas vierem
# "verdes": duas leituras quebradas também são iguais entre si.
#
# TESTEMUNHA: `TRATADO_N` tem de ser 1. Se for 0, o handler não rodou e o
# `CONTROLE_N=0` seria "o leitor não lê", não "o controle é mudo".
#
# Uso (raiz do repo, demo no ar):  bash infra/test/gate_bg_task_visibility.sh
# Sai: 0 = VERDE · 1 = REPROVOU · 2 = INCONCLUSIVO

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SVC="${SVC:-orchestrator-bridge}"
SRC="/app/packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py"

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⛔ INCONCLUSIVO: $1"; exit 2; }

echo "══ Pendência 4 — task de fundo morre CALADA? · serviço=$SVC ══"
echo

# ── 1. PREFLIGHT DE CÓDIGO — conferir o que RODA, não o que está no repo ───────
# O container não monta o fonte: mudança em main.py só chega por `build`. Contar
# no repositório mediria a intenção; contar aqui mede o binário.
echo "── 1. o que roda no container ────────────────────────────────────────────"
$DC exec -T "$SVC" test -f "$SRC" < /dev/null 2>/dev/null \
  || die "não achei $SRC dentro do container. Serviço no ar? Nome certo em SVC=?"

# ⚠️ Casar o TOKEN DA CHAMADA (`asyncio.create_task(`, com parêntese), nunca o
# identificador solto: os comentários desta própria mudança citam o nome e
# recomporiam o número antigo.
N_RAW=$($DC exec -T "$SVC" grep -c 'asyncio\.create_task(' "$SRC" < /dev/null 2>/dev/null | tr -d '\r')
N_SPAWN=$($DC exec -T "$SVC" grep -c '_spawn(' "$SRC" < /dev/null 2>/dev/null | tr -d '\r')
echo "   asyncio.create_task( = ${N_RAW:-?}   ·   _spawn( = ${N_SPAWN:-?}"

case "${N_RAW:-x}" in
  ''|*[!0-9]*) die "contagem não numérica ('$N_RAW') — o grep no container falhou" ;;
esac
case "${N_SPAWN:-x}" in
  ''|*[!0-9]*) die "contagem não numérica ('$N_SPAWN')" ;;
  0)           die "nenhum '_spawn(' no fonte que roda — o build da mudança não chegou.
        Rodar: docker compose -f docker-compose.demo.yml build $SVC && up -d $SVC
        (\`restart\` NÃO basta: recarrega a imagem antiga)." ;;
esac

# Exatamente UMA `asyncio.create_task(` deve sobreviver: a de dentro do `_spawn`.
# Conferir o NÚMERO não basta — tem de ser a linha certa.
if [ "${N_RAW:-0}" -eq 1 ]; then
  LINHA=$($DC exec -T "$SVC" grep -n 'asyncio\.create_task(' "$SRC" < /dev/null 2>/dev/null | tr -d '\r')
  echo "   única sobrevivente: $LINHA"
  if printf '%s' "$LINHA" | grep -q 'task = asyncio\.create_task(coro, name=name)'; then
    ok "a única create_task crua é a de dentro do _spawn — migração completa"
  else
    bad "sobrou 1 create_task, mas NÃO é a do _spawn: $LINHA"
  fi
else
  bad "$N_RAW create_task cruas no fonte que roda (esperado 1, a do _spawn).
       $(( N_RAW - 1 )) site(s) escapou(aram) da migração."
fi
echo

# ── 2. O DIFERENCIAL — mesma exceção, dois caminhos, mesma janela ─────────────
echo "── 2. diferencial: _spawn × create_task cru, no mesmo processo ───────────"

# Sem aspas simples no corpo do Python: ele viaja dentro de aspas simples do bash.
PY='
import sys, asyncio, logging
sys.path.insert(0, "/app/packages/orchestrator-bridge/src")
try:
    import plughub_orchestrator_bridge.main as m
except Exception as e:
    print("IMPORT_ERRO=%s: %s" % (type(e).__name__, e)); raise SystemExit(9)

if not hasattr(m, "_spawn"):
    print("SEM_SPAWN=1"); raise SystemExit(9)

vistos = []
class Cap(logging.Handler):
    def emit(self, r):
        try:
            vistos.append((r.name, r.levelname, r.getMessage()))
        except Exception:
            pass

raiz = logging.getLogger()
raiz.addHandler(Cap())
raiz.setLevel(logging.DEBUG)

async def explode(session_id, tenant_id):
    raise RuntimeError("falha proposital do gate")

async def run():
    # TRATAMENTO
    m._spawn(explode("sess_tratado_gate", "tenant_demo"))
    await asyncio.sleep(0.3)
    # CONTROLE — mesma corrotina, mesma exceção, spawn cru
    asyncio.create_task(explode("sess_controle_gate", "tenant_demo"))
    await asyncio.sleep(0.3)

    trat = [t for t in vistos if "sess_tratado_gate" in t[2]]
    ctrl = [t for t in vistos if "sess_controle_gate" in t[2]]
    nomeou = [t for t in trat if "explode" in t[2]]
    erro   = [t for t in trat if t[1] == "ERROR"]

    print("TRATADO_N=%d" % len(trat))
    print("TRATADO_ERROR_N=%d" % len(erro))
    print("TRATADO_NOMEOU_COROTINA=%d" % len(nomeou))
    print("CONTROLE_N=%d" % len(ctrl))
    print("RETIDAS_APOS=%d" % len(m._BG_TASKS))
    if trat:
        print("TRATADO_MSG=%s" % trat[0][2].replace(chr(10), " ")[:240])
    # OBSERVACAO, nao criterio: o que o caminho CRU emite de fato. O asyncio loga
    # no __del__ da task, e a hora do GC nao e garantida — por isso isto so
    # informa. O ponto nao e que o caminho cru seja mudo; e que ele nao consegue
    # dizer QUAL sessao, porque o repr da corrotina nao carrega os argumentos.
    # Filtrar pelo TEXTO da mensagem, nao so pelo logger: a 1a versao pegava
    # qualquer registro do logger asyncio e CRU_MSG saiu com o DEBUG
    # "Using selector: EpollSelector" — linha de observacao mostrando a coisa
    # errada. Medido em 2026-08-18: CRU_N previsto 1, veio 2.
    cru = [t for t in vistos if t[0] == "asyncio" and "never retrieved" in t[2]]
    print("CRU_N=%d" % len(cru))
    if cru:
        print("CRU_MSG=%s" % cru[0][2].replace(chr(10), " ")[:240])

asyncio.run(run())
'

OUT=$($DC exec -T "$SVC" python3 -c "$PY" < /dev/null 2>&1)
echo "$OUT" | sed 's/^/   | /'
echo

printf '%s' "$OUT" | grep -q 'IMPORT_ERRO=' \
  && die "o módulo não importa dentro do container — nada foi medido"
printf '%s' "$OUT" | grep -q 'SEM_SPAWN=1' \
  && die "o módulo importou mas NÃO tem _spawn — imagem antiga (faltou build)"

val() { printf '%s' "$OUT" | grep -E "^$1=" | head -1 | cut -d= -f2 | tr -d '\r'; }
TRAT=$(val TRATADO_N); TERR=$(val TRATADO_ERROR_N)
TNOME=$(val TRATADO_NOMEOU_COROTINA); CTRL=$(val CONTROLE_N); RET=$(val RETIDAS_APOS)

# ── TESTEMUNHA ANTES DO VEREDICTO ─────────────────────────────────────────────
case "${TRAT:-x}" in
  ''|*[!0-9]*) die "TRATADO_N ausente/não numérico ('$TRAT') — o Python não chegou ao fim" ;;
  0)           die "o caminho TRATADO não produziu linha nenhuma. CONTROLE_N=${CTRL:-?} aqui
        significa 'o leitor não lê', NÃO 'o controle é mudo'. Sem esta testemunha
        as duas leituras seriam iguais por quebra, não por igualdade real." ;;
esac
ok "o tratamento produziu $TRAT linha(s) — o leitor lê"

[ "${TERR:-0}" -ge 1 ] 2>/dev/null \
  && ok "a falha saiu em nível ERROR (não fica abaixo do corte de log do serviço)" \
  || bad "nenhuma linha ERROR: a falha existe mas não sobe ao nível que alguém olha"

[ "${TNOME:-0}" -ge 1 ] 2>/dev/null \
  && ok "a linha NOMEIA a corrotina ('explode') — dá para ir atrás do caminho" \
  || bad "a linha não nomeia a corrotina; volta a ser 'algo morreu em algum lugar'"

# ── A DIVERGÊNCIA, que é o que o gate afirma ──────────────────────────────────
case "${CTRL:-x}" in
  ''|*[!0-9]*) die "CONTROLE_N não numérico ('$CTRL')" ;;
  0) ok "DIVERGÊNCIA: mesma exceção pelo caminho cru não nomeou a sessão em lugar
       nenhum na mesma janela — é exatamente a dívida que o helper fecha" ;;
  *) bad "as duas leituras vieram IGUAIS ($TRAT × $CTRL): o caminho cru também
       nomeou a sessão. Ou o ambiente já tinha um handler global, ou o gate está
       medindo outra coisa — não aceitar como verde." ;;
esac

# ── vazamento: a referência forte tem de ser SOLTA no fim ─────────────────────
[ "${RET:-1}" -eq 0 ] 2>/dev/null \
  && ok "_BG_TASKS voltou a 0 — a retenção solta a referência no done" \
  || bad "sobraram ${RET:-?} task(s) em _BG_TASKS: a retenção virou vazamento,
       que é trocar um defeito raro por um crescimento sem fim"

echo
echo "   ✅ $PASS · ❌ $FAIL"
echo
echo '   ⚠️  LIMITE DECLARADO: este gate prova ATRIBUIÇÃO (a falha vira linha'
echo '      nomeada). NÃO prova que a coleta precoce acontecia nem que parou —'
echo '      isso pediria pressão de GC reproduzível, que ninguém tem. A retenção'
echo '      entra como conformidade com a doc do asyncio, não como conserto medido.'
[ "$FAIL" -eq 0 ] && { echo "   ✅ TASK DE FUNDO NÃO MORRE MAIS CALADA"; exit 0; }
echo "   ❌ REPROVOU"; exit 1
