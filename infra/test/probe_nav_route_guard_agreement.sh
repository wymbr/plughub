#!/usr/bin/env bash
# ==============================================================================
# probe_nav_route_guard_agreement.sh — a ROTA responde o mesmo que o MENU
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Toda entrada do Sidebar que declara uma regra ABAC tem a sua rota envolvida em
# `<RequireAbac>` com **o mesmo par (módulo, campo)**. Menu e rota deixam de ser
# duas casas com respostas diferentes para a mesma pergunta.
#
# POR QUE ELE EXISTE (AUT-37, reescrita em 2026-09-09)
# ----------------------------------------------------
# Medido a partir de uma pergunta do dono: das 31 entradas de menu com regra
# ABAC, **11 tinham rota gateada, 6 "gateavam por dentro" e 14 não tinham guarda
# nenhuma** — alcançáveis por URL por qualquer autenticado. O menu escondia e a
# rota não barrava, a mesma forma do item (b) da MOD-11.
#
# ⚠️ **Das 6 que "gateavam por dentro", DUAS não gateavam nada.** `AccessPage` e
# `GroupsPage` tinham `canGrant = perms.can('config','permissions','read_write')`
# — o portão de CAMPO da MOD-02, que decide um botão, não a página. Um `grep` por
# `perms.can` as classificava como protegidas. *Exposição e dano são grandezas
# separadas, e presença de checagem não é evidência de que ela decide o render.*
# Por isso o veredicto aqui é sobre a ROTA, que é o que de fato barra.
#
# ⚠️ **A ficha anterior estava VENCIDA, e nasceu vencida no ledger.** A AUT-37
# dizia `analise/*`; aquelas 6 rotas ganharam `RequireAbac` entre 27 e 29/08/2026,
# e a ficha só foi movida para o `pending.md` em 05/09 — carregada verbatim, sem
# remedir. O buraco estava no VIZINHO que ela não olhou (Monitor e Configuração).
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   A  entrada de menu com regra ABAC cuja rota NÃO tem `RequireAbac`
#   B  rota gateada por um par (módulo, campo) DIFERENTE do que o menu declara
#   C  leitor quebrado — poucas entradas lidas (INCONCLUSIVO, nunca verde)
#
# ⚠️ O ramo B é o que importa a médio prazo. Sem ele, "tem guarda" bastaria, e o
# dia em que alguém trocar o campo do menu sem trocar o da rota volta a ter duas
# respostas para a mesma pergunta — com a mais permissiva valendo.
#
# ⚠️ **Isto NÃO é a fronteira de autorização** — ela é o backend. É coerência de
# UX e defesa em profundidade: as duas pontas chamam `passesAbacRule`.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

NAV=packages/platform-ui/src/shell/Sidebar.tsx
ROT=packages/platform-ui/src/app/routes.tsx
for f in "$NAV" "$ROT"; do
  [ -f "$f" ] || { echo "INCONCLUSIVO: $f ausente"; exit 2; }
done
command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }

printf '\033[1mprobe: a ROTA responde o mesmo que o MENU\033[0m\n'

python3 - "$NAV" "$ROT" <<'PY'
import re, sys

nav = open(sys.argv[1], encoding="utf-8").read()
rot = open(sys.argv[2], encoding="utf-8").read()
fail = 0

def ok(m):  print(f"  \033[32mOK\033[0m           {m}")
def bad(m):
    global fail
    print(f"  \033[31mFALHA\033[0m        {m}"); fail = 1

# ── entradas de menu: href + regra, exigidos na MESMA linha ─────────────────
itens = []
for linha in nav.split("\n"):
    h = re.search(r"href:\s*'(/[^']+)'", linha)
    a = re.search(r"abac:\s*\{\s*module:\s*'([a-z_]+)',\s*field:\s*'([a-z_]+)'", linha)
    if h and a:
        itens.append((h.group(1), a.group(1), a.group(2)))
itens = sorted(set(itens))

if len(itens) < 15:
    print(f"  \033[33mINCONCLUSIVO\033[0m so {len(itens)} entrada(s) de menu lidas — leitor quebrado?")
    sys.exit(2)

# ── rotas declaradas ────────────────────────────────────────────────────────
rotas = {}
for m in re.finditer(r"\{\s*path:\s*'([^']+)',\s*element:\s*(.+?)\},?\s*\n", rot, re.S):
    rotas["/" + m.group(1)] = m.group(2)

print(f"\n\033[1mA. toda entrada de menu com regra ABAC tem rota GATEADA\033[0m")
print(f"               {len(itens)} entradas de menu · {len(rotas)} rotas declaradas")
nuas, ausentes = [], []
for href, mod, campo in itens:
    elem = rotas.get(href)
    if elem is None:
        ausentes.append(href)
    elif (not ("Navigate" in elem or "RedirectPreserving" in elem)
          and "RequireAbac" not in elem and "RequireEvalAccess" not in elem):
        nuas.append((href, mod, campo))
if ausentes:
    bad(f"{len(ausentes)} entrada(s) de menu sem rota declarada: {', '.join(ausentes[:6])}")
if nuas:
    bad(f"{len(nuas)} rota(s) SEM guarda — o menu esconde e a URL nao barra:")
    for href, mod, campo in nuas[:14]:
        print(f"                 {href:26} deveria exigir {mod}.{campo}")
if not nuas and not ausentes:
    ok(f"as {len(itens)} entradas tem rota, e nenhuma esta nua")

print(f"\n\033[1mB. o PAR (modulo, campo) da rota e o mesmo que o menu declara\033[0m")
divergentes = []
for href, mod, campo in itens:
    elem = rotas.get(href) or ""
    # `RequireEvalAccess` e o guard do modulo `evaluation`: o modulo e implicito
    # no proprio componente, entao o par lido dali e ("evaluation", field).
    if "RequireEvalAccess" in elem:
        g = re.search(r'RequireEvalAccess\s+field="([a-z_]+)"', elem)
        achado = ("evaluation", g.group(1)) if g else None
    elif "RequireAbac" in elem:
        g = re.search(r'RequireAbac\s+module="([a-z_]+)"\s+field="([a-z_]+)"', elem)
        achado = (g.group(1), g.group(2)) if g else None
    else:
        continue
    if achado is None:
        divergentes.append((href, f"{mod}.{campo}", "<nao consegui ler o par>"))
    elif achado != (mod, campo):
        divergentes.append((href, f"{mod}.{campo}", f"{achado[0]}.{achado[1]}"))
if divergentes:
    bad(f"{len(divergentes)} rota(s) exigem campo DIFERENTE do menu:")
    for href, esperado, achado in divergentes[:10]:
        print(f"                 {href:26} menu={esperado}  rota={achado}")
else:
    gateadas = sum(1 for h, m_, c in itens
                   if "RequireAbac" in (rotas.get(h) or "")
                   or "RequireEvalAccess" in (rotas.get(h) or ""))
    ok(f"as {gateadas} rotas gateadas exigem exatamente o campo do menu")

print()
if fail:
    print("\033[31mVERMELHO\033[0m - ver secoes acima.")
else:
    print("\033[32mVERDE\033[0m - menu e rota dao a MESMA resposta.")
sys.exit(fail)
PY
exit $?
