# -*- coding: utf-8 -*-
"""Helper do probe_caller_token_chain.sh — CTR-06.

Modos:
  mecanismo  o engine tem as DUAS metades (capturar no nascimento, restaurar na
             retomada) e a chave do capturado esta FORA do padrao de sentinela
  criterio   `decideVerb` concorda com o mecanismo: sem `cadeia_delegate`, e com
             `nao_retorna` decidido pela TAG DO CHAMADOR, nunca pela mera
             presenca da tool
  vivo       censo sobre os destinos do mapa de navegacao: quem e cadeia, quem
             devolve ao chamador, e nenhum delegavel que nao devolva
"""
import io
import json
import re
import sys
import urllib.request

REG = "http://localhost:3300"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo"}

ENGINE = "packages/skill-flow-engine/src/engine.ts"
NAV    = "packages/mcp-server-plughub/src/tools/navigation.ts"
TAG    = "core.workflow.delegate_resume_token"


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


# ── mecanismo ────────────────────────────────────────────────────────────────

def mecanismo():
    """As duas metades so funcionam JUNTAS, e por isso sao um ramo so.

    Capturar sem restaurar e uma chave que ninguem le. Restaurar sem capturar
    nao tem o que escrever. Um gate que aceitasse metade daria verde a um
    mecanismo inerte.
    """
    src = ler(ENGINE)
    if not src:
        print("VEREDICTO: SEM AMOSTRA — nao li %s" % ENGINE)
        return 3

    achados = {
        "declara captura":  "_capturarTokenDoChamador(" in src,
        "declara restauro": "_restaurarTokenDoChamador(" in src,
        # ⚠️ Declarar nao e CHAMAR: um helper orfao passa por `grep` e nao roda.
        # As chamadas tem de estar nos dois ramos certos do `_execute`.
        "chama captura no nascimento":
            bool(re.search(r"PipelineStateManager\.create\([\s\S]{0,900}?"
                           r"_capturarTokenDoChamador\(", src)),
        "chama restauro na retomada":
            bool(re.search(r'status:\s*"in_progress" as const\s*\}[\s\S]{0,400}?'
                           r"_restaurarTokenDoChamador\(", src)),
        "escreve com overwrite": '"overwrite"' in src,
    }
    for rotulo, ok in achados.items():
        print("   %-30s %s" % (rotulo, "OK" if ok else "AUSENTE"))

    # A chave do capturado NAO pode cair no padrao `{id}:__x__` — aquela familia
    # e apagada ao ENTRAR num step, e o `delegate` e exatamente o step em que se
    # reentra. Seria a licao da RET-04 outra vez, e o modo de falha e mudo: a
    # captura acontece, a limpeza a apaga, o restauro nao acha nada.
    m = re.search(r"CHAVE_TOKEN_CHAMADOR\s*=\s*\"([^\"]+)\"", src)
    chave = m.group(1) if m else ""
    print("   chave do capturado           %s" % (chave or "(nao achei)"))
    padrao_sentinela = chave.startswith("__") or chave.endswith("__") or ":" in chave
    if padrao_sentinela:
        print("   ⚠️ a chave casa com o padrao de sentinela")

    falta = [k for k, v in achados.items() if not v]
    if falta or not chave or padrao_sentinela:
        print("VEREDICTO: FALHA — o mecanismo da CTR-06 esta incompleto:")
        print("           metade sozinha nao repara cadeia nenhuma, e o modo de")
        print("           falha e o mesmo de antes (contato pendurado, nada vermelho)")
        return 1
    print("VEREDICTO: OK — captura no nascimento + restauro na retomada, chave isolada")
    return 0


# ── criterio ─────────────────────────────────────────────────────────────────

def criterio():
    """Duas casas respondem *"este destino pode receber delegate?"*.

    O engine responde com o MECANISMO; o `decideVerb` responde com o CRITERIO.
    Quando elas discordam, a mais permissiva e a que vale — e aqui a permissiva
    pendura o contato.
    """
    eng = ler(ENGINE)
    nav = ler(NAV)
    if not eng or not nav:
        print("VEREDICTO: SEM AMOSTRA — nao li os dois fontes")
        return 3

    tem_reparo = "_restaurarTokenDoChamador(" in eng
    # A recusa por cadeia so pode existir se o reparo NAO existir. Procura pelo
    # `return` do motivo, nunca pela palavra: o comentario que explica a remocao
    # cita o nome, e cobrar a palavra proibiria documentar a decisao.
    recusa_cadeia = bool(re.search(r'reason:\s*"cadeia_delegate"', nav))
    # `nao_retorna` tem de medir a proposicao CERTA: devolve AO CHAMADOR. Medir
    # so a presenca da tool aceita quem retoma um `suspend` proprio.
    afere_token = "TAG_TOKEN_CHAMADOR" in nav and '"resume_token"' in nav

    print("   engine repara a cadeia            %s" % tem_reparo)
    print("   decideVerb recusa por cadeia      %s" % recusa_cadeia)
    print("   `nao_retorna` afere o TOKEN       %s" % afere_token)

    if tem_reparo and recusa_cadeia:
        print("VEREDICTO: FALHA — o reparo existe e o criterio ainda recusa:")
        print("           destino delegavel tratado como folha, sem motivo vivo")
        return 1
    if not tem_reparo and not recusa_cadeia:
        print("VEREDICTO: FALHA — sem reparo e sem recusa: a cadeia colide de novo")
        return 1
    if not afere_token:
        print("VEREDICTO: FALHA — `nao_retorna` mede a proposicao ADJACENTE")
        print("           (*existe a tool?*) e nao a que interessa (*devolve ao")
        print("           CHAMADOR?*): quem retoma um `suspend` proprio passa")
        return 1
    print("VEREDICTO: OK — mecanismo e criterio concordam")
    return 0


