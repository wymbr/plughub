# Session & Conference Lifecycle — Three-Layer Model

> Última atualização: 2026-05-25 · Estado: Arc 16
> Design reference for the orchestrator-bridge session lifecycle.
> Covers the three-layer model and the gap history.

---

## Status — gaps G1–G6 resolved (2026-05-10)

The six implementation gaps (G1–G6) described in the body of this document were **all addressed in the 2026-05-10 fix set** (see § Applied Fixes for the full detail). The gap analysis sections below are retained as design rationale — they describe the *original* problem each fix solved, not the current behavior.

| Gap | Original problem | Resolution |
|---|---|---|
| G1 | Contact stats (AHT) included wrap-up/NPS time | `_mark_contact_ended()` freezes `contact_ended_at` at customer departure; `contact_closed` event carries `ended_at` |
| G2 | `remaining` count ignored AI specialists | `session:{id}:active_ai_specialists` SET defers `on_human_end` until AI specialists complete |
| G3 | AI instance restored while skill flow still running | `session:{id}:ai_completing:{instance_id}` marker defers restore to the natural `process_routed` path |
| G4 | Supervisor with no heartbeat could block hook dispatch | Reclassified: supervisors have no lifecycle by design, are NOT tracked in `human_agents` — purely an analytics gap |
| G5 | Primary AI close expelled conference participants silently | `participant_left` dedup guard emits the event for external conference specialists before key cleanup |
| G6 | Redundant `_restore_all_instances` on `agent_done` close | Resolved by the G3 guards — the redundant scan is a no-op in practice |

The three-layer model (contact / segment / conference) remains the target architecture; the 2026-05-10 fixes decouple the layers at the critical points (AHT freezing, instance restore, participant accounting) without the full `_trigger_contact_close()` two-stage split.

---

## The Three Independent Layers

Every contact involves three conceptually separate lifecycles. Confusing them — or mapping one event to multiple layer boundaries — is the root cause of all the gaps described in this document.

### Layer 1 — Contact lifecycle (session_id / customer perspective)

The contact belongs to the customer. It begins when the customer arrives and ends when the customer has no more reason to be in the conference room. Its statistics (handle time, close_reason, outcome) must be frozen at the moment of customer departure, regardless of how long agents remain in the room doing wrap-up.

```
contact_opened   ← channel-gateway publishes conversations.inbound
contact_active   ← at least one agent segment is open
contact_ended    ← customer leaves (hangup, disconnect, abandon)
                   OR active primary segments = 0 AND no queue pending
```

At `contact_ended`, the following must happen **immediately and atomically**:
- Freeze contact statistics (duration, close_reason, outcome)
- Publish `conversations.session_opened/closed` to analytics Kafka
- Notify all conference participants that the contact has ended

### Layer 2 — Agent segment lifecycle (per-participant window)

Each participant has an independent window of involvement. A segment begins when the agent joins the conference and ends when that specific agent finishes their task. Crucially, `agent_done` is the signal for the **segment end** of that agent — not for the contact or the conference.

```
segment_opened   ← participant_joined (published to conversations.participants)
segment_active   ← agent is processing / waiting for customer input
segment_ended    ← agent_done OR heartbeat TTL expired
```

Segment statistics are independent: duration, outcome, close_reason all belong to the segment, not to the contact. A session can have many overlapping segments (supervisor + primary + specialist) and sequential segments (A → escalation → B → escalation → C).

Pool resource release must happen at segment end (when `agent_done` fires), not at contact end. An agent whose segment ends must immediately free capacity for new contacts — the fact that the conference room is still open for other participants is irrelevant to capacity accounting.

### Layer 3 — Conference infrastructure (the "room")

The conference room exists as long as there is any active participant or pending obligation (e.g. a hook agent that was dispatched but has not yet started). It can outlive the contact (for post-contact wrap-up and NPS). It must be destroyed only when:

- All participants have sent `agent_done` (or their heartbeat TTL has expired), AND
- No hook agents remain pending

Room destruction releases Redis keys, cleans up the stream, and publishes the final `contact_closed` analytics event.

