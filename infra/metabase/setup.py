#!/usr/bin/env python3
"""
setup.py
Metabase automated initialization for PlugHub Analytics.

Runs ONCE after Metabase starts (managed by docker-compose as a one-shot service).
Idempotent: checks if setup is already complete before doing anything.

Steps:
  1. Wait for Metabase API to be ready
  2. Complete initial setup (admin account + site name)
  3. Create ClickHouse database connections (one per tenant for sandboxing)
  4. Create 5 base questions (saved native SQL queries with tenant_id filter)
  5. Create the "PlugHub Analytics" dashboard and wire all 5 questions into it

Environment variables (all have defaults for local development):
  METABASE_URL            http://metabase:3000
  METABASE_ADMIN_EMAIL    admin@plughub.local
  METABASE_ADMIN_PASSWORD plughub_admin_2024
  METABASE_SITE_NAME      PlugHub Analytics
  CH_HOST                 clickhouse
  CH_PORT                 8123
  CH_DATABASE             plughub
  TENANTS                 tenant_telco,tenant_bank   (comma-separated)
  CH_PASS_PREFIX          tenant_  (password = <prefix><tenant_id>_ro_2024)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any

# ─── Config ───────────────────────────────────────────────────────────────────

METABASE_URL     = os.environ.get("METABASE_URL", "http://metabase:3000").rstrip("/")
ADMIN_EMAIL      = os.environ.get("METABASE_ADMIN_EMAIL",    "admin@plughub.local")
ADMIN_PASSWORD   = os.environ.get("METABASE_ADMIN_PASSWORD", "plughub_admin_2024")
SITE_NAME        = os.environ.get("METABASE_SITE_NAME",      "PlugHub Analytics")
CH_HOST          = os.environ.get("CH_HOST",       "clickhouse")
CH_PORT          = int(os.environ.get("CH_PORT",   "8123"))
CH_DATABASE      = os.environ.get("CH_DATABASE",   "plughub")
TENANTS          = [t.strip() for t in os.environ.get("TENANTS", "tenant_telco,tenant_bank").split(",") if t.strip()]
CH_PASS_PREFIX   = os.environ.get("CH_PASS_PREFIX", "tenant_")
# Optional override: single shared CH user (used when per-tenant users are not created)
CH_USER_OVERRIDE     = os.environ.get("CH_USER", "")
CH_PASSWORD_OVERRIDE = os.environ.get("CH_PASSWORD", "")


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _req(method: str, path: str, body: Any = None, token: str | None = None) -> Any:
    url  = f"{METABASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Metabase-Session", token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {body_text}") from e


def get(path: str, token: str | None = None) -> Any:
    return _req("GET", path, token=token)


def post(path: str, body: Any, token: str | None = None) -> Any:
    return _req("POST", path, body=body, token=token)


def put(path: str, body: Any, token: str | None = None) -> Any:
    return _req("PUT", path, body=body, token=token)


# ─── Wait for Metabase ────────────────────────────────────────────────────────

def wait_for_metabase(max_wait: int = 300) -> None:
    print(f"Waiting for Metabase at {METABASE_URL}…")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            get("/api/health")
            print("Metabase is up.")
            return
        except Exception:
            time.sleep(5)
    raise RuntimeError("Metabase did not become ready in time")


# ─── Setup step 1: admin account ─────────────────────────────────────────────

def initial_setup() -> str:
    """
    Complete Metabase initial setup. Returns session token.
    If already set up, just logs in.
    """
    props = get("/api/session/properties")
    setup_token = props.get("setup-token")

    if not setup_token:
        # Already set up — just log in
        print("Metabase already configured, logging in…")
        session = post("/api/session", {
            "username": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        })
        return session["id"]

    print("Running initial Metabase setup…")
    try:
        result = post("/api/setup", {
            "token": setup_token,
            "prefs": {
                "site_name":    SITE_NAME,
                "site_locale":  "pt",
                "allow_tracking": False,
            },
            "user": {
                "email":      ADMIN_EMAIL,
                "password":   ADMIN_PASSWORD,
                "first_name": "PlugHub",
                "last_name":  "Admin",
                "site_name":  SITE_NAME,
            },
            "database": None,
        })
        token = result.get("id")
    except RuntimeError as exc:
        if "403" in str(exc):
            # User already exists (e.g. volume persisted from previous run) — just log in
            print("  Setup already completed (403), falling back to login…")
            token = None
        else:
            raise

    if not token:
        session = post("/api/session", {
            "username": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        })
        token = session["id"]

    print(f"Initial setup complete. Session: {token[:8]}…")
    return token


# ─── Setup step 2: ClickHouse database connections ───────────────────────────

def create_ch_databases(token: str) -> dict[str, int]:
    """
    Create one ClickHouse connection per tenant.
    Returns dict: tenant_id → database_id
    """
    existing_dbs = {db["name"]: db["id"] for db in get("/api/database", token=token).get("data", [])}

    tenant_db_ids: dict[str, int] = {}

    for tenant in TENANTS:
        conn_name = f"ClickHouse — {tenant}"
        if conn_name in existing_dbs:
            tenant_db_ids[tenant] = existing_dbs[conn_name]
            print(f"  DB already exists: {conn_name} (id={existing_dbs[conn_name]})")
            continue

        # Use override credentials if set (single shared user), else per-tenant convention
        if CH_USER_OVERRIDE:
            ch_user     = CH_USER_OVERRIDE
            ch_password = CH_PASSWORD_OVERRIDE
        else:
            ch_user     = tenant
            ch_password = f"{CH_PASS_PREFIX}{tenant}_ro_2024"

        result = post("/api/database", {
            "name":   conn_name,
            "engine": "clickhouse",
            "details": {
                "host":     CH_HOST,
                "port":     CH_PORT,
                "dbname":   CH_DATABASE,
                "user":     ch_user,
                "password": ch_password,
                "ssl":      False,
            },
            "auto_run_queries":      True,
            "is_full_sync":          True,
            "is_on_demand":          False,
        }, token=token)
        db_id = result["id"]
        tenant_db_ids[tenant] = db_id
        print(f"  Created DB: {conn_name} (id={db_id})")

    return tenant_db_ids


# ─── Base SQL questions ────────────────────────────────────────────────────────

# Each question is defined as (name, display, sql, visualization_settings)
# SQL uses ClickHouse syntax; no template tags needed because the DB connection
# already scopes to one tenant via row policies.

_QUESTIONS = [
    (
        "Sessões por Canal e Outcome (24h)",
        "table",
        """SELECT
    channel,
    outcome,
    count()                              AS sessions,
    round(avg(handle_time_ms) / 1000, 1) AS avg_handle_s,
    round(avg(wait_time_ms)   / 1000, 1) AS avg_wait_s
