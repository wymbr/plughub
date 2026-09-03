#!/usr/bin/env bash
#
# probe_masked_channel_gate.sh — valor MASCARADO não sai por canal que não sabe
# mascarar, e o deploy impossível não chega a existir. NIV-03 (ADR § F3).
#
# POR QUE ESTE PROBE EXISTE
# =========================
# MSK-01, medida em 2026-09-03: o pool `limite_ia` declara `[webchat, whatsapp]` e
# roda um fluxo que mascara CVV. No WhatsApp o campo virava **formulário comum** —
# sem fallback, sem aviso, sem recusa. O `supports_masked_input: false` que a
# tabela antiga atribuía a whatsapp/sms/email era **comentário sem leitor**.
# Exposição real; dano medido ~zero (2 sessões WhatsApp na instalação inteira).
# Não era incidente: era porta aberta.
#
# AS DUAS METADES, e por que nenhuma substitui a outra
# ====================================================
#   RUNTIME (`notification_send`, mcp-server) — é onde o vazamento acontece e
#     onde ele para. Recusar ali é ANTES do Kafka (nada mascarado é publicado) e
#     devolve `isError`, que o `menu` step já converte em `on_failure` — sem
#     protocolo novo e sem esperar `timeout_s` inteiro.
#   DEPLOY (`set-next`/`promote`, agent-registry) — pega o pool em que NENHUM
#     canal sabe mascarar: ali o deploy não funciona em contato nenhum, e o
#     runtime só descobriria isso com cliente do outro lado.
#
# ⚠️ **Bloquear × avisar não é gradação de rigor, é natureza da pergunta.** Pool
# sem nenhum canal capaz é estático ⇒ 422. Pool PARCIALMENTE capaz depende de por
# onde o contato chega ⇒ aviso (corpo da resposta **e** log). Recusar o parcial
# proibiria a configuração legítima que `auth_ia` e `auth_form_ia` têm hoje.
#
# O QUE ESTE PROBE PODE REPROVAR
# ==============================
#   A  suíte de RUNTIME (mcp-server, na IMAGEM)                        → VERMELHO
#      Pares: recusar × entregar · mascarado × não mascarado ·
#      cliente × `agents_only`. E a asserção que mais importa não é o
#      `isError`: é que NADA foi publicado em `conversations.outbound`.
#   B  suíte de DEPLOY (agent-registry, na IMAGEM)                     → VERMELHO
#   C  a guarda sair de `notification_send`, ou passar a rodar DEPOIS
#      do publish em `conversations.outbound`                          → VERMELHO
#   D  o julgamento de deploy sumir do `set-next` ou do `promote`, ou
#      APARECER no `rollback` (emergência nunca bloqueia)              → VERMELHO
#   E  `masked_fallback` ganhar leitor sem que a guarda o consulte     → VERMELHO
#      — não é defeito hoje: `MaskedFallbackPolicySchema` prevê `message` e
#        `link`, e **não existe namespace `masking` no config-api** (medido:
#        `GET /v1/config/masking` → 404). Sem política, o restritivo é o único
#        desfecho honesto. Ganhando leitor, a recusa deixa de ser o único.
#
# Uso:  bash infra/test/probe_masked_channel_gate.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMG_MCP="${MCP_IMAGE:-plughub-demo-mcp-server-plughub:latest}"
IMG_REG="${REG_IMAGE:-plughub-demo-agent-registry:latest}"
RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
warn() { echo "  ${YEL}—${RST} $*"; }

echo "${BLD}probe_masked_channel_gate — mascarado não sai por canal incapaz${RST}"
echo

# ── suítes, contra a IMAGEM (nunca `docker exec`: aquilo responde sobre a
#    instância, que pode ter estado posto à mão) ───────────────────────────────
suite() {
  local ramo="$1" img="$2" pkg="$3" arquivo="$4"
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    warn "$ramo — imagem $img ausente: INCONCLUSIVO"; FAIL=$((FAIL+1)); return
  fi
  # `sh -c`, NUNCA `docker run IMG test -f ...`: o entrypoint do node image
  # reescreve o argv (prepende `node` conforme o 1o argumento), e o veredicto
  # passa a responder outra pergunta. Medido: com caminho ABSOLUTO devolvia 1
  # para arquivo que o `ls` do MESMO container mostra.
  if ! docker run --rm "$img" sh -c "test -f /app/packages/$pkg/$arquivo" >/dev/null 2>&1; then
    warn "$ramo — a IMAGEM não tem $arquivo: INCONCLUSIVO (imagem velha, não suíte verde)"
    FAIL=$((FAIL+1)); return
  fi
  local out
  out="$(docker run --rm "$img" sh -c \
    "cd /app/packages/$pkg && ./node_modules/.bin/vitest run $arquivo 2>&1" \
    | grep -E '^ +Tests +' | tail -1)"
  if [ -z "$out" ]; then
    bad "$ramo — vitest não produziu sumário (execução abortou)"; return
  fi
  case "$out" in
    *failed*) bad "$ramo — $(echo "$out" | tr -s ' ')" ;;
    *)        ok  "$ramo — $(echo "$out" | tr -s ' ')" ;;
  esac
}