```
conference_created   ← first participant joins
conference_active    ← ≥1 participant present
conference_destroyed ← all participants left + no pending hooks
```

---

## Current Implementation — Actual Behavior

The sections below describe what `orchestrator-bridge/main.py` **actually does** today, traced per close path.

### Path A: Primary AI agent finishes (non-conference, no escalation)

```
activate_native_agent() returns
    ↓
agent result not in ("escalated_human", "escalated_ai", "transferred")
AND conference_id is None
    ↓
asyncio.create_task(_trigger_contact_close(redis_client, session_id))
```

`_trigger_contact_close` publishes `conversations.outbound session.closed`, which causes channel-gateway to close the customer WebSocket. Channel-gateway then fires `contact_closed` with `reason=agent_done`, which triggers Path C below.

Pool resource release: the bridge now publishes `agent_done` to `agent.lifecycle` (fix applied 2026-05-10), which causes routing-engine's `remove_conversation()` to decrement `_pool_active_count_key`.

### Path B: Human agent calls /agent_done (agent_closed)

```
process_contact_event(reason="agent_closed", instance_id=X)
    ↓
srem(human_agents, X)
remaining = scard(human_agents)
    ↓
if remaining <= 0:
    if pool has on_human_end hooks:
        fire_pool_hooks(hook_type="on_human_end")
        _hook_timeout_guard(180s)
    else:
        _trigger_contact_close()
```

Hook agents are dispatched as conference specialists via a new `conversations.inbound` event. They complete through Path D below.

### Path C: Customer disconnects (customer_side=True)

Triggered by `contact_closed` events with reason in `("client_disconnect", "timeout", "session_timeout", "agent_done")`.

```
process_contact_event(reason=<customer_side>)
    ↓
setex(session:{id}:closed, 604800, reason)   ← ghost-contact guard
    ↓
lpush(session:closed:{id}, reason × n_waiting)  ← unblocks menu BLPOPs
xadd(session:{id}:stream, {type: session_closed})  ← unblocks XREADGROUP
    ↓
if human agent was active:
    publish(agent:events:{id}, session.closed)  ← notifies Agent Assist UI
    _restore_all_instances()                    ← frees human agent instances
    _publish_participant_left() for each human
    if pool has on_human_end hooks:
        fire_pool_hooks(on_human_end)
    else:
        _trigger_contact_close()
else:
    _trigger_contact_close()                    ← immediate (AI-only session)
    ↓
_restore_all_instances() for AI agents         ← frees AI agent instances
```

### Path D: Hook agent (NPS, wrap-up) finishes

```
process_routed() — hook agent completes
    ↓
hook_label = getdel(session:{id}:hook_conf:{conference_id})
    ↓
remaining_hooks = decr(session:{id}:hook_pending:{hook_type})
    ↓
if remaining_hooks <= 0:
    if hook_type == "on_human_end":
        if pool has post_human hooks:
            fire_pool_hooks(post_human)
        else:
            _trigger_contact_close()
    elif hook_type == "post_human":
        _trigger_contact_close()
```

### _trigger_contact_close — what it does

```python
# NX idempotency guard — fires exactly once per session
await redis_client.set(f"session:{session_id}:close_fired", "1", nx=True, ex=3600)
# Publishes conversations.outbound {type: session.closed}
# Publishes conversations.events {type: contact_closed} → analytics
```

---

## Gap Analysis

### G1 — Contact statistics time includes wrap-up and NPS duration

**Layer violation:** Layer 1 (contact stats) and Layer 3 (conference destruction) are collapsed into a single event.

**Current:** `_trigger_contact_close()` fires only after all `on_human_end` and `post_human` hook agents complete. The `contact_closed` analytics event — and its embedded timestamp — is recorded after wrap-up. Contact duration (from `session_opened_at` to `contact_closed_at`) includes NPS + wrap-up time.

**Correct:** Contact statistics should be frozen the moment the customer leaves (or the last primary segment ends). Wrap-up and NPS are post-contact activities that belong to the conference room, not to the contact handle time.

**Impact:** AHT (Average Handle Time) is inflated. Agent performance reports are incorrect for any pool with wrap-up hooks.

