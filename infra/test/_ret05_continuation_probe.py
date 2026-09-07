# -*- coding: utf-8 -*-
"""Helper do probe_tree_continuation.sh — RET-05.

Modos:
  ponteiros  o `on_return` esta nas folhas cujo destino DEVOLVE, e NAO esta nas
             que nao devolvem — um ponteiro numa folha nao-delegavel pendura
  caminhos   todo caminho de continuacao ou e POOL (no `navigation_pools`) ou e
             COMANDO (declarado no fluxo). Nenhum orfao
  menus      os especialistas PULAM o menu de continuidade quando ha chamador,
             senao o cliente e perguntado duas vezes
  composicao o gemeo Python de `categoryPathFor` casa com os vetores que o
             TypeScript le — a discordancia entre as duas casas custou um contato
             real em 2026-09-07 (o comando de continuacao nunca casava)
"""
import io
import json
import sys
import urllib.request

REG = "http://localhost:3300"
DIALOG = "http://localhost:3760"
FORM = "dialog_navegacao_atendimento_v1"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo"}


def get(u, h=None):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=h or H)))
    except Exception:
        return None


def forma():
    return get("%s/v1/dialog/forms/%s" % (DIALOG, FORM), {"x-tenant-id": "tenant_demo"})


def folhas(no, trilha=()):
    """(caminho_pontuado, on_return) de cada folha."""
    out = []
    for o in no:
        cam = trilha + (o["id"],)
        if o.get("options"):
            out.extend(folhas(o["options"], cam))
        else:
            out.append((".".join(cam), o.get("on_return")))
    return out


def passos(snap):
    return [x for x in (snap or {}).get("steps") or [] if isinstance(x, dict)]


def snapshot(pool_id):
    s = get("%s/v1/pools/%s/slots" % (REG, pool_id))
    cur = ((s or {}).get("slots") or {}).get("current") or {}
    return cur.get("skill_id"), (cur.get("yaml_snapshot") or {})


def devolve(alvo):
    _sid, snap = snapshot(alvo)
    ps = passos(snap)
    if not ps:
        return False
    if any(p.get("type") == "delegate" for p in ps):
        return False
    return any(p.get("tool") == "workflow_resume" for p in ps)


def orquestradores():
    p = get(REG + "/v1/pools") or []
    p = p if isinstance(p, list) else p.get("pools", [])
    return sorted((x["pool_id"], x.get("navigation_pools") or {}) for x in p
                  if x.get("navigation_pools"))


def _mapa_casa(mapa, caminho):
    segs = caminho.split(".")
    for n in range(len(segs), 0, -1):
        if ".".join(segs[:n]) in mapa:
            return True
    return False


# ── ponteiros ────────────────────────────────────────────────────────────────

def ponteiros():
    f = forma()
    if not f:
        print("VEREDICTO: SEM AMOSTRA — nao li a forma %s" % FORM)
        return 3
    orqs = orquestradores()
    if not orqs:
        print("VEREDICTO: SEM AMOSTRA — nenhum orquestrador")
        return 3
    mapa = orqs[0][1]

    entrada = [n for n in f["nodes"] if n.get("kind") == "question" and n["id"] == "destino"]
    if not entrada:
        print("VEREDICTO: SEM AMOSTRA — a question de entrada sumiu")
        return 3

    com, sem, erradas = 0, 0, []
    for caminho, ponteiro in folhas(entrada[0]["options"]):
        # Destino da folha, pelo mesmo casamento por prefixo do roteador.
        alvo = None
        segs = caminho.split(".")
        for n in range(len(segs), 0, -1):
            alvo = mapa.get(".".join(segs[:n]))
            if alvo:
                break
        pode = bool(alvo) and devolve(alvo)
        if ponteiro and not pode:
            erradas.append((caminho, alvo or "(sem rota)", "APONTA e o destino NAO devolve"))
        if ponteiro:
            com += 1
        else:
            sem += 1

    print("folhas com `on_return`: %d · sem: %d" % (com, sem))
    for e in erradas:
        print("   %-24s -> %-18s %s" % e)
    if com == 0:
        print("VEREDICTO: INCONCLUSIVO — nenhuma folha continua; o ciclo esta")
        print("           DESLIGADO e o resto do gate nao diria nada")
        return 3
    if erradas:
        print("VEREDICTO: FALHA — ponteiro em folha cujo destino nao devolve:")
        print("           delegar ali PENDURA o contato ate o timeout_hours")
        return 1
    print("VEREDICTO: OK — so continua quem tem para onde voltar")
    return 0


# ── caminhos ─────────────────────────────────────────────────────────────────

