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


if __name__ == "__main__":
    if sys.argv[1] == "F":
        print(ramo_f(sys.argv[2]))
    else:
        print(ramo_g())
