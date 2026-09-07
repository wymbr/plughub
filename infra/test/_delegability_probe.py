# -*- coding: utf-8 -*-
"""Helper do probe_orchestrator_delegability.sh — CTR-03 / G3.

PERGUNTA: um destino de navegação pode receber `delegate` do orquestrador sem
deixar o contato pendurado?

Delegar é SUSPENDER o chamador. O orquestrador só volta se o especialista chamar
`workflow_resume` com o token. Um alvo que não chama deixa o orquestrador
suspenso até o `timeout_hours` — e o modo de falha é o pior do catálogo: o
cliente vê o especialista atender normalmente, o especialista encerra o próprio
segmento, e o contato fica pendurado sem erro em lugar nenhum.

DOIS DISQUALIFICADORES, cada um com nome:
  sem_deploy       o pool não tem slot `current` com snapshot — não há skill a
                   quem delegar que possa retornar (é o caso dos pools humanos)
  nao_retorna      o snapshot não invoca `workflow_resume` **com o token do
                   CHAMADOR** — delegar pendura

⚠️ `cadeia_delegate` SAIU em 2026-09-07 (CTR-06). Ele recusava todo snapshot com
um `delegate`, porque `core.workflow.delegate_resume_token` é tag ÚNICA da sessão
e a delegação de dentro sobrescrevia o token do orquestrador. O engine passou a
CAPTURAR o token no nascimento do pipeline (isolado por segmento) e a RESTAURÁ-LO
na retomada, então a cadeia deixou de ser fato do artefato.

⚠️ **Tirá-lo sozinho abriria um buraco**, e isso é medição: `nao_retorna`
perguntava *"existe um step com `tool: workflow_resume`?"* — proposição ADJACENTE.
O `agente_portabilidade_intake_v1` invoca a tool cinco vezes e nenhuma delas
retoma o chamador (retoma um `suspend` PRÓPRIO); ele passava por ser barrado antes
pelo outro critério. Hoje o critério exige que o `resume_token` seja a TAG do
chamador, que é a pergunta que se queria fazer desde o começo.

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


TAG_CHAMADOR = "@ctx.core.workflow.delegate_resume_token"


def devolve_ao_chamador(snap):
    """Invoca `workflow_resume` COM O TOKEN DO CHAMADOR — não só a tool."""
    for p in passos(snap):
        if p.get("tool") != "workflow_resume":
            continue
        ent = p.get("input")
        if isinstance(ent, dict) and ent.get("resume_token") == TAG_CHAMADOR:
            return True
    return False


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
    if not devolve_ao_chamador(snap):
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
    for motivo in ("nao_retorna", "sem_deploy"):
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

def _guarda_do_verbo(p):
    """O step e a guarda que decide DELEGAR? (RET-02)

    Um `choice` com condicao sobre um campo terminado em `.verb`, valendo
    "delegate". E assim que o orquestrador filtra o alcance de um `delegate` cujo
    `pool` e referencia.
    """
    if p.get("type") != "choice":
        return False
    for c in p.get("conditions") or []:
        if not isinstance(c, dict):
            continue
        campo = str(c.get("field") or "")
        if campo.endswith(".verb") and c.get("value") == "delegate":
            return True
    return False


def _alvos_alcancaveis(snap, nav):
    """Destinos que um step `delegate` deste fluxo pode realmente alcancar.

    ⚠️ **Esta funcao mudou em 2026-09-07 (RET-02), e a mudanca e um APERTO com
    cara de afrouxamento — vale ler o porque.**

    Ate a RET-02 nao havia guarda nenhuma: um `delegate` com `pool` por ref
    (`$.pipeline_state.rota.pool`) alcancava QUALQUER destino do mapa, e a trava
    cobrava o mapa inteiro. Estava certo entao.

    A RET-02 introduziu o `decidir_verbo`: o `pool_route_resolve` devolve o VERBO
    derivado do deploy do destino, e o `choice` so entra no `delegate` quando ele
    e "delegate". Ou seja, o alcance REAL passou a ser filtrado em runtime, e
    continuar cobrando o mapa inteiro faria o gate reprovar um fluxo CORRETO —
    um teste que reprova a proposicao errada, que e o defeito da secao Postura.

    Entao: ref GUARDADA nao cobra o mapa; ref DESGUARDADA continua cobrando. A
    guarda tem de estar em TODOS os predecessores do delegate — um caminho
    lateral que chegue nele sem passar pelo verbo reabre o buraco inteiro.
    """
    ps = passos(snap)
    porid = dict((p.get("id"), p) for p in ps if p.get("id"))

    def predecessores(alvo_id):
        out = []
        for p in ps:
            saidas = set()
            for k in ("on_success", "on_failure", "on_timeout", "on_disconnect", "default"):
                v = p.get(k)
                if isinstance(v, str):
                    saidas.add(v)
            for k in ("on_resume", "on_reject", "on_timeout"):
                v = p.get(k)
                if isinstance(v, dict) and v.get("next"):
                    saidas.add(v["next"])
            for c in p.get("conditions") or []:
                if isinstance(c, dict) and c.get("next"):
                    saidas.add(c["next"])
            if alvo_id in saidas:
                out.append(p)
        return out

    alvos = []
    for p in ps:
        if p.get("type") != "delegate":
            continue
        alvo = str(p.get("pool") or "")
        if alvo.startswith("$.") or alvo.startswith("@ctx."):
            preds = predecessores(p.get("id"))
            guardado = bool(preds) and all(_guarda_do_verbo(q) for q in preds)
            if not guardado:
                alvos.extend(nav.values())
        elif alvo:
            alvos.append(alvo)
    return sorted(set(alvos))




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
            # Finge o pior caso: um delegate DESGUARDADO para todos os destinos.
            # Precisa ser desguardado — com guarda a trava (corretamente) nao
            # acusa, e a mutacao nao provaria nada.
            alvos = sorted(set(nav.values()))
        else:
            alvos = _alvos_alcancaveis(snap, nav)

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


def guarda_mut():
    """A mutacao da GUARDA — e ela que exercita `_alvos_alcancaveis`.

    ⚠️ Existe porque a mutacao `trava-mut` NAO passa por aquela funcao: ela finge
    a lista de alvos diretamente. Se `_alvos_alcancaveis` tivesse um defeito que
    a fizesse devolver sempre vazio, a trava ficaria verde e a `trava-mut`
    continuaria acusando — verde por cegueira, com a mutacao ao lado dando falsa
    tranquilidade. E o defeito de medir a proposicao ADJACENTE.

    Aqui a mutacao e no INSUMO: tira as condicoes do `choice` que decide o verbo
    e exige que a funcao volte a cobrar o mapa inteiro.
    """
    orqs = orquestradores()
    if not orqs:
        print("VEREDICTO: SEM AMOSTRA — nenhum orquestrador")
        return 3

    examinados, cegos = 0, []
    for pid, nav in orqs:
        _sid, snap = snapshot_vivo(pid)
        if snap is None:
            continue
        antes = _alvos_alcancaveis(snap, nav)
        if not any(p.get("type") == "delegate" for p in passos(snap)):
            continue
        examinados += 1

        # Desarma a guarda: o choice deixa de condicionar sobre `.verb`.
        import copy
        mutado = copy.deepcopy(snap)
        for p in passos(mutado):
            if p.get("type") == "choice":
                for c in p.get("conditions") or []:
                    if isinstance(c, dict) and str(c.get("field") or "").endswith(".verb"):
                        c["field"] = "$.pipeline_state.qualquer_outra_coisa"
        depois = _alvos_alcancaveis(mutado, nav)

        ok = len(depois) > len(antes)
        print("   %-14s guardado=%d alvo(s) · desguardado=%d alvo(s)  %s" % (
            pid, len(antes), len(depois), "OK" if ok else "FALHA"))
        if not ok:
            cegos.append(pid)

    if examinados == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhum orquestrador com step delegate")
        return 3
    if cegos:
        print("VEREDICTO: FALHA — a funcao nao reagiu a remocao da guarda: %s" % ", ".join(cegos))
        return 1
    print("VEREDICTO: OK — sem a guarda, a trava volta a cobrar o mapa inteiro")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "censo"
    if modo == "censo":
        sys.exit(censo())
    elif modo == "trava":
        sys.exit(trava(False))
    elif modo == "trava-mut":
        sys.exit(trava(True))
    elif modo == "guarda-mut":
        sys.exit(guarda_mut())
    else:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
