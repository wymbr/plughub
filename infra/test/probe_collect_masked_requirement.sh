#!/usr/bin/env bash
#
# probe_collect_masked_requirement.sh — a exigência de canal do `collect` é
# DERIVADA da declaração `masked:`, e quem elege o canal CONSULTA capacidade.
# NIV-02 (ADR `adr-agent-flow-single-authored-level.md` § F2).
#
# POR QUE ESTE PROBE EXISTE
# =========================
# Medido em 2026-09-03, antes de escrever código. A leitura da ADR era *"o portão
# existe e falta o insumo"*; a medição achou três coisas, e só a primeira era a
# esperada:
#
#   1. **Nenhum YAML declara `requires:`** — o insumo de fato não existia.
#   2. **O portão que se ia alimentar está em RAMO MORTO.** `select_channel` é
#      chamada de `main.py::_dispatch_collect`, consumidor de `collect.requested`;
#      o único produtor (`workflow_api.kafka_emitter.emit_collect_requested`) tem
#      **zero chamadores** (AST, não `grep` — o nome aparece só no `import`), e as
#      duas rotas de collect da workflow-api respondem **410** desde o Arc 19 Fase
#      D. Alimentar `requires[]` ali não mudaria nada.
#   3. **A eleição VIVA é outra e era CEGA.** `WebhookAdapter._negotiate_channel`
#      escolhe por `preferred_order` e nunca perguntava o que o canal sabe fazer —
#      enquanto `requires` chegava até ela pelo corpo do POST e era **descartado**
#      (zero usos em `Load`, medido por AST).
#
# Duas implementações de eleição, e a que roda é a permissiva: mesma forma que a
# NIV-01 fechou um nível abaixo, no INVENTÁRIO de capacidade.
#
# O QUE ESTE PROBE PODE REPROVAR
# ==============================
#   A  a suíte de comportamento (18 testes na IMAGEM)                   → VERMELHO
#      Pares obrigatórios: derivar × não derivar · eleger × recusar ·
#      negociado × canal FIXO. Cada metade sozinha passa pelo motivo errado.
#   B  `handle_collect` deixar de repassar a exigência ao negociador    → VERMELHO
#   C  o ramo do `channel:` FIXO deixar de conferir capacidade          → VERMELHO
#      (é o bypass: um portão que o autor desliga escrevendo uma linha)
#   D  uma TERCEIRA casa de eleição de canal aparecer                   → VERMELHO
#   E  o ramo morto GANHAR produtor sem que alguém decida sobre ele     → VERMELHO
#      Não é defeito hoje; é o gatilho. Enquanto `emit_collect_requested`
#      tiver zero chamadores, `select_channel` é inalcançável e a única
#      eleição que decide é a do webhook. Ganhando produtor, passam a existir
#      duas outra vez — e aí a escolha tem de ser feita, não herdada.
#
# ⚠️ **A** roda contra a IMAGEM, nunca contra o container: `docker exec` responde
# sobre a instância, que pode ter estado posto à mão. Imagem sem o módulo ⇒
# **INCONCLUSIVO**, nunca verde por ausência.
#
# Uso:  bash infra/test/probe_collect_masked_requirement.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMG="${CG_IMAGE:-plughub-demo-channel-gateway:latest}"
RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
warn() { echo "  ${YEL}—${RST} $*"; }

echo "${BLD}probe_collect_masked_requirement — exigência derivada, eleição que consulta${RST}"
echo

# ── A — comportamento, na IMAGEM ─────────────────────────────────────────────
TESTE="src/plughub_channel_gateway/tests/test_collect_masked_requirement.py"
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  warn "A — imagem $IMG ausente: INCONCLUSIVO (rebuild o channel-gateway)"
  FAIL=$((FAIL+1))
elif ! docker run --rm "$IMG" test -f "$TESTE" >/dev/null 2>&1; then
  warn "A — a IMAGEM não tem $TESTE: INCONCLUSIVO (imagem velha, não suíte verde)"
  FAIL=$((FAIL+1))
else
  SAIDA_A="$(docker run --rm "$IMG" python -m pytest "$TESTE" -q 2>&1 | tail -n 3)"
  if echo "$SAIDA_A" | grep -qE '^[0-9]+ passed'; then
    ok "A — $(echo "$SAIDA_A" | grep -E '^[0-9]+ passed')"
  else
    bad "A — suíte de comportamento reprovou:"
    printf '%s\n' "$SAIDA_A" | sed 's/^/      /'
  fi
fi

# ── B/C/D/E — costura e censo, por AST ───────────────────────────────────────
SAIDA="$(cd "$RAIZ" && python3 - <<'PY'
import ast, io, re

W  = "packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py"
KE = "packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py"
RT = "packages/workflow-api/src/plughub_workflow_api/router.py"

wsrc = io.open(W, encoding="utf-8").read()
warv = ast.parse(wsrc)

def func(arvore, nome):
    for n in ast.walk(arvore):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nome:
            return n
    return None