**Fix required:** Split `_trigger_contact_close()` into two stages:
- `_finalize_contact_stats(session_id, reason)` — emits the analytics event with the correct timestamp; called at customer departure.
- `_destroy_conference(session_id)` — cleans Redis keys and stream; called when the last participant leaves.

---

### G2 — `remaining` count is human-only; AI conference specialists are invisible to it

**Code:** `remaining = await redis_client.scard(f"session:{session_id}:human_agents")` (process_contact_event, agent_closed path)

**Current:** when the last human agent calls `agent_done`, `remaining` drops to 0 even if an AI specialist (e.g. a `task` step assist agent) is still responding to the customer's last message. `on_human_end` hooks fire immediately, dispatching NPS and wrap-up agents. These agents may send messages to the customer while the specialist AI is still writing.

**Impact:** interleaved messages from specialist AI and NPS agent in the customer channel. Edge case (specialist must still be active when human decides to end), but recoverable — `_trigger_contact_close()` eventually closes the specialist via `session.closed` signal.

**Fix required:** before firing `on_human_end`, check a parallel `session:{id}:active_ai_specialists` SET (non-hook conference participants). Wait for them to complete or send a terminate signal first.

---

### G3 — AI instance restored while its skill flow is still running

**Code:** lines 2383–2391 of process_contact_event — `_restore_instance()` is called directly (not via `create_task`) before `_trigger_contact_close()` is scheduled.

**Sequence when customer abandons with queue agent active:**

```
contact_closed (customer_side=True, no human agent)
    ↓
asyncio.create_task(_trigger_contact_close(...))   ← queued, not yet run
    ↓
await _restore_instance(queue_agent_instance)       ← runs NOW
    → writes status=ready, current_sessions=X-1 to Redis
    ↓
[next event loop tick]
_trigger_contact_close runs
    → lpush session:closed:{id}                    ← unblocks BLPOP
    ↓
queue agent skill flow ends
    → bridge publishes agent_done
    → remove_conversation() decrements pool counter
```

**Window:** between `_restore_instance()` and the BLPOP unblock, routing engine sees the instance as `ready` and may assign a new contact. The queue agent is actually still running.

For stateless AI agents this is low severity (LLM calls are independent). For stateful AI agents this is a correctness bug: two simultaneous contexts on one instance.

**Fix required:** do not call `_restore_all_instances()` eagerly on customer disconnect for AI agents that are still tracked as running. Instead, let the `agent_done` publish (now in place after the 2026-05-10 fix) drive instance restoration. The bridge should use a "pending restore" marker that `agent_done` clears, not an immediate write.

---

### G4 — Human supervisors and evaluators have no heartbeat / segment cleanup

**Current:** human supervisors join via the platform UI. Their connection to the session is tracked through `session:{id}:human_agents` SET (if `activate_human_agent` is called for them). When a supervisor closes the browser without calling `agent_done`, no cleanup path fires. The instance stays in `human_agents` and `remaining` never reaches 0 when the primary agent finishes — so `on_human_end` hooks never dispatch.

**Impact:** sessions with an active supervisor that disconnects silently can get stuck. The primary human agent finishes, `remaining == 1` (supervisor still counted), hooks never fire, contact never closes.

**Needs verification:** whether supervisors are added to `human_agents` via `activate_human_agent`. If they are added through a separate mechanism (direct Redis pub/sub without SADD), this gap may not apply.

**Fix required (if confirmed):** track supervisors and evaluators in a separate SET with an explicit TTL-based heartbeat. Remove them from the close-blocking count — supervisor presence should not prevent `on_human_end` from firing. Supervisor segment cleanup should be driven by heartbeat TTL expiry, not by `agent_done`.

---

### G5 — Primary AI agent closes contact while supervisor / evaluator is online

**Code:** `if not conference_id and _ai_outcome not in _escalation_outcomes: asyncio.create_task(_trigger_contact_close(...))`

