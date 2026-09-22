# -*- coding: utf-8 -*-
"""Censo AST: a chave do `menu_result` e a MESMA nos quatro produtores e no leitor?

O DEFEITO QUE O ORIGINOU (2026-09-07, VOZ-03)
  `sms.py` publicava `payload = {"menu_id", "answers"}`. O bridge le
  `content["payload"]["result"]`. Ninguem le `answers`. O collect sequencial de
  SMS entregava string VAZIA ao skill -- sem erro, sem log, sem vermelho.

  E a suite estava VERDE por cima: `test_sms_adapter.py` afirmava
  `payload["answers"]["topic"] == "suporte"`. Produtor e teste olhando um para o
  outro, nenhum dos dois para o consumidor. E a familia do *"a mesma pergunta
  respondida em duas casas"*: o contrato de um payload nao vive em nenhum dos
  lados, vive ENTRE eles -- e por isso nao ha arquivo onde um `grep` o encontre.

POR QUE O LEITOR E MEDIDO, NUNCA ESCRITO AQUI
  Se este gate fixasse `"result"` como constante, ele mediria a concordancia dos
  produtores com o GATE, e nao com quem consome: trocar a chave no bridge deixaria
  os quatro produtores verdes contra um leitor que mudou. A chave e EXTRAIDA do
  fonte do bridge, e mais de um leitor com chaves diferentes tambem REPROVA --
  seria o defeito do lado do consumidor, com a mesma forma.

DOIS FORMATOS DESDE A VOZ-05 FATIA 5a (2026-09-16)
  O desfecho da coleta sem valor (`timeout`/`invalid`) e `{"menu_id", "outcome"}` e o
  bridge o le ANTES do valor, como `(content.get("payload") or {}).get("outcome")`. O
  leitor do sinal e MEDIDO do mesmo jeito (a forma `or {}`); um produtor vale se carrega
  a chave do valor OU uma chave de sinal que algum leitor le. Sem isso, o desfecho
  aparecia como produtor fora do contrato.

RAMOS
  contrato      todo produtor carrega a chave que o leitor le
  contrato-mut  renomeia a chave num produtor e exige acusacao

EXIT: 0 OK - 1 FALHA - 3 SEM AMOSTRA
"""
import ast
import io
import os
import sys

ADAPTERS = "packages/channel-gateway/src/plughub_channel_gateway/adapters"
BRIDGE = "packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py"
TIPO = "menu_result"


def _kw(call, nome):
    for k in call.keywords:
        if k.arg == nome:
            return k.value
    return None


def _const(no):
    return no.value if isinstance(no, ast.Constant) else None


def produtores(fontes):
    """[(arquivo, linha, {chaves do payload})] de todo Call type="menu_result"."""
    achados = []
    for arq, src in sorted(fontes.items()):
        try:
            arv = ast.parse(src)
        except SyntaxError:
            continue
        for n in ast.walk(arv):
            if not isinstance(n, ast.Call):
                continue
            if _const(_kw(n, "type")) != TIPO:
                continue
            pay = _kw(n, "payload")
            if not isinstance(pay, ast.Dict):
                # payload montado fora do literal: nao da para afirmar nada.
                achados.append((os.path.basename(arq), n.lineno, None))
                continue
            chaves = set()
            for k in pay.keys:
                v = _const(k)
                if isinstance(v, str):
                    chaves.add(v)
            achados.append((os.path.basename(arq), n.lineno, chaves))
    return achados


def leitores_sinal(src):
    """Chaves lidas como `(<algo>.get("payload") or {}).get(K)` no bridge."""
    fora = []
    try:
        arv = ast.parse(src)
    except SyntaxError:
        return fora
    for n in ast.walk(arv):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args):
            continue
        base = n.func.value
        if not (isinstance(base, ast.BoolOp) and isinstance(base.op, ast.Or) and base.values):
            continue
        interno = base.values[0]
        if not (isinstance(interno, ast.Call) and isinstance(interno.func, ast.Attribute)
                and interno.func.attr == "get" and interno.args
                and _const(interno.args[0]) == "payload"):
            continue
        chave = _const(n.args[0])
        if isinstance(chave, str):
            fora.append((chave, n.lineno))
    return fora


def leitores(src):
    """Chaves lidas como `<algo>.get("payload", ...).get(K, ...)` no bridge."""
    fora = []
    try:
        arv = ast.parse(src)
    except SyntaxError:
        return fora
    for n in ast.walk(arv):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "get" and n.args):
            continue
        interno = f.value
        if not isinstance(interno, ast.Call):
            continue
        g = interno.func
        if not (isinstance(g, ast.Attribute) and g.attr == "get" and interno.args):
            continue
        if _const(interno.args[0]) != "payload":
            continue
        chave = _const(n.args[0])
        if isinstance(chave, str):
            fora.append((chave, n.lineno))
    return fora


def metadados(src):
    """Chaves lidas como `_menu_meta(<algo>, K)` no bridge (WCH-11).

    Metadado NAO e o contrato do valor: produtor antigo legitimamente nao o manda, e por isso
    ele nao entra no censo de `leitores` (que exige a chave em TODOS). O que se exige aqui e
    mais fraco e ainda assim um mecanismo: ALGUM produtor tem de carregar cada metadado lido.
    Sem isto, renomear `via` no canal faria o bridge voltar a registrar a fala duas vezes, em
    silencio -- e o defeito so apareceria numa transcricao que ninguem confere.
    """
    fora = []
    try:
        arv = ast.parse(src)
    except SyntaxError:
        return fora
    for n in ast.walk(arv):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_menu_meta" and len(n.args) >= 2):
            continue
        chave = _const(n.args[1])
        if isinstance(chave, str):
            fora.append((chave, n.lineno))
    return fora


