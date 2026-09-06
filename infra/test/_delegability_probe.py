# -*- coding: utf-8 -*-
"""Helper do probe_orchestrator_delegability.sh — CTR-03 / G3.

PERGUNTA: um destino de navegação pode receber `delegate` do orquestrador sem
deixar o contato pendurado?

Delegar é SUSPENDER o chamador. O orquestrador só volta se o especialista chamar
`workflow_resume` com o token. Um alvo que não chama deixa o orquestrador
suspenso até o `timeout_hours` — e o modo de falha é o pior do catálogo: o
cliente vê o especialista atender normalmente, o especialista encerra o próprio
segmento, e o contato fica pendurado sem erro em lugar nenhum.

TRÊS DISQUALIFICADORES, cada um com nome:
  sem_deploy       o pool não tem slot `current` com snapshot — não há skill a
                   quem delegar que possa retornar (é o caso dos pools humanos)
  nao_retorna      o snapshot não invoca `workflow_resume` — delegar pendura
  cadeia_delegate  o snapshot usa `delegate` — `core.workflow.delegate_resume_token`
                   é tag ÚNICA da sessão, então a delegação de dentro SOBRESCREVE
                   o token do orquestrador, que nunca retoma (CTR-06)

⚠️ Lê o SNAPSHOT VIVO do slot, nunca o YAML do disco. O YAML é seed-if-absent e
editar skill já semeado é no-op — medir o arquivo responderia sobre um artefato
que pode não ser o que roda.

Modos:
  censo    — a tabela de delegabilidade por destino
  trava    — REPROVA orquestrador que delegue a alvo não-delegável
  trava-mut— a mutação da trava: finge um delegate e exige que ela acuse
"""
import json
import sys
import urllib.error
import urllib.request

REG = "http://localhost:3300"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo"}


def get(u):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H)))
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def pools():
    p = get(REG + "/v1/pools")
    if p is None:
        return []
    return p if isinstance(p, list) else p.get("pools", [])


def snapshot_vivo(pool_id):
    s = get("%s/v1/pools/%s/slots" % (REG, pool_id))
    if not s:
        return None, None
    cur = (s.get("slots") or {}).get("current")
    if not cur or not cur.get("yaml_snapshot"):
        return None, None
    return cur.get("skill_id"), cur["yaml_snapshot"]


def passos(snap):
    return [x for x in (snap or {}).get("steps") or [] if isinstance(x, dict)]


def invoca(snap, tool):
    return any(p.get("tool") == tool for p in passos(snap))


def usa_tipo(snap, tipo):
    return any(p.get("type") == tipo for p in passos(snap))


def orquestradores():
    """Pools que DECLARAM navigation_pools — os que podem delegar por rota."""
    saida = []
    for p in pools():
        nav = p.get("navigation_pools") or {}
        if nav:
            saida.append((p["pool_id"], nav))
    return sorted(saida)


def julga(alvo):
    """(delegavel, motivo, skill_id) para um pool alvo."""
    skill_id, snap = snapshot_vivo(alvo)
    if snap is None:
        return False, "sem_deploy", None
    if usa_tipo(snap, "delegate"):
        return False, "cadeia_delegate", skill_id
    if not invoca(snap, "workflow_resume"):
        return False, "nao_retorna", skill_id
    return True, "delegavel", skill_id


# ── censo ────────────────────────────────────────────────────────────────────