**Current:** when a primary AI agent finishes a 1:1 session, `_trigger_contact_close()` fires. This is correct for 1:1. However, if a supervisor or evaluator joined the session (via conference) and the primary AI completes, the contact closes abruptly — the supervisor's segment is never properly closed, no `participant_left` is emitted for them.

**Impact:** supervisor/evaluator session ends without a `segment_ended` event. Analytics miss the supervisor's participation window.

**Fix required:** before calling `_trigger_contact_close()`, check for any active conference participants. If present, notify them via `agent:events:{id}` and wait for their `agent_done` (or TTL) before destroying the conference.

---

### G6 — `contact_closed` with reason=agent_done triggers `_restore_all_instances` redundantly

**Code:** `customer_side = reason in (..., "agent_done")` (line 2108). When `_trigger_contact_close()` fires after a primary AI completes, channel-gateway closes the customer WebSocket and fires `contact_closed` with `reason=agent_done`. This re-enters `process_contact_event` as `customer_side=True` and calls `_restore_all_instances()` — but the primary AI instance was already restored in `process_routed`.

The `_restore_instance()` call is idempotent (`max(0, current_sessions-1)`) so this does not corrupt state. However, if other AI agents from the same session are still running (hook agents dispatched after AI completion), they get their instances restored while active.

**Impact:** same race window as G3, but narrower — hook agents are typically quick (seconds).

**Fix required:** when `reason=agent_done`, skip `_restore_all_instances()` — the primary agent's restore was already handled in `process_routed`. Use the `ai_agents` SET as a guard: only restore instances that are NOT already in the process of completing naturally.

---

## Correct Close Logic (Target Model)

```
CONTACT FINALIZED when:
  customer leaves (any reason)
  OR: primary segments == 0 AND no resource in queue for this session

  → _finalize_contact_stats(session_id, reason, timestamp=now)
     Publishes conversations.session_opened/closed (analytics)
     Records duration, close_reason, outcome
     Does NOT close WebSocket, does NOT destroy Redis keys

  → Notify all conference participants: "contact ended, wrap up now"
     publish(agent:events:{id}, {type: contact_ended})

SEGMENT ENDED when:
  agent_done received for that specific participant
  OR: heartbeat TTL expired for that participant

  → participant_left published (Arc 5 analytics)
  → instance restored to ready
  → pool busy counter decremented (via agent_done → agent.lifecycle)

CONFERENCE DESTROYED when:
  active_participants_count == 0
  AND hook_pending counters are all 0

  → _destroy_conference(session_id)
     lpush session:closed (unblocks any remaining BLPOPs)
     xadd session_closed to stream
     delete Redis keys (stream, messages, meta, context, etc.)
     publish contact_closed to conversations.events (for remaining cleanup)
```

---

## Implementation Gaps — Resolution Status

All six gaps were resolved in the 2026-05-10 fix set. The table below records the original severity and the resolution.

| # | Gap | Original severity | Status |
|---|---|---|---|
| G4 | Supervisor/evaluator stuck sessions | High | ✅ Resolved — reclassified (supervisors have no lifecycle, not in `human_agents`); remaining analytics-only gap |
| G3 | AI instance restored while running | Medium-High | ✅ Resolved — `ai_completing` marker defers restore to `process_routed` |
| G1 | Contact stats include wrap-up time | Medium | ✅ Resolved — `_mark_contact_ended()` + `ended_at` in `contact_closed` event |
| G2 | remaining ignores AI specialists | Medium | ✅ Resolved — `active_ai_specialists` SET defers `on_human_end` |
| G5 | AI close expels supervisor silently | Medium | ✅ Resolved — `participant_left` dedup guard for external specialists |
| G6 | Redundant restore on agent_done close | Low | ✅ Resolved — no-op in practice via G3 guards |

Note: the G1 fix freezes AHT via `_mark_contact_ended()` rather than the full two-stage split of `_trigger_contact_close()` proposed earlier in this document; the two-stage split remains a possible future refinement but is no longer required for correctness.

---

## Applied Fixes

### 2026-05-10

