#!/usr/bin/env bash
#
# probe_masking_display_domain.sh — o domínio de exibição/eco é ABSTRATO, e a
# migração não afrouxou nada (ALW-10).
#
# POR QUE ESTE PROBE EXISTE
# =========================
# `mascara.display` tinha quatro campos e três vocabulários: `display_screen`
# (3 valores, tela), `display_voice` (3, voz) e dois booleanos de eco. Medido em
# 2026-09-02: **zero consumidores** para `display_voice`, `echo_to_customer` e
# `echo_to_operator` — três interruptores que pareciam vivos.
#
# A decisão do dono foi padronizar em domínio CHANNEL-ABSTRACT, cada adapter
# traduzindo, e **sem parcial no eco** — porque eco devolve entrada FRESCA, não
# renderiza token, e não há `***-00` embutido para ler. Eco é coisa de INPUT;
# armazenamento continua com o masking padrão.
#
#     display_screen  →  token_display          (mesmos 3 valores, agnóstico)
#     display_voice   →  (sai; o adapter traduz o token_display)
#     echo_*: boolean →  EchoMode = plain | none | masked
#
# O QUE ESTE PROBE PODE REPROVAR
# ==============================
#   A  o schema voltar a ter os nomes/enums antigos                    → VERMELHO
#   B  qualquer ESCOPO do store vivo ainda na forma velha              → VERMELHO
#      (enumera os escopos por `/_provenance` — supor "só o global"
#       deixaria intacto todo tenant com linha própria, que é o defeito
#       que a ALW-06 mediu no mesmo dia)
#   C  a migração deixar de ser idempotente                            → VERMELHO
#   D  i18n perder as chaves novas ou dessincronizar                   → VERMELHO
#   E  **a migração AFROUXAR a política de algum tipo**                → VERMELHO
#      — é o ramo que dá valor aos outros. Uma renomeação que troque
#      `hidden` por `display_partial`, ou `none` por `masked`/`plain`,
#      não quebra build nenhum e não aparece em tela nenhuma.
#
# INCONCLUSIVO é ramo próprio: sem config-api, o probe NÃO declara verde.
#
# Uso:  bash infra/test/probe_masking_display_domain.sh
set -uo pipefail

CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0; INC=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc()  { echo "  ${YLW}?${RST} $*"; INC=$((INC+1)); }

echo "${BLD}probe_masking_display_domain — domínio abstrato de exibição e eco${RST}"
echo "  config-api=$CFG  tenant=$TENANT"
echo

# ── A — o schema ─────────────────────────────────────────────────────────────
echo "${BLD}A) schema: os nomes novos existem e os antigos não voltaram${RST}"
A_OUT="$(cd "$RAIZ" && python3 - <<'PY'
import io, re
A = "packages/schemas/src/audit.ts"
s = io.open(A, encoding="utf-8").read()
# Só linhas de CÓDIGO: o docstring cita os nomes antigos de propósito, para
# explicar a mudança. Proibir a palavra proibiria documentar.
codigo = "\n".join(l for l in s.split("\n") if not l.lstrip().startswith(("*", "//", "/*")))
erros = []
for esperado in ("TokenDisplayModeSchema", "EchoModeSchema",
                 "token_display:", "echo_to_customer:", "echo_to_operator:"):
    if esperado not in codigo:
        erros.append("faltou %s" % esperado)
for morto in ("DisplayScreenSchema", "DisplayVoiceSchema", "display_screen:", "display_voice:"):
    if morto in codigo:
        erros.append("voltou %s" % morto)
m = re.search(r'EchoModeSchema = z\.enum\(\[([^\]]*)\]\)', codigo)
valores = sorted(re.findall(r'"(\w+)"', m.group(1))) if m else []
if valores != ["masked", "none", "plain"]:
    erros.append("EchoMode = %s (esperado masked/none/plain)" % valores)
if 'z.boolean()' in (m2.group(0) if (m2 := re.search(r'MaskingDisplayRuleSchema = z\.object\(\{[^}]*\}\)', codigo, re.S)) else ""):
    erros.append("eco voltou a ser booleano")
