# @plughub/skill-flow-engine — Skill Flow Interpreter

## What it is

Interpreter for the Skill Flow — reads the `flow` field of an orchestration skill
and executes the declarative step graph sequentially.
Persists pipeline_state to Redis on every transition.

## Responsibilities

1. Resume an existing session pipeline or start from the entry point
2. Execute each step by its type (9 types)
3. Persist pipeline_state to Redis on every transition
4. Manage retry_counts for catch steps
5. Resolve JSONPath references in invoke, reason, and menu inputs
6. Suspend execution on `menu` steps (status: awaiting_selection) and resume on the next inbound turn

## What is NOT this package's responsibility

- Does not make business decisions — executes what is declared in the flow
- Does not communicate with the customer directly — delegates to Notification Agent via tool
- Does not implement routing logic — delegates to mcp-server-plughub
- Does not validate the skill schema — responsibility of the Agent Registry
- Does not implement channel-specific rendering for menu/form — that is Channel Gateway's responsibility
- Does not validate business rules on captured form fields — validation belongs to subsequent steps

## Main files

- `engine.ts`   — SkillFlowEngine: initialises, resumes and coordinates execution
- `executor.ts` — executes a step and returns the next step id
- `state.ts`    — reads/writes PipelineState to Redis
- `steps/`      — one file per step type (9 files)

## Step types

| Type | Suspends turn? | Result in pipeline_state |
|---|---|---|
| `task` | Yes (A2A) | agent_done payload |
| `choice` | No | — |
| `catch` | No (internal) | retry_counts updated |
| `escalate` | Yes | handoff context |
| `complete` | No (terminal) | outcome |
| `invoke` | No | tool output at output_as key |
| `reason` | No | LLM JSON at output_as key |
| `notify` | No | delivery status |
| `menu` | Yes (awaiting_selection) | captured value at result key |

### menu step — result types by interaction mode

| Interaction | pipeline_state result type |
|---|---|
| `text` | `string` |
| `button` | `string` (option id) |
| `list` | `string` (option id) |
| `checklist` | `string[]` (option ids) |
| `form` | `object` (field id → value) |

The menu step emits a `MenuPayload` to the Notification Agent, which forwards it to
the Channel Gateway. The Channel Gateway handles channel-specific rendering and
collects input (sequentially for channels without native multi-field support).
skill-flow always receives a single normalised `MenuSubmitEvent` regardless of
how many channel turns were required.

## Dependencies

- `@plughub/schemas`  — SkillFlow, FlowStep, PipelineState, MenuPayload
- `ioredis`           — Redis client for pipeline_state
- `jsonpath-plus`     — JSONPath resolution in choice, invoke, menu
- `@modelcontextprotocol/sdk` — calls to mcp-server-plughub

## Invariants

- pipeline_state is persisted BEFORE executing the next step
- Automatic resume: if pipeline_state.status === "in_progress" → resume
- menu steps set status to "awaiting_selection" — engine does NOT advance until MenuSubmitEvent arrives
- catch steps execute internally — no A2A delegation
- invoke and reason steps are atomic operations — no reasoning loop
- retry_counts persists in pipeline_state on every catch attempt

## menu step — BLPOP protocol and conference handling

The `menu` step suspends execution by blocking on Redis BLPOP. Understanding the
exact Redis key semantics is essential for both single-agent and conference scenarios.

### Redis keys

| Key | Written by | Read by | Purpose |
|---|---|---|---|
| `menu:result:{session_id}` | Orchestrator Bridge | menu step (BLPOP) | Delivers the customer's reply |
| `session:closed:{session_id}` | Orchestrator Bridge | menu step (BLPOP) | Signals customer disconnect |
| `menu:signal:{session_id}[:{instance_id}]` | Orchestrator Bridge (only) | menu and resolve steps (BLPOP) | Platform signals: @mention interrupts (`trigger_step`, `terminate`) and channel collect outcome (`timeout` → `on_timeout`, `invalid` → `on_invalid`, both falling back to `on_failure`). Parsed by `steps/signals.ts`. **Nothing in `menu:result` is interpreted** — until 2026-09-16 interrupts rode that queue and a customer typing the JSON jumped the flow (MEN-07) |
| `menu:waiting:{session_id}` | menu step (before BLPOP) | Orchestrator Bridge | Flag: an AI agent is waiting for input |

