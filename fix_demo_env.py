#!/usr/bin/env python3
"""
fix_demo_env.py
Apply all required demo environment fixes:
  1. ai-gateway/models.py  — make agent_id / tenant_id optional
  2. skill-flow-service/dist/index.js  — unwrap data.result from AI Gateway response
  3. skill-flow-engine reason.js (node_modules) — treat null as absent for optional schema fields

Run on the demo machine:
  cd ~/plughub   (or wherever the repo lives)
  python3 fix_demo_env.py
"""

import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

ERRORS = []


def patch(path, old, new, label):
    full = os.path.join(BASE, path)
    if not os.path.exists(full):
        print(f"  ⚠️  SKIP (file not found): {path}")
        return
    content = open(full).read()
    if new.strip() in content:
        print(f"  ✅ Already applied: {label}")
        return
    if old not in content:
        print(f"  ❌ Pattern not found — cannot patch: {label}")
        ERRORS.append(label)
        return
    patched = content.replace(old, new, 1)
    open(full, "w").write(patched)
    print(f"  ✅ Patched: {label}")


# ── 1. ai-gateway models.py ────────────────────────────────────────────────────
print("\n[1] ai-gateway/models.py — make agent_id / tenant_id optional")

patch(
    "packages/ai-gateway/src/plughub_ai_gateway/models.py",
    old=(
        "    agent_id:      str\n"
        "    tenant_id:     str\n"
        "    prompt_id:     str"
    ),
    new=(
        '    agent_id:      str = ""   # optional — forwarded for audit, not used in inference logic\n'
        '    tenant_id:     str = ""   # optional — used for session-param analytics (best-effort)\n'
        "    prompt_id:     str"
    ),
    label="agent_id / tenant_id optional",
)

# ── 2. skill-flow-service dist/index.js — unwrap data.result ──────────────────
print("\n[2] skill-flow-service/dist/index.js — unwrap AI Gateway ReasonResponse")

DIST_JS = "packages/e2e-tests/services/skill-flow-service/dist/index.js"

patch(
    DIST_JS,
    old="    return res.json();\n}",
    new=(
        "    // ReasonResponse wrapper: { session_id, result, model_used, ... }\n"
        "    // executeReason validates the *inner* result — unwrap it here.\n"
        "    const data = await res.json();\n"
        "    const unwrapped = data.result !== undefined ? data.result : data;\n"
        "    console.log(`[aiGatewayCall] status=200 keys=${Object.keys(unwrapped)} "
        "result=${JSON.stringify(unwrapped).substring(0, 400)}`);\n"
        "    return unwrapped;\n"
        "}"
    ),
    label="unwrap data.result in aiGatewayCall",
)

# ── 3. reason.js in node_modules (two possible paths) — null → absent ─────────
print("\n[3] reason.js — treat JSON null as absent for optional output_schema fields")

REASON_PATHS = [
    "packages/e2e-tests/services/skill-flow-service/node_modules/@plughub/skill-flow-engine/dist/steps/reason.js",
    "packages/e2e-tests/services/skill-flow-service/node_modules/@plughub/skill-flow-engine/dist/skill-flow-engine/src/steps/reason.js",
    "packages/skill-flow-engine/dist/steps/reason.js",
]

for rp in REASON_PATHS:
    full = os.path.join(BASE, rp)
    if not os.path.exists(full):
        continue  # path not present in this layout — skip silently
    content = open(full).read()
    already_fixed = "value === null" in content
    if already_fixed:
        print(f"  ✅ Already applied: null check in {rp}")
        continue
    patch(
        rp,
        old="        if (value === undefined) {",
        new="        if (value === undefined || value === null) { // null treated as absent for optional fields",
        label=f"null check in {os.path.basename(os.path.dirname(rp))}/reason.js",
    )

# ── 4. menu.js in node_modules — timeout_ms → timeout_s (NaN / Lua bug) ───────
print("\n[4] menu.js — fix NaN in renewLock: timeout_ms renamed to timeout_s (add ?? 300 guard)")

MENU_PATHS = [
    "packages/e2e-tests/services/skill-flow-service/node_modules/@plughub/skill-flow-engine/dist/steps/menu.js",
    "packages/skill-flow-engine/dist/steps/menu.js",
]

for mp in MENU_PATHS:
    full = os.path.join(BASE, mp)
    if not os.path.exists(full):
        continue  # not present — skip
    content = open(full).read()

    # Guard already present?
    if "timeout_s ?? 300" in content or "step.timeout_s ?? 300" in content:
        print(f"  ✅ Already applied: timeout_s guard in {mp}")
        continue

    # Old version uses timeout_ms — replace the whole timeoutSec line
    if "step.timeout_ms" in content:
        patched = content.replace(
            "const timeoutSec = Math.ceil(step.timeout_ms / 1000);",
            "const timeoutSec = step.timeout_s !== undefined ? step.timeout_s : (step.timeout_ms !== undefined ? Math.ceil(step.timeout_ms / 1000) : 300); // compat: was timeout_ms",
        )
        if patched == content:
            print(f"  ❌ timeout_ms pattern not found in {mp}")
            ERRORS.append(f"timeout_ms pattern missing in {mp}")
        else:
            open(full, "w").write(patched)
            print(f"  ✅ Patched (timeout_ms compat): {mp}")
        continue

    # New version uses timeout_s but without ?? 300 guard
    if "isInfinite ? 14400 : step.timeout_s;" in content:
        patched = content.replace(
            "const timeoutSec = isInfinite ? 14400 : step.timeout_s;",
            "const timeoutSec = isInfinite ? 14400 : (step.timeout_s ?? 300);",
        )
        if patched == content:
            print(f"  ❌ timeout_s pattern not found in {mp}")
            ERRORS.append(f"timeout_s pattern missing in {mp}")
        else:
            open(full, "w").write(patched)
            print(f"  ✅ Patched (timeout_s guard): {mp}")
        continue

    print(f"  ⚠️  No matching pattern in {mp} — skipping")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
if ERRORS:
    print(f"⚠️  {len(ERRORS)} patch(es) failed — check patterns above:")
    for e in ERRORS:
        print(f"   • {e}")
    sys.exit(1)
else:
    print("✅ All patches applied successfully.")
    print()
    print("Next steps:")
    print("  1. Restart ai-gateway:")
    print("       pm2 restart ai-gateway  (or pm2 delete ai-gateway && PLUGHUB_ANTHROPIC_API_KEY=sk-ant-... pm2 start ecosystem.config.js --only ai-gateway)")
    print("  2. Restart skill-flow-service:")
    print("       pm2 restart skill-flow-service")
    print("  3. Check ai-gateway key has credits: https://console.anthropic.com")
    print()
    print("Note: The 'conversation-writer — invalid message payload' WARNING is harmless.")
    print("It appears because the channel-gateway publishes a routing event to conversations.inbound")
    print("on each new WebSocket connection. The writer skips it and continues normally.")