print("OK EchoMode=%s, sem os enums antigos" % valores if not erros else "ERRO " + " ; ".join(erros))
PY
)"
case "$A_OUT" in OK*) ok "${A_OUT#OK }" ;; *) bad "${A_OUT#ERRO }" ;; esac

# ── pré-condição para B/C/E ──────────────────────────────────────────────────
if [ "$(curl -s -o /dev/null -w '%{http_code}' "$CFG/v1/health")" != "200" ]; then
  inc "config-api fora do ar — ramos B, C e E não executados"
  echo; echo "${YLW}${BLD}INCONCLUSIVO${RST}"; exit 2
fi

# ── B — o store vivo, em TODOS os escopos ────────────────────────────────────
echo
echo "${BLD}B) store vivo: nenhum escopo na forma velha${RST}"
B_OUT="$(python3 - "$CFG" "$TENANT" <<'PY'
import json, sys, urllib.request
cfg, tenant = sys.argv[1], sys.argv[2]
def get(u):
    with urllib.request.urlopen(u, timeout=15) as r: return json.load(r)
prov = get(f"{cfg}/config/masking/_provenance?tenant_id={tenant}").get("keys", {}).get("types")
if not prov:
    print("ERRO `masking.types` não existe em escopo nenhum"); raise SystemExit
escopos = (["__global__"] if prov["global_present"] else []) + ([tenant] if prov["tenant_present"] else [])
erros = []
for esc in escopos:
    doc = get(f"{cfg}/config/masking/types?tenant_id={esc}")["value"]
    for t in doc.get("types", []):
        d = (t.get("mascara") or {}).get("display")
        if not isinstance(d, dict): continue
        for morto in ("display_screen", "display_voice"):
            if morto in d: erros.append("%s/%s: %s" % (esc, t.get("id"), morto))
        for campo in ("echo_to_customer", "echo_to_operator"):
            if isinstance(d.get(campo), bool):
                erros.append("%s/%s: %s ainda booleano" % (esc, t.get("id"), campo))
            elif campo in d and d[campo] not in ("plain", "none", "masked"):
                erros.append("%s/%s: %s=%r fora do domínio" % (esc, t.get("id"), campo, d[campo]))
print("OK %d escopo(s) conferido(s): %s" % (len(escopos), escopos) if not erros
      else "ERRO " + " ; ".join(erros[:6]))
PY
)"
case "$B_OUT" in OK*) ok "${B_OUT#OK }" ;; *) bad "${B_OUT#ERRO }" ;; esac

# ── C — a migração é idempotente ─────────────────────────────────────────────
echo
echo "${BLD}C) rodar a migração de novo não muda nada${RST}"
C_OUT="$(cd "$RAIZ" && python3 infra/scripts/migrate_masking_display_rule.py 2>&1 | tail -1)"
if printf '%s' "$C_OUT" | grep -q '^0 mudança'; then
  ok "segunda passada: 0 mudanças previstas"
else
  bad "migração não é idempotente — $C_OUT"
fi

# ── D — i18n ─────────────────────────────────────────────────────────────────
echo
echo "${BLD}D) i18n: chaves novas, pareadas, sem as antigas${RST}"
D_OUT="$(cd "$RAIZ" && python3 - <<'PY'
import collections, io, json
BASE = "packages/platform-ui/src/i18n/locales/%s/masking.json"
def hook(c):
    def h(p):
        cnt = collections.Counter(k for k, _ in p)
        rep = sorted(k for k, n in cnt.items() if n > 1)
        if rep: raise ValueError("chave repetida em %s: %s" % (c, rep))
        return dict(p)
    return h
