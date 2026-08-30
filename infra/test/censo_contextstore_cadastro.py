#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Censo de cadastro do ContextStore — a lista da D9 por ANÁLISE ESTÁTICA.

    python3 infra/test/censo_contextstore_cadastro.py [--json]

⚠️ **Isto é um INSTRUMENTO, não um gate.** Não tem veredicto: não há proposição
que ele possa reprovar. Ele produz a LISTA e os números que dimensionam a
migração da D9 (`docs/adr/adr-contextstore-allowlist.md`). O gate — se a D9 for
adiante — é o portão de publish, e ele consumirá este mesmo extrator.

Por que o extrator NÃO é um walker de YAML
------------------------------------------
Medido em 2026-08-30: são **SEIS** superfícies de escrita, e quatro não aparecem
caminhando a árvore do YAML. As três correções abaixo são o TESTE que qualquer
reescrita deste extrator tem de passar — cada uma nasceu de um falso negativo:

  (1) `context_json` é uma **string JSON dentro do YAML** (7 skills, 15 nomes).
      Um walker a vê como texto e segue em frente.
  (2) `invoke tool: context_set` / `context_write` guardam o nome em
      `input.tag` — um campo de step como outro qualquer.
  (3) `delegate.context` / `collect.context` têm o prefixo `session.`
      **composto no gateway** (`webhook.py:1695`, `:2721`), não no arquivo — o
      nome final não existe no YAML.

E há a origem que NENHUMA análise estática alcança: o corpo HTTP do webhook
(`webhook.py:630` escreve cada chave verbatim, sem prefixo). É de lá que vêm as
duas únicas tags sem namespace, `campaign_id` e `target_pool`.

Resultado de referência (2026-08-30, mapa 75 canônicas / 53 aliases):
    91 escritos (61 tenant · 35 plataforma) · 54 cobertos · 37 a cadastrar
    21 lidos sem escritor · 0 dinâmicos