FROM plughub.sessions
WHERE opened_at >= now() - INTERVAL 1 DAY
GROUP BY channel, outcome
ORDER BY sessions DESC""",
        {"column_settings": {}},
    ),
    (
        "Queue Events — Abandono por Pool (24h)",
        "table",
        """SELECT
    pool_id,
    event_type,
    count()                                    AS events,
    round(avg(estimated_wait_ms) / 1000, 1)   AS avg_estimated_wait_s
FROM plughub.queue_events
WHERE timestamp >= now() - INTERVAL 1 DAY
GROUP BY pool_id, event_type
ORDER BY pool_id, event_type""",
        {},
    ),
    (
        "Agent Performance — Handle Time e Outcome (24h)",
        "table",
        """SELECT
    agent_type_id,
    outcome,
    count()                              AS sessions,
    round(avg(handle_time_ms) / 1000, 1) AS avg_handle_s,
    round(min(handle_time_ms) / 1000, 1) AS min_handle_s,
    round(max(handle_time_ms) / 1000, 1) AS max_handle_s
FROM plughub.agent_events
WHERE timestamp >= now() - INTERVAL 1 DAY
  AND event_type = 'agent_done'
GROUP BY agent_type_id, outcome
ORDER BY sessions DESC""",
        {},
    ),
    (
        "Usage Metering por Dimensão (7 dias)",
        "row",
        """SELECT
    dimension,
    sum(quantity)  AS total,
    count()        AS events
FROM plughub.usage_events
WHERE timestamp >= now() - INTERVAL 7 DAY
GROUP BY dimension
ORDER BY total DESC""",
        {},
    ),
    (
        "Sentiment Timeline por Pool (24h)",
        "line",
        """SELECT
    pool_id,
    toStartOfHour(timestamp)  AS hour,
    round(avg(score), 3)      AS avg_score,
    count()                   AS samples