erros, blocos = [], {}
for loc in ("en", "pt-BR"):
    c = BASE % loc
    try:
        doc = json.loads(io.open(c, encoding="utf-8").read(), object_pairs_hook=hook(c))
    except ValueError as e:
        erros.append(str(e)); continue
    for morto in ("displayScreen", "displayVoice"):
        if morto in doc: erros.append("%s: `%s` voltou" % (c, morto))
    for novo, esperadas in (("tokenDisplay", {"partial", "full", "hidden"}),
                            ("echoMode", {"none", "masked", "plain"})):
        b = doc.get(novo)
        if not isinstance(b, dict):
            erros.append("%s: bloco `%s` ausente" % (c, novo)); continue
        falta = sorted(esperadas - set(b))
        if falta: erros.append("%s.%s: faltam %s" % (c, novo, falta))
        blocos.setdefault(novo, {})[loc] = set(b)
    dr = doc.get("section", {}).get("displayRules", {})
    for morto in ("screen", "voice"):
        if morto in dr: erros.append("%s: section.displayRules.%s voltou" % (c, morto))
    for obrig in ("token", "echoCustomer", "echoOperator"):
        if obrig not in dr: erros.append("%s: section.displayRules.%s ausente" % (c, obrig))
for novo, por_loc in blocos.items():
    if len(por_loc) == 2 and por_loc["en"] != por_loc["pt-BR"]:
        erros.append("paridade %s: en-pt=%s pt-en=%s" % (
            novo, sorted(por_loc["en"] - por_loc["pt-BR"]), sorted(por_loc["pt-BR"] - por_loc["en"])))
print("OK tokenDisplay+echoMode pareados nos dois locales" if not erros else "ERRO " + " ; ".join(erros[:6]))
PY
)"
case "$D_OUT" in OK*) ok "${D_OUT#OK }" ;; *) bad "${D_OUT#ERRO }" ;; esac

# ── E — a migração NÃO afrouxou ──────────────────────────────────────────────
echo
echo "${BLD}E) política máxima preservada — a testemunha de segurança${RST}"
E_OUT="$(python3 - "$CFG" "$TENANT" <<'PY'
import json, sys, urllib.request
cfg, tenant = sys.argv[1], sys.argv[2]
def get(u):
    with urllib.request.urlopen(u, timeout=15) as r: return json.load(r)
# Os três tipos de restrição MÁXIMA do catálogo. Se a renomeação os afrouxar,
# nada quebra e nada aparece — por isso a asserção é sobre o VALOR, não a forma.
ESPERADO = {
    "credential":  {"token_display": "hidden", "echo_to_customer": "none", "echo_to_operator": "none"},
    "card_cvv":    {"token_display": "hidden", "echo_to_customer": "none", "echo_to_operator": "none"},
    "opaque":      {"token_display": "hidden", "echo_to_customer": "none", "echo_to_operator": "none"},
}
prov = get(f"{cfg}/config/masking/_provenance?tenant_id={tenant}")["keys"]["types"]
escopos = (["__global__"] if prov["global_present"] else []) + ([tenant] if prov["tenant_present"] else [])
erros, vistos = [], 0
for esc in escopos:
    tipos = {t.get("id"): t for t in get(f"{cfg}/config/masking/types?tenant_id={esc}")["value"]["types"]}
    for tid, esperado in ESPERADO.items():
        t = tipos.get(tid)
        if t is None:
            erros.append("%s: tipo `%s` sumiu do catálogo" % (esc, tid)); continue
        d = (t.get("mascara") or {}).get("display") or {}
        vistos += 1
        for campo, val in esperado.items():
            if d.get(campo) != val:
                erros.append("%s/%s.%s = %r (esperado %r)" % (esc, tid, campo, d.get(campo), val))
if vistos == 0:
    print("ERRO nenhum tipo de restrição máxima encontrado — SEM AMOSTRA, não é verde")
else:
    print("OK %d conferências em %d escopo(s): credential/card_cvv/opaque seguem hidden+none+none"
          % (vistos, len(escopos)) if not erros else "ERRO " + " ; ".join(erros[:6]))
PY
)"
case "$E_OUT" in OK*) ok "${E_OUT#OK }" ;; *) bad "${E_OUT#ERRO }" ;; esac