# ── vivo ─────────────────────────────────────────────────────────────────────

def _passos(snap):
    return [x for x in (snap or {}).get("steps") or [] if isinstance(x, dict)]


def _snapshot(pool_id):
    s = get("%s/v1/pools/%s/slots" % (REG, pool_id))
    cur = ((s or {}).get("slots") or {}).get("current") or {}
    return cur.get("skill_id"), (cur.get("yaml_snapshot") or {})


def _devolve_ao_chamador(passos):
    for p in passos:
        if p.get("tool") != "workflow_resume":
            continue
        ent = p.get("input")
        if isinstance(ent, dict) and ent.get("resume_token") == "@ctx.%s" % TAG:
            return True
    return False


def _reparo_no_artefato():
    """O reparo esta no JS COMPILADO que roda, nao so no `.ts` do repositorio.

    ⚠️ E a licao da RET-04, na forma dela: `max_iterations` foi publicado, deu 200
    duas vezes, e nao existia — quem media o FONTE ficava verde. Aqui a distancia
    e outra (o `tsc` roda no build da imagem), mas a conclusao e a mesma: fonte
    verde com imagem velha e um mecanismo que nao existe em lugar nenhum.
    """
    import subprocess
    cmd = ["docker", "compose", "-f", "docker-compose.demo.yml", "exec", "-T",
           "skill-flow-service", "grep", "-c", "_restaurarTokenDoChamador",
           "/app/packages/skill-flow-engine/dist/engine.js"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return None, str(e)
    if r.returncode not in (0, 1):
        return None, (r.stderr or "").strip()[:120]
    try:
        return int((r.stdout or "0").strip()), ""
    except ValueError:
        return None, "saida inesperada"


def vivo():
    pools = get(REG + "/v1/pools") or []
    pools = pools if isinstance(pools, list) else pools.get("pools", [])
    mapas = [x for x in pools if x.get("navigation_pools")]
    if not mapas:
        print("VEREDICTO: SEM AMOSTRA — nenhum orquestrador com mapa")
        return 3

    destinos = sorted({v for m in mapas for v in m["navigation_pools"].values()})
    cadeias = 0
    for pool in destinos:
        sid, snap = _snapshot(pool)
        ps = _passos(snap)
        if not ps:
            print("   %-18s SEM DEPLOY" % pool)
            continue
        cadeia   = any(p.get("type") == "delegate" for p in ps)
        devolve  = _devolve_ao_chamador(ps)
        cadeias += 1 if cadeia else 0
        print("   %-18s %-34s cadeia=%-5s devolve=%-5s -> %s" % (
            pool, sid, cadeia, devolve, "delegate" if devolve else "escalate"))

    ocorrencias, erro = _reparo_no_artefato()
    print("   reparo no dist que roda: %s%s" % (
        ocorrencias, (" (%s)" % erro) if erro else ""))
    print("   cadeias entre os destinos: %d de %d" % (cadeias, len(destinos)))

    if ocorrencias is None:
        print("VEREDICTO: SEM AMOSTRA — nao consegui perguntar ao servico")
        return 3
    if ocorrencias == 0:
        print("VEREDICTO: FALHA — o `skill-flow-service` que esta de pe NAO tem o")
        print("           reparo: o fonte esta verde e a cadeia colide igual.")
        print("           Rebuildar o servico (a imagem, nunca `docker cp`)")
        return 1
    if cadeias == 0:
        print("VEREDICTO: INCONCLUSIVO — o reparo esta no ar e NENHUM destino do")
        print("           mapa delega por dentro: ele nao esta sendo exercido, e")
        print("           o verde nao seria evidencia de que funciona")
        return 3
    print("VEREDICTO: OK — ha cadeia entre os destinos e o reparo esta no ar")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "mecanismo"
    fn = {"mecanismo": mecanismo, "criterio": criterio, "vivo": vivo}.get(modo)
    if not fn:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
    sys.exit(fn())
