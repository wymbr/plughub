# -*- coding: utf-8 -*-
"""Helper do probe_choice_operator_parity.sh.

O `choice` tem DOIS avaliadores — o de `$.` e o de `@ctx.`/`@segment.` — e um
`switch` cada. Operador presente num e ausente no outro NAO da erro: cai no
`default` do switch, devolve `false`, e a condicao vira o `default` do STEP, que
quase sempre e caminho legitimo. Foi assim que `exists` sobre `$.` passou de
2026-09-06 a 2026-09-07 sempre falso.

Modos:
  codigo   todo operador do enum do schema esta implementado no avaliador de `$.`,
           salvo os declarados SO-CTX aqui, com motivo
  vivo     nenhum snapshot PROMOVIDO usa operador SO-CTX sobre campo `$.`, nem
           operador fora do enum
  vivo-mut a mutacao do ramo `vivo`: injeta a combinacao invalida e exige acusacao
"""
import io
import json
import re
import sys
import urllib.request

REG = "http://localhost:3300"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo"}

SCHEMA = "packages/schemas/src/skill.ts"
CHOICE = "packages/skill-flow-engine/src/steps/choice.ts"

# Operadores que SO fazem sentido sobre uma ContextEntry — e o motivo, porque
# uma lista de excecao sem motivo envelhece como permissao.
SO_CTX = {
    "confidence_gte": "confianca e campo da ContextEntry; pipeline_state nao tem o que comparar",
}


def ler(caminho):
    try:
        return io.open(caminho, encoding="utf-8").read()
    except Exception:
        return ""


def get(u):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H)))
    except Exception:
        return None


def operadores_do_schema(src):
    m = re.search(r'operator:\s*z\.enum\(\[([^\]]+)\]\)', src)
    if not m:
        return []
    return re.findall(r'"([a-z_]+)"', m.group(1))


def _corpo(src, nome):
    """O corpo da funcao `nome` — do `function nome(` ate a proxima em coluna 0."""
    i = src.find("function %s(" % nome)
    if i < 0:
        return ""
    j = src.find("\nfunction ", i + 1)
    return src[i:j if j > 0 else len(src)]


# ── codigo ───────────────────────────────────────────────────────────────────

def codigo():
    esquema = ler(SCHEMA)
    avaliador = ler(CHOICE)
    if not esquema or not avaliador:
        print("VEREDICTO: SEM AMOSTRA — nao li os dois fontes")
        return 3

    ops = operadores_do_schema(esquema)
    if not ops:
        print("VEREDICTO: SEM AMOSTRA — nao achei o enum de operadores no schema")
        return 3

    corpo_pipeline = _corpo(avaliador, "evaluateCondition")
    corpo_ctx      = _corpo(avaliador, "evaluateCtxCondition")
    if not corpo_pipeline or not corpo_ctx:
        print("VEREDICTO: SEM AMOSTRA — nao achei os dois avaliadores em %s" % CHOICE)
        return 3

    def tem(corpo, op):
        return re.search(r'case\s+"%s"\s*:' % re.escape(op), corpo) is not None

    faltando = []
    print("   operador          $.    @ctx.   nota")
    for op in ops:
        no_pipe = tem(corpo_pipeline, op)
        # O ramo @ctx. delega ao de `$.` no `default`, entao ele cobre tudo que o
        # outro cobre, mais os proprios `case`s.
        no_ctx = tem(corpo_ctx, op) or no_pipe
        nota = ""
        if op in SO_CTX:
            nota = "SO-CTX: %s" % SO_CTX[op]
            if not no_ctx:
                faltando.append((op, "@ctx."))
        elif not no_pipe:
            faltando.append((op, "$."))
        print("   %-16s %-5s %-6s %s" % (op, "OK" if no_pipe else "--",
                                         "OK" if no_ctx else "--", nota))

    if faltando:
        print("VEREDICTO: FALHA — operador declarado no schema e nao implementado:")
        for op, ramo in faltando:
            print("           `%s` no ramo %s" % (op, ramo))
        print("           Isso NAO da erro em runtime: a condicao vira sempre falsa")
        print("           e o step segue pelo `default`, que parece um caminho normal.")
        return 1
    print("VEREDICTO: OK — os %d operadores do schema tem avaliador nos ramos certos" % len(ops))
    return 0


# ── vivo ─────────────────────────────────────────────────────────────────────

def _condicoes_vivas():
    """(pool, skill, step, field, operator) de todo snapshot PROMOVIDO."""
    pools = get(REG + "/v1/pools") or []
    pools = pools if isinstance(pools, list) else pools.get("pools", [])
    saida = []
    for p in pools:
        s = get("%s/v1/pools/%s/slots" % (REG, p["pool_id"]))
        cur = ((s or {}).get("slots") or {}).get("current") or {}
        snap = cur.get("yaml_snapshot") or {}
        for st in snap.get("steps") or []:
            if not isinstance(st, dict):
                continue
            for c in st.get("conditions") or []:
                if isinstance(c, dict):
                    saida.append((p["pool_id"], cur.get("skill_id"), st.get("id"),
                                  str(c.get("field", "")), str(c.get("operator", ""))))
    return saida


def _julga(condicoes, ops_validos):
    ruins = []
    for pool, skill, step, campo, op in condicoes:
        de_ctx = campo.startswith("@ctx.") or campo.startswith("@segment.")
        if op and op not in ops_validos:
            ruins.append((pool, skill, step, campo, op, "operador fora do enum"))
        elif op in SO_CTX and not de_ctx:
            ruins.append((pool, skill, step, campo, op, "SO-CTX sobre campo `$.`"))
    return ruins


def vivo(mutar=False):
    ops = operadores_do_schema(ler(SCHEMA))
    cond = _condicoes_vivas()
    if not cond:
        print("VEREDICTO: SEM AMOSTRA — nenhuma condicao em snapshot promovido")
        return 3

    if mutar:
        # A combinacao que o ramo existe para pegar, injetada de proposito.
        cond = list(cond) + [("__mutante__", "__mutante__", "guarda",
                              "$.pipeline_state.qualquer", "confidence_gte")]

    ruins = _julga(cond, set(ops))
    print("   condicoes em snapshots promovidos: %d" % len(cond))
    for r in ruins:
        print("   RUIM  %-16s %-26s %-20s %-34s %-14s %s" % r)

    if mutar:
        if any(x[0] == "__mutante__" for x in ruins):
            print("VEREDICTO: OK — a mutacao foi ACUSADA")
            return 0
        print("VEREDICTO: FALHA — a mutacao passou; o ramo `vivo` nao pega nada")
        return 1

    if ruins:
        print("VEREDICTO: FALHA — condicao que nao pode casar em deploy VIVO:")
        print("           ela nao da erro, ela sempre segue pelo `default` do step")
        return 1
    print("VEREDICTO: OK — nenhuma condicao viva usa operador que o seu ramo nao tem")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "codigo"
    fn = {"codigo": codigo, "vivo": vivo,
          "vivo-mut": lambda: vivo(mutar=True)}.get(modo)
    if not fn:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
    sys.exit(fn())
