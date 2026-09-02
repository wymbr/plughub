# -*- coding: utf-8 -*-
"""plughub-contextstore — o GÊMEO Python das funções puras do ContextStore.

── Por que este pacote existe, e por que ele é gêmeo em vez de único ─────────────

A ALW-02 precisa de um choke point de escrita: todo `HSET` no ctx tem de passar por um
ponto que **carimbe o `atributo`** a partir do cadastro (D9.6). O funil que já existia é
TypeScript (`writeContextTag`, em `mcp-server-plughub/src/tools/journey.ts`), e **20 dos
21 sítios de escrita são Python**.

Três saídas estavam na mesa e a medição decidiu (2026-09-02):

  (a) gêmeo Python solto            duas implementações da mesma regra — o defeito que
                                    este arco inteiro persegue
  (b) Python chamando o funil TS    20 sítios de caminho quente ganhando salto de rede,
      por rede                      e o alvo seria a porta 3100, que saiu da LAN em
                                    2026-09-01 (CAP-13) por servir transporte anônimo
  (c) declaração compartilhada +    ESCOLHIDA
      duas implementações finas
      + gate comparativo

A (c) ficou barata por dois fatos que só apareceram ao MEDIR, e nenhum era óbvio:

  1. **Os dados já são neutros de linguagem.** O mapa (`masking.context_map`) viaja como
     JSON pelo config-api — não há constante a espelhar, e é por isso que este pacote
     **não contém mapa nenhum**. Só o que precisa de gêmeo é lógica pura.
  2. **A dependência já está quase toda paga.** Quatro dos cinco serviços Python já falam
     com o config-api (bridge 5 arquivos · gateway 8 · routing 10 · ai-gateway 6). O único
     que não fala, `evaluation-api`, tem **um** sítio.

── O que torna a (c) diferente da (a): o GATE ───────────────────────────────────

Sem verificação, (c) **é** (a) com melhores intenções. O que separa as duas é
`infra/test/probe_context_stamp_parity.sh`: uma fixture única alimenta as duas
implementações e as saídas são comparadas byte a byte.

É por isso que tudo aqui é **puro e sem I/O**. Função pura é a única espécie que se
cross-checa barato entre duas linguagens — e o dia em que alguém puser um `requests` aqui
dentro, o gate deixa de conseguir rodar as duas metades lado a lado.

── A ORDEM é a única coisa que um gêmeo razoável erra sem parecer errado ────────

`resolve_context_tag` consulta **canônica → alias → dinâmica → desconhecida**, e a ordem é
deliberada: consultar o alias primeiro deixa uma canônica ser sombreada por uma grafia
legada que outro nó reivindicou. O tipo resolvido seria outro, a máscara seria outra, e
nada ficaria vermelho até alguém contar a população. A fixture do gate cobre esse caso
nomeadamente.

Espelho de: `packages/schemas/src/context-map.ts`.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, MutableMapping, NamedTuple, Sequence

__all__ = [
    "CONTEXT_ROUTE_PREFIXES",
    "DEFAULT_DYNAMIC_PREFIXES",
    "ContextTagIndex",
    "ContextTagResolution",
    "build_context_tag_index",
    "resolve_context_store",
    "resolve_context_tag",
    "stamp_context_entry",
]

ContextStoreKind = Literal["session", "journey", "customer"]
ContextTagOrigin = Literal["canonical", "alias", "dynamic", "unknown"]

#: Espelha o `.default()` de `ContextMapSchema.dynamic_prefixes`. Existe como constante
#: porque `dynamic_prefixes` ausente é mapa VÁLIDO no Zod, e cair em lista vazia faria
#: toda tag `segment.*` virar `unknown`.
DEFAULT_DYNAMIC_PREFIXES: Sequence[str] = ("agent.", "segment.", "core.segment.")

#: Rotas DECLARADAS de retenção. Espelha `CONTEXT_ROUTE_PREFIXES` do schemas — a ordem
#: importa e é a de lá (o primeiro prefixo que casar vence).
#:
#: ⚠️ `customer.` NÃO está aqui, e isso é medido, não esquecimento: o nome do STORE e o
#: prefixo que roteia para ele não são a mesma string. O oráculo do mapa acusa a confusão
#: como `mismatched_retention`.
CONTEXT_ROUTE_PREFIXES: Sequence[tuple[str, ContextStoreKind]] = (
    ("insight.historico", "customer"),
    ("pricing",           "customer"),
    ("core.customer.",    "customer"),
    ("journey.",          "journey"),
    ("core.journey.",     "journey"),
)


def resolve_context_store(tag: str) -> ContextStoreKind:
    """Store (e portanto hash + TTL) de uma tag.

    **O default `session` é honesto, não omissão**: qualquer root não listado vive no hash
    da sessão com o TTL da sessão, que é o que o roteamento sempre fez para tudo que não
    fosse journey/cliente. É o que permite a CNS-02 liberar roots de tenant sem tocar em
    roteamento nenhum.
    """
    for prefix, store in CONTEXT_ROUTE_PREFIXES:
        if tag.startswith(prefix):
            return store
    return "session"


class ContextTagIndex(NamedTuple):
    """Índice de resolução, derivado do mapa. Espelha `ContextTagIndex` do schemas."""

    canonical: Mapping[str, str]        # canônica → tipo
    alias: Mapping[str, str]            # grafia legada → canônica
    dynamic_prefixes: Sequence[str]


class ContextTagResolution(NamedTuple):
    canonical: str
    origin: ContextTagOrigin
    tipo: str | None = None


def build_context_tag_index(context_map: Mapping[str, Any]) -> ContextTagIndex:
    """Constrói o índice a partir do mapa. Espelha `buildContextTagIndex` do schemas.

    ⚠️ **ESTRITO: levanta `ValueError` em mapa malformado, e isso é a decisão.** O gêmeo TS
    recebe um `ContextMap` **já validado pelo Zod** — malformado não chega lá, e quando não
    passa, `getContextMap` cai no mapa embutido e marca `fallback: true`. Se esta metade
    tolerasse folha torta, as duas divergiriam justamente no caso perigoso: o Python
    carimbaria a partir de **meio mapa** afirmando `fallback: false`, isto é, dizendo que o
    tipo é o que o tenant declarou quando metade dele foi descartada em silêncio.

    Levantar aqui, e o carregador (que faz I/O) capturar, é o mesmo desenho do Zod —
    validação na borda, uma vez, com desfecho único. A alternativa seria um segundo
    validador estrutural espelhando o `ContextMapSchema`, que é outra cópia a manter
    sincronizada e mais superfície do que o arco comprou.

    `dynamic_prefixes` ausente vira o DEFAULT do schema (`.default([...])` no Zod), não
    lista vazia: vazia faria toda tag `segment.*` cair em `unknown` e inflar a população
    que a V4 conta.
    """
    canonical: dict[str, str] = {}
    alias: dict[str, str] = {}

    contexto = context_map.get("contexto")
    if not isinstance(contexto, Mapping):
        raise ValueError("mapa sem `contexto` (ou de tipo inesperado)")

    for escopo, dominios in contexto.items():
        if not isinstance(dominios, Mapping):
            raise ValueError(f"escopo `{escopo}` não é objeto")
        for dominio, campos in dominios.items():
            if not isinstance(campos, Mapping):
                raise ValueError(f"domínio `{escopo}.{dominio}` não é objeto")
            for campo, leaf in campos.items():
                name = f"{escopo}.{dominio}.{campo}"
                if not isinstance(leaf, Mapping):
                    raise ValueError(f"folha `{name}` não é objeto")
                tipo = leaf.get("tipo")
                if not isinstance(tipo, str) or not tipo:
                    raise ValueError(f"folha `{name}` sem `tipo`")
                canonical[name] = tipo
                legado = leaf.get("legado") or []
                if not isinstance(legado, Sequence) or isinstance(legado, (str, bytes)):
                    raise ValueError(f"folha `{name}`: `legado` não é lista")
                for old in legado:
                    if not isinstance(old, str) or not old:
                        raise ValueError(f"folha `{name}`: alias inválido")
                    alias[old] = name

    prefixes = context_map.get("dynamic_prefixes", DEFAULT_DYNAMIC_PREFIXES)
    if not isinstance(prefixes, Sequence) or isinstance(prefixes, (str, bytes)):
        raise ValueError("`dynamic_prefixes` não é lista")
    if any(not isinstance(p, str) for p in prefixes):
        raise ValueError("`dynamic_prefixes` com item não-string")
    return ContextTagIndex(canonical, alias, list(prefixes))


def resolve_context_tag(tag: str, index: ContextTagIndex) -> ContextTagResolution:
    """Resolve uma tag observada **na BORDA, antes de qualquer decisão de política** (D3.1).

    ⚠️ **A ordem é canônica → alias → dinâmica → desconhecida, e é deliberada.** Consultar
    o alias primeiro deixaria uma canônica ser sombreada por uma grafia legada que outro nó
    reivindicasse — tipo diferente, máscara diferente, nada vermelho. É o único ramo em que
    uma reimplementação razoável diverge sem parecer errada, e por isso ele tem caso próprio
    na fixture do gate de paridade.
    """
    tipo = index.canonical.get(tag)
    if tipo is not None:
        return ContextTagResolution(tag, "canonical", tipo)

    canon = index.alias.get(tag)
    if canon is not None:
        return ContextTagResolution(canon, "alias", index.canonical.get(canon))

    if any(tag.startswith(p) for p in index.dynamic_prefixes):
        return ContextTagResolution(tag, "dynamic")
    return ContextTagResolution(tag, "unknown")


def stamp_context_entry(
    entry: Mapping[str, Any],
    tag: str,
    index: ContextTagIndex,
    map_is_fallback: bool,
) -> MutableMapping[str, Any]:
    """Carimba o `atributo` numa entrada do ContextStore a partir do cadastro (D9.6).

    ⚠️ **Gêmeo de `stampContextEntry`** (`packages/schemas/src/context-map.ts`), com gate
    comparativo em `infra/test/probe_context_stamp_parity.sh`. Qualquer mudança aqui move
    o gate e a outra metade junto.

    A ordem de inserção das chaves é a do gêmeo TS (`origem` → `tipo` → `canonica` →
    `fallback`) — não porque JSON tenha ordem semântica, mas porque a comparação do gate
    fica legível quando as duas saídas serializam igual, e uma divergência de ordem seria
    ruído que esconde a divergência de conteúdo.

    `atributo` AUSENTE numa entrada significa uma coisa só: **ela não passou pelo funil**.
    Por isso `dynamic` e `unknown` também carimbam, mesmo sem tipo a declarar — sem eles a
    entrada de uma tag não cadastrada ficaria byte a byte igual à de um `HSET` direto, e o
    furo que a D9.6 chama de silencioso continuaria silencioso, agora com aparência de
    cobertura.

    Nunca muta a entrada recebida: o escritor reusa o objeto num `mapping=` de N tags, e
    mutar faria a segunda herdar o carimbo da primeira.
    """
    r = resolve_context_tag(tag, index)

    atributo: dict[str, Any] = {"origem": r.origin}
    if r.tipo is not None:
        atributo["tipo"] = r.tipo
    if r.origin == "alias":
        atributo["canonica"] = r.canonical
    if map_is_fallback:
        atributo["fallback"] = True

    return {**entry, "atributo": atributo}
