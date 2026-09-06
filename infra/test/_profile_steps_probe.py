# -*- coding: utf-8 -*-
"""Helper do probe_skill_profile_steps.sh — CTR-01 / G1.

Modos:
  censo       — deploys VIVOS (slot "current") julgados pela lista canônica
  censo-mut   — o MESMO censo com "delegate" injetado nos proibidos do agente.
                É o controle de que o censo enxerga violação: se ele fica verde
                aqui, ele estava verde por cegueira, não por conformidade.
  vivo        — exerce o PORTÃO de deploy ao vivo (set-next), nos dois perfis e
                nos dois sentidos (recusa e passagem)

⚠️ A lista está DUPLICADA aqui, em Python, e o @plughub/schemas a tem em
TypeScript. Isso é deliberado e é o ponto do modo "vivo": o censo sozinho seria
paridade PRESUMIDA entre duas linguagens — exatamente o defeito que o gate de
paridade do channel-gateway existe para pegar. Os ramos E/F comparam a lista
deste arquivo contra a decisão REAL do serviço.
"""
import json
import sys
import urllib.error
import urllib.request

REG = "http://localhost:3300"
H = {
    "x-tenant-id": "tenant_demo",
    "x-service-token": "changeme_agent_registry_service_token_demo",
    "content-type": "application/json",
}

PROIBIDO = {
    "workflow": {"menu", "notify", "begin_transaction", "end_transaction"},
    # "delegate" NÃO entra: medido em 2026-09-06 que é caminho VIVO em perfil de
    # agente (limite_ia, 186 segmentos; alvo dialog_runner, 24).
    "agent": {"suspend", "collect"},
}

POOL_AGENTE = "probe_profile_agent"
POOL_WORKFLOW = "probe_profile_workflow"


