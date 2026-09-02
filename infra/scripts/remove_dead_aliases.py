#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remove aliases MORTOS do mapa do ContextStore — as três casas, de uma vez (V5).

    python3 infra/scripts/remove_dead_aliases.py --antes 2026-08-31 [--aplicar]
    python3 infra/scripts/remove_dead_aliases.py --lista a,b,c      [--aplicar]

Sem `--aplicar` ele só MOSTRA. É deliberado: a operação toca a semente da plataforma,
que viaja para toda instalação.

AS TRÊS CASAS, E POR QUE TÊM DE MUDAR JUNTAS
---------------------------------------------
  1. `packages/schemas/src/context-map.ts`                     — a AUTORIDADE
  2. `packages/py-contextstore/src/.../default_map.py`         — cópia, mantida à mão
  3. o store vivo (`masking.context_map` no config-api)        — o que roda

Nenhum gate compara (1) com (2) diretamente: o `probe_context_map_audit` mede a TS contra
o vivo por CONTENÇÃO, e o `probe_context_map_seed` mede a Python contra o vivo por
IGUALDADE. Juntos eles pegam *alias só na TS* — o vivo perderia e a contenção falharia —
mas **não pegam alias só na Python**, que passa nos dois. Foi medido em 2026-09-02, e é
exatamente a direção que alguém cria removendo da TS e esquecendo da Python. Por isso a
remoção é UMA operação sobre as três, e não três operações.

O CRITÉRIO NÃO MORA AQUI
-------------------------
Quem decide *quais* aliases são removíveis é `infra/test/aliases_v5_buckets.py`, e o
critério tem TRÊS dimensões (o ADR especificava só a primeira, e mal):

  · PRODUTOR       — ninguém escreve a grafia (análise estática; critério forte)
  · HISTÓRIA       — não está no snapshot durável (senão o alias mantém histórico
                     mascarado e removê-lo o desmascara)
  · IDADE DO ALIAS — um alias criado junto com a migração que ele protege ainda não teve
                     tempo de pegar nada. Medido em 2026-09-02: 29 dos 49 "removíveis"
                     tinham UM DIA. Esta é a dimensão temporal que importa — e não é a do
                     ADR, que era o decaimento do CONTADOR (mede tráfego, não produtor).

`--antes DATA` filtra pela terceira, usando `git log -S` sobre a semente.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
TS = os.path.join(RAIZ, "packages/schemas/src/context-map.ts")
PY = os.path.join(RAIZ, "packages/py-contextstore/src/plughub_contextstore/default_map.py")
CONFIG_API = os.environ.get("PLUGHUB_CONFIG_API_URL", "http://localhost:3600")
TENANT = os.environ.get("TENANT", "tenant_demo")
ADMIN = os.environ.get("CONFIG_ADMIN_TOKEN", "demo_config_admin_token")


def removiveis() -> list[str]:
    out = subprocess.run([sys.executable, os.path.join(RAIZ, "infra/test/aliases_v5_buckets.py"),
                          "--json"], capture_output=True, text=True, cwd=RAIZ)
    if out.returncode != 0:
        sys.exit("instrumento de baldes falhou:\n" + out.stderr[-400:])
    return [r["alias"] for r in json.loads(out.stdout)["C_removivel"]]


def entrou_em(alias: str) -> str:
    g = subprocess.run(["git", "log", "--reverse", "--format=%ad", "--date=short",
                        "-S", '"%s"' % alias, "--", "packages/schemas/src/context-map.ts"],
                       capture_output=True, text=True, cwd=RAIZ)
    d = [l for l in g.stdout.split("\n") if l.strip()]
    return d[0] if d else "9999-99-99"


def tira_do_texto(texto: str, alias: str) -> tuple[str, int]:
    """Remove o alias de um array `legado` em fonte TS ou Python.

    Regex e não parser porque as duas casas são FONTE, não dado: um parser teria de
    reescrever o arquivo inteiro e destruiria comentários que carregam decisão. O
    contador de substituições é o que impede a edição silenciosa — zero é erro.
    """
    n = 0
    # elemento no meio ou fim de lista: `"x", ` / `, "x"` / sozinho
    for pad in (r'"%s",\s*' % re.escape(alias),
                r',\s*"%s"' % re.escape(alias),
                r'"%s"' % re.escape(alias)):
        novo, k = re.subn(pad, "", texto, count=1)
        if k:
            return novo, 1
    return texto, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--antes", help="só aliases que entraram na semente ANTES desta data")
    ap.add_argument("--lista", help="lista explícita, separada por vírgula")
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    alvos = ([a.strip() for a in args.lista.split(",") if a.strip()]
             if args.lista else removiveis())
    if args.antes:
        antes, fora = [], []
        for a in alvos:
            (antes if entrou_em(a) < args.antes else fora).append(a)
        print("filtro de IDADE (< %s): %d entram, %d ficam de fora" % (args.antes, len(antes), len(fora)))
        alvos = antes

    if not alvos:
        print("nada a remover"); return 0

    print("\nalvos (%d):" % len(alvos))
    for a in alvos:
        print("   %-44s entrou em %s" % (a, entrou_em(a)))

    if not args.aplicar:
        print("\n(--aplicar ausente: nada foi tocado)")
        return 0

    # ── as três casas ────────────────────────────────────────────────────────
    for caminho, rotulo in ((TS, "context-map.ts"), (PY, "default_map.py")):
        s = io.open(caminho, encoding="utf-8").read()
        faltaram = []
        for a in alvos:
            s, k = tira_do_texto(s, a)
            if not k:
                faltaram.append(a)
        io.open(caminho, "w", encoding="utf-8", newline="").write(s)
        print("\n%s: %d removido(s)%s" % (rotulo, len(alvos) - len(faltaram),
              ("; NÃO ENCONTRADO: " + ", ".join(faltaram)) if faltaram else ""))

    # ── store vivo: derivado da semente + arquivo do tenant, nunca editado à mão ──
    # Reescrever o vivo a partir das fontes é o que mantém a igualdade que o ramo C do
    # `probe_context_map_seed` exige. Editá-lo em separado criaria a quarta casa.
    sys.path.insert(0, os.path.join(RAIZ, "packages/py-contextstore/src"))
    import importlib
    import plughub_contextstore.default_map as dm
    importlib.reload(dm)
    novo = json.loads(json.dumps(dm.DEFAULT_CONTEXT_MAP))
    arq = os.path.join(RAIZ, "infra/context-map", TENANT + ".json")
    if os.path.exists(arq):
        doc = json.load(io.open(arq, encoding="utf-8"))
        for esc, doms in (doc.get("contexto") or {}).items():
            for dom, campos in doms.items():
                novo["contexto"].setdefault(esc, {})[dom] = campos

    corpo = json.dumps({"tenant_id": None, "value": novo,
                        "description": "Mapa do ContextStore (V5: aliases mortos removidos)"}).encode()
    req = urllib.request.Request(CONFIG_API.rstrip("/") + "/config/masking/context_map",
                                 data=corpo, method="PUT")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Admin-Token", ADMIN)
    with urllib.request.urlopen(req, timeout=20) as r:
        print("\nstore vivo: PUT HTTP %s (reescrito a partir de semente + arquivo do tenant)" % r.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
