#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Baldes da V5 — quais aliases do ContextStore podem SAIR do mapa.

    python3 infra/test/aliases_v5_buckets.py [--json]

⚠️ **Isto é um INSTRUMENTO, não um gate.** Não tem veredicto: ele produz a lista e os
números que autorizam a remoção. O que reprova é o conjunto de gates do mapa.

O CRITÉRIO MUDOU EM 2026-09-02, E A MUDANÇA É MEDIÇÃO
------------------------------------------------------
O ADR especificava *"fechamento dos aliases cujo contador zerou"* por N dias. Medido: o
contador da auditoria mede **TRÁFEGO**, não produtor. Os 17 aliases observados no dia
eram as grafias legadas que os skills de DEMO escrevem — eles não decaem esperando,
reaparecem a cada execução. Um zero ali seria indistinguível de *"ninguém rodou o demo"*,
que é a mesma família do teste que não pode reprovar.

É o mesmo movimento que a D9.2 já tinha feito na V4: sai OBSERVAÇÃO (rodar tráfego até
secar), entra ENUMERAÇÃO. O contador **fica** — como rede para as superfícies que a
análise estática não alcança (corpo HTTP de webhook, chamador externo).

OS DOIS CRITÉRIOS, E POR QUE SÃO DOIS
--------------------------------------
  (1) PRODUTOR — algum artefato ou código de plataforma escreve a grafia legada?
      Fonte: o censo (`extrair_de_doc` + metade plataforma), cujo gêmeo TS é gateado
      por `probe_context_tag_extractor_parity.sh`. É o critério FORTE.

  (2) HISTÓRIA DURÁVEL — a grafia velha existe em `session_context_snapshot`?
      Se existe, o alias **FICA**: é ele que mantém aquele histórico MASCARADO, e
      removê-lo desmascararia dado já gravado. É a regra do `CLAUDE.md`
      (*"as canônicas antigas viraram legado, e ficam"*), e é o critério de SEGURANÇA.

⚠️ **A força da evidência de ausência é limitada e está medida.** O snapshot durável
nasceu com a F5 e tem poucos dias de história; *"não está lá"* sozinho seria fraco. O que
sustenta o balde C é o critério (1); o (2) é a rede.