def req(metodo, url, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    r = urllib.request.Request(url, data=dados, headers=H, method=metodo)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def get(url):
    return req("GET", url)[1]


def perfil_de(canais):
    return "workflow" if "webhook" in (canais or []) else "agent"


def tipos_do(snapshot):
    passos = (snapshot or {}).get("steps") or []
    return set(s.get("type") for s in passos if isinstance(s, dict))


def pools():
    p = get(REG + "/v1/pools")
    return p if isinstance(p, list) else p.get("pools", [])


# ── censo ────────────────────────────────────────────────────────────────────

def censo(mutado):
    proibido = dict((k, set(v)) for k, v in PROIBIDO.items())
    if mutado:
        proibido["agent"].add("delegate")

    total, violacoes, testemunha = 0, [], []
    for p in sorted(pools(), key=lambda x: x.get("pool_id", "")):
        pid = p["pool_id"]
        if pid in (POOL_AGENTE, POOL_WORKFLOW):
            continue  # fixture do proprio probe: nao e parque
        try:
            cur = (get("%s/v1/pools/%s/slots" % (REG, pid)).get("slots") or {}).get("current")
        except Exception:
            cur = None
        if not cur or not cur.get("yaml_snapshot"):
            continue
        total += 1
        pf = perfil_de(p.get("channel_types"))
        t = tipos_do(cur["yaml_snapshot"])
        ruins = t & proibido[pf]
        if ruins:
            violacoes.append((pid, cur.get("skill_id"), pf, sorted(ruins)))
        if pf == "agent" and "delegate" in t:
            testemunha.append((pid, cur.get("skill_id")))

    print("deploys vivos com snapshot: %d" % total)
    print("delegate em perfil AGENTE (testemunha de presenca): %d" % len(testemunha))
    for d in testemunha:
        print("   %-26s %s" % d)
    print("violacoes: %d" % len(violacoes))
    for v in violacoes:
        print("   %-26s %-34s %-8s %s" % (v[0], v[1], v[2], ",".join(v[3])))

    if total == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhum deploy vivo; o censo nao julga nada")
        return 3

    if mutado:
        # Controle: com "delegate" proibido no agente, o censo TEM de acusar.
        if violacoes:
            print("VEREDICTO: OK — o censo enxerga violacao quando ela existe")
            return 0
        print("VEREDICTO: FALHA — censo cego: mutei a lista e ele continuou verde")
        return 1

    if not testemunha:
        print("VEREDICTO: INCONCLUSIVO — sem delegate em perfil agente no parque,")
        print("           a decisao que o removeu da lista perdeu o lastro vivo.")
        print("           Reveja a CTR-01 antes de confiar neste verde.")
        return 3
    if violacoes:
        print("VEREDICTO: FALHA — ha deploy vivo que o portao recusaria")
        return 1
    print("VEREDICTO: OK — parque conforme, e a lista tem lastro vivo")
    return 0


# ── ao vivo ──────────────────────────────────────────────────────────────────

def acha_skill(com):
    """Primeiro skill PUBLICADO cujo flow use o step pedido. None se nao houver."""
    sk = get(REG + "/v1/skills")
    sk = sk if isinstance(sk, list) else sk.get("skills", [])
    for s in sorted(sk, key=lambda x: x.get("skill_id", "")):
        if com in tipos_do(s.get("flow")):
            return s["skill_id"]
    return None


def garante_pool(pid, canais):
    st, _ = req("GET", "%s/v1/pools/%s" % (REG, pid))
    if st == 200:
        return True
    # ⚠️ O pool nasce com 1 vaga porque o schema exige > 0 — e fica INERTE por
    # nunca ser PROMOVIDO: o bridge executa exclusivamente o snapshot do slot
    # "current", e este pool só terá "next". Um pool com next e sem current não
    # instancia agente, não consome licença e não recebe contato.
    st, corpo = req("POST", REG + "/v1/pools", {
        "pool_id": pid,
        "agent_kind": "ai",
        "description": "fixture do probe_skill_profile_steps — NUNCA promover",
        "channel_types": canais,
        "sla_target_ms": 60000,
        "max_concurrent_sessions": 1,
    })
    if st in (200, 201, 409):
        return True
    print("   nao consegui criar %s: %s %s" % (pid, st, corpo))
    return False


def set_next(pid, skill_id):
    return req("PUT", "%s/v1/pools/%s/slots/next" % (REG, pid), {"skill_id": skill_id})


def vivo():
    falhas, inconclusivos = 0, 0

    if not (garante_pool(POOL_AGENTE, ["webchat"]) and garante_pool(POOL_WORKFLOW, ["webhook"])):
        print("VEREDICTO: INCONCLUSIVO — sem as fixtures nao ha o que exercer")
        return 3

    casos = [
        (POOL_AGENTE,   "suspend",  "recusa", "C: suspend em perfil AGENTE"),
        (POOL_WORKFLOW, "menu",     "recusa", "D: menu em perfil WORKFLOW"),
        # ── OS CONTROLES POSITIVOS ──
        # Sem eles, um portao que recusasse TUDO passaria em C e D. E o E e o
        # unico lugar do gate onde a decisao real do servico (TypeScript) e
        # comparada com a lista deste arquivo (Python): se alguem devolver
        # "delegate" a lista de proibidos do agente, E fica vermelho.
        (POOL_AGENTE,   "delegate", "passa",  "E: delegate em perfil AGENTE (caminho vivo)"),
        (POOL_WORKFLOW, "suspend",  "passa",  "F: suspend em perfil WORKFLOW"),
    ]

    for pid, step, esperado, rotulo in casos:
        sid = acha_skill(step)
        if not sid:
            print("%-46s SEM AMOSTRA (nenhum skill publicado com %s)" % (rotulo, step))
            inconclusivos += 1
            continue
        st, corpo = set_next(pid, sid)
        erro = (corpo or {}).get("error")
        if esperado == "recusa":
            ok = st == 422 and erro == "step_fora_do_perfil"
            print("%-46s %s  [%s %s] skill=%s" % (
                rotulo, "OK" if ok else "FALHA", st, erro or "-", sid))
        else:
            # Passar e ausencia DESTA recusa. Outro 422 (capacidade, definicao)
            # nao e evidencia sobre o perfil — declara-se inconclusivo, nunca verde.
            if erro == "step_fora_do_perfil":
                ok = False
                print("%-46s FALHA  [%s %s] skill=%s" % (rotulo, st, erro, sid))
            elif st in (200, 201):
                ok = True
                print("%-46s OK  [%s] skill=%s" % (rotulo, st, sid))
            else:
                print("%-46s INCONCLUSIVO  [%s %s] skill=%s" % (rotulo, st, erro or "-", sid))
                inconclusivos += 1
                continue
        if not ok:
            falhas += 1

    if falhas:
        print("VEREDICTO: FALHA — %d caso(s) ao vivo" % falhas)
        return 1
    if inconclusivos:
        print("VEREDICTO: INCONCLUSIVO — %d caso(s) sem amostra" % inconclusivos)
        return 3
    print("VEREDICTO: OK — o portao recusa nos dois perfis e deixa passar nos dois")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "censo"
    if modo == "censo":
        sys.exit(censo(False))
    elif modo == "censo-mut":
        sys.exit(censo(True))
    elif modo == "vivo":
        sys.exit(vivo())
    else:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
