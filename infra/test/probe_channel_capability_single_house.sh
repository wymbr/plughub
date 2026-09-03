#!/usr/bin/env bash
#
# probe_channel_capability_single_house.sh — capacidade de canal tem UMA casa, e a
# tabela é exaustiva sobre o domínio de canais (NIV-01).
#
# POR QUE ESTE PROBE EXISTE
# =========================
# Medido em 2026-09-03, antes do conserto — três defeitos, todos mudos:
#
#   1. **Duas casas, e nem no mesmo vocabulário.** A que roda é
#      `channel_capability_registry.CHANNEL_CAPABILITIES` (`X ∈ caps`, alimentando
#      `collect.requires[]`); a outra era `ChannelCapabilitiesSchema` em
#      `channel-events.ts` (`supports_X: boolean`, config por tenant). Duas respostas
#      para o mesmo fato significam que a permissiva vale.
#   2. **E elas já discordavam**, em `voice`: o schema afirmava
#      `supports_masked_input: true — DTMF nativo`; o registry não declara
#      `masked_input` para voz. A divergência estava dormente porque o schema tinha
#      **zero consumidores** — dar-lhe um leitor teria feito voz ganhar capacidade de
#      mascaramento em silêncio.
#   3. **A tabela cobria 6 dos 9 canais do `ChannelSchema`.** `instagram` e `telegram`
#      caíam em `.get(ch, frozenset())`, satisfaziam requisito nenhum e **nunca eram
#      eleitos** — restritivo, que é o default certo, mas MUDO, que não é.
#
# O QUE ESTE PROBE PODE REPROVAR
# ==============================
#   A  canal do domínio sem linha na tabela (ou linha para canal inexistente) → VERMELHO
#   B  capacidade fora do vocabulário do `ChannelCapabilitySchema`            → VERMELHO
#   C  uma SEGUNDA casa de capacidade reaparecer                              → VERMELHO
#   D  canal elegível fora de `_CHANNEL_PRIORITY` (desempate por acidente)    → VERMELHO
#   E  **`voice` ganhar `masked_input`**                                      → VERMELHO
#      — é a testemunha de segurança. São TRÊS impedimentos empilhados, e a
#        primeira redação deste probe citava só o primeiro:
#          (a) o canal não está provisionado (Arc 15) — resolve-se por DEPLOY;
#          (b) não existe supressão de DTMF, nem em transcript nem em gravação,
#              e DTMF é decodificável do áudio gravado — é TRABALHO (NIV-06);
#          (c) a definição da capacidade é por MECANISMO
#              (`password-overlay … (webchat)`), o que exclui voz por
#              construção — enquanto for assim, (a) e (b) não bastam (NIV-05).
#        ⚠️ Nada disto restringe o TRATAMENTO de eco em voz (`plain` verbaliza
#        o dígito · `masked` bipa · `none` cala): esse é o `EchoMode` da ALW-10,
#        traduzido pelo adapter, e está intacto. O que E guarda é ELEIÇÃO.
#
# Uso:  bash infra/test/probe_channel_capability_single_house.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RED=$'\e[31m'; GRN=$'\e[32m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }

echo "${BLD}probe_channel_capability_single_house — uma casa, tabela exaustiva${RST}"
echo

SAIDA="$(cd "$RAIZ" && python3 - <<'PY'
import ast, io, re, sys

REG = "packages/channel-gateway/src/plughub_channel_gateway/channel_capability_registry.py"
COMMON = "packages/schemas/src/common.ts"
SKILL = "packages/schemas/src/skill.ts"

fonte = io.open(REG, encoding="utf-8").read()

# ── a tabela, por AST — `grep` contaria os nomes citados nos comentários ─────
tabela, prioridade = None, None
for no in ast.parse(fonte).body:
    if not isinstance(no, ast.AnnAssign) or not isinstance(no.target, ast.Name):
        continue
    if no.target.id == "CHANNEL_CAPABILITIES":
        tabela = {
            k.value: {a.value for a in v.args[0].elts} if v.args else set()
            for k, v in zip(no.value.keys, no.value.values)
        }
    elif no.target.id == "_CHANNEL_PRIORITY":
        prioridade = [e.value for e in no.value.elts]

