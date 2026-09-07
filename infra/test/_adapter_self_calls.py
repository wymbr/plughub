# -*- coding: utf-8 -*-
"""Censo AST: `self.X(...)` chamado e nunca definido, nos adapters de canal.

⚠️ **Este e o instrumento que teria pego a VOZ-03 no dia em que ela nasceu.** O
`voice.py` chamava SEIS metodos que nao existiam em lugar nenhum do MRO, e nada
ficava vermelho: o `AttributeError` caia num `except Exception` largo que o
reportava como fim NORMAL do laco, em nivel `debug`.

E a suite nao pegava porque **mockava os inexistentes** — `adapter._normalize_text
= MagicMock()` sob o comentario *"Mock inherited base methods"*, uma afirmacao
falsa que a atribuicao tornava verdadeira dentro do teste. Teste que prova a
CHAMADA e esconde a AUSENCIA.

Como decide, e por que quase nao tem falso positivo:
  * so conta `self.NOME(...)` — chamada DIRETA. `self.attr.metodo()` e chamada no
    atributo, nao no adapter, e fica de fora;
  * o universo de nomes validos e a UNIAO de: metodos definidos na classe,
    metodos das bases (resolvidas dentro do pacote), qualquer `self.NOME = ...`
    (atributo calavel atribuido no `__init__`) e `__getattr__` declarado.

Modos:
  censo    lista as chamadas orfas; reprova se houver
  censo-mut a mutacao: injeta uma chamada a metodo inexistente e exige acusacao
"""
import ast
import io
import os
import sys

BASE = "packages/channel-gateway/src/plughub_channel_gateway"
ADAPTERS = BASE + "/adapters"

# ── Divida DECLARADA, com dono e motivo ──────────────────────────────────────
#
# ⚠️ Isto NAO e isencao — e o par `_SCOPE_DEBT`/`_SCOPE_EXEMPT` da analytics-api:
# *"decidimos que nao"* e *"ainda nao sabemos"* sao fatos diferentes, e junta-los
# faria a divida herdar a tranquilidade da decisao. Aqui so ha divida, e ela e
# CONTADA no placar toda vez que o gate roda.
#
# As tres sao o ciclo de vida de SESSAO do canal de voz, e foram escritas contra
# uma API de classe-base que **nunca existiu** — nao ha `_open_session` nem
# `_route_inbound` em adapter nenhum do pacote (`webrtc.py` tem um
# `_close_session` proprio, e so). Consertar nao e "definir tres metodos": e
# decidir como uma chamada PSTN abre sessao na plataforma, roteia a um pool e
# fecha com a taxonomia de `contact_closed`. E metade da VOZ-03, e tem ficha.
DIVIDA = {
    ("voice.py", "VoiceAdapter", "_open_session"):  "VOZ-03 (metade restante)",
    ("voice.py", "VoiceAdapter", "_route_inbound"): "VOZ-03 (metade restante)",
    ("voice.py", "VoiceAdapter", "_close_session"): "VOZ-03 (metade restante)",
}

MUTANTE = """

class _Mutante(ChannelAdapter):
    async def roda(self):
        await self._metodo_que_nunca_existiu(1, 2)
"""


def _classes(arv):
    return [n for n in ast.walk(arv) if isinstance(n, ast.ClassDef)]


def _definidos(cls):
    """Nomes que uma chamada `self.X()` pode legitimamente encontrar nesta classe."""
    nomes = set()
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nomes.add(n.name)
        if isinstance(n, ast.Assign):
            for a in n.targets:
                if isinstance(a, ast.Name):
                    nomes.add(a.id)
    # `self.X = ...` em qualquer lugar da classe — atributo calavel conta.
    for n in ast.walk(cls):
        if isinstance(n, ast.Assign):
            for a in n.targets:
                if (isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                        and a.value.id == "self"):
                    nomes.add(a.attr)
    return nomes


def _chamadas_self(cls):
    """(nome, linha) de cada `self.NOME(...)` — chamada DIRETA, nunca `self.a.b()`."""
    saida = []
    for n in ast.walk(cls):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "self"):
            saida.append((f.attr, getattr(n, "lineno", 0)))
    return saida


def _universo(arquivos):
    """nome da classe -> nomes que ela oferece (incluindo bases do pacote)."""
    porclasse, bases = {}, {}
    for f in arquivos:
        try:
            arv = ast.parse(io.open(f, encoding="utf-8").read())
        except Exception:
            continue
        for c in _classes(arv):
            porclasse[c.name] = _definidos(c)
            bases[c.name] = [b.id for b in c.bases if isinstance(b, ast.Name)]

    def resolve(nome, vistos=None):
        vistos = vistos or set()
        if nome in vistos or nome not in porclasse:
            return set()
        vistos.add(nome)
        out = set(porclasse[nome])
        for b in bases.get(nome, []):
            out |= resolve(b, vistos)
        return out
    return porclasse, resolve