suite "A (runtime)" "$IMG_MCP" "mcp-server-plughub" \
      "src/__tests__/masked-menu-channel-gate.test.ts"
suite "B (deploy)"  "$IMG_REG" "agent-registry" \
      "src/__tests__/masked-deploy.test.ts"

# ── C/D/E — costura, por leitura do fonte ────────────────────────────────────
SAIDA="$(cd "$RAIZ" && python3 - <<'PY'
import io, re

BPM = "packages/mcp-server-plughub/src/tools/bpm.ts"
SLT = "packages/agent-registry/src/routes/pool-slots.ts"

bpm = io.open(BPM, encoding="utf-8").read()

# ── C — a guarda existe e vem ANTES do primeiro publish do menu ──────────────
i_guarda  = bpm.find("masked_input_unsupported")
i_publish = bpm.find('type:          "menu.payload"')
if i_guarda < 0:
    print("ERRO|C|a guarda de runtime SUMIU de notification_send — menu mascarado "
          "volta a sair por qualquer canal (MSK-01)")
elif i_publish < 0:
    print("ERRO|C|nao achei o publish de menu.payload — o probe perdeu a referencia")
elif i_guarda > i_publish:
    print("ERRO|C|a guarda esta DEPOIS do publish em conversations.outbound: recusar "
          "e publicar ao mesmo tempo e o pior dos dois mundos (o fluxo falha e o "
          "valor vaza assim mesmo)")
else:
    print("OK|C|guarda presente e ANTES do publish de menu.payload")

# ── D — o julgamento de deploy nos dois momentos, e NUNCA no rollback ────────
slt = io.open(SLT, encoding="utf-8").read()

def bloco(nome, ini, fim=None):
    a = slt.find(ini)
    if a < 0:
        return None
    b = slt.find(fim) if fim else len(slt)
    return slt[a:b if b > a else len(slt)]

set_next = bloco("set-next", 'poolSlotsRouter.put("/slots/:slot"',
                 'poolSlotsRouter.post("/promote"')
promote  = bloco("promote",  'poolSlotsRouter.post("/promote"',
                 'poolSlotsRouter.post("/rollback"')
rollback = bloco("rollback", 'poolSlotsRouter.post("/rollback"')

faltando = [n for n, b in (("set-next", set_next), ("promote", promote))
            if b is None or "judgeMaskedDeploy(" not in b]
if faltando:
    print("ERRO|D|judgeMaskedDeploy ausente em: %s — o momento barato do NIV-03 "
          "deixa de existir" % ", ".join(faltando))
elif rollback is not None and "judgeMaskedDeploy(" in rollback:
    print("ERRO|D|judgeMaskedDeploy no ROLLBACK — operacao de emergencia nunca "
          "bloqueia (mesma isencao do deployViolation de capacidade)")
else:
    print("OK|D|julgamento no set-next e no promote; rollback isento")

# ── E — masked_fallback continua sem leitor ─────────────────────────────────
# `grep` contaria a propria declaracao e os comentarios que a explicam; o que
# interessa e CONSUMO: alguem lendo o campo para decidir.
leitores = []
for cam in ("packages/mcp-server-plughub/src", "packages/channel-gateway/src",
            "packages/skill-flow-engine/src", "packages/agent-registry/src"):
    import os
    for raiz, _, arqs in os.walk(cam):
        if "node_modules" in raiz or "/dist" in raiz or "__pycache__" in raiz:
            continue
        for a in arqs:
            if not a.endswith((".ts", ".py")) or a.endswith(".d.ts"):
                continue
            p = os.path.join(raiz, a)
            try:
                t = io.open(p, encoding="utf-8").read()
            except Exception:
                continue
            for m in re.finditer(r'^(?!\s*(?://|#|\*)).*\bmasked_fallback\b', t, re.M):
                leitores.append("%s:%d" % (p, t[:m.start()].count("\n") + 1))

if not leitores:
    print("OK|E|masked_fallback segue SEM leitor — a recusa e o unico desfecho, e a "
          "mensagem da guarda diz isso")
else:
    print("ERRO|E|masked_fallback ganhou leitor(es) (%s): a guarda de runtime tem de "
          "CONSULTAR a politica antes de recusar — `message` e `link` deixaram de "
          "ser hipoteticos" % ", ".join(leitores[:4]))
PY
)"

while IFS='|' read -r st ramo msg; do
  [ -z "${st:-}" ] && continue
  if [ "$st" = "OK" ]; then ok "$ramo — $msg"; else bad "$ramo — $msg"; fi
done <<< "$SAIDA"

# ⚠️ Se o bloco Python morrer (SyntaxError em qualquer arquivo lido), o `while`
# não itera, nenhum `bad` é chamado e o probe fica VERDE por ausência de
# veredicto. Já aconteceu no gate irmão da NIV-02.
VISTOS="$(printf '%s\n' "$SAIDA" | cut -d'|' -f2 | tr -d ' ' | sort -u | tr '\n' ',')"
for r in C D E; do
  case ",$VISTOS" in
    *",$r,"*) ;;
    *) bad "$r — RAMO AUSENTE: o censo não chegou a julgar. Veredicto que não sai não é veredicto que passa" ;;
  esac
done

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s)"; exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — runtime recusa antes do Kafka, deploy impossível não passa, parcial avisa"
