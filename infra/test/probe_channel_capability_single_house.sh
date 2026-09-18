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
#   E  **`voice` declarar `masked_input` SEM os mecanismos que o tornam verdade** → VERMELHO
#      (até 2026-09-18 era "voice ganhar masked_input" → VERMELHO; a NIV-07 construiu os
#      mecanismos e o dono decidiu declarar. A testemunha mudou de proposição, não sumiu:
#      declaração sem mecanismo é a promessa que este ramo existe para pegar.)
#   F  o gêmeo Python DIVERGIR do canônico em `@plughub/schemas`             → VERMELHO
#      — desde a NIV-03 o canônico é `schemas/src/channel-capabilities.ts`, porque
#        dois decisores de capacidade são TypeScript (`notification_send` e o
#        `set-next`/`promote`). O gêmeo fica porque o gateway é Python. Duas cópias
#        com gate é o arranjo do `py-contextstore`; duas cópias SEM gate foi o
#        defeito que a NIV-01 removeu.
#      — é a testemunha de segurança. A lista abaixo é o HISTÓRICO de impedimentos que a
#        NIV-07 (2026-09-18) fechou; o ramo agora exige, no código, a pausa de mídia, a rota
#        de token fechada durante ela, a coleta desfeita quando alguém entra, a recusa da
#        perna Twilio e a recusa de `masked` + fala (NIV-08). O comportamento é provado ao
#        vivo pelo `probe_voz02_sip_inbound.sh` (K3/K4). As duas primeiras redações deste
#        probe erraram a lista:
#          (a) o canal não está provisionado (Arc 15) — resolve-se por DEPLOY;
#          (b) o TRATAMENTO de eco não existe no adapter (zero ocorrências de
#              "masked" em `voice.py`) — é lacuna, não vazamento (NIV-06);
#          (c) a negociação out-of-band do DTMF não é asserida (NIV-07);
#          (d) `masked` + `input_mode: voice` não é recusado — aí o cliente FALA
#              o valor e o STT o transcreve, e nenhum RFC ajuda (NIV-08);
#          (e) ~~a definição da capacidade é por MECANISMO~~ — resolvida em
#              2026-09-15 (NIV-05): definida pela GARANTIA quando o webrtc a
#              reivindicou.
#        ⚠️ **NÃO** está na lista "DTMF decodificável da gravação": era o
#        impedimento que eu citava e ele está ERRADO para SIP/WebRTC (o dígito
#        viaja fora do áudio) e inaplicável em CTI (a gravação é do PABX).
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
GW_WEBRTC = "packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc.py"
GW_ROUTER = "packages/channel-gateway/src/plughub_channel_gateway/adapters/voice_router.py"

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
def _funcoes(caminho):
    """Nomes de funções e métodos DEFINIDOS (AST: comentário não conta)."""
    arv = ast.parse(io.open(caminho, encoding="utf-8").read())
    return {n.name for n in ast.walk(arv) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

def _levanta(caminho, funcao, excecao):
    """`funcao` tem um `raise excecao(...)` de verdade?"""
    arv = ast.parse(io.open(caminho, encoding="utf-8").read())
    for n in ast.walk(arv):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == funcao:
            for r in ast.walk(n):
                if (isinstance(r, ast.Raise) and isinstance(r.exc, ast.Call)
                        and getattr(r.exc.func, "id", "") == excecao):
                    return True
    return False

if "masked_input" in tabela.get("voice", set()):
    gw = _funcoes(GW_WEBRTC)
    falta = [nome for nome, ok in (
        ("pausa de midia (_media_hold_start)", "_media_hold_start" in gw),
        ("liberacao da pausa (_media_hold_release)", "_media_hold_release" in gw),
        ("coleta desfeita quando alguem entra (_media_hold_intrusion)", "_media_hold_intrusion" in gw),
        ("rota de token fechada na pausa (get_token levanta MaskedCollectInProgress)",
         _levanta(GW_WEBRTC, "get_token", "MaskedCollectInProgress")),
        ("recusa da perna Twilio (VoiceChannelRouter.deliver_menu)",
         "RECUSADO na perna Twilio" in io.open(GW_ROUTER, encoding="utf-8").read()),
        ("NIV-08 no schema (masked + input voice recusado)",
         "menu mascarado não aceita input voice" in io.open(SKILL, encoding="utf-8").read()),
    ) if not ok]
    if falta:
        print("ERRO|E|`voice` declara masked_input SEM: %s — declaracao sem mecanismo" % "; ".join(falta))
    else:
        print("OK|E|voice declara masked_input COM os 6 mecanismos (pausa, liberacao, intrusao, "
              "token fechado, recusa Twilio, NIV-08) — comportamento: probe_voz02_sip_inbound K3/K4")
else:
    quem = sorted(ch for ch, cs in tabela.items() if "masked_input" in cs)
    print("OK|E|masked_input so em %s; voice fora" % quem)
PY
)"