def censo():
    orqs = orquestradores()
    if not orqs:
        print("VEREDICTO: SEM AMOSTRA — nenhum pool declara navigation_pools")
        return 3

    vistos, delegaveis = {}, 0
    for pid, nav in orqs:
        print("== %s — %d destino(s) de navegacao ==" % (pid, len(nav)))
        for caminho, alvo in sorted(nav.items()):
            if alvo not in vistos:
                vistos[alvo] = julga(alvo)
            ok, motivo, sid = vistos[alvo]
            print("   %-22s -> %-20s %-32s %s" % (
                caminho, alvo, sid or "(sem skill)",
                "DELEGAVEL" if ok else motivo))
        print()

    delegaveis = sum(1 for v in vistos.values() if v[0])
    print("destinos DISTINTOS: %d · delegaveis: %d" % (len(vistos), delegaveis))
    for motivo in ("nao_retorna", "cadeia_delegate", "sem_deploy"):
        n = sum(1 for v in vistos.values() if v[1] == motivo)
        if n:
            print("   %-16s %d" % (motivo, n))

    if delegaveis == 0:
        print()
        print("VEREDICTO: NENHUM DESTINO E DELEGAVEL HOJE — a CTR-03 nao e")
        print("           entregavel antes da CTR-04. Nao e falha: e o estado")
        print("           medido, e a trava abaixo existe para que ninguem ligue")
        print("           o delegate assim mesmo.")
        return 0
    print()
    print("VEREDICTO: %d destino(s) delegavel(is) — a CTR-03 destravou para eles" % delegaveis)
    return 0


# ── trava ────────────────────────────────────────────────────────────────────

def trava(mutar):
    """Orquestrador que DELEGA a alvo nao-delegavel reprova.

    A mutacao finge que o orquestrador tem um step delegate para cada destino
    declarado; se a trava continuar verde com isso, ela nao trava nada.
    """
    orqs = orquestradores()
    if not orqs:
        print("VEREDICTO: SEM AMOSTRA — nenhum orquestrador para travar")
        return 3

    infracoes, examinados = [], 0
    for pid, nav in orqs:
        skill_id, snap = snapshot_vivo(pid)
        if snap is None:
            print("   %-16s sem deploy vivo — nada a examinar" % pid)
            continue
        examinados += 1

        if mutar:
            # Finge o pior caso: delega para TODOS os destinos declarados.
            alvos = sorted(set(nav.values()))
        else:
            # Alvos REAIS de step delegate. O `pool` pode ser uma ref
            # ($.pipeline_state.rota.pool) — nesse caso o alvo e decidido em
            # runtime e QUALQUER destino do mapa e alcancavel, entao a trava
            # cobra o mapa inteiro. Recusar-se a julgar a ref seria deixar
            # passar exatamente a forma que o orquestrador usa.
            alvos = []
            for p in passos(snap):
                if p.get("type") != "delegate":
                    continue
                alvo = p.get("pool") or ""
                if alvo.startswith("$.") or alvo.startswith("@ctx."):
                    alvos.extend(nav.values())
                elif alvo:
                    alvos.append(alvo)
            alvos = sorted(set(alvos))

        for alvo in alvos:
            ok, motivo, sid = julga(alvo)
            if not ok:
                infracoes.append((pid, skill_id, alvo, sid, motivo))

    print("orquestradores com deploy vivo: %d" % examinados)
    if examinados == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhum orquestrador deployado")
        return 3

    print("infracoes: %d" % len(infracoes))
    for i in infracoes:
        print("   %-14s %-30s -> %-20s %-30s %s" % (
            i[0], i[1] or "?", i[2], i[3] or "(sem skill)", i[4]))

    if mutar:
        if infracoes:
            print("VEREDICTO: OK — a trava acusa quando o delegate existe")
            return 0
        print("VEREDICTO: FALHA — trava cega: finji o delegate e ela ficou verde")
        return 1

    if infracoes:
        print()
        print("VEREDICTO: FALHA — orquestrador delega a alvo que nao devolve o")
        print("           controle. O contato fica PENDURADO ate o timeout_hours,")
        print("           sem erro em lugar nenhum. Faca a CTR-04 no alvo antes.")
        return 1
    print("VEREDICTO: OK — nenhum orquestrador delega a alvo nao-delegavel")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "censo"
    if modo == "censo":
        sys.exit(censo())
    elif modo == "trava":
        sys.exit(trava(False))
    elif modo == "trava-mut":
        sys.exit(trava(True))
    else:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