O zero de dinâmicos foi CONFERIDO por mutação (2026-08-30): trocando um
`tag: "approval.summary"` por `"approval.{{$.pipeline_state.x}}"` o contador
vai a 1 e nomeia o sítio; revertendo, volta a 0. Sem essa conferência o zero
seria indistinguível de um detector morto — que é o modo de falha que este
repositório cataloga como "teste que não pode reprovar".
"""
import argparse
import io
import json
import os
import re
import sys
from collections import defaultdict

try:
    import yaml
except ImportError:                                        # pragma: no cover
    sys.exit("PyYAML necessário: pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SKILLS = os.path.join(ROOT, "packages/skill-flow-engine/skills")
MAPTS = os.path.join(ROOT, "packages/schemas/src/context-map.ts")

# Arquivos de PLATAFORMA que escrevem no hash de contexto. Lista explícita e não
# varredura: `hset` sobre outros hashes (instância, resume_tokens, admissão) não
# é escrita de ContextStore, e incluí-los inflaria o número em silêncio.
ESCRITORES = [
    "packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py",
    "packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py",
    "packages/routing-engine/src/plughub_routing/main.py",
    "packages/ai-gateway/src/plughub_ai_gateway/sentiment_emitter.py",
    "packages/ai-gateway/src/plughub_ai_gateway/copilot_emitter.py",
    "packages/evaluation-api/src/plughub_evaluation_api/router.py",
    "packages/mcp-server-plughub/src/tools/bpm.ts",
    "packages/mcp-server-plughub/src/tools/session.ts",
    "packages/mcp-server-plughub/src/server.ts",
]

LIT = re.compile(r"""["']((?:session|journey|caller|account|insight|approval)\.[a-zA-Z0-9_.]+)["']""")
JSONKEY = re.compile(r'"([a-zA-Z0-9_.]+)"\s*:')
# Marcas de ESCRITA na vizinhança do literal. Sem elas o mesmo literal conta como
# leitura — o arquivo escreve E lê os mesmos nomes.
MARCA_W = re.compile(r"ctx_writes\[|mapping\[|hset\(|\bmapping\b\s*[:=]|_write_ctx\(|writeContextTag")


# ── mapa vigente ─────────────────────────────────────────────────────────────
def ler_mapa():
    src = io.open(MAPTS, encoding="utf-8").read()
    bloco = src[src.index("export const DEFAULT_CONTEXT_MAP"):]
    aliases, canonicas = {}, {}
    esc = dom = None
    for ln in bloco.split("\n"):
        s = ln.strip()
        if re.match(r"^(session|journey|customer):\s*\{", s):
            esc, dom = s.split(":")[0], None
            continue
        m = re.match(r"^([a-z_]+):\s*\{\s*$", s)
        if m and esc and "tipo:" not in ln:
            dom = m.group(1)
            continue
        m = re.match(r"^([a-z_0-9]+):\s*\{.*tipo:\s*\"([a-z_]+)\"", s)
        if m and esc and dom:
            canon = "%s.%s.%s" % (esc, dom, m.group(1))
            canonicas[canon] = m.group(2)
            if "legado:" in s:
                for a in re.findall(r"\"([a-zA-Z0-9_.]+)\"", s.split("legado:")[1]):
                    aliases[a] = canon
    return canonicas, aliases


# ── metade TENANT: as cinco superfícies de autoria ───────────────────────────
def censo_tenant():
    escritas, leituras, dinamicas = defaultdict(set), defaultdict(set), []

    def anda(node, arq):
        if isinstance(node, dict):
            ct = node.get("context_tags")
            if isinstance(ct, dict):                       # (a) anotação
                for lado in ("outputs", "inputs"):
                    b = ct.get(lado)
                    if isinstance(b, dict):
                        for _d, ent in b.items():
                            tag = ent.get("tag") if isinstance(ent, dict) else ent
                            if not isinstance(tag, str):
                                dinamicas.append((arq, "context_tags." + lado, repr(ent)[:60]))
                                continue
                            escopo = ent.get("scope") if isinstance(ent, dict) else None
                            nome = ("segment.{segId}." + tag) if escopo == "segment" else tag
                            alvo = escritas if lado == "outputs" else leituras
                            alvo[nome].add((arq, "context_tags." + lado))
            tipo = node.get("type")
            if tipo in ("delegate", "collect") and isinstance(node.get("context"), dict):
                for k in node["context"]:                  # (b) prefixo composto no gateway
                    if not isinstance(k, str):
                        dinamicas.append((arq, tipo + ".context", repr(k)[:60]))
                        continue
                    escritas[k if k.startswith("session.") else "session." + k].add(
                        (arq, tipo + ".context"))
            sc = node.get("set_context")
            if isinstance(sc, dict):                       # (c) mention_commands
                for k in sc:
                    if isinstance(k, str):
                        escritas[k].add((arq, "mention.set_context"))
            inp = node.get("input") if isinstance(node.get("input"), dict) else {}
            cj = inp.get("context_json")
            if isinstance(cj, str):                        # (d) string JSON
                for k in JSONKEY.findall(cj):
                    escritas[k].add((arq, "context_json (string)"))
            if node.get("tool") in ("context_set", "context_write"):   # (e) input.tag
                t = inp.get("tag")
                if isinstance(t, str) and "@" not in t and "$" not in t:
                    escritas[t].add((arq, "invoke " + node["tool"]))
                elif t is not None:
                    dinamicas.append((arq, "invoke " + str(node.get("tool")), repr(t)[:60]))
            for v in node.values():
                anda(v, arq)
        elif isinstance(node, list):
            for v in node:
                anda(v, arq)
        elif isinstance(node, str):
            for m in re.finditer(r"@ctx\.([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*)", node):
                nome = m.group(1)
                (dinamicas.append((arq, "@ctx", nome)) if "{" in nome
                 else leituras[nome].add((arq, "@ctx")))

    for fn in sorted(os.listdir(SKILLS)):
        if fn.endswith((".yaml", ".yml")):
            anda(yaml.safe_load(io.open(os.path.join(SKILLS, fn), encoding="utf-8").read()), fn)
    return escritas, leituras, dinamicas


# ── metade PLATAFORMA ────────────────────────────────────────────────────────
def censo_plataforma():
    escritas = defaultdict(set)
    for rel in ESCRITORES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        linhas = io.open(p, encoding="utf-8", errors="ignore").read().split("\n")
        for i, ln in enumerate(linhas):
            for m in LIT.finditer(ln):
                if MARCA_W.search("\n".join(linhas[max(0, i - 6):i + 2])):
                    escritas[m.group(1)].add(os.path.basename(rel))
    return escritas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    canonicas, aliases = ler_mapa()
    tenant, leituras, dinamicas = censo_tenant()
    plat = censo_plataforma()

    def estado(n):
        if n in aliases:
            return "alias -> " + aliases[n]
        if n in canonicas:
            return "canonica (%s)" % canonicas[n]
        if n.startswith("segment.{segId}."):
            return "FAMILIA dinamica"
        return "NAO DECLARADO"

    todos = defaultdict(lambda: {"orig": set(), "sup": set()})
    for n, v in tenant.items():
        todos[n]["orig"].add("tenant")
        todos[n]["sup"] |= {s for _a, s in v}
    for n, v in plat.items():
        todos[n]["orig"].add("plataforma")
        todos[n]["sup"] |= {"codigo:" + a for a in v}

    nao_decl = sorted(n for n in todos if estado(n) == "NAO DECLARADO")
    sem_escritor = sorted(set(leituras) - set(todos))

    if args.json:
        json.dump({
            "canonicas": len(canonicas), "aliases": len(aliases),
            "escritos": sorted(todos), "nao_declarados": nao_decl,
            "lidos_sem_escritor": sem_escritor,
            "dinamicos": [list(d) for d in dinamicas],
        }, sys.stdout, ensure_ascii=False, indent=1)
        return

    print("=" * 88)
    print("CENSO DE CADASTRO DO CONTEXTSTORE (D9) — %d nomes escritos" % len(todos))
    print("=" * 88)
    for n in sorted(todos):
        print("  %-46s %-11s %s" % (n[:46], "+".join(sorted(todos[n]["orig"]))[:11], estado(n)))

    print("\nNAO DECLARADOS — %d" % len(nao_decl))
    for n in nao_decl:
        print("  %-46s %s" % (n, " · ".join(sorted(todos[n]["sup"]))[:38]))

    print("\nLIDOS SEM ESCRITOR CONHECIDO — %d" % len(sem_escritor))
    for n in sem_escritor:
        print("  %-46s %s" % (n, estado(n)))

    print("\n" + "=" * 88)
    print("  mapa vigente     : %d canonicas · %d aliases" % (len(canonicas), len(aliases)))
    print("  escritos         : %d  (tenant %d · plataforma %d)" % (len(todos), len(tenant), len(plat)))
    print("  ja cobertos      : %d" % (len(todos) - len(nao_decl)))
    print("  NAO cobertos     : %d" % len(nao_decl))
    print("  lidos s/ escritor: %d" % len(sem_escritor))
    print("  DINAMICOS        : %d   <- a premissa da D9.2 vive deste zero" % len(dinamicas))
    for d in dinamicas:
        print("      %s" % (d,))


if __name__ == "__main__":
    main()