def carregar():
    if not os.path.isdir(ADAPTERS) or not os.path.exists(BRIDGE):
        return None, None
    fontes = {}
    for x in sorted(os.listdir(ADAPTERS)):
        if x.endswith(".py"):
            p = os.path.join(ADAPTERS, x)
            fontes[p] = io.open(p, encoding="utf-8").read()
    return fontes, io.open(BRIDGE, encoding="utf-8").read()


def contrato(mutar=False):
    fontes, bridge = carregar()
    if fontes is None:
        print("VEREDICTO: SEM AMOSTRA - fontes ausentes")
        return 3

    lidos = leitores(bridge)
    if not lidos:
        print("VEREDICTO: SEM AMOSTRA - nenhum leitor de payload no bridge")
        return 3
    chaves_lidas = sorted(set(k for k, _ln in lidos))
    for k, ln in lidos:
        print("   leitor   main.py linha %-6d payload[%r]" % (ln, k))
    if len(chaves_lidas) != 1:
        print("VEREDICTO: FALHA - o bridge le %d chaves diferentes: %s"
              % (len(chaves_lidas), ", ".join(chaves_lidas)))
        print("           Duas casas do lado do CONSUMIDOR e o mesmo defeito de")
        print("           costas: o produtor nao tem contra o que ser conferido.")
        return 1
    chave = chaves_lidas[0]
    print("   chave do contrato (medida no leitor): %r" % chave)
    sinais = sorted(set(k for k, _ln in leitores_sinal(bridge)))
    for k, ln in leitores_sinal(bridge):
        print("   sinal    main.py linha %-6d payload[%r]" % (ln, k))

    if mutar:
        alvo = [p for p in fontes if p.endswith("sms.py")] or [sorted(fontes)[0]]
        fontes[alvo[0]] = fontes[alvo[0]].replace(
            '"result":      answers', '"answers":     answers')

    achados = produtores(fontes)
    if not achados:
        print("VEREDICTO: SEM AMOSTRA - nenhum produtor de %s" % TIPO)
        return 3

    ruins = []
    for arq, ln, chaves in achados:
        if chaves is None:
            print("   OPACO    %-14s linha %-6d payload nao e literal" % (arq, ln))
            ruins.append((arq, ln, "payload nao literal"))
            continue
        if chave in chaves:
            print("   ok       %-14s linha %-6d %s" % (arq, ln, sorted(chaves)))
        elif chaves & set(sinais):
            print("   ok sinal %-14s linha %-6d %s" % (arq, ln, sorted(chaves)))
        else:
            print("   QUEBRA   %-14s linha %-6d %s" % (arq, ln, sorted(chaves)))
            ruins.append((arq, ln, sorted(chaves)))

    print("   produtores: %d - fora do contrato: %d" % (len(achados), len(ruins)))

    if mutar:
        if ruins:
            print("VEREDICTO: OK - a mutacao foi ACUSADA")
            return 0
        print("VEREDICTO: FALHA - a mutacao passou; o censo nao pega nada")
        return 1

    if ruins:
        print("VEREDICTO: FALHA - produtor publica chave que ninguem le.")
        print("           Nao fica vermelho sozinho: o leitor devolve o default")
        print("           (string vazia) e o skill segue com uma resposta que o")
        print("           cliente nunca deu.")
        return 1
    print("VEREDICTO: OK - os %d produtores publicam %r, que e o que o bridge le"
          % (len(achados), chave))
    return 0


def metadado(mutar=False):
    """Ramo C (WCH-11) -- todo METADADO lido pelo bridge e produzido por ALGUEM."""
    fontes, bridge = carregar()
    if fontes is None:
        print("VEREDICTO: SEM AMOSTRA - fontes ausentes")
        return 3
    lidos = metadados(bridge)
    if not lidos:
        print("VEREDICTO: SEM AMOSTRA - o bridge nao le metadado de menu_result")
        return 3
    for k, ln in lidos:
        print("   leitor   main.py linha %-6d metadado %r" % (ln, k))

    if mutar:
        alvo = [p for p in fontes if p.endswith("webrtc.py")] or [sorted(fontes)[0]]
        fontes[alvo[0]] = fontes[alvo[0]].replace('"via": done.via', '"por_onde": done.via')

    achados = produtores(fontes)
    if not achados:
        print("VEREDICTO: SEM AMOSTRA - nenhum produtor de %s" % TIPO)
        return 3
    publicadas = set()
    for _arq, _ln, chaves in achados:
        if chaves:
            publicadas |= chaves

    orfas = sorted(set(k for k, _ln in lidos) - publicadas)
    for k in sorted(set(k for k, _ln in lidos)):
        print("   %-8s metadado %r" % ("ok" if k in publicadas else "ORFAO", k))

    if mutar:
        if orfas:
            print("VEREDICTO: OK - a mutacao foi ACUSADA (orfaos: %s)" % ", ".join(orfas))
            return 0
        print("VEREDICTO: FALHA - a mutacao passou; o censo nao pega renomeacao de metadado")
        return 1

    if orfas:
        print("VEREDICTO: FALHA - o bridge le metadado que produtor nenhum manda: %s"
              % ", ".join(orfas))
        print("           Nao fica vermelho sozinho: o bridge decide como se o metadado")
        print("           nao existisse -- e a fala volta a ser registrada duas vezes.")
        return 1
    print("VEREDICTO: OK - todo metadado lido tem produtor")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "contrato"
    fn = {"contrato": contrato, "contrato-mut": lambda: contrato(mutar=True),
          "metadado": metadado, "metadado-mut": lambda: metadado(mutar=True)}.get(modo)
    if not fn:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
    sys.exit(fn())
