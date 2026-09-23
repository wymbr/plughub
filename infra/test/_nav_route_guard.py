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


# ── Ramo J — a D6: o LLM aterrissa em folha DECLARADA, e isso e conferido ─────

LLM_SKILL   = "skill_navegacao_llm_v1"
DET_SKILL   = "skill_navegacao_v1"


def _steps(skill_id):
    import yaml
    p = os.path.join(RAIZ, "packages", "skill-flow-engine", "skills", skill_id + ".yaml")
    if not os.path.isfile(p):
        return None
    d = yaml.safe_load(io.open(p, encoding="utf-8").read())
    return {s["id"]: s for s in (d.get("steps") or []) if isinstance(s, dict) and "id" in s}


def _serie(passos):
    """(emitter, metric_key) do `agent_event_record` — a unidade de medida."""
    for s in passos.values():
        if s.get("tool") == "agent_event_record":
            i = s.get("input") or {}
            return (i.get("emitter"), i.get("metric_key"))
    return None


def ramo_j():
    """Duas proposicoes, e a primeira e a que costuma ser so promessa.

    (1) A resposta do LLM e CONFERIDA contra o vocabulario — nao apenas pedida no
        prompt. Instrucao nao e mecanismo: o modelo pode devolver um caminho
        plausivel que nao existe, e sem conferencia ele viraria uma categoria que
        a lente desenha como se alguem a tivesse autorado.
    (2) Os DOIS orquestradores emitem a MESMA serie. Metricas diferentes tornam a
        comparacao impossivel, que e justamente o que a fase existe para permitir.
    """
    try:
        import yaml  # noqa: F401
    except ImportError:
        return "SEM_YAML"

    llm = _steps(LLM_SKILL)
    det = _steps(DET_SKILL)
    if llm is None:
        return "SEM_SKILL_LLM"
    if det is None:
        return "SEM_SKILL_DET"

    # (1) o LLM decide...
    reason = [s for s in llm.values() if s.get("type") == "reason"]
    if not reason:
        return "SEM_REASON"
    r = reason[0]
    saida = r.get("output_as")
    if not saida:
        return "REASON_SEM_OUTPUT_AS"

    # ...e a decisao passa por uma CONFERENCIA que usa a projecao.
    conf = [
        s for s in llm.values()
        if s.get("tool") == "dialog_tree_level"
        and saida in str((s.get("input") or {}).get("chosen_id", ""))
    ]
    if not conf:
        return "SEM_CONFERENCIA"

    # A decisao do LLM nao pode chegar ao ROTEAMENTO sem passar por uma
    # conferencia. Ate 2026-09-22 isto era uma aresta DIRETA
    # (`reason.on_success == conferir`); a ORQ-13 poe uma triagem no meio, e a
    # pergunta de esclarecimento confere pelo `only_paths` em vez do `chosen_id`.
    # A REGRA continua a mesma — o que mudou foi a forma.
    #
    # ⚠️ Afrouxar para *"existe uma conferencia em algum lugar do skill"* seria
    # perder o ramo: o defeito que ele existe para pegar e o caminho que DESVIA.
    # Por isso o teste virou ALCANCABILIDADE — todo caminho que sai do `reason`
    # encontra uma conferencia ANTES de rotear, registrar ou escalar. Provado por
    # mutacao: apontar `reason.on_success` para `escalar` volta a reprovar.
    # A VARIAVEL que a medicao le — derivada do proprio skill, nunca fixada aqui:
    # e ela que a conferencia tem de ESCREVER. Sem este vinculo, uma conferencia
    # qualquer (a das opcoes da pergunta, por exemplo) contaria como se fosse a do
    # destino, e o caminho registrado seria o da variavel ANTIGA — categoria errada
    # na serie, sem nada ficar vermelho. (Mutacao M3, medida em 2026-09-22.)
    var_medida = None
    for s in llm.values():
        if s.get("tool") == "agent_event_record":
            ref = str((s.get("input") or {}).get("path", ""))
            if ref.startswith("$.pipeline_state."):
                partes = ref.split(".")
                if len(partes) > 2:
                    var_medida = partes[2]
            break
    if not var_medida:
        return "SEM_VARIAVEL_MEDIDA"

    def _confere(s):
        if s.get("tool") != "dialog_tree_level":
            return False
        # ⚠️ Tem de escrever a variavel que a medicao le — ver acima.
        if s.get("output_as") != var_medida:
            return False
        inp = s.get("input") or {}
        # confere a resposta do modelo, ou a escolha que o cliente devolveu
        ci = str(inp.get("chosen_id", ""))
        if saida in ci or ci.startswith("$.pipeline_state."):
            return True
        # ...ou ancora num caminho LITERAL (o escape), que e folha declarada por
        # construcao: e a mesma projecao decidindo, so que sobre um valor nosso.
        return bool(ci) and not ci.startswith("$.") and not ci.startswith("@")

    def _saidas(s):
        """Todo sucessor: `on_*` string, `on_*` objeto ({next}), `default` e as
        condicoes do `choice` — que a varredura anterior NAO seguia."""
        for k, v in s.items():
            if k.startswith("on_") or k in ("next", "default"):
                if isinstance(v, str):
                    yield v
                elif isinstance(v, dict) and isinstance(v.get("next"), str):
                    yield v["next"]
        for c in (s.get("conditions") or []):
            if isinstance(c, dict) and isinstance(c.get("next"), str):
                yield c["next"]

    # ── ORQ-17: quem confere valor DERIVADO endereça a question ATIVA ─────────
    #
    # Medido em 2026-09-22 na sessao `5f339d70`: o cliente disse "obrigado", o LLM
    # respondeu `encerrar` (certo), e a conferencia procurou `encerrar` na arvore de
    # ENTRADA porque o step dizia `output_key: destino` — `found: false`, escape,
    # atendente humano. O modelo acertou e a conferencia jogou fora, sem nada ficar
    # vermelho. Depois de uma continuacao o cursor vive em OUTRA question.
    #
    # A excecao e o ESCAPE: `chosen_id` LITERAL ancora numa folha declarada por
    # construcao, e a folha de escape so existe na entrada.
    for skill_nome, passos in (("llm", llm), ("det", det)):
        for s in passos.values():
            if s.get("tool") != "dialog_tree_level":
                continue
            inp = s.get("input") or {}
            derivado = any(
                str(inp.get(k, "")).startswith("$.")
                for k in ("chosen_id", "only_paths")
            )
            if not derivado:
                continue
            endereco = str(inp.get("question_id", inp.get("output_key", "")))
            if not endereco.startswith("$."):
                return "ENDERECO_FIXO_NA_CONFERENCIA(%s:%s=%s)" % (
                    skill_nome, s["id"], endereco or "(ausente)")

    conf_ids = {s["id"] for s in llm.values() if _confere(s)}
    consome  = {
        s["id"] for s in llm.values()
        if s.get("type") in ("escalate", "delegate")
        or s.get("tool") in ("pool_route_resolve", "agent_event_record")
    }
    # estado = (passo, ja passou por conferencia)
    inicio = r.get("on_success")
    fila, vistos = [(inicio, inicio in conf_ids)], set()
    while fila:
        no, conferiu = fila.pop()
        if (no, conferiu) in vistos or no not in llm:
            continue
        vistos.add((no, conferiu))
        if no in consome and not conferiu:
            return "REASON_DESVIA_DA_CONFERENCIA(%s->%s)" % (inicio, no)
        for prox in _saidas(llm[no]):
            fila.append((prox, conferiu or prox in conf_ids))

    # E o veredicto tem de ramificar sobre found/is_leaf — senao a conferencia
    # roda e ninguem olha o resultado.
    julga = []
    for s in llm.values():
        if s.get("type") != "choice":
            continue
        campos = {str(c.get("field", "")) for c in (s.get("conditions") or [])}
        if any(x.endswith(".found") for x in campos) and any(x.endswith(".is_leaf") for x in campos):
            julga.append(s)
    if not julga:
        return "SEM_VEREDICTO_FOUND_IS_LEAF"

    # O escape tem de ser CONTAVEL: o caminho de recusa precisa alcancar o
    # registro do evento, senao "o LLM nao soube" vira um nulo indistinguivel de
    # "nao perguntamos" — exatamente o que a D7 recusa.
    alvos_recusa = {c.get("next") for c in (julga[0].get("conditions") or [])}
    registra = {s["id"] for s in llm.values() if s.get("tool") == "agent_event_record"}
    alcanca = False
    for a in alvos_recusa:
        vistos, fila = set(), [a]
        while fila:
            n = fila.pop()
            if n in vistos or n not in llm:
                continue
            vistos.add(n)
            if n in registra:
                alcanca = True
                break
            for k, v in llm[n].items():
                if (k.startswith("on_") or k in ("next", "default")) and isinstance(v, str):
                    fila.append(v)
        if alcanca:
            break
    if not alcanca:
        return "ESCAPE_NAO_CONTAVEL"

    # (2) mesma unidade de medida
    sl, sd = _serie(llm), _serie(det)
    if sl is None or sd is None:
        return "SEM_EVENTO(llm=%s det=%s)" % (sl, sd)
    if sl != sd:
        return "SERIES_DIFERENTES(llm=%s det=%s)" % (sl, sd)

    return "J_OK serie=%s.%s" % sl


if __name__ == "__main__":
    if sys.argv[1] == "F":
        print(ramo_f(sys.argv[2]))
    elif sys.argv[1] == "H":
        print(ramo_h())
    elif sys.argv[1] == "J":
        print(ramo_j())
    else:
        print(ramo_g())