def censo(mutar=False):
    if not os.path.isdir(ADAPTERS):
        print("VEREDICTO: SEM AMOSTRA — nao achei %s" % ADAPTERS)
        return 3
    arquivos = sorted(
        os.path.join(ADAPTERS, x) for x in os.listdir(ADAPTERS) if x.endswith(".py")
    )
    # A base pode morar fora de `adapters/`.
    for extra in ("channel_capability_registry.py", "models.py"):
        p = os.path.join(BASE, extra)
        if os.path.exists(p):
            arquivos.append(p)
    if not arquivos:
        print("VEREDICTO: SEM AMOSTRA — nenhum adapter")
        return 3

    fontes = {f: io.open(f, encoding="utf-8").read() for f in arquivos}
    if mutar:
        alvo = [f for f in fontes if f.endswith("voice.py")] or [arquivos[0]]
        fontes[alvo[0]] += MUTANTE

    # universo de nomes, a partir dos fontes (possivelmente mutados)
    porclasse, bases = {}, {}
    for f, src in fontes.items():
        try:
            arv = ast.parse(src)
        except Exception:
            continue
        for c in _classes(arv):
            porclasse[c.name] = _definidos(c)
            bases[c.name] = [b.id for b in c.bases if isinstance(b, ast.Name)]

    def resolve(nome, vistos=None):
        vistos = vistos or set()
        if nome in vistos or nome not in porclasse:
            return set()
        vistos.add(nome)
        out = set(porclasse[nome])
        for b in bases.get(nome, []):
            out |= resolve(b, vistos)
        return out

    orfas, examinadas = [], 0
    for f, src in sorted(fontes.items()):
        try:
            arv = ast.parse(src)
        except Exception:
            continue
        for c in _classes(arv):
            oferece = resolve(c.name)
            # `__getattr__` faz qualquer nome resolver em runtime — nao ha orfa.
            if "__getattr__" in oferece:
                continue
            for nome, ln in _chamadas_self(c):
                examinadas += 1
                if nome not in oferece:
                    orfas.append((os.path.basename(f), c.name, nome, ln))

    print("   chamadas `self.X(...)` examinadas: %d" % examinadas)
    vistos, novas, em_divida = set(), [], 0
    for arq, cls, nome, ln in orfas:
        chave = (arq, cls, nome)
        if chave in vistos:
            continue
        vistos.add(chave)
        if chave in DIVIDA:
            em_divida += 1
            print("   divida  %-14s self.%-28s -> %s" % (arq, nome, DIVIDA[chave]))
        else:
            novas.append((arq, cls, nome, ln))
            print("   ORFA    %-14s %-22s self.%-28s linha %d" % (arq, cls, nome, ln))
    print("   orfas NOVAS: %d · em divida declarada: %d de %d"
          % (len(novas), em_divida, len(DIVIDA)))

    if mutar:
        if any(n == "_metodo_que_nunca_existiu" for _a, _c, n, _l in orfas):
            print("VEREDICTO: OK — a mutacao foi ACUSADA")
            return 0
        print("VEREDICTO: FALHA — a mutacao passou; o censo nao pega nada")
        return 1

    # Divida que SUMIU do fonte tambem reprova: a tabela nao pode envelhecer
    # como permissao para um nome que ninguem chama mais.
    fantasmas = [k for k in DIVIDA if k not in vistos]
    for arq, cls, nome in fantasmas:
        print("   FANTASMA %-14s self.%-28s — na divida e nao existe mais no fonte"
              % (arq, nome))
    if fantasmas:
        print("VEREDICTO: FALHA — a tabela de divida esta velha; limpe as linhas acima")
        return 1

    if novas:
        print("VEREDICTO: FALHA — metodo chamado e nunca definido em lugar nenhum")
        print("           do MRO, e sem ficha. Nao fica vermelho sozinho: o")
        print("           `AttributeError` cai no `except` do chamador e vira log")
        print("           de fim normal. Conserte, ou declare em `DIVIDA` com dono.")
        return 1
    if examinadas == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhuma chamada a examinar")
        return 3
    print("VEREDICTO: OK — nenhuma orfa NOVA (a divida declarada segue contada)")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "censo"
    fn = {"censo": censo, "censo-mut": lambda: censo(mutar=True)}.get(modo)
    if not fn:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
    sys.exit(fn())
