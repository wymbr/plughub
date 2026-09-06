# -*- coding: utf-8 -*-
"""Ramos F e G do `probe_orchestrator_tree_nav` — a F3 e a guarda da D10.

Vive em arquivo proprio pela mesma razao do `_nav_epoch_stamp.py`: o gate ja usa
heredoc `PY` no ramo D, e um segundo heredoc aninhado fecha o primeiro.

⚠️ A GUARDA e o ponto. O ADR e explicito: *"a F3 e a porta da erosao"* — tornar o
alvo do `escalate` interpolavel e necessario, e e exatamente por onde
"roteamento condicional no formulario" entraria depois. A guarda tem de nascer
JUNTO com a interpolacao, nunca depois, porque depois ja ha um formulario com
destino dentro e a decisao vira migracao em vez de regra.
"""
import glob, io, json, os, sys

# Campos que, dentro de uma opcao de DialogForm, significariam DESTINO. Nao e
# lista de estilo: qualquer um deles poe roteamento no conteudo congelado, que e
# o que a D2 desfaz e a D10 protege.
DESTINO = ("pool", "pool_id", "target", "target_pool", "escalate_to",
           "route", "route_to", "skill_id", "agent_type_id")

# De "infra/test/" sao DOIS niveis ate a raiz do repo. Um so aponta para "infra/", e
# ai o leitor nao acha nada e o ramo sai INCONCLUSIVO — que foi o que aconteceu.
RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def anda(opcoes, achados, trilha=""):
    for o in opcoes or []:
        if not isinstance(o, dict):
            continue
        onde = (trilha + "." + str(o.get("id"))).lstrip(".")
        for k in DESTINO:
            if k in o:
                achados.append(onde + ":" + k)
        anda(o.get("options"), achados, onde)


def ramo_g():
    """Nenhuma folha publicada carrega destino, e o SCHEMA nao tem onde poe-lo."""
    achados = []
    formas = 0
    for p in sorted(glob.glob(os.path.join(RAIZ, "infra", "dialog", "*.json"))):
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        formas += 1
        for n in d.get("nodes") or []:
            anda(n.get("options"), achados, os.path.basename(p) + "#" + str(n.get("id")))
    if achados:
        return "FORMA_COM_DESTINO " + ",".join(achados[:5])

    # A segunda metade: o schema. Uma forma limpa hoje nao impede um campo novo
    # amanha — e um campo que EXISTE acaba sendo usado.
    sp = os.path.join(RAIZ, "packages", "schemas", "src", "dialog.ts")
    if not os.path.isfile(sp):
        return "SEM_SCHEMA"
    src = io.open(sp, encoding="utf-8").read()
    i = src.find("DialogOptionSchema")
    if i < 0:
        return "SEM_DIALOG_OPTION"
    corpo = src[i:i + 1600]
    sujos = [k for k in DESTINO if (k + ":") in corpo]
    if sujos:
        return "SCHEMA_COM_DESTINO " + ",".join(sujos)
    return "GUARDA_OK formas=%d" % formas


def ramo_f(skill_path):
    """O alvo do escalate e uma REFERENCIA, e o mapa nao esta no fluxo."""
    try:
        import yaml
    except ImportError:
        return "SEM_YAML"
    if not os.path.isfile(skill_path):
        return "SEM_SKILL"
    d = yaml.safe_load(io.open(skill_path, encoding="utf-8").read())
    steps = d.get("steps") or []

    escs = [s for s in steps if s.get("type") == "escalate"]
    refs = [s["id"] for s in escs
            if str((s.get("target") or {}).get("pool", "")).startswith(("$.", "@"))
            or "{{" in str((s.get("target") or {}).get("pool", ""))]
    if not refs:
        return "NENHUM_ESCALATE_INTERPOLADO escalates=%d" % len(escs)

    # A resolucao tem de ser um step VISIVEL, com on_failure proprio: escondida
    # dentro do escalate daria o mesmo despacho sem lugar para o erro aparecer.
    res = [s for s in steps if s.get("tool") == "pool_route_resolve"]
    if not res:
        return "SEM_RESOLUCAO_DE_ROTA"
    if not res[0].get("on_failure"):
        return "RESOLUCAO_SEM_ON_FAILURE"

    # E o fluxo nao pode ter voltado a carregar a tabela: um `choice` cujas
    # condicoes escolhem entre escalates literais e a tabela de novo.
    literais = [s["id"] for s in escs if s["id"] not in refs]
    return "F3_OK interpolados=%d literais=%d resolucao=%s" % (
        len(refs), len(literais), res[0]["id"])