### Execution flow

```
1. Send prompt via notification_send (→ Notification Agent → Channel Gateway → customer)
2. SET menu:waiting:{session_id} EX (timeout_sec + 10)
3. BLPOP [menu:result:{session_id}, session:closed:{session_id}, menu:signal:{session_id}] timeout_sec
4. DEL menu:waiting:{session_id}   ← always, in finally block
```

The `menu:waiting` flag is set **before** the BLPOP and deleted in a `finally` block
so it is always removed regardless of how the BLPOP resolves (success, timeout, or error).

### Three BLPOP outcomes

| Result | `next_step_id` used |
|---|---|
| `menu:result` key fired (customer replied) | `on_success` |
| `session:closed` key fired (customer disconnected) | `on_disconnect` (falls back to `on_failure`) |
| `nil` (BLPOP timed out) | `on_timeout` (falls back to `on_failure`) |
| `menu:signal` key fired | per signal: `trigger_step` → that step · `terminate` → `on_failure` · collect `timeout` → `on_timeout` · collect `invalid` → `on_invalid` (both fall back to `on_failure`) · unreadable → warn + `on_failure` |

**Choice menus only accept an option id (NIV-13, 2026-09-24):** for `button`/`list`/`checklist`,
`answerOutsideOptions` checks the answer against the resolved option ids (static or dynamic). Outside
them the SAME menu is re-offered — the author's `retry.reprompt` when declared, otherwise the prompt
itself (the platform never invents customer-facing text) — up to `OPTION_RESENDS` (2), then
`on_invalid` (declared) or `on_failure`. The refused value is never logged. Before this, any text
became "the choice" (6 of 11 skill menus fed it to a `choice` default).

**Inside `begin_transaction` (NIV-19, 2026-09-24):** when the destination is a branch the author
DECLARED (`on_timeout` / `on_invalid` / `on_disconnect`), the menu returns `declared_branch: true`
and the engine follows it instead of rewinding to the block's `on_failure`. Either way the transaction
ends and the masked scope is discarded. A fallback to the menu's own `on_failure`, and a channel
`aborted`, still rewind. Tests: `engine-transaction.test.ts` § NIV-19 (mutation-checked).

### Why multi-key BLPOP on both result and closed

Without the `session:closed` key, a disconnect mid-wait would cause the BLPOP to run
until its timeout (up to 5 minutes), leaving the AI agent instance occupied and
unavailable in the Routing Engine for the full duration. The multi-key BLPOP unblocks
immediately when the Orchestrator Bridge detects the disconnect and pushes to
`session:closed:{session_id}`.

### Conference scenario: `menu:waiting` flag

In a conference, both a human agent and an AI `menu` step may be active simultaneously.
The Orchestrator Bridge processes each inbound customer message with independent checks:

```python
if is_human:      # → publish to agent:events:{session_id} (human agent WebSocket)
if menu_waiting:  # → lpush to menu:result:{session_id}   (AI BLPOP)
```

The checks are **not mutually exclusive**. The `menu:waiting` flag tells the bridge
whether the AI side has an active BLPOP. Without it, the bridge would have no safe
way to determine whether to push to `menu:result` while a human agent is also active
(pushing to a key with no consumer would leave orphaned data in Redis).

If `menu:waiting` is absent (AI is not in a `menu` step), the bridge skips the LPUSH
entirely. There is no race condition: the flag is set before the BLPOP and deleted
immediately after in the `finally` block.

### HTTP timeout requirement

The bridge HTTP timeout for AI session calls must exceed the maximum `menu` step
`timeout_ms`. Default `timeout_ms` is 300 000 ms (300 s); the bridge timeout is
360 s. If the HTTP call times out before the BLPOP completes, the engine's in-flight
turn would be cancelled without updating `pipeline_state`, leaving the session
stranded in `awaiting_selection`.

## MCP call idempotency — invoke and notify steps

### Problem

