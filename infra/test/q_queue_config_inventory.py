#!/usr/bin/env python3
"""
Inventário do endereçamento de fila — probe de MEDIÇÃO, sem efeito colateral.

Pergunta que ele responde (defeito 2, TODO § "O tenant default de fila é
suprimido pelo `skill_id` legado"):

  Desde 2026-07-13 `resolve_flow_for_agent` resolve produção **só** pelo slot
  `current` do POOL. Logo, dentro de `queue_config`, apenas `pool_id` é um
  ENDEREÇO; `skill_id` e `agent_type_id` não resolvem flow nenhum. O probe conta
  quantos pools estão em cada balde e diz se o default de tenant existe.

  Baldes (mutuamente exclusivos):
    none            — sem `queue_config` (cai no default de tenant)
    addressable     — tem `pool_id`
    legacy_only     — tem `skill_id`/`agent_type_id` e NÃO tem `pool_id`
                      ⇒ suprime o default de tenant E não resolve nada
    empty_object    — objeto presente e vazio

  Testemunha obrigatória: `addressable` é o contador de PRESENÇA ao lado do
  contador de ausência. Se ele vier 0, "ninguém usa o endereço que funciona" é
  medição; sem ele, `legacy_only > 0` sozinho não diz se o modelo novo é usado.

Uso (no host, fora do container):
    python3 infra/test/q_queue_config_inventory.py
Env:
    TENANT (default tenant_demo) · REGISTRY (3300) · CONFIG_API (3600)
"""
import json
import os
import urllib.error
import urllib.request

TENANT = os.environ.get("TENANT", "tenant_demo")
REGISTRY = os.environ.get("REGISTRY", "http://localhost:3300")
CONFIG_API = os.environ.get("CONFIG_API", "http://localhost:3600")


def fetch(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def unwrap(payload):
    """`GET /v1/pools` devolve ENVELOPE, não lista."""
    if isinstance(payload, list):
        return payload
    for key in ("pools", "data", "items"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise SystemExit(f"INCONCLUSIVO: envelope desconhecido, chaves={list(payload)}")


def main() -> None:
    try:
        pools = unwrap(fetch(f"{REGISTRY}/v1/pools", {"x-tenant-id": TENANT}))
    except urllib.error.URLError as exc:
        raise SystemExit(f"INCONCLUSIVO: agent-registry inalcançável ({exc})")

    buckets: dict[str, list] = {
        "none": [], "addressable": [], "legacy_only": [], "empty_object": [],
    }
    for pool in pools:
        pid = pool.get("pool_id") or pool.get("id") or "?"
        cfg = pool.get("queue_config")
        if cfg is None:
            buckets["none"].append((pid, ""))
        elif not isinstance(cfg, dict) or not cfg:
            buckets["empty_object"].append((pid, repr(cfg)))
        elif (cfg.get("pool_id") or "").strip():
            buckets["addressable"].append((pid, json.dumps(cfg, sort_keys=True)))
        else:
            buckets["legacy_only"].append((pid, json.dumps(cfg, sort_keys=True)))

    print(f"== pools no tenant {TENANT}: {len(pools)}")
    for name in ("none", "empty_object", "legacy_only", "addressable"):
        rows = buckets[name]
        print(f"-- {name}: {len(rows)}")
        for pid, detail in rows:
            if name in ("legacy_only", "addressable", "empty_object"):
                print(f"     {pid}  {detail}")

    print("== tenant default (config-api, namespace session)")
    for key in ("queue_default_agent_type_id", "queue_default_skill_id"):
        try:
            val = fetch(f"{CONFIG_API}/config/session/{key}?tenant_id={TENANT}")["value"]
            print(f"   {key} = {val!r}")
        except urllib.error.HTTPError as exc:
            print(f"   {key} = AUSENTE (HTTP {exc.code})")
        except urllib.error.URLError as exc:
            print(f"   {key} = INCONCLUSIVO (config-api inalcançável: {exc})")

    # `GET /v1/pools` já anexa `deployed_skill_id` quando existe slot `current`
    # com skill (pools.ts:167-176) — não é preciso um GET por pool.
    with_slot = [
        f"{p.get('pool_id')}:{p['deployed_skill_id']}"
        for p in pools if p.get("deployed_skill_id")
    ]
    print(f"== pools com slot `current` (quem PODE executar um flow): {len(with_slot)}")
    for row in sorted(with_slot):
        print(f"     {row}")

    # A pergunta que decide o conserto: o pool onde o contato ESPERA tem slot?
    # Enquanto `_flow_pool_id = queue_pool_id or pool_id`, é esse slot que o
    # bridge procura — e pool humano não tem nenhum, por construção.
    print("== legacy_only × slot no próprio pool de destino")
    deployed = {p.get("pool_id") for p in pools if p.get("deployed_skill_id")}
    for pid, _ in buckets["legacy_only"]:
        print(f"     {pid}: slot no destino = {'SIM' if pid in deployed else 'NÃO'}")


if __name__ == "__main__":
    main()