FROM plughub.sentiment_events
WHERE timestamp >= now() - INTERVAL 1 DAY
GROUP BY pool_id, hour
ORDER BY pool_id, hour""",
        {
            "graph.dimensions": ["hour"],
            "graph.metrics":    ["avg_score"],
            "graph.series_labels": {"avg_score": "Sentimento médio"},
        },
    ),
]


def create_questions(token: str, db_id: int, collection_id: int | None) -> list[int]:
    """Create the 5 base questions. Returns list of card IDs."""
    existing_cards = {c["name"]: c["id"] for c in get("/api/card", token=token)}
    card_ids: list[int] = []

    for (name, display, sql, vis_settings) in _QUESTIONS:
        if name in existing_cards:
            card_ids.append(existing_cards[name])
            print(f"  Card already exists: {name!r}")
            continue

        payload: dict[str, Any] = {
            "name":    name,
            "display": display,
            "dataset_query": {
                "type":     "native",
                "database": db_id,
                "native":   {"query": sql},
            },
            "visualization_settings": vis_settings,
        }
        if collection_id:
            payload["collection_id"] = collection_id

        result = post("/api/card", payload, token=token)
        card_ids.append(result["id"])
        print(f"  Created card: {name!r} (id={result['id']})")

    return card_ids


# ─── Dashboard ────────────────────────────────────────────────────────────────

_GRID_LAYOUT = [
    # (col, row, size_x, size_y)
    (0,  0, 12, 6),   # Sessões por Canal e Outcome
    (0,  6, 12, 6),   # Queue Events
    (0, 12, 12, 6),   # Agent Performance
    (0, 18,  8, 5),   # Usage Metering
    (8, 18, 16, 5),   # Sentiment Timeline
]


def create_dashboard(token: str, card_ids: list[int], collection_id: int | None) -> int:
    """Create main dashboard; returns dashboard id."""
    existing = {d["name"]: d["id"] for d in get("/api/dashboard", token=token)}

    dash_name = "PlugHub Analytics"
    if dash_name in existing:
        dash_id = existing[dash_name]
        print(f"  Dashboard already exists: {dash_name!r} (id={dash_id}) — checking cards…")
    else:
        payload: dict[str, Any] = {
            "name":        dash_name,
            "description": "Visão operacional e de qualidade da plataforma PlugHub",
        }
        if collection_id:
            payload["collection_id"] = collection_id

        dash = post("/api/dashboard", payload, token=token)
        dash_id = dash["id"]
        print(f"  Created dashboard: {dash_name!r} (id={dash_id})")

    # Check existing dashcards to avoid duplicates
    try:
        dash_detail = get(f"/api/dashboard/{dash_id}", token=token)
        existing_card_ids = {dc["card_id"] for dc in dash_detail.get("dashcards", [])}
    except Exception:
        existing_card_ids = set()

    # Build list of cards to add (skip already present)
    cards_to_add = []
    for idx, card_id in enumerate(card_ids):
        if card_id in existing_card_ids:
            print(f"  Card {card_id} already on dashboard — skipping")
            continue
        col, row, sx, sy = _GRID_LAYOUT[idx] if idx < len(_GRID_LAYOUT) else (0, idx * 6, 12, 5)
        cards_to_add.append({
            "id":      -(len(cards_to_add) + 1),   # negative id = new card
            "card_id": card_id,
            "col":     col,
            "row":     row,
            "size_x":  sx,
            "size_y":  sy,
            "parameter_mappings":   [],
            "visualization_settings": {},
            "series": [],
        })

    if cards_to_add:
        # Metabase v0.47+ accepts all cards in a single PUT /api/dashboard/{id}/cards
        # with body {"cards": [...]}. Falls back to individual POST /dashcards on error.
        try:
            put(f"/api/dashboard/{dash_id}/cards", {"cards": cards_to_add}, token=token)
            print(f"  {len(cards_to_add)} cards added via PUT /cards.")
        except RuntimeError as exc:
            print(f"  PUT /cards failed ({exc}) — trying individual POST /dashcards…")
            for c in cards_to_add:
                post(f"/api/dashboard/{dash_id}/dashcards", {
                    "cardId":  c["card_id"],
                    "col":     c["col"],
                    "row":     c["row"],
                    "size_x":  c["size_x"],
                    "size_y":  c["size_y"],
                    "parameter_mappings":   [],
                    "visualization_settings": {},
                }, token=token)
            print(f"  {len(cards_to_add)} cards added via POST /dashcards.")
    else:
        print("  All cards already present on dashboard.")

    return dash_id


# ─── Collection ───────────────────────────────────────────────────────────────

def ensure_collection(token: str) -> int | None:
    """Create 'PlugHub' collection if it doesn't exist; return its id."""
    try:
        cols = get("/api/collection", token=token)
        items = cols if isinstance(cols, list) else cols.get("data", [])
        for col in items:
            if col.get("name") == "PlugHub":
                return col["id"]
        result = post("/api/collection", {"name": "PlugHub", "color": "#509EE3"}, token=token)
        return result["id"]
    except Exception as exc:
        print(f"  Warning: could not create collection: {exc}")
        return None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    wait_for_metabase()

    print("\n── Step 1: Admin setup ──────────────────────────────────────────────")
    token = initial_setup()

    print("\n── Step 2: ClickHouse database connections ──────────────────────────")
    tenant_db_ids = create_ch_databases(token)

    print("\n── Step 3: Collection ───────────────────────────────────────────────")
    collection_id = ensure_collection(token)
    print(f"  Collection id: {collection_id}")

    # Use first tenant's DB for questions (row policies restrict per-connection anyway)
    primary_tenant = TENANTS[0] if TENANTS else None
    if not primary_tenant or primary_tenant not in tenant_db_ids:
        print("No tenant DB available — skipping questions/dashboard.")
        return

    primary_db_id = tenant_db_ids[primary_tenant]

    print("\n── Step 4: Base questions ────────────────────────────────────────────")
    card_ids = create_questions(token, primary_db_id, collection_id)

    print("\n── Step 5: Dashboard ────────────────────────────────────────────────")
    dash_id = create_dashboard(token, card_ids, collection_id)

    print(f"\n✅ Metabase setup complete.")
    print(f"   Dashboard: {METABASE_URL}/dashboard/{dash_id}")
    print(f"   Admin:     {ADMIN_EMAIL}")
    for tenant, db_id in tenant_db_ids.items():
        print(f"   Tenant DB: {tenant} → database id {db_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n❌ Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