# ── Ramo H — o especialista cobre as folhas que a navegacao lhe manda ───────

def _folhas(nodes):
    """Todos os caminhos de FOLHA da arvore (pasta = tem `options`)."""
    saida = []

    def anda(opts, trilha):
        for o in opts or []:
            if not isinstance(o, dict):
                continue
            aqui = (trilha + [str(o.get("id"))])
            filhos = o.get("options")
            if filhos:
                anda(filhos, aqui)
            else:
                saida.append(".".join(aqui))

    for n in nodes or []:
        anda(n.get("options"), [])
    return saida


def _resolve(mapa, caminho):
    segs = [x for x in caminho.split(".") if x]
    for n in range(len(segs), 0, -1):
        k = ".".join(segs[:n])
        v = mapa.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def ramo_h():
    """Cada folha roteada para um pool casa com um ramo do skill DAQUELE pool.

    ⚠️ O defeito que este ramo existe para pegar e SILENCIOSO: renomear uma folha
    na forma, ou acrescentar uma sob uma pasta ja mapeada, faz o especialista
    deixar de reconhecer o caminho e **voltar a perguntar** — o menu duplicado
    ressuscita, e nada fica vermelho porque o `default` do `choice` e justamente
    "pergunte". Default seguro esconde regressao: por isso a checagem e de fora.
    """
    try:
        import yaml
    except ImportError:
        return "SEM_YAML"

    seed = os.path.join(RAIZ, "infra", "registry", "tenant_demo.yaml")
    if not os.path.isfile(seed):
        return "SEM_SEED"
    cfg = yaml.safe_load(io.open(seed, encoding="utf-8").read())
    pools = {p["pool_id"]: p for p in (cfg.get("pools") or []) if isinstance(p, dict)}

    orquestradores = {pid: p for pid, p in pools.items() if p.get("navigation_pools")}
    if not orquestradores:
        return "SEM_ORQUESTRADOR"

    problemas, conferidos = [], 0
    for pid, p in orquestradores.items():
        mapa = p["navigation_pools"]
        folhas = []
        for fp in sorted(glob.glob(os.path.join(RAIZ, "infra", "dialog", "*.json"))):
            try:
                d = json.load(io.open(fp, encoding="utf-8"))
            except Exception:
                continue
            if "navegacao" not in os.path.basename(fp):
                continue
            folhas += _folhas(d.get("nodes"))

        porpool = {}
        for f in folhas:
            alvo = _resolve(mapa, f)
            if alvo:
                porpool.setdefault(alvo, set()).add(f)

        for alvo, esperadas in sorted(porpool.items()):
            skill_id = ((pools.get(alvo) or {}).get("deploy") or {}).get("skill_id")
            if not skill_id:
                continue   # pool humano, ou sem skill declarada no seed
            sp = os.path.join(RAIZ, "packages", "skill-flow-engine", "skills", skill_id + ".yaml")
            if not os.path.isfile(sp):
                continue
            sk = yaml.safe_load(io.open(sp, encoding="utf-8").read())
            ramos = set()
            for st in sk.get("steps") or []:
                if st.get("type") != "choice":
                    continue
                for c in st.get("conditions") or []:
                    if str(c.get("field", "")).endswith("session.navegacao.path"):
                        ramos.add(str(c.get("value")))
            if not ramos:
                # Skill que nao declara o atalho continua perguntando — comportamento
                # antigo, nao regressao. Nao ha promessa a conferir.
                continue
            conferidos += 1
            orfas   = ramos - esperadas         # ramo que a navegacao nunca manda
            perdidas = esperadas - ramos        # folha que chega e o skill nao reconhece
            if orfas:
                problemas.append("%s:ramo_sem_folha(%s)" % (skill_id, ",".join(sorted(orfas))))
            if perdidas:
                problemas.append("%s:folha_sem_ramo(%s)" % (skill_id, ",".join(sorted(perdidas))))

    if not conferidos:
        return "NENHUM_ATALHO_DECLARADO"
    if problemas:
        return "DIVERGE " + " ".join(problemas)
    return "H_OK skills=%d" % conferidos


if __name__ == "__main__":
    if sys.argv[1] == "F":
        print(ramo_f(sys.argv[2]))
    elif sys.argv[1] == "H":
        print(ramo_h())
    else:
        print(ramo_g())