def caminhos():
    f = forma()
    if not f:
        print("VEREDICTO: SEM AMOSTRA — nao li a forma")
        return 3

    conts = [n for n in f["nodes"] if n.get("kind") == "question" and n["id"] != "destino"]
    if not conts:
        print("VEREDICTO: INCONCLUSIVO — nenhuma question de continuacao na forma")
        return 3

    falhas = 0
    for pid, mapa in orquestradores():
        _sid, snap = snapshot(pid)
        # Comandos: valores literais comparados contra `category_path` no fluxo.
        comandos = set()
        for p in passos(snap):
            for c in p.get("conditions") or []:
                if isinstance(c, dict) and str(c.get("field", "")).endswith("category_path"):
                    if isinstance(c.get("value"), str):
                        comandos.add(c["value"])

        orfaos = []
        total = 0
        for q in conts:
            for caminho, _pt in folhas(q.get("options") or [], (q["id"],)):
                total += 1
                if caminho in comandos:
                    continue
                if _mapa_casa(mapa, caminho):
                    continue
                orfaos.append(caminho)
        print("   %-14s continuacoes=%d comandos=%d orfaos=%s" % (
            pid, total, len(comandos), orfaos or "nenhum"))
        if orfaos:
            falhas += 1

    if falhas:
        print("VEREDICTO: FALHA — caminho de continuacao que nao e pool nem comando:")
        print("           o cliente escolheria e o roteador recusaria (sem default, por decisao)")
        return 1
    print("VEREDICTO: OK — toda continuacao e POOL declarado ou COMANDO do fluxo")
    return 0


# ── menus ────────────────────────────────────────────────────────────────────

def menus():
    """O especialista pula o proprio menu quando ha chamador."""
    # (pool, id do choice que decide)
    ALVOS = [("sac_ia", "continuar_ou_devolver"), ("auth_sac_ia", "continuar_ou_devolver")]
    falhas, vistos = 0, 0
    for pool, guarda in ALVOS:
        sid, snap = snapshot(pool)
        ps = passos(snap)
        if not ps:
            print("   %-14s SEM DEPLOY VIVO" % pool)
            continue
        vistos += 1
        g = [p for p in ps if p.get("id") == guarda]
        ok = False
        if g:
            conds = g[0].get("conditions") or []
            ok = any(str(c.get("field", "")).endswith("delegate_resume_token")
                     and c.get("operator") == "exists" for c in conds if isinstance(c, dict))
            ok = ok and bool(g[0].get("default"))
        print("   %-14s %-30s guarda=%s  %s" % (pool, sid, bool(g), "OK" if ok else "FALHA"))
        if not ok:
            falhas += 1
    if vistos == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhum especialista deployado")
        return 3
    if falhas:
        print("VEREDICTO: FALHA — especialista sem a guarda: com chamador ele")
        print("           pergunta, e o orquestrador pergunta DE NOVO no retorno")
        return 1
    print("VEREDICTO: OK — com chamador o especialista devolve em vez de perguntar")
    return 0


# ── composicao ───────────────────────────────────────────────────────────────

def _category_path(entry, question, path):
    """Gemeo do `categoryPathFor` de `@plughub/schemas` — MESMA regra, outra casa.

    ⚠️ E copia de proposito (topologia, como `is_tree`/`temArvore`): o Python nao
    importa TypeScript. O que impede a divergencia sao os VETORES compartilhados,
    nao a boa vontade de quem editar.
    """
    cru = ".".join(path)
    if not question or question == entry:
        return cru
    return "%s.%s" % (question, cru) if cru else question


def composicao():
    import os
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "category_path_vectors.json")
    try:
        vet = json.load(io.open(caminho, encoding="utf-8"))["vetores"]
    except Exception as e:
        print("VEREDICTO: SEM AMOSTRA — nao li os vetores (%s)" % e)
        return 3

    falhas = 0
    for v in vet:
        obtido = _category_path(v.get("entry"), v.get("question"), v["path"])
        ok = obtido == v["category_path"]
        if not ok:
            falhas += 1
            print("   FALHA  %-58s esperado=%-34s obtido=%s" % (
                v["nome"][:58], v["category_path"], obtido))
    print("   vetores: %d · divergencias Python x contrato: %d" % (len(vet), falhas))
    if falhas:
        print("VEREDICTO: FALHA — as duas casas compoem o caminho DIFERENTE.")
        print("           Foi assim que o comando de continuacao nunca casou: o")
        print("           fluxo comparava `pos_atendimento.outra_coisa` e o runtime")
        print("           media `outra_coisa`. Nenhum dos dois errado sozinho.")
        return 1
    print("VEREDICTO: OK — o gemeo Python casa com os vetores (o TS os le na sua suite)")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "ponteiros"
    if modo == "ponteiros":
        sys.exit(ponteiros())
    elif modo == "caminhos":
        sys.exit(caminhos())
    elif modo == "menus":
        sys.exit(menus())
    elif modo == "composicao":
        sys.exit(composicao())
    else:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