if tabela is None or prioridade is None:
    print("ERRO|A|nao achei CHANNEL_CAPABILITIES ou _CHANNEL_PRIORITY por AST"); sys.exit()

def enum_ts(caminho, nome):
    t = io.open(caminho, encoding="utf-8").read()
    m = re.search(r"export const %s = z\.enum\(\[(.*?)\]\)" % nome, t, re.S)
    return set(re.findall(r'"([a-z_]+)"', m.group(1))) if m else set()

canais = enum_ts(COMMON, "ChannelSchema")
caps = enum_ts(SKILL, "ChannelCapabilitySchema")

# ── A ────────────────────────────────────────────────────────────────────────
faltando = sorted(canais - set(tabela))
sobrando = sorted(set(tabela) - canais)
if faltando or sobrando:
    print("ERRO|A|sem linha: %s | linha sem canal: %s" % (faltando or "-", sobrando or "-"))
else:
    print("OK|A|%d canais do dominio, %d linhas — exaustiva" % (len(canais), len(tabela)))

# ── B ────────────────────────────────────────────────────────────────────────
fora = sorted({c for cs in tabela.values() for c in cs} - caps)
print("ERRO|B|capacidade fora do vocabulario: %s" % fora if fora
      else "OK|B|todas as capacidades pertencem ao ChannelCapabilitySchema (%d)" % len(caps))

# ── D ────────────────────────────────────────────────────────────────────────
# Só canais ELEGÍVEIS precisam de prioridade: quem não tem capacidade nenhuma
# nunca é eleito, e listá-lo sugeriria que poderia ser.
elegiveis = {ch for ch, cs in tabela.items() if cs}
sem_prio = sorted(elegiveis - set(prioridade))
print("ERRO|D|canal elegivel fora de _CHANNEL_PRIORITY (desempate por acidente): %s" % sem_prio
      if sem_prio else "OK|D|todos os %d canais elegiveis tem prioridade" % len(elegiveis))

# ── E — testemunha de seguranca ─────────────────────────────────────────────
if "masked_input" in tabela.get("voice", set()):
    print("ERRO|E|`voice` declara masked_input — tres impedimentos seguem de pe: canal "
          "nao provisionado (Arc 15), supressao de DTMF inexistente (NIV-06) e a "
          "definicao da capacidade por MECANISMO, que exclui voz por construcao "
          "(NIV-05). Isso o tornaria elegivel para um CVV num canal que nao funciona")
else:
    quem = sorted(ch for ch, cs in tabela.items() if "masked_input" in cs)
    print("OK|E|masked_input so em %s; voice permanece fora (gatilho no registry)" % quem)
PY
)"

while IFS='|' read -r st ramo msg; do
  [ -z "${st:-}" ] && continue
  if [ "$st" = "OK" ]; then ok "$ramo — $msg"; else bad "$ramo — $msg"; fi
done <<< "$SAIDA"

# ── C — a segunda casa não voltou ────────────────────────────────────────────
# Só linhas de CÓDIGO: os comentários citam os nomes antigos de propósito, para
# explicar a remoção. Proibir a palavra proibiria documentar.
C_HITS="$(cd "$RAIZ" && grep -rn "ChannelCapabilitiesSchema\|supports_masked_input" \
  --include=*.ts --include=*.py packages/ 2>/dev/null \
  | grep -v node_modules | grep -v "/dist/" \
  | grep -vE ':\s*(\*|//|#)' || true)"
if [ -z "$C_HITS" ]; then
  ok "C — nenhuma segunda casa de capacidade no código"
else
  bad "C — capacidade declarada fora do registry:"
  printf '%s\n' "$C_HITS" | sed 's/^/      /'
fi

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s)"; exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — uma casa, tabela exaustiva, e voice sem masked_input por decisão"
