# -*- coding: utf-8 -*-
"""Helper do probe_contact_close_outcomes.sh.

Censo AST: quem decide *"o contato acabou?"* no orchestrator-bridge.

⚠️ E AST, nao `grep`. O texto `"suspended"` aparece em comentario, em log e em
comparacao legitima de OUTRO assunto (o ramo do Arc 19 que publica
`session_suspended`); contar por texto acusaria os inocentes e ensinaria a
ignorar o gate.

Modos:
  casas    toda comparacao de OUTCOME que decide fechamento passa pelo predicado
  predicado o predicado existe e cobre os quatro outcomes de continuidade
  casas-mut a mutacao do censo: injeta uma terceira casa e exige acusacao
"""
import ast
import io
import sys

BRIDGE = ("packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py")
PREDICADO = "contato_encerra_com"
FECHADORES = {"_trigger_contact_close", "_close_contact_layer"}
CONTINUIDADE = {"escalated_human", "escalated_ai", "transferred", "suspended"}

MUTANTE = '''
def _mutante(_ai_outcome, redis_client, session_id):
    if _ai_outcome != "suspended":
        _spawn(_trigger_contact_close(redis_client, session_id))
'''


def fonte(extra=""):
    try:
        return io.open(BRIDGE, encoding="utf-8").read() + extra
    except Exception:
        return ""


def _nomes_chamados(no):
    saida = set()
    for x in ast.walk(no):
        if isinstance(x, ast.Call):
            f = x.func
            if isinstance(f, ast.Name):
                saida.add(f.id)
            elif isinstance(f, ast.Attribute):
                saida.add(f.attr)
    return saida


def _compara_outcome(teste):
    """A condicao compara um OUTCOME com literal(is) de continuidade?"""
    for x in ast.walk(teste):
        if not isinstance(x, ast.Compare):
            continue
        alvo = ast.unparse(x.left)
        if "outcome" not in alvo.lower():
            continue
        for comp in x.comparators:
            try:
                valor = ast.literal_eval(comp)
            except Exception:
                continue
            vals = set(valor) if isinstance(valor, (tuple, list, set)) else {valor}
            if vals & CONTINUIDADE:
                return True, alvo
    return False, ""


def casas(mutar=False):
    src = fonte(MUTANTE if mutar else "")
    if not src:
        print("VEREDICTO: SEM AMOSTRA — nao li %s" % BRIDGE)
        return 3
    try:
        arvore = ast.parse(src)
    except SyntaxError as e:
        print("VEREDICTO: SEM AMOSTRA — o fonte nao parseia (%s)" % e)
        return 3

    ruins, boas = [], 0
    for no in ast.walk(arvore):
        if not isinstance(no, ast.If):
            continue
        # A decisao so conta se o corpo REALMENTE fecha o contato.
        if not (_nomes_chamados(no) & FECHADORES):
            continue
        usa_predicado = PREDICADO in _nomes_chamados(no.test)
        compara, alvo = _compara_outcome(no.test)
        if usa_predicado:
            boas += 1
        elif compara:
            ruins.append((getattr(no, "lineno", 0), alvo))

    print("   decisoes que fecham contato via predicado: %d" % boas)
    for ln, alvo in ruins:
        print("   TERCEIRA CASA  linha %-6d compara %s com literal de continuidade" % (ln, alvo))

    if mutar:
        if ruins:
            print("VEREDICTO: OK — a mutacao foi ACUSADA")
            return 0
        print("VEREDICTO: FALHA — a mutacao passou; o censo nao pega nada")
        return 1

    if ruins:
        print("VEREDICTO: FALHA — ha decisao de fechamento que NAO usa o predicado.")
        print("           Duas casas para a mesma pergunta nao tem dois valores:")
        print("           tem o da casa que roda naquele caminho. Foi assim que")
        print("           `escalate` depois de `delegate` fechou o contato 30 ms")
        print("           antes de a fila recebe-lo (ver CHANGELOG 2026-09-07).")
        return 1
    if boas == 0:
        print("VEREDICTO: SEM AMOSTRA — nao achei decisao de fechamento nenhuma")
        return 3
    print("VEREDICTO: OK — toda decisao de fechamento passa por `%s`" % PREDICADO)
    return 0


def predicado():
    src = fonte()
    if not src:
        print("VEREDICTO: SEM AMOSTRA — nao li %s" % BRIDGE)
        return 3
    arvore = ast.parse(src)
    achou = None
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name) and alvo.id == "_OUTCOMES_QUE_NAO_FECHAM":
                    try:
                        achou = set(ast.literal_eval(no.value))
                    except Exception:
                        achou = None
    tem_fn = any(isinstance(n, ast.FunctionDef) and n.name == PREDICADO
                 for n in ast.walk(arvore))
    print("   predicado `%s` declarado: %s" % (PREDICADO, tem_fn))
    print("   lista: %s" % (sorted(achou) if achou else "(nao achei)"))

    if not tem_fn or achou is None:
        print("VEREDICTO: FALHA — sem predicado nao ha juiz unico a impor")
        return 1
    faltando = CONTINUIDADE - achou
    if faltando:
        print("VEREDICTO: FALHA — a lista foi APARADA: falta %s" % sorted(faltando))
        print("           Cada um desses significa *a sessao continua com outro")
        print("           agente*; fecha-los e corrida contra a alocacao seguinte.")
        return 1
    print("VEREDICTO: OK — o predicado existe e cobre os %d outcomes de continuidade"
          % len(CONTINUIDADE))
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "casas"
    fn = {"casas": casas, "predicado": predicado,
          "casas-mut": lambda: casas(mutar=True)}.get(modo)
    if not fn:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
    sys.exit(fn())
