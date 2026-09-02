"""
Publica UM skill do arquivo e re-snapshota o slot `current` de UM pool.

Por que existe, em vez de `REGISTRY_SYNC_RECONCILE=true`: o reconcile reaplica o YAML
sobre **skills, pools E deploy-slots** do tenant inteiro. Neste ambiente isso
destruiria config DB-owned deliberada (ex.: `retencao_humano.queue_config.pool_id`,
que só existe no DB e que a passagem manda NÃO reverter). O martelo certo para
"quero este arquivo em produção" é o cirúrgico.

E são DOIS passos, não um — confundi-los custa um ciclo de diagnóstico:
  · publicar  → escreve `skill.flow` (o que o editor mostra)
  · promover  → re-snapshota `PoolSkillSlot.current` (o que o BRIDGE executa)
Publicar sem promover deixa o skill certo e o pool rodando o snapshot velho.

Roda DENTRO do orchestrator-bridge: é lá que `/skills` está montado, que o
`AGENT_REGISTRY_SERVICE_TOKEN` existe e que o payload é montado do mesmo jeito que o
RegistrySyncer monta (`registry_syncer.py:663-705`) — reimplementar a montagem noutro
lugar criaria uma segunda verdade sobre o formato.

Uso:  python3 /tmp/_publish_skill.py <skill_file.yaml> <pool_id> [tenant_id]
      python3 /tmp/_publish_skill.py <skill_file.yaml> -          [tenant_id]
Saída: uma linha por passo, com o HTTP. Código 0 = os dois passos OK.

`pool_id` = `-` publica e **não promove**, para skill que nenhum pool deploya. É um
estado legítimo (há skills de fixture sem deploy), e o alternativo é pior: passar um
pool qualquer promoveria naquele pool um skill que ele não roda. A saída DIZ que
pulou, porque *"publiquei e nada mudou em produção"* é justamente a confusão que o
cabeçalho acima existe para evitar.
"""
import json
import os
import sys
import urllib.error
import urllib.request

import yaml

SKILLS_DIR = os.environ.get("SKILLS_DIR", "/skills")
REGISTRY   = os.environ.get("AGENT_REGISTRY_URL", "http://agent-registry:3300")
SVC_TOKEN  = os.environ.get("AGENT_REGISTRY_SERVICE_TOKEN", "")


def call(method: str, path: str, headers: dict, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(REGISTRY + path, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    filename = sys.argv[1]
    pool_id  = sys.argv[2]
    tenant   = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("PLUGHUB_TENANT_ID", "tenant_demo")

    raw = yaml.safe_load(open(os.path.join(SKILLS_DIR, filename)).read())
    skill_id = raw["id"]

    headers = {"x-tenant-id": tenant, "x-user-id": "publish-skill-script"}
    if SVC_TOKEN:
        headers["x-service-token"] = SVC_TOKEN

    # ── 1. publicar ───────────────────────────────────────────────────────────
    # Mesmo payload do RegistrySyncer. `x-skill-publish: true` grava
    # {flow: <yaml>, flow_draft: DbNull} — publica direto em produção.
    flow = {"entry": raw["entry"], "steps": raw["steps"]}
    if raw.get("required_context"):
        flow["required_context"] = raw["required_context"]
    if raw.get("mention_commands"):
        flow["mention_commands"] = raw["mention_commands"]
    payload = {
        "skill_id":       skill_id,
        "name":           raw.get("name", skill_id),
        "version":        raw.get("version", "1.0"),
        "description":    (raw.get("description") or raw.get("name") or skill_id).strip(),
        "classification": raw.get("classification", {"type": "orchestrator"}),
        "flow":           flow,
    }
    # `agent_role` saiu da lista em 2026-09-01 (CAP-03): o PUT agora o RECUSA com
    # 422, então repassá-lo faria toda publicação de skill falhar.
    for opt in ("delegation_input", "config_params"):
        if raw.get(opt):
            payload[opt] = raw[opt]

    st, body = call("PUT", f"/v1/skills/{skill_id}",
                    {**headers, "x-skill-publish": "true"}, payload)
    print(f"PUBLISH {skill_id} HTTP {st} {body[:160]}", flush=True)
    if st not in (200, 201):
        return 1

    if pool_id == "-":
        print("PROMOTE pulado — nenhum pool deploya este skill (pool_id='-'). "
              "`skill.flow` atualizado; nenhum slot `current` foi re-snapshotado.",
              flush=True)
        return 0

    # ── 2. promover ───────────────────────────────────────────────────────────
    # `config_json` é PRESERVADO do slot corrente. Mandar `{}` apagaria config de
    # deploy (channel_policy, form_id, max_concurrent_sessions) sem aviso — o slot
    # não faz merge, ele grava o que recebe.
    st, body = call("GET", f"/v1/pools/{pool_id}/slots", headers)
    if st != 200:
        print(f"SLOTS_READ HTTP {st} {body[:160]}", flush=True)
        return 1
    cur = ((json.loads(body).get("slots") or {}).get("current") or {})
    cfg = cur.get("config_json") or {}
    print(f"SLOT_CURRENT skill={cur.get('skill_id')} config_keys={sorted(cfg)}", flush=True)

    st, body = call("PUT", f"/v1/pools/{pool_id}/slots/next", headers,
                    {"skill_id": skill_id, "config_json": cfg})
    print(f"SET_NEXT {pool_id} HTTP {st} {body[:160]}", flush=True)
    if st not in (200, 201):
        return 1

    st, body = call("POST", f"/v1/pools/{pool_id}/promote", headers, {})
    print(f"PROMOTE {pool_id} HTTP {st} {body[:160]}", flush=True)
    return 0 if st in (200, 201) else 1


if __name__ == "__main__":
    raise SystemExit(main())
