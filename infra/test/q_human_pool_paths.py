#!/usr/bin/env python3
"""
Inventário dos caminhos que produzem uma SEGUNDA passagem pela fila.

Pergunta que ele responde (opção (A), 2026-09-01): o `queue_wait_segment_id` é
`uuid5(tenant, session_id)` — sem discriminador —, logo duas esperas na mesma
sessão colapsam numa linha. O caso não existe na população (medido: 3 saídas de
fila no log, nenhuma sessão repetida). Para produzi-lo é preciso saber POR ONDE.

Dois caminhos possíveis, e eles decidem coisas DIFERENTES:

  · transferência A → B  ⇒ duas esperas em pools DISTINTOS.
    Discriminar por pool bastaria. Precisa de 2 pools humanos.

  · devolução à fila (release / re-enqueue) ⇒ duas esperas no MESMO pool.
    Discriminar por pool NÃO basta — só o carimbo de passagem
    (`first_queued_ms`) separa. Precisa de 1 pool humano só.

⇒ O probe não é só reconhecimento: se o 2º caminho existir neste ambiente, ele
  é o experimento DECISIVO entre as duas propostas de conserto, e mais barato.

Uso (no host, fora do container):
    python3 infra/test/q_human_pool_paths.py
Env:
    TENANT (default tenant_demo) · REGISTRY (3300)
"""
import json
import os
import urllib.error
import urllib.request

TENANT = os.environ.get("TENANT", "tenant_demo")
REGISTRY = os.environ.get("REGISTRY", "http://localhost:3300")

# Campos que decidem o caminho. `dispatch_mode` separa push (drain roteia) de
# pull (agente reivindica) — e o ponto de saída da fila é DIFERENTE nos dois.
_FIELDS = ("agent_kind", "dispatch_mode", "channel_types", "mentionable_pools",
           "max_concurrent", "queue_config")


def fetch(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def unwrap(payload):
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

    humans, unknown = [], []
    for pool in pools:
        pid = pool.get("pool_id") or pool.get("id") or "?"
        kind = pool.get("agent_kind")
        if kind == "human":
            humans.append((pid, pool))
        elif not kind:
            # `agent_kind` ausente cai no balde `unknown` do rollup de capacidade
            # (CLAUDE.md § Operational Visibility) — aqui vira INCONCLUSIVO, não
            # "não é humano": um pool humano sem o campo sumiria em silêncio.
            unknown.append(pid)

    print(f"== pools no tenant {TENANT}: {len(pools)}")
    print(f"-- agent_kind=human: {len(humans)}")
    for pid, pool in sorted(humans):
        print(f"     {pid}")
        for field in _FIELDS:
            if field in pool:
                print(f"        {field} = {json.dumps(pool[field], sort_keys=True)}")
    print(f"-- agent_kind AUSENTE (inconclusivo, não 'não-humano'): {len(unknown)}")
    for pid in sorted(unknown):
        print(f"     {pid}")

    # Testemunha de presença ao lado do contador de ausência: se `human` vier 0
    # E `unknown` vier 0, o campo é confiável e o ambiente não tem pool humano.
    # Se `human` vier 0 e `unknown` > 0, a leitura não conclui nada.
    print("== veredicto do caminho")
    if len(humans) >= 2:
        print("   transferência A→B: POSSÍVEL (2+ pools humanos)")
    else:
        print(f"   transferência A→B: precisa de 2 pools humanos, há {len(humans)}")
    if len(humans) >= 1:
        print("   devolução à fila no MESMO pool: candidato — confirmar se o pool")
        print("   tem ponto de saída que RE-ENFILEIRA (release/return to queue)")


if __name__ == "__main__":
    main()
