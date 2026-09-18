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
    atributo, nao no adapter, e fica de fora DESTE censo — o caso que importava
    (o `AttachmentStore`) tem censo proprio, modo `store`, logo abaixo;
  * o universo de nomes validos e a UNIAO de: metodos definidos na classe,
    metodos das bases (resolvidas dentro do pacote), qualquer `self.NOME = ...`
    (atributo calavel atribuido no `__init__`) e `__getattr__` declarado.

Modos:
  censo     lista as chamadas orfas; reprova se houver
  censo-mut a mutacao: injeta uma chamada a metodo inexistente e exige acusacao
  store     toda chamada ao AttachmentStore bate com o Protocol (metodo E kwargs)
  store-mut a mutacao: injeta `store()` inexistente e `commit(mime_type=)` e exige
            as duas acusacoes

⚠️ **Por que o `store` existe (VOZ-06, 2026-09-13).** O censo de cima declarava
nao ver `self.attr.metodo()`, e foi exatamente ali que TRES escritores do mesmo
store estavam quebrados ao mesmo tempo: `voice.py` chamava `self._store.store()`,
metodo que o Protocol nunca teve, e `whatsapp.py`/`email.py` chamavam `commit`
com `mime_type=` (inexistente) e sem `tenant_id` (obrigatorio). Os tres caiam num
`except Exception` e viravam uma linha de log — gravacao, midia e anexo de e-mail
nunca foram armazenados, e nada ficou vermelho. `hasattr` nao pegaria os dois
ultimos: o metodo EXISTE, o que esta errado e a assinatura.
"""
import ast
import io
import os
import sys

# Sobrescrevivel para a CONTRAPROVA: apontar para uma copia do pacote extraida de
# um commit anterior mostra se o censo teria acusado o defeito que ja existiu.
BASE = os.environ.get("PLUGHUB_CG_BASE", "packages/channel-gateway/src/plughub_channel_gateway")
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


# ══ Censo do CONTRATO do AttachmentStore ═════════════════════════════════════
#
# A pergunta e outra que a de cima: nao "o metodo existe no MRO do adapter?", e
# sim "a chamada ao STORE bate com o Protocol — nome do metodo, kwargs aceitos e
# kwargs obrigatorios?". Por isso censo separado, e nao um ramo a mais do outro.

STORE_ARQ = "attachment_store.py"
STORE_PROTOCOLO = "AttachmentStore"
STORE_CONCRETAS = ("FilesystemAttachmentStore", "S3AttachmentStore")

MUTANTE_STORE = """

class _MutanteStore:
    def __init__(self, attachment_store: AttachmentStore | None = None):
        self._store = attachment_store

    async def roda(self):
        await self._store.store(session_id="s", file_bytes=b"")
        await self._store.commit(file_id="f", data=b"", mime_type="audio/mpeg")
