# -*- coding: utf-8 -*-
"""Helper do probe_delegate_cycle_cap.sh — RET-04.

Modos:
  teto        todo `delegate` que participa de um CICLO declara teto
  colisao     o contador NAO usa o padrao `{id}:__x__` — se usasse, a limpeza de
              sentinelas o zeraria a cada volta e o teto nunca dispararia
  sentinelas  a lista de limpeza cobre as familias que SUSPENDEM (delegate,
              collect, suspend), nao so a do invoke
"""
import io
import json
import re
import sys
import urllib.request

REG = "http://localhost:3300"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo"}
STATE = "packages/skill-flow-engine/src/state.ts"
DELEG = "packages/skill-flow-engine/src/steps/delegate.ts"


def get(u):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H)))
    except Exception:
        return None


def passos(snap):
    return [x for x in (snap or {}).get("steps") or [] if isinstance(x, dict)]


def saidas(p):
    out = set()
    for k in ("on_success", "on_failure", "on_timeout", "on_disconnect", "default",
              "on_max_iterations"):
        v = p.get(k)
        if isinstance(v, str):
            out.add(v)
    for k in ("on_resume", "on_reject", "on_timeout"):
        v = p.get(k)
        if isinstance(v, dict) and v.get("next"):
            out.add(v["next"])
    for c in p.get("conditions") or []:
        if isinstance(c, dict) and c.get("next"):
            out.add(c["next"])
    return out


def em_ciclo(ps, alvo_id):
    """Ha caminho de volta de `alvo_id` ate ele mesmo?"""
    porid = dict((p.get("id"), p) for p in ps if p.get("id"))
    vistos, fila = set(), list(saidas(porid.get(alvo_id, {})))
    while fila:
        c = fila.pop()
        if c == alvo_id:
            return True
        if c in vistos or c not in porid:
            continue
        vistos.add(c)
        fila.extend(saidas(porid[c]))
    return False


def teto():
    pools = get(REG + "/v1/pools") or []
    pools = pools if isinstance(pools, list) else pools.get("pools", [])
    total, ciclicos, sem_teto = 0, 0, []
    for p in sorted(pools, key=lambda x: x.get("pool_id", "")):
        pid = p["pool_id"]
        sl = get("%s/v1/pools/%s/slots" % (REG, pid))
        cur = ((sl or {}).get("slots") or {}).get("current") or {}
        ps = passos(cur.get("yaml_snapshot"))
        for st in ps:
            if st.get("type") != "delegate":
                continue
            total += 1
            if not em_ciclo(ps, st.get("id")):
                continue
            ciclicos += 1
            if st.get("max_iterations") is None:
                sem_teto.append((pid, cur.get("skill_id"), st.get("id")))

    print("steps `delegate` vivos: %d · em CICLO: %d" % (total, ciclicos))
    if total == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhum delegate deployado")
        return 3
    if ciclicos == 0:
        print("VEREDICTO: INCONCLUSIVO — nenhum delegate participa de ciclo;")
        print("           o teto nao seria exercido e o verde nao diria nada")
        return 3
    for v in sem_teto:
        print("   SEM TETO  %-14s %-26s step=%s" % v)
    if sem_teto:
        print("VEREDICTO: FALHA — %d delegate(s) em ciclo sem `max_iterations`" % len(sem_teto))
        return 1
    print("VEREDICTO: OK — todo delegate em ciclo declara teto")
    return 0


def _sentinelas_declaradas():
    txt = io.open(STATE, encoding="utf-8").read()
    m = re.search(r"SENTINELAS\s*=\s*\[(.*?)\]\s*as const", txt, re.S)
    if not m:
        return None
    return set(re.findall(r'"(__[a-z_]+__)"', m.group(1)))


def colisao():
    """O contador do teto NAO pode cair na lista de limpeza."""
    lista = _sentinelas_declaradas()
    if lista is None:
        print("VEREDICTO: SEM AMOSTRA — nao achei SENTINELAS em %s" % STATE)
        return 3
    txt = io.open(DELEG, encoding="utf-8").read()
    m = re.search(r"const iterKey\s*=\s*`([^`]+)`", txt)
    if not m:
        print("VEREDICTO: SEM AMOSTRA — nao achei a chave do contador em %s" % DELEG)
        return 3
    chave = m.group(1)
    print("chave do contador: %s" % chave)
    print("sentinelas limpas na transicao: %d" % len(lista))

    # A limpeza casa `${toStep}:${sufixo}` — o contador so colide se a chave
    # tiver a forma `...:__algo__` com o sufixo declarado.
    sufixo = re.search(r":(__[a-z_]+__)\s*$", chave)
    if sufixo and sufixo.group(1) in lista:
        print("VEREDICTO: FALHA — o contador usa `%s`, que a transicao LIMPA:" % sufixo.group(1))
        print("           ele zeraria a cada volta e o teto nunca dispararia")
        return 1
    if sufixo:
        print("VEREDICTO: FALHA — o contador esta no padrao `{id}:__x__`, a um passo")
        print("           de ser incluido na lista por alguem que nao souber disso")
        return 1
    print("VEREDICTO: OK — o contador esta FORA do padrao de sentinela, imune por construcao")
    return 0


def sentinelas():
    """A limpeza cobre as familias que SUSPENDEM, nao so a do invoke."""
    lista = _sentinelas_declaradas()
    if lista is None:
        print("VEREDICTO: SEM AMOSTRA — nao achei SENTINELAS em %s" % STATE)
        return 3
    # Sufixos que os steps de suspensao carimbam. Um deles fora da lista significa
    # que a SEGUNDA volta do ciclo devolve o resultado da primeira, em silencio.
    exigidos = {
        "delegate": {"__delegated__", "__resume_decision__", "__resume_payload__"},
        "collect":  {"__collected__", "__collect_decision__", "__collect_response__"},
    }
    falta = {}
    for familia, req in exigidos.items():
        ausentes = sorted(req - lista)
        if ausentes:
            falta[familia] = ausentes
    print("sentinelas limpas: %d — %s" % (len(lista), ", ".join(sorted(lista))))
    for familia, aus in falta.items():
        print("   FALTA %-10s %s" % (familia, ", ".join(aus)))
    if falta:
        print("VEREDICTO: FALHA — familia que SUSPENDE fora da limpeza: a segunda")
        print("           volta do ciclo devolveria o resultado da primeira")
        return 1
    print("VEREDICTO: OK — as familias que suspendem estao cobertas")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "teto"
    if modo == "teto":
        sys.exit(teto())
    elif modo == "colisao":
        sys.exit(colisao())
    elif modo == "sentinelas":
        sys.exit(sentinelas())
    else:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