The execution lease prevents two engine instances from running the same pipeline
concurrently. However, within a single execution there is still a crash window
specific to steps that call MCP tools with side effects (`invoke`, `notify`):

```
ctx.mcpCall(tool, input)    ← MCP tool executes (CRM write, notification sent, …)
        ↓
  [engine crash here]       ← result obtained but pipeline_state not yet saved
        ↓
engine resumes from current_step_id (same step)
        ↓
ctx.mcpCall called again    ← duplicate side effect
```

For read-only tools this is harmless. For writes (CRM update, record creation) or
notifications (message sent to customer), a duplicate call is a real problem.

### Solution: two-phase sentinel in pipeline_state.results

Both `invoke` and `notify` use a sentinel key stored in `pipeline_state.results`
alongside the step output to make the operation effectively idempotent across crashes:

| Sentinel value | Meaning | Resume behaviour |
|---|---|---|
| absent | Not yet called | Call MCP normally |
| `"dispatched"` | Called; result not yet persisted | Re-call MCP (at-least-once, residual window) |
| `"completed"` | Called and result persisted | Skip MCP — return stored result |

**Execution sequence:**

1. Set `{step_id}:__invoked__` (or `__notified__`) = `"dispatched"` → `saveState`
2. Execute `ctx.mcpCall(…)`
3. Set sentinel = `"completed"` + store result → `saveState`
4. Return to engine loop (engine saves again with transition — harmless duplicate)

**Crash window analysis:**

- Crash before step 1: no sentinel → fresh execution ✓
- Crash between steps 1 and 2: sentinel = `"dispatched"`, no result → re-call MCP
  (window is in-process, ~microseconds between two Redis writes)
- Crash between steps 2 and 3: sentinel = `"dispatched"`, no result → re-call MCP
  (window is between MCP returning and the saveState completing — unavoidable without
  distributed transactions; at-least-once semantics for this residual case)
- Crash after step 3, before engine transition: sentinel = `"completed"`, result present
  → engine resumes, finds sentinel, returns stored result without re-calling MCP ✓

The last case is the primary improvement: it closes the largest crash window (between
MCP success and the engine loop's `stateManager.save`).

### Sentinel key naming

```
invoke:  {step.id}:__invoked__
notify:  {step.id}:__notified__
task:    {step.id}:__job_id__    (existing, same pattern)
```

### On failure: sentinel stays as "dispatched"

When the MCP call throws, the sentinel remains `"dispatched"`. This is intentional:
- On the *failure* path nothing removes the sentinel — it is left as `"dispatched"` on purpose
  (this line previously claimed `pipeline_state.results` was append-only; that is no longer true —
  see **Sentinel scope: one INVOCATION, not one flow** below)
- On retry (via a `catch` step), the step re-executes with sentinel = `"dispatched"`,
  which correctly triggers a new MCP call
- The `"dispatched"` state never prevents a retry — it only affects the crash recovery
  path when `"completed"` would suppress re-execution

### Sentinel scope: one INVOCATION, not one flow

**`addTransition` clears the entering step's sentinels** (`__invoked__`, `__notified__`,
`__job_id__`) before persisting. Entering a step through a transition is a NEW logical
invocation; resuming after a crash is not.

**Why this exists — measured on a real contact, 2026-09-06.** The sentinel key is just the
`step.id`, so it did not say *"this MCP call already happened in this execution"*, it said
*"this step already ran, ever"*. In a cycle `menu → invoke → choice → menu` — a shape the flow
validator explicitly SANCTIONS, because the back-edge passes through a step that blocks on I/O —
the second visit to the `invoke` returned the stored result and never called the world again. The
navigation cursor stayed on the same level, the customer saw the same menu rebuilt, and **nothing
went red**: the step reported `on_success` every time. The validator and the runtime were answering
the same question differently, and the silent one won.

**Crash idempotency is untouched, and that is structural, not a promise.** The engine's resume path
starts by executing `current_step_id` **without calling `addTransition`** — so a sentinel written
before a crash survives and still suppresses the duplicate side effect. The unit test carries that
as its load-bearing case: *"re-executes on re-entry"* alone would stay green under an implementation
that simply never wrote the sentinel, and that implementation would send the customer two messages.