while IFS='|' read -r st ramo msg; do
  [ -z "${st:-}" ] && continue
  if [ "$st" = "OK" ]; then ok "$ramo — $msg"; else bad "$ramo — $msg"; fi
done <<< "$SAIDA"

# ── F — o gêmeo Python bate com o canônico TS ────────────────────────────────
SAIDA_F="$(cd "$RAIZ" && python3 - <<'PY'
import ast, io, json, re

PY_ = "packages/channel-gateway/src/plughub_channel_gateway/channel_capability_registry.py"
TS_ = "packages/schemas/src/channel-capabilities.ts"

# gemeo: por AST, para nao contar nome citado em comentario
tab_py = None
for no in ast.parse(io.open(PY_, encoding="utf-8").read()).body:
    if (isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name)
            and no.target.id == "CHANNEL_CAPABILITIES"):
        tab_py = {k.value: sorted(a.value for a in v.args[0].elts) if v.args else []
                  for k, v in zip(no.value.keys, no.value.values)}

# canonico: o corpo do objeto literal, por recorte + regex de linha
ts = io.open(TS_, encoding="utf-8").read()
m = re.search(r"CHANNEL_CAPABILITIES[^=]*=\s*\{(.*?)\n\}", ts, re.S)
tab_ts = {}
if m:
    for linha in m.group(1).split("\n"):
        mm = re.match(r'\s*([a-z_]+):\s*\[(.*?)\],', linha)
        if mm:
            tab_ts[mm.group(1)] = sorted(re.findall(r'"([a-z_]+)"', mm.group(2)))

if tab_py is None or not tab_ts:
    print("ERRO|F|nao consegui ler as duas tabelas (py=%s ts=%s)"
          % (tab_py is not None, bool(tab_ts)))
elif tab_py != tab_ts:
    so_py = {k: v for k, v in tab_py.items() if tab_ts.get(k) != v}
    so_ts = {k: v for k, v in tab_ts.items() if tab_py.get(k) != v}
    print("ERRO|F|gemeo DIVERGE do canonico: python=%s typescript=%s"
          % (json.dumps(so_py, sort_keys=True), json.dumps(so_ts, sort_keys=True)))
else:
    print("OK|F|gemeo Python identico ao canonico TS (%d canais)" % len(tab_ts))
PY
)"
while IFS='|' read -r st ramo msg; do
  [ -z "${st:-}" ] && continue
  if [ "$st" = "OK" ]; then ok "$ramo — $msg"; else bad "$ramo — $msg"; fi
done <<< "$SAIDA_F"
[ -z "$SAIDA_F" ] && bad "F — RAMO AUSENTE: o censo de paridade nao chegou a julgar"

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
echo "${GRN}${BLD}VERDE${RST} — uma casa, tabela exaustiva, e voice so declara masked_input com os mecanismos"
