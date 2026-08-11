#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_flow_transitions.sh — alvos de transição que não existem
#
# POR QUE EXISTE. Em 2026-08-11, renomear um step (`parquear_resultado` →
# `disparar_entrega`) sem atualizar dois `on_success`/`on_failure` produziu o pior
# tipo de falha: o workflow executou até a transição órfã e PAROU. Sem erro, sem
# `isError`, sem step de falha, sem log. A sessão ficou `active` para sempre e o
# segmento durou 19 ms — indistinguível de sucesso.
#
# `validateFlow` (skill-flow-engine/engine.ts) valida CICLOS não-guardados, não a
# EXISTÊNCIA dos alvos. Este probe fecha essa lacuna por fora, sobre os YAMLs.
#
# Roda dentro do orchestrator-bridge, que monta /skills e já tem PyYAML — sem
# depender de nada instalado no host.
#
# Veredicto: 0 = nenhum alvo órfão · 1 = alvo órfão encontrado · 2 = inconclusivo.
# Uso: bash infra/test/probe_flow_transitions.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"

$COMPOSE exec -T orchestrator-bridge python3 - <<'PY' < /dev/null
import glob, os, sys
try:
    import yaml
except ImportError:
    print("⚠️  INCONCLUSIVO: PyYAML ausente no container"); sys.exit(2)

SKILLS = "/skills"
files = sorted(glob.glob(os.path.join(SKILLS, "*.yaml")) + glob.glob(os.path.join(SKILLS, "*.yml")))
if not files:
    print(f"⚠️  INCONCLUSIVO: nenhum YAML em {SKILLS} — o volume está montado?"); sys.exit(2)

# Chaves cujo VALOR é um id de step. Restrito de propósito: um probe barulhento é
# pior que nenhum, e um falso positivo aqui manda procurar bug onde não há.
STR_KEYS  = ("on_success", "on_failure", "on_timeout", "on_disconnect", "default")
DICT_KEYS = ("on_resume", "on_reject", "on_timeout", "on_success", "on_failure")

def looks_like_step_id(v):
    # refs (`$.`, `@ctx.`), textos e valores não são alvos de transição
    return isinstance(v, str) and v and not any(c in v for c in ".$@ /{}")

problems, scanned, steps_total = [], 0, 0
for path in files:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except Exception as exc:
        problems.append((os.path.basename(path), "<parse>", f"YAML ilegível: {exc}"))
        continue
    if not isinstance(doc, dict) or not isinstance(doc.get("steps"), list):
        continue
    scanned += 1
    steps = [s for s in doc["steps"] if isinstance(s, dict)]
    ids = {s.get("id") for s in steps if s.get("id")}
    steps_total += len(steps)

    entry = doc.get("entry")
    if looks_like_step_id(entry) and entry not in ids:
        problems.append((os.path.basename(path), "<entry>", entry))

    for s in steps:
        sid = s.get("id", "<sem id>")
        for k in STR_KEYS:
            v = s.get(k)
            if looks_like_step_id(v) and v not in ids:
                problems.append((os.path.basename(path), sid, f"{k} → {v}"))
        for k in DICT_KEYS:
            v = s.get(k)
            if isinstance(v, dict):
                nxt = v.get("next")
                if looks_like_step_id(nxt) and nxt not in ids:
                    problems.append((os.path.basename(path), sid, f"{k}.next → {nxt}"))
        for cond in (s.get("conditions") or []):
            if isinstance(cond, dict):
                nxt = cond.get("next")
                if looks_like_step_id(nxt) and nxt not in ids:
                    problems.append((os.path.basename(path), sid, f"conditions.next → {nxt}"))

print(f"── {scanned} skills · {steps_total} steps varridos ──")
if problems:
    for f, sid, what in problems:
        print(f"   ❌ {f} · step '{sid}': {what}  (alvo INEXISTENTE)")
    print(f"\n   {len(problems)} transição(ões) órfã(s). Cada uma PARA o workflow em silêncio")
    print("   quando alcançada: sessão presa em 'active', último segmento curtíssimo,")
    print("   e nenhum rastro do step seguinte nos logs.")
    sys.exit(1)
print("   ✅ todos os alvos de transição existem")
sys.exit(0)
PY
RC=$?
case "$RC" in
  0) echo "✅ VERDE" ;;
  1) echo "❌ DEFEITO — transição órfã" ;;
  *) echo "⚠️  INCONCLUSIVO (rc=$RC) — nada se conclui" ;;
esac
exit $RC