**The step's RESULT is not cleared with it.** Other steps reference it via `$.pipeline_state.*`, and
deleting it would open a window where the ref resolves empty. Re-execution overwrites it, which is
the wanted behaviour.

**Census at the time of the change** — 3 of 40 skills had a sentinel-bearing step inside a cycle:
`skill_navegacao_v1` (`descer`/`nivel_raiz` invoke, `reiniciar` notify), `agente_fila_v1`
(`enviar_resposta` notify — the queue agent would go MUTE after its first reply while the `reason`
step kept burning tokens) and `agente_copilot_v1` (`enviar_sugestao` notify). All three WANT
re-execution, so the change fixes them rather than altering them. ⚠️ Only the first was measured as
damaged; the other two are **exposure**, not confirmed damage — two different numbers.

Tests: `src/__tests__/sentinel-cycle.test.ts` (6 cases, verified by mutation).

### Scope: invoke and notify only

`reason` (AI Gateway) and `task` (agent_delegate with job_id) do not need this sentinel
because `reason` is a pure computation with no external side effects, and `task` already
has its own robust idempotency mechanism via `job_id`.

`escalate` calls `routing_escalate` on mcp-server-plughub, which is idempotent by design
(re-escalating a session that is already escalated is a no-op at the Routing Engine level).

## Execution lease — preventing duplicate execution and false-positive crash recovery

### Problem

The Routing Engine keeps per-instance heartbeat keys with a 30-second TTL.
Native AI agents are stateless and do not send heartbeats while executing a skill.
A skill flow with a `menu` step can block for up to 300 seconds (BLPOP timeout).
After 30 seconds of silence, the CrashDetector would declare the instance dead and
re-queue its active conversations — restarting a perfectly healthy execution.

Additionally, without instance-aware locking, two Orchestrator Bridge replicas could
simultaneously invoke the same session's skill flow (network retry + crash recovery
arriving at the same time), advancing `pipeline_state` twice from the same step.

### Solution: instance-aware execution lock

The engine stores `instance_id` as the value of the lock key:

```
{tenant_id}:pipeline:{session_id}:running  →  "<instance_id>"   (TTL 400s)
```

Key properties:

| Property | Detail |
|---|---|
| Key | `{tenant_id}:pipeline:{session_id}:running` |
| Value | `instance_id` from the Orchestrator Bridge |
| Default TTL | 400 seconds (> 300s menu BLPOP + 60s HTTP margin + 40s network margin) |
| Acquisition | `SET NX` — fails immediately if lock already held |
| Release | Lua atomic: GET → compare `instance_id` → DEL only if owner |
| Renewal | Lua atomic: GET → compare `instance_id` → EXPIRE only if owner |

### renewLock in the menu step

Before entering the BLPOP wait, `executeMenu` calls `ctx.renewLock(timeoutSec + 60)`:

- Extends the lock TTL to cover the full BLPOP duration plus 60 seconds of HTTP margin.
- Returns `false` if the lock was taken by another instance (crash recovery race):
  in that case the step returns `on_failure` and aborts gracefully — the new instance
  is already running the pipeline.
- Uses a Lua atomic script to prevent the TOCTOU race between checking ownership and
  updating the TTL.

### CrashDetector integration

Before re-queuing any conversation, the CrashDetector checks whether the execution
lock key exists. If the lock is present, the engine is still actively executing and
the heartbeat expiry is a false positive — the conversation is skipped. See
routing-engine `CLAUDE.md` for the full rationale.

### Lock TTL rationale: 400 seconds

- Maximum `menu` `timeout_ms`: 300 000 ms (300 s)
- Bridge HTTP timeout: 360 s (60 s above menu max)
- Lock default TTL: 400 s (40 s above bridge timeout)
- `renewLock` called as `timeoutSec + 60` — for a 300s menu, the lock is renewed
  to 360 s, fitting within the 400 s default and covering the bridge HTTP timeout.

## Spec reference

- 4.7  — Skill Flow schema and fields for each step (menu: section 4.7m)
- 9.5i — execution of task, escalate, catch steps and resume