⚠️ **O que remover errado custa hoje é MENOS do que custava quando o ADR foi escrito.**
Antes da V4 uma grafia órfã degradava em silêncio. Hoje ela é carimbada `unknown`,
contada pela auditoria, LOGADA pelo funil (F1) e — se vier de skill — RECUSADA no publish
(F3). Três instrumentos que não existiam.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))          # infra/test
RAIZ = os.path.dirname(os.path.dirname(AQUI))              # a raiz do repo
sys.path.insert(0, os.path.join(RAIZ, "packages/py-contextstore/src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plughub_contextstore import build_context_tag_index          # noqa: E402
from plughub_contextstore.default_map import DEFAULT_CONTEXT_MAP  # noqa: E402

CONFIG_API = os.environ.get("PLUGHUB_CONFIG_API_URL", "http://localhost:3600")
TENANT = os.environ.get("TENANT", "tenant_demo")
ARQ_TENANT = os.path.join(RAIZ, "infra/context-map", TENANT + ".json")


def mapa_vivo() -> dict:
    u = "%s/config/masking/context_map?tenant_id=%s" % (CONFIG_API.rstrip("/"), TENANT)
    with urllib.request.urlopen(u, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))["value"]


def produtores() -> set[str]:
    """Grafias que ALGUÉM escreve — as duas metades do censo, tenant e plataforma."""
    out = subprocess.run(
        [sys.executable, os.path.join(RAIZ, "infra/test/censo_contextstore_cadastro.py"), "--json"],
        capture_output=True, text=True, cwd=RAIZ)
    if out.returncode != 0:
        sys.exit("censo falhou (%s): %s" % (out.returncode, out.stderr[-300:]))
    return set(json.loads(out.stdout)["escritos"])


def duraveis() -> tuple[set[str], str]:
    """Grafias presentes no snapshot DURÁVEL, e a janela que ele cobre."""
    sql = ("SELECT string_agg(DISTINCT k, chr(10)) FROM session_context_snapshot, "
           "jsonb_object_keys(entries) AS k")
    janela = ("SELECT min(captured_at)::text || ' .. ' || max(captured_at)::text || "
              "' (n=' || count(*) || ')' FROM session_context_snapshot")
    base = ["docker", "compose", "-f", "docker-compose.demo.yml", "exec", "-T", "postgres",
            "psql", "-U", "plughub", "-d", "plughub_demo", "-tAc"]
    r1 = subprocess.run(base + [sql], capture_output=True, text=True, cwd=RAIZ)
    r2 = subprocess.run(base + [janela], capture_output=True, text=True, cwd=RAIZ)
    if r1.returncode != 0:
        # Ausência de banco NÃO pode virar "balde C maior" em silêncio: sem o critério de
        # segurança o instrumento não tem o que dizer sobre remoção.
        sys.exit("snapshot durável ilegível — sem o critério (2) não há balde C:\n  %s"
                 % r1.stderr.strip()[:300])
    return {l.strip() for l in r1.stdout.split("\n") if l.strip()}, r2.stdout.strip()


def onde_declarado(alias: str) -> str:
    """Semente da PLATAFORMA, arquivo do TENANT, ou só no store vivo."""
    i = build_context_tag_index(DEFAULT_CONTEXT_MAP)
    if alias in i.alias:
        return "semente"
    if os.path.exists(ARQ_TENANT):
        doc = json.load(io.open(ARQ_TENANT, encoding="utf-8"))
        for doms in (doc.get("contexto") or {}).values():
            for campos in doms.values():
                for folha in campos.values():
                    leg = folha.get("legado") or []
                    if alias in ([leg] if isinstance(leg, str) else leg):
                        return "tenant"
    return "só-no-vivo"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    idx = build_context_tag_index(mapa_vivo())
    prod = produtores()
    dur, janela = duraveis()

    A, B, C = [], [], []
    for a, canon in sorted(idx.alias.items()):
        reg = {"alias": a, "canonica": canon, "onde": onde_declarado(a)}
        if a in prod:
            A.append(reg)
        elif a in dur:
            B.append(reg)
        else:
            C.append(reg)

    if args.json:
        json.dump({"janela_duravel": janela, "produtores": len(prod), "duraveis": len(dur),
                   "A_tem_produtor": A, "B_historia_duravel": B, "C_removivel": C},
                  sys.stdout, ensure_ascii=False, indent=1)
        return 0

    print("=" * 84)
    print("V5 — %d aliases do mapa vivo de %s" % (len(idx.alias), TENANT))
    print("=" * 84)
    print("  janela do snapshot durável: %s" % janela)
    print("  produtores conhecidos     : %d grafias" % len(prod))
    print("  grafias no durável        : %d" % len(dur))

    for titulo, balde, nota in (
        ("A. TEM PRODUTOR — fica; migrar o produtor é trabalho à parte", A, ""),
        ("B. sem produtor, MAS na história durável — FICA POR REGRA", B,
         "removê-lo desmascararia histórico já gravado"),
        ("C. sem produtor e sem história — REMOVÍVEL", C,
         "é este o balde que a V5 fecha"),
    ):
        print()
        print("-" * 84)
        print("%s: %d" % (titulo, len(balde)))
        if nota:
            print("   (%s)" % nota)
        print("-" * 84)
        for r in balde:
            print("   %-42s %-10s -> %s" % (r["alias"][:42], r["onde"], r["canonica"]))

    print()
    print("=" * 84)
    print("  A=%d  B=%d  C=%d   (soma %d)" % (len(A), len(B), len(C), len(A) + len(B) + len(C)))
    por_onde: dict[str, int] = {}
    for r in C:
        por_onde[r["onde"]] = por_onde.get(r["onde"], 0) + 1
    print("  o balde C por origem: %s" % (por_onde or "vazio"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