# ── F — a fiação: o tipo viaja, e as duas casas do operador concordam ────────
echo
echo "${BLD}F) fiação: \`masked_types\` chega às duas casas de ECO do operador${RST}"
F_OUT="$(cd "$RAIZ" && python3 - <<'PY'
import io, re
erros = []

# ── F1: todo forward de `masked_fields` no produtor leva `masked_types` junto ──
# Mesma forma de `test_todo_destino_chama_o_redator`: não afirma QUAIS destinos,
# afirma que nenhum ficou para trás. Um destino novo com só metade do par entrega
# ao consumidor a informação de que o campo é secreto SEM a de qual é a política —
# e aí ele cai no fallback para sempre, sem nada ficar vermelho.
bpm = io.open("packages/mcp-server-plughub/src/tools/bpm.ts", encoding="utf-8").read()
campos = len(re.findall(r"^\s*masked_fields:\s*parsed\.menu", bpm, re.M))
tipos = len(re.findall(r"^\s*masked_types:\s*parsed\.menu", bpm, re.M))
if campos != tipos:
    erros.append("bpm.ts: %d forwards de masked_fields x %d de masked_types" % (campos, tipos))
elif campos == 0:
    erros.append("bpm.ts: nenhum forward encontrado — o censo perdeu o alvo")

# ── F2: o produtor do menu emite os dois ──────────────────────────────────────
menu = io.open("packages/skill-flow-engine/src/steps/menu.ts", encoding="utf-8").read()
if menu.count("masked_types:") < 3:      # 2 payloads de canal + 1 waitingMeta
    erros.append("menu.ts: masked_types em %d sítios (esperado >=3)" % menu.count("masked_types:"))

# ── F3: as DUAS casas de eco do operador consomem e apertam ──────────────────
# bridge (destino 1) e echo do Console. As duas têm de tratar `none` REMOVENDO,
# e nenhuma pode devolver o valor cru para `plain` — o tipo aperta, nunca afrouxa.
bridge = io.open("packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py",
                 encoding="utf-8").read()
if "echo_policy" not in bridge:
    erros.append("bridge: destino 1 não passa echo_policy")
if 'modo == "none"' not in bridge:
    erros.append("bridge: `none` não remove o campo")

console = io.open("packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx",
                  encoding="utf-8").read()
if "masked_types" not in console:
    erros.append("Console: o eco não lê masked_types")
if 'modo === "none"' not in console or "delete redacted[fieldId]" not in console:
    erros.append("Console: `none` não remove o campo")
# A regressão que importa: o Console voltar a exibir o valor cru de campo mascarado.
if re.search(r"redacted\[fieldId\]\s*=\s*(result|value)", console):
    erros.append("Console: campo mascarado recebendo valor CRU")

# ── F4: a política NÃO alcança armazenamento ─────────────────────────────────
# `echo_policy` só pode aparecer no destino 1. Se surgir num dos quatro destinos
# de persistência, a fronteira "eco é input" foi apagada.
usos = len(re.findall(r"echo_policy\s*=\s*echo_policy", bridge))
if usos != 1:
    erros.append("bridge: echo_policy passada a %d destinos (esperado 1 — só o Agent Assist)" % usos)

print("OK bpm=%d pares, menu=%d, as 2 casas apertam, 1 só destino recebe a política"
      % (campos, menu.count("masked_types:")) if not erros else "ERRO " + " ; ".join(erros[:6]))
PY
)"
case "$F_OUT" in OK*) ok "${F_OUT#OK }" ;; *) bad "${F_OUT#ERRO }" ;; esac

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — $FAIL falha(s), $INC inconclusivo(s)"; exit 1
fi
if [ "$INC" -gt 0 ]; then
  echo "${YLW}${BLD}INCONCLUSIVO${RST} — $INC ramo(s) sem amostra"; exit 2
fi
echo "${GRN}${BLD}VERDE${RST} — domínio abstrato em vigor, e nenhuma política afrouxou"
