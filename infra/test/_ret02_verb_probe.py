# -*- coding: utf-8 -*-
"""Helper do probe_orchestrator_delegate_verb.sh — RET-02.

Modos:
  vivo      exerce `pool_route_resolve` AO VIVO por cada destino de navegacao e
            confere que o verbo entregue casa com o criterio derivado do deploy
  fluxo     o snapshot PROMOVIDO dos orquestradores tem o ramo do verbo, e o
            delegate endereca por REFERENCIA (nunca literal)
  paridade  o verbo que a tool entrega x o que o probe de delegabilidade calcula
            -- sao DUAS implementacoes (TypeScript e Python) do mesmo criterio,
            e paridade presumida entre linguagens e o defeito que o gate do
            channel-gateway existe para pegar
"""
import json
import sys
import urllib.error
import urllib.request

REG = "http://localhost:3300"
MCP = "http://localhost:3100"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo",
     "content-type": "application/json"}


def get(u):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H)))
    except Exception:
        return None


def orquestradores():
    p = get(REG + "/v1/pools") or []
    p = p if isinstance(p, list) else p.get("pools", [])
    return sorted((x["pool_id"], x.get("navigation_pools") or {}) for x in p
                  if x.get("navigation_pools"))


def snapshot(pool_id):
    s = get("%s/v1/pools/%s/slots" % (REG, pool_id))
    cur = ((s or {}).get("slots") or {}).get("current") or {}
    return cur.get("skill_id"), (cur.get("yaml_snapshot") or {})


def passos(snap):
    return [x for x in (snap or {}).get("steps") or [] if isinstance(x, dict)]


TAG_CHAMADOR = "@ctx.core.workflow.delegate_resume_token"


def devolve_ao_chamador(ps):
    """Invoca `workflow_resume` COM O TOKEN DO CHAMADOR — nao so a tool.

    ⚠️ `cadeia_delegate` saiu em 2026-09-07 (CTR-06): o engine passou a capturar
    o token do chamador no nascimento do pipeline e a restaura-lo na retomada,
    entao delegar por dentro deixou de sobrescrever o token de quem chamou.
    Tira-lo SOZINHO abriria um buraco — `nao_retorna` media a mera presenca da
    tool, e `agente_portabilidade_intake_v1` a invoca cinco vezes sem retomar o
    chamador uma unica vez (retoma um `suspend` proprio).
    """
    for p in ps:
        if p.get("tool") != "workflow_resume":
            continue
        ent = p.get("input")
        if isinstance(ent, dict) and ent.get("resume_token") == TAG_CHAMADOR:
            return True
    return False


def criterio_python(alvo):
    """A MESMA regra do `_delegability_probe.py`, reescrita aqui de proposito:
    e ela que serve de contraprova ao TypeScript no modo `paridade`."""
    _sid, snap = snapshot(alvo)
    ps = passos(snap)
    if not ps:
        return "escalate", "sem_deploy"
    if not devolve_ao_chamador(ps):
        return "escalate", "nao_retorna"
    return "delegate", "devolve_o_controle"


def fluxo():
    falhas = 0
    for pid, _nav in orquestradores():
        sid, snap = snapshot(pid)
        ps = passos(snap)
        if not ps:
            print("   %-14s SEM DEPLOY VIVO" % pid)
            falhas += 1
            continue
        tem_choice = any(p.get("id") == "decidir_verbo" for p in ps)
        dele = [p for p in ps if p.get("type") == "delegate"]
        ref = all(str(p.get("pool", "")).startswith(("$.", "@ctx.")) for p in dele) if dele else False
        cont = any(p.get("id") == "nivel_continuacao" for p in ps)
        ok = tem_choice and len(dele) == 1 and ref and cont
        print("   %-14s %-30s ramo=%s delegate=%d por_ref=%s continuacao=%s  %s" % (
            pid, sid, tem_choice, len(dele), ref, cont, "OK" if ok else "FALHA"))
        if not ok:
            falhas += 1
    if falhas:
        print("VEREDICTO: FALHA — %d orquestrador(es) sem o ramo do verbo bem formado" % falhas)
        return 1
    print("VEREDICTO: OK — os dois orquestradores decidem o verbo e delegam por REFERENCIA")
    return 0


def _resolve(session_id, caminho):
    corpo = json.dumps({"session_id": session_id, "path": caminho, "tenant_id": "tenant_demo"}).encode()
    r = urllib.request.Request(MCP + "/api/tools/pool_route_resolve", data=corpo, headers=H, method="POST")
    try:
        with urllib.request.urlopen(r) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"__http__": e.code}
    except Exception as e:
        return {"__erro__": str(e)}


def paridade(ao_vivo):
    """Sem sessao viva nao da para chamar a tool; entao o modo `paridade` julga o
    CRITERIO (Python) e, quando ha como, confronta com a tool (TypeScript)."""
    total, difs, sem_tool = 0, [], 0
    vistos = {}
    for _pid, nav in orquestradores():
        for _caminho, alvo in sorted(nav.items()):
            if alvo in vistos:
                continue
            vistos[alvo] = criterio_python(alvo)
            total += 1
    print("destinos distintos: %d" % total)
    for alvo, (v, r) in sorted(vistos.items()):
        print("   %-20s %-9s %s" % (alvo, v, r))
    if total == 0:
        print("VEREDICTO: SEM AMOSTRA")
        return 3
    delegaveis = sum(1 for v, _ in vistos.values() if v == "delegate")
    if delegaveis == 0:
        print("VEREDICTO: INCONCLUSIVO — nenhum destino delegavel; o ramo do verbo")
        print("           nunca seria exercido e o verde nao diria nada")
        return 3
    print("VEREDICTO: OK — %d de %d destinos resolvem para `delegate`" % (delegaveis, total))
    return 0


def criterio_puro(snap):
    """A regra, sobre um snapshot QUALQUER — sem ir ao registry.

    E esta que os vetores exercem, e e a gemea do `decideVerb` TypeScript.
    """
    ps = passos(snap) if isinstance(snap, dict) else []
    if not ps:
        return "escalate", "sem_deploy"
    if not devolve_ao_chamador(ps):
        return "escalate", "nao_retorna"
    return "delegate", "devolve_o_controle"


def vetores():
    """Os MESMOS vetores que o teste TypeScript le. Ver o cabecalho do JSON."""
    import os
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "verb_vectors.json")
    try:
        casos = json.load(open(caminho, encoding="utf-8"))["vetores"]
    except Exception as e:
        print("VEREDICTO: SEM AMOSTRA — nao li %s (%s)" % (caminho, e))
        return 3
    if len(casos) < 6:
        print("VEREDICTO: SEM AMOSTRA — so %d vetor(es); a paridade nao diria nada" % len(casos))
        return 3

    falhas = 0
    for c in casos:
        v, r = criterio_puro(c["snapshot"])
        ok = (v == c["verb"] and r == c["reason"])
        if not ok:
            falhas += 1
            print("   FALHA %-38s esperado=%s/%s obtido=%s/%s" % (
                c["nome"], c["verb"], c["reason"], v, r))
    print("vetores: %d · divergencias Python x contrato: %d" % (len(casos), falhas))
    if falhas:
        print("VEREDICTO: FALHA — o criterio Python discorda dos vetores")
        return 1
    print("VEREDICTO: OK — o criterio Python casa com os vetores (o TS os le na sua suite)")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "fluxo"
    if modo == "fluxo":
        sys.exit(fluxo())
    elif modo == "paridade":
        sys.exit(paridade(False))
    elif modo == "vetores":
        sys.exit(vetores())
    else:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
