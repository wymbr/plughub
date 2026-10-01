"""
_cfg01_description.py — corpo do `probe_cfg01_description_kept.sh` (CFG-01). Roda DENTRO do
channel-gateway (alcança o config-api e o banco em que ele grava). Escreve pela API numa chave
de rascunho GLOBAL (`probe_cfg01.scratch`), lê a descrição no banco (nenhuma rota a devolve) e
apaga a chave no fim.

  K1 PUT com descrição VAZIA mantém a gravada (o que a tela manda em toda gravação)
  V1 ...e o VALOR muda (o PUT não virou no-op)
  C1 CONTROLE: PUT com descrição nova a troca (descrição continua editável)
  Z1 CENSO: quantas chaves seguem sem descrição (informativo)

ADMIN = X-Admin-Token. Saída: 0 verde · 1 alguma falha · 2 não mediu.
"""
import asyncio
import json
import os
import sys

import asyncpg
import httpx

from plughub_channel_gateway.config import get_settings

NS, KEY = "probe_cfg01", "scratch"


async def main() -> int:
    s = get_settings()
    base = s.config_api_url.rstrip("/")
    hdr = {"X-Admin-Token": os.environ["ADMIN"]}
    falhas = []

    def ok(ramo, cond, txt):
        print(f"  {'✓' if cond else '✗'} {ramo} {txt}", flush=True)
        if not cond:
            falhas.append(ramo)

    conn = await asyncpg.connect(s.database_url)

    async def linha():
        r = await conn.fetchrow("SELECT value, description FROM public.platform_config "
                                "WHERE tenant_id = '__global__' AND namespace = $1 AND key = $2", NS, KEY)
        return (json.loads(r["value"]), r["description"]) if r else (None, None)

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            async def put(valor, desc):
                r = await c.put(f"{base}/config/{NS}/{KEY}", headers=hdr,
                                json={"value": valor, "tenant_id": None, "description": desc})
                return r.status_code
            try:
                if await put(1, "descricao original") != 200:
                    print("  INCONCL o PUT inicial não gravou")
                    return 2
                _, d0 = await linha()
                if d0 != "descricao original":
                    print(f"  INCONCL a linha inicial não tem a descrição ({d0!r})")
                    return 2
                st = await put(2, "")
                v, d = await linha()
                ok("K1", st == 200 and d == "descricao original", f"PUT com descrição vazia → descrição {d!r}")
                ok("V1", v == 2, f"...e o valor mudou para {v}")
                await put(3, "descricao nova")
                _, d = await linha()
                ok("C1", d == "descricao nova", f"PUT com descrição nova → {d!r}")
            finally:
                await c.delete(f"{base}/config/{NS}/{KEY}", headers=hdr)
        vazias = await conn.fetch("SELECT namespace || '.' || key || ' (' || tenant_id || ')' AS k "
                                  "FROM public.platform_config WHERE description = '' ORDER BY 1")
        total = await conn.fetchval("SELECT count(*) FROM public.platform_config")
        print(f"  · Z1 chaves sem descrição: {len(vazias)} de {total} {[r['k'] for r in vazias]}")
        sobrou = await conn.fetchval("SELECT count(*) FROM public.platform_config WHERE namespace = $1", NS)
        ok("L1", sobrou == 0, f"rascunho apagado (linhas restantes: {sobrou})")
    finally:
        await conn.close()
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