"""


def _assinatura(fn):
    """(posicionais alem de self, kw aceitos, kw obrigatorios, aceita **kw)."""
    a = fn.args
    pos = [p.arg for p in a.posonlyargs + a.args if p.arg != "self"]
    kw = [p.arg for p in a.kwonlyargs]
    obrig = [p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
    # posicional sem default tambem e obrigatorio, mas pode vir por nome
    n_def = len(a.defaults)
    pos_obrig = pos[: len(pos) - n_def] if n_def else list(pos)
    return {
        "pos": pos, "kw": set(pos) | set(kw), "obrig": set(obrig),
        "pos_obrig": pos_obrig, "varkw": a.kwarg is not None,
    }


def _contrato(src_store):
    """metodo -> assinatura, do Protocol; metodos so das concretas entram a parte."""
    arv = ast.parse(src_store)
    classes = {c.name: c for c in _classes(arv)}
    if STORE_PROTOCOLO not in classes:
        return None, None

    def metodos(nome):
        c = classes.get(nome)
        if c is None:
            return {}
        return {n.name: _assinatura(n) for n in c.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")}

    proto = metodos(STORE_PROTOCOLO)
    # metodo publico presente em TODAS as concretas mas fora do Protocol
    # (ex.: `ensure_schema`, chamado pelo boot sobre o tipo concreto)
    concretos = [metodos(n) for n in STORE_CONCRETAS]
    comuns = {}
    if concretos and all(concretos):
        for nome in set.intersection(*(set(m) for m in concretos)) - set(proto):
            comuns[nome] = concretos[0][nome]
    return proto, comuns


def _eh_tipo_store(anot):
    return anot is not None and "AttachmentStore" in ast.unparse(anot)


def _params(fn):
    a = fn.args
    return {p.arg: p.annotation for p in a.posonlyargs + a.args + a.kwonlyargs}


def _ligacoes(arv, globais_store):
    """Onde, neste arquivo, um nome ou `self.attr` segura o store.

    Quatro formas, todas medidas no pacote:
      * parametro de funcao anotado `AttachmentStore` (`attachment_expiry.py`);
      * `self.A = P`, com P parametro anotado `AttachmentStore` OU chamado
        `attachment_store` (o `webrtc.py` anota `Any`, com o tipo em comentario);
      * global de modulo anotado com o tipo (`main._attachment_store`);
      * nome local atribuido a partir desse global (`store = _main._attachment_store`);
      * nome LOCAL anotado com o tipo (`store: AttachmentStore | None = self._store()`) — a forma
        do gravador da VOZ-06, que recebe o store por uma funcao (o adapter o cria depois).
    """
    attrs, nomes = set(), set()
    for n in ast.walk(arv):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ps = _params(n)
            # parametro anotado com o tipo e usado direto (`store.expire_due(...)`),
            # a forma do job de expurgo — VOZ-07. Sem esta linha as chamadas dele
            # ficavam fora da populacao, e a testemunha nao as pegaria (nao levam
            # `file_id`/`session_id`).
            for p, anot in ps.items():
                if _eh_tipo_store(anot):
                    nomes.add(p)
            for m in ast.walk(n):
                if (isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name)
                        and _eh_tipo_store(m.annotation)):
                    nomes.add(m.target.id)
            for m in ast.walk(n):
                if not isinstance(m, ast.Assign) or not isinstance(m.value, ast.Name):
                    continue
                p = m.value.id
                if p not in ps or not (p == "attachment_store" or _eh_tipo_store(ps[p])):
                    continue
                for alvo in m.targets:
                    if (isinstance(alvo, ast.Attribute) and isinstance(alvo.value, ast.Name)
                            and alvo.value.id == "self"):
                        attrs.add(alvo.attr)
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            fonte = v.attr if isinstance(v, ast.Attribute) else (v.id if isinstance(v, ast.Name) else None)
            if fonte in globais_store:
                nomes.add(n.targets[0].id)
    nomes |= globais_store
    return attrs, nomes


def _globais_store(arv):
    out = set()
    for n in arv.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and _eh_tipo_store(n.annotation):
            out.add(n.target.id)
    return out


def censo_store(mutar=False):
    p_store = os.path.join(BASE, STORE_ARQ)
    if not os.path.exists(p_store):
        print("VEREDICTO: SEM AMOSTRA — nao achei %s" % p_store)
        return 3
    proto, comuns = _contrato(io.open(p_store, encoding="utf-8").read())
    if not proto:
        print("VEREDICTO: SEM AMOSTRA — Protocol `%s` nao encontrado" % STORE_PROTOCOLO)
        return 3
    print("   contrato: %s" % ", ".join(
        "%s(%s)" % (m, ",".join(sorted(s["obrig"]))) for m, s in sorted(proto.items())))

    # POPULACAO: todo .py do pacote, fora testes e o proprio store.
    arquivos = []
    for raiz, dirs, fs in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
        for f in fs:
            if f.endswith(".py") and not (raiz == BASE and f == STORE_ARQ):
                arquivos.append(os.path.join(raiz, f))
    fontes = {f: io.open(f, encoding="utf-8").read() for f in sorted(arquivos)}
    if mutar:
        alvo = [f for f in fontes if f.endswith("voice.py")] or sorted(fontes)[:1]
        fontes[alvo[0]] += MUTANTE_STORE

    arvores = {}
    for f, src in fontes.items():
        try:
            arvores[f] = ast.parse(src)
        except Exception as exc:
            print("   ⚠️ nao parseou %s: %s" % (f, exc))
    globais = set()
    for arv in arvores.values():
        globais |= _globais_store(arv)

    defeitos, examinadas, por_arquivo, suspeitas = [], 0, {}, []
    for f, arv in sorted(arvores.items()):
        attrs, nomes = _ligacoes(arv, globais)
        for n in ast.walk(arv):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            rec, metodo = n.func.value, n.func.attr
            eh_store = (
                (isinstance(rec, ast.Attribute) and isinstance(rec.value, ast.Name)
                 and rec.value.id == "self" and rec.attr in attrs)
                or (isinstance(rec, ast.Name) and rec.id in nomes)
            )
            kws = [k.arg for k in n.keywords]
            if not eh_store:
                # TESTEMUNHA do criterio de ligacao: chamada com a CARA do store
                # (`reserve`/`commit` com `file_id`/`session_id` nomeado) sobre um
                # receptor que o censo nao reconheceu. Sem isto, renomear o atributo
                # tiraria o arquivo da populacao em silencio.
                if metodo in proto and ({"file_id", "session_id"} & set(kws)):
                    suspeitas.append((os.path.relpath(f, BASE), n.lineno, ast.unparse(n.func)))
                continue
            examinadas += 1
            arq = os.path.relpath(f, BASE)
            por_arquivo[arq] = por_arquivo.get(arq, 0) + 1
            sig = proto.get(metodo) or comuns.get(metodo)
            onde = "%s:%d %s" % (arq, n.lineno, ast.unparse(n.func))
            if sig is None:
                defeitos.append("%s — metodo `%s` NAO EXISTE no Protocol" % (onde, metodo))
                continue
            if any(k is None for k in kws):
                defeitos.append("%s — `**kwargs` na chamada: assinatura inverificavel" % onde)
                continue
            if len(n.args) > len(sig["pos"]):
                defeitos.append("%s — %d posicional(is), o metodo aceita %d"
                                % (onde, len(n.args), len(sig["pos"])))
            if not sig["varkw"]:
                for k in kws:
                    if k not in sig["kw"]:
                        defeitos.append("%s — kwarg `%s` nao existe na assinatura" % (onde, k))
            dados = set(kws) | set(sig["pos"][: len(n.args)])
            for k in sorted((sig["obrig"] | set(sig["pos_obrig"])) - dados):
                defeitos.append("%s — faltou `%s`, obrigatorio" % (onde, k))

    for arq, q in sorted(por_arquivo.items()):
        print("   %-28s %d chamada(s) ao store" % (arq, q))
    print("   chamadas examinadas: %d · defeitos: %d · receptores nao reconhecidos: %d"
          % (examinadas, len(defeitos), len(suspeitas)))
    for d in defeitos:
        print("   DEFEITO  %s" % d)
    for arq, ln, txt in suspeitas:
        print("   SUSPEITA %s:%d %s — tem cara de store e o censo nao ligou o receptor" % (arq, ln, txt))

    if mutar:
        acusou_metodo = any("`store` NAO EXISTE" in d for d in defeitos)
        acusou_kwarg = any("`mime_type` nao existe" in d for d in defeitos)
        acusou_falta = any("faltou `tenant_id`" in d for d in defeitos)
        if acusou_metodo and acusou_kwarg and acusou_falta:
            print("VEREDICTO: OK — as tres formas do defeito foram ACUSADAS")
            return 0
        print("VEREDICTO: FALHA — mutacao passou (metodo=%s kwarg=%s falta=%s)"
              % (acusou_metodo, acusou_kwarg, acusou_falta))
        return 1

    if suspeitas:
        print("VEREDICTO: FALHA — o criterio de ligacao perdeu receptor(es); o arquivo")
        print("           saiu da populacao sem ninguem decidir isso")
        return 1
    if defeitos:
        print("VEREDICTO: FALHA — chamada ao AttachmentStore fora do contrato. Nao fica")
        print("           vermelho sozinho: o TypeError/AttributeError cai no `except`")
        print("           do escritor e o arquivo simplesmente nao e armazenado.")
        return 1
    if examinadas == 0:
        print("VEREDICTO: SEM AMOSTRA — nenhuma chamada ao store encontrada")
        return 3
    print("VEREDICTO: OK — toda chamada ao store bate com o Protocol")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "censo"
    fn = {"censo": censo, "censo-mut": lambda: censo(mutar=True),
          "store": censo_store, "store-mut": lambda: censo_store(mutar=True)}.get(modo)
    if not fn:
        print("modo desconhecido: %s" % modo)
        sys.exit(2)
    sys.exit(fn())
