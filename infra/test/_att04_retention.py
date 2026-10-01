"""
_att04_retention.py — corpo do `probe_att04_attachment_retention_class.sh` (ATT-04). Roda DENTRO do
channel-gateway: usa o `config_api_url` e o leitor de verdade (`resolve_attachment_expiry_days`).

MODE=read   → C1 · C2 · R1, imprime JSON {c1, c2, r1, seeded, resolved}
A escrita mora em `_config_put.py` (ATT-07): a que morava aqui apagava a descrição da chave.
"""
import asyncio
import json
import os

import httpx

from plughub_channel_gateway.attachment_store import resolve_attachment_expiry_days
from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.webchat_config import retention_config


def _valor(entry):
    return entry.get("value") if isinstance(entry, dict) and "value" in entry else entry


async def main() -> None:
    s = get_settings()
    base = s.config_api_url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as c:
        ret = (await c.get(f"{base}/config/retention", params={"tenant_id": s.tenant_id})).json()
        web = (await c.get(f"{base}/config/webchat", params={"tenant_id": s.tenant_id})).json()
    ret_e = ret.get("entries") or ret
    web_e = web.get("entries") or web
    seeded = _valor(ret_e.get("attachment_days")) if "attachment_days" in ret_e else None
    await retention_config.reload(s.config_api_url, s.tenant_id)
    resolved = await resolve_attachment_expiry_days(None, s.tenant_id, -1)
    print(json.dumps({
        "c1": seeded is not None,
        "c2": "attachment_expiry_days" not in web_e,
        "r1": seeded is not None and resolved == int(seeded),
        "seeded": seeded, "resolved": resolved,
    }))


asyncio.run(main())