hc = func(warv, "handle_collect")
ng = func(warv, "_negotiate_channel")

# ── B — handle_collect repassa a exigência ao negociador ─────────────────────
if hc is None or ng is None:
    print("ERRO|B|nao achei handle_collect ou _negotiate_channel")
else:
    passou = False
    for ch in ast.walk(hc):
        if (isinstance(ch, ast.Call)
                and isinstance(ch.func, ast.Attribute)
                and ch.func.attr == "_negotiate_channel"):
            nomes = {a.id for a in ch.args if isinstance(a, ast.Name)}
            nomes |= {k.value.id for k in ch.keywords
                      if isinstance(k.value, ast.Name)}
            if "requires_efetivo" in nomes:
                passou = True
    print("OK|B|handle_collect repassa a exigencia derivada ao _negotiate_channel"
          if passou else
          "ERRO|B|_negotiate_channel e chamado SEM a exigencia derivada — o "
          "insumo volta a ser descartado, que e o defeito que a NIV-02 fechou")

# ── C — o ramo do `channel:` FIXO confere capacidade ─────────────────────────
if hc is None:
    print("ERRO|C|sem handle_collect")
else:
    trecho = ast.get_source_segment(wsrc, hc) or ""
    tem = "channel_satisfies(channel, requires_efetivo)" in trecho
    print("OK|C|o ramo do `channel:` fixo confere capacidade (sem bypass)" if tem else
          "ERRO|C|o ramo do `channel:` fixo NAO confere capacidade — escrever "
          "`channel: sms` no YAML volta a contornar a exigencia inteira")

# ── D — quantas casas ELEGEM canal ───────────────────────────────────────────
# Eleger = decidir qual canal recebe o contato. Duas hoje, e e conhecido:
# `_negotiate_channel` (viva) e `select_channel` (ramo morto). Uma TERCEIRA e
# reprovacao — a NIV-01 acabou de pagar o preco de duas casas no inventario.
CASAS = {
    "channel_capability_registry.select_channel": "morta (sem produtor de evento)",
    "adapters.webhook._negotiate_channel":        "VIVA",
}
achadas = []
for cam, mod in (
    ("packages/channel-gateway/src/plughub_channel_gateway/channel_capability_registry.py",
     "channel_capability_registry"),
    ("packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py",
     "adapters.webhook"),
):
    a = ast.parse(io.open(cam, encoding="utf-8").read())
    for n in ast.walk(a):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name in (
                "select_channel", "_negotiate_channel"):
            achadas.append("%s.%s" % (mod, n.name))
extra = sorted(set(achadas) - set(CASAS))
sumiu = sorted(set(CASAS) - set(achadas))
if extra or sumiu:
    print("ERRO|D|casas de eleicao mudaram: novas=%s sumidas=%s (declare no probe "
          "e diga QUAL decide)" % (extra or "-", sumiu or "-"))
else:
    print("OK|D|2 casas de eleicao, ambas declaradas (1 viva, 1 em ramo morto)")

# ── E — o ramo morto continua sem produtor ───────────────────────────────────
chamadas = 0
for cam in (KE, RT):
    a = ast.parse(io.open(cam, encoding="utf-8").read())
    chamadas += sum(1 for n in ast.walk(a)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "emit_collect_requested")
if chamadas == 0:
    print("OK|E|emit_collect_requested segue com ZERO chamadores — select_channel "
          "inalcancavel, uma unica eleicao decide")
else:
    print("ERRO|E|emit_collect_requested ganhou %d chamador(es): `collect.requested` "
          "volta a fluir e passam a existir DUAS eleicoes de canal decidindo. "
          "Escolha uma (a viva ja consulta capacidade; a outra usa `requires[]` "
          "declarado e nao derivado) antes de religar o produtor" % chamadas)
PY
)"

while IFS='|' read -r st ramo msg; do
  [ -z "${st:-}" ] && continue
  if [ "$st" = "OK" ]; then ok "$ramo — $msg"; else bad "$ramo — $msg"; fi
done <<< "$SAIDA"

# ⚠️ O bloco AST tem de ter PRODUZIDO os quatro ramos. Sem esta conferência, um
# `SyntaxError` num dos arquivos analisados mata o Python inteiro, o `while` não
# itera, nenhum `bad` é chamado — e o probe fica VERDE por ausência de veredicto.
# Foi assim que a mutação E sobreviveu na primeira bateria de falseabilidade: ela
# quebrou o parse do `router.py`, e o silêncio passou por aprovação.
VISTOS="$(printf '%s
' "$SAIDA" | cut -d'|' -f2 | tr -d ' ' | sort -u | tr '
' ',')"
for r in B C D E; do
  case ",$VISTOS" in
    *",$r,"*) ;;
    *) bad "$r — RAMO AUSENTE: o censo AST não chegou a julgar (parse quebrado?). Veredicto que não sai não é veredicto que passa" ;;
  esac
done

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s)"; exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — a exigência é derivada, a eleição viva a consulta, e o canal fixo não escapa"
