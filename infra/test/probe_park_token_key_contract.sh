#!/usr/bin/env bash
# ==============================================================================
# probe_park_token_key_contract.sh — o bridge reconhece TODO token que o engine grava
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Todo sufixo de chave de token que um step do `skill-flow-engine` grava no
# `pipeline_state.results` está na lista que o `orchestrator-bridge` usa para
# descobri-lo ao publicar `session_suspended`.
#
# POR QUE ELE EXISTE (RET-12, 2026-09-09)
# ---------------------------------------
# O bridge lia UM sufixo — `:__resume_token__` — e o step `collect` grava
# `:__collect_token__`. Uma string. Consequência medida: **49 sessões sem registro
# durável de parque**, 41 delas do `limite_entrega`, que suspende por `collect`.
# O evento saía sem token, então a analytics não escrevia `session_transitions`
# (ela só o faz `if payload.get("resume_token")`) e a sessão ficava suspensa sem
# nada que a endereçasse.
#
# ⚠️ **O `collect` nunca foi um mecanismo à parte.** Ele grava no MESMO hash
# `{tenant}:resume_tokens` e chama o mesmo `_write_resume_meta`
# (`adapters/webhook.py:2226`: *"the collect_token DOUBLES AS the resume_token"*).
# A máquina sempre foi uma; o que divergia era o NOME pelo qual o leitor procurava.
#
# ⚠️ **É um contrato ENTRE pacotes** — o engine (TypeScript) escreve, o bridge
# (Python) lê —, e por isso não mora em nenhum dos dois lados. Mesma família do
# `payload["answers"]` × `payload["result"]` que o `CLAUDE.md` registra: *"produtor
# e teste olhando um para o outro, nenhum dos dois para o consumidor"*.
#
# ⚠️ **A população é o que o ENGINE escreve.** Declarar a lista aqui mediria os
# produtores contra o gate, não contra o leitor — que é exatamente o erro que o
# `probe_menu_result_contract.sh` evita ao medir a chave no LEITOR. Aqui a direção
# é a mesma: o gate não sabe quais sufixos existem, ele DESCOBRE.
#
# O QUE O DEIXARIA VERMELHO
#   A  step novo grava um sufixo de token que o bridge não reconhece
#   B  o bloco de descoberta do bridge sumiu ou mudou de forma
#   C  leitor quebrado (nenhum sufixo achado) → INCONCLUSIVO, nunca verde
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

STEPS=packages/skill-flow-engine/src/steps
BRIDGE=packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py
[ -d "$STEPS" ] || { echo "INCONCLUSIVO: $STEPS ausente"; exit 2; }
[ -f "$BRIDGE" ] || { echo "INCONCLUSIVO: $BRIDGE ausente"; exit 2; }
command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }

printf '\033[1mprobe: o bridge reconhece TODO token de parque que o engine grava\033[0m\n'

python3 - "$STEPS" "$BRIDGE" <<'PY'
import glob, os, re, sys

steps_dir, bridge_p = sys.argv[1], sys.argv[2]
fail = 0

def ok(m):  print(f"  \033[32mOK\033[0m           {m}")
def bad(m):
    global fail
    print(f"  \033[31mFALHA\033[0m        {m}"); fail = 1

# ── o que o ENGINE escreve ──────────────────────────────────────────────────
# Chave de token = `${...}:__<algo>_token__`. `__expires_at__`, `__collected__` e
# `__collect_decision__` NAO sao token e ficam de fora por construcao do padrao.
escritos: dict[str, list[str]] = {}
for arq in sorted(glob.glob(os.path.join(steps_dir, "*.ts"))):
    if arq.endswith(".test.ts"):
        continue
    src = open(arq, encoding="utf-8").read()
    for m in re.finditer(r"\$\{[^}]+\}(:__[a-z_]*token__)", src):
        escritos.setdefault(m.group(1), []).append(os.path.basename(arq))

if not escritos:
    print("  \033[33mINCONCLUSIVO\033[0m nenhum sufixo de token achado nos steps — leitor quebrado?")
    sys.exit(2)

print(f"\n\033[1mA. sufixos que os steps do engine gravam\033[0m")
for suf, arqs in sorted(escritos.items()):
    print(f"               {suf:24} {', '.join(sorted(set(arqs)))}")

# ── o que o BRIDGE reconhece ────────────────────────────────────────────────
bsrc = open(bridge_p, encoding="utf-8").read()
m = re.search(r"SUFIXOS_DE_TOKEN_DE_PARQUE\s*=\s*\(([^)]*)\)", bsrc)
if not m:
    bad("nao achei `SUFIXOS_DE_TOKEN_DE_PARQUE` no bridge — o bloco de descoberta mudou de forma")
    print()
    print("\033[31mVERMELHO\033[0m - ver secoes acima.")
    sys.exit(1)
reconhecidos = set(re.findall(r'"(:__[a-z_]*token__)"', m.group(1)))

print(f"\n\033[1mB. o bridge reconhece todos eles\033[0m")
print(f"               reconhecidos: {', '.join(sorted(reconhecidos)) or '<nenhum>'}")
faltando = sorted(set(escritos) - reconhecidos)
if faltando:
    bad(f"{len(faltando)} sufixo(s) que o engine grava e o bridge NAO enxerga:")
    for suf in faltando:
        print(f"                 {suf}  (em {', '.join(sorted(set(escritos[suf])))})")
    print("                 -> o `session_suspended` sai SEM token, e a sessao fica")
    print("                    suspensa sem nada que a enderece. Foi assim que 49")
    print("                    sessoes ficaram sem registro duravel (RET-12).")
else:
    ok(f"os {len(escritos)} sufixos gravados pelo engine estao na lista do bridge")

# Sobra nao reprova: um sufixo reconhecido e nao mais escrito e resto inofensivo —
# mas e informacao, e some do radar se ninguem a imprimir.
sobra = sorted(reconhecidos - set(escritos))
if sobra:
    print(f"               (nota: {', '.join(sobra)} reconhecido(s) e nao gravado(s) por step nenhum)")

print()
if fail:
    print("\033[31mVERMELHO\033[0m - ver secoes acima.")
else:
    print("\033[32mVERDE\033[0m - engine e bridge concordam sobre como o token se chama.")
sys.exit(fail)
PY
exit $?