- **Busy counter on cross-pool transfer:** `mark_busy()` now writes `_session_serving_pool_key`; `release_session_from_pool()` detects pool change and decrements origin pool counter.
- **Pool counter on queue entry:** `route()` calls `_release_session_from_pool()` when a contact enters a new pool's queue without agent allocation.
- **`agent_done` for native and YAML-fallback AI agents:** `orchestrator-bridge` now publishes `agent_done` to `agent.lifecycle` after skill flow completes, so routing-engine's `remove_conversation()` correctly decrements `_pool_active_count_key`.
- **G4 reclassified:** Supervisors have no lifecycle by design (`supervisor.ts`: "Supervisor não é um agente — não tem ciclo de vida"). They are NOT tracked in `human_agents` and cannot block hook dispatch. G4 is an analytics gap (no `participant_joined`/`participant_left` for supervisors), not a functional gap.
- **G3 fix — AI instance completing guard:** added `session:{id}:ai_completing:{instance_id}` marker (TTL 4h) set when `activate_native_agent()` is called and cleared when it returns. `process_contact_event` now skips immediate `_restore_instance()` for AI agents whose completing marker is present, deferring restore to the natural `process_routed` path. Both YAML-fallback and plughub-native paths SREM from `ai_agents` after natural restore + `agent_done` publish, so the emergency restore in `process_contact_event` only fires for crash recovery (marker absent or expired).
- **G1 fix — AHT decoupled from wrap-up/NPS time:** added `_mark_contact_ended(redis_client, session_id)` helper using `SET NX` (first call wins, TTL 7d) at the three true contact-end points: (a) customer disconnect path in `process_contact_event`; (b) last human `agent_done` path when `remaining <= 0`; (c) primary AI done path in `process_routed` before `_trigger_contact_close`. Updated `_trigger_contact_close()` to read `session:{id}:contact_ended_at` from Redis and include as `ended_at` in the `contact_closed` Kafka event payload. `analytics-api parse_conversations_event()` already reads `ended_at` (falls back to `_now()` when absent), so AHT (`handle_time_ms`) is now frozen at customer departure, regardless of how long hook agents run afterward.
- **G2 fix — `remaining` ignores AI specialists:** added `session:{id}:active_ai_specialists` SET. In `process_routed`, non-hook AI conference specialists (task-step) are SADD'd to this SET at join time (detected by checking for absence of `hook_conf` key). In the `agent_closed` path when `remaining <= 0`, `scard(active_ai_specialists)` is checked before dispatching `on_human_end`: if > 0, hook config is persisted to `session:{id}:pending_on_human_end` (TTL 5 min) and hooks are deferred. In `conference_agent_completed`, SREM from the SET; when the count hits 0 and `pending_on_human_end` exists, hooks are dispatched from the stored config. Fallback: if `setex` for `pending_on_human_end` fails, dispatch hooks immediately (belt-and-suspenders).
- **G5 fix — primary AI close expels conference participants silently:** `participant_left` was already emitted for native bridge specialists in `process_routed` when `activate_native_agent` returns (including after session-close signal). The gap was **external conference specialists** (external-mcp SDK, running outside the bridge) who never go through `activate_native_agent`. Fix: added `SET NX` dedup guard key `session:{id}:participant_left:{instance_id}` (TTL 24h) written by `process_routed` immediately after emitting the event. `conference_agent_completed` handler attempts the same `SET NX`; if it wins (guard absent), this is an external agent and it reads `participant_joined_at`, `segment`, `conference:specialist:*`, and `meta` to build and emit `participant_left` before key cleanup. Notification of participants (`agent:events:{id}`) was already handled by `_trigger_contact_close`.
- **G6 resolved by G3 — redundant restore on agent_done close:** Analysis shows no additional code is needed. `_restore_all_instances` for human agents is gated on `if is_human:`, which is always False when `reason=agent_done` (primary AI session, no human agent active). The AI restore block already uses `ai_completing` marker + `ai_agents` SREM as guards (G3 fix): the primary AI instance is SREM'd from `ai_agents` in `process_routed` before `_trigger_contact_close` fires, so the re-entry scan finds no primary AI to restore. Hook agents with active `ai_completing` markers are correctly skipped. The redundant scan is a no-op in practice.
