"""
grants.py — o guard de RANK: ninguem concede o que nao detem.

MOD-02 / fase G1 do `docs/adr/adr-abac-module-granularity-and-delegation.md`
(emenda de 2026-09-08, decisao E2).

O QUE ELE DECIDE
----------------
Dois regimes, sobre os DOIS campos que ja existiam — nenhum campo novo:

  master    `config.permissions: read_write`  -> concede qualquer campo, qualquer nivel
  delegado  `config.users: read_write`        -> concede `<=` o que detem, campo a campo,
                                                 com escopo contido no proprio

O delegado e o que fecha a Costura 1 do ADR: ate aqui `_assert_may_grant` RECUSAVA
qualquer campo de capacidade a quem nao tivesse `config.permissions`, entao o
supervisor criava usuario e nao conseguia dar-lhe papel nem pool — o contratado
nascia enxergando nada e quem o contratou nao podia corrigir.

POR QUE RANK, E NAO PRESENCA NEM HIERARQUIA
-------------------------------------------
Duas alternativas foram medidas e caem, as duas em 2026-09-08:

  · HIERARQUIA de papeis (`admin > developer > supervisor > operator`): refutada pelo
    catalogo — nenhum par adjacente estava ordenado. `operator` tem
    `evaluation.contestar` que `supervisor` nao tinha, e de proposito (o operador
    contesta a propria avaliacao; o supervisor revisa). Papeis sao FUNCOES, nao
    niveis. Pior: o rotulo nao sabe o que tem dentro — editar o template "supervisor"
    para incluir `config.permissions` o mantem rotulado supervisor.

  · PRESENCA (deter o modulo em qualquer nivel autoriza conceder em qualquer nivel):
    vira maquina de subir de nivel. O delegado cria um usuario, concede-lhe
    `read_write` num campo que ele so tem em `read_only`, define a senha
    (`CreateUserRequest.password` e escolhida por quem cria) e entra na conta.

RANK nao tem essa saida: a procuracao NUNCA excede o outorgante em campo nenhum,
entao criar-conceder-logar nao ganha nada. E por isso o corte da personificacao
deixou de ser pre-requisito deste desenho (segue devido por higiene: o vetor existe
contra qualquer alvo nao-privilegiado, e e anterior a esta mudanca).

⚠️ O QUE ESTE MODULO **NAO** RESOLVE
------------------------------------
PROPAGACAO. Um delegado com `config.users: read_write` pode conceder
`config.users: read_write` a outro (rank igual). Nao e escalacao — ninguem passa do
proprio teto, e o conjunto de capacidades do tenant nunca cresce, so o numero de
portadores. Quem torna isso visivel e o CENSO (`probe_config_permissions_census.sh`).
`config.permissions` esta protegido sem excecao nenhuma: quem nao o detem tem rank 0
nele, logo nao pode conceder — o guard cobre a maquina de conceder por construcao,
sem lista mantida a mao.

AS TRES PRECISOES QUE A IMPLEMENTACAO NAO PODE PERDER
-----------------------------------------------------
1. O guard roda sobre o EFEITO, nunca sobre o literal. `roles` expande em preset
   (`router.py` chama `apply_role_preset` na criacao), entao conceder
   `roles: ["admin"]` concede os 44 campos pela porta do preset. Um guard que
   inspecionasse so `module_config` deixaria isso passar inteiro.

2. `scope` e `accessible_pools` tem semantica OPOSTA para lista vazia, e os dois
   viajam no mesmo formulario:

       campo                     []           nao-vazio
       ------------------------  -----------  ---------------------------
       module_config[..].scope   GLOBAL       recorte (abac_can, ramo 1)
       accessible_pools          NENHUM pool  o escopo (AUT-03, 2026-08-31)

   Copiar a logica de um para o outro inverte a regra sem erro em lugar nenhum.

3. As DUAS portas usam este mesmo predicado. O `PUT /users/{id}/module-config` nao
   comparava nada com o config do chamador; guard so na rota nova seria "duas portas
   para o mesmo dado, e so uma trancada", que este repositorio ja pagou duas vezes.
"""
from __future__ import annotations

from typing import Any

from plughub_authz import ACCESS_RANK

# Quem detem isto e MASTER: concede qualquer coisa, sem passar pelo guard.
CAMPO_MASTER = ("config", "permissions")


def _rank(acesso: Any) -> int:
    """Rank do valor, com desconhecido valendo 0.

    ⚠️ Desconhecido = 0 e seguro AQUI e so aqui: do lado do CHAMADOR ele nega (nao
    detem), e do lado do PRETENDIDO ele tambem nega (nao pode conceder o que nao se
    sabe medir). Nas duas pontas o default recusa — que e o oposto do `.get(x, 0)`
    da "divergencia 4", onde um `min_access` digitado errado LIBERAVA tudo.
    """
    return ACCESS_RANK.get(acesso if isinstance(acesso, str) else "", 0)


def _acesso_do(config: dict[str, Any], modulo: str, campo: str) -> str:
    entrada = ((config or {}).get(modulo) or {}).get(campo)
    if not isinstance(entrada, dict):
        return "none"
    acesso = entrada.get("access")
    return acesso if isinstance(acesso, str) else "none"


def _escopo_do(config: dict[str, Any], modulo: str, campo: str) -> list[str]:
    entrada = ((config or {}).get(modulo) or {}).get(campo)
    if not isinstance(entrada, dict):
        return []
    escopo = entrada.get("scope")
    return escopo if isinstance(escopo, list) else []


def _norm_pool(v: str) -> str:
    """`pool:x` e `x` sao a mesma coisa — mesma normalizacao do `abac_can`."""
    return v[5:] if isinstance(v, str) and v.startswith("pool:") else v


def e_master(claims: dict[str, Any]) -> bool:
    mc = (claims or {}).get("module_config") or {}
    return _rank(_acesso_do(mc, *CAMPO_MASTER)) >= ACCESS_RANK["read_write"]


def config_efetivo(
    module_config: dict[str, Any] | None,
    roles: list[str] | None,
    presets: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """`module_config` UNIAO o preset dos papeis — o EFEITO, nao o literal.

    Sem esta uniao, `{"roles": ["admin"], "module_config": {}}` atravessa o guard
    intacto e concede os 44 campos pela porta do preset. `presets` e
    `{papel: {modulo: {campo: {access, scope}}}}`, montado por
    `presets.build_module_config` — uma implementacao, nao duas.
    """
    saida: dict[str, dict[str, dict[str, Any]]] = {}
    for papel in roles or []:
        for modulo, campos in (presets or {}).get(papel, {}).items():
            for campo, entrada in campos.items():
                atual = saida.setdefault(modulo, {}).get(campo)
                if atual is None or _rank(entrada.get("access")) > _rank(atual.get("access")):
                    saida.setdefault(modulo, {})[campo] = dict(entrada)
    for modulo, campos in (module_config or {}).items():
        if not isinstance(campos, dict):
            continue
        for campo, entrada in campos.items():
            if isinstance(entrada, dict):
                saida.setdefault(modulo, {})[campo] = dict(entrada)
    return saida


def violacoes(
    claims: dict[str, Any],
    *,
    module_config: dict[str, Any] | None = None,
    roles: list[str] | None = None,
    accessible_pools: list[str] | None = None,
    presets: dict[str, dict[str, dict[str, Any]]] | None = None,
    atual: dict[str, Any] | None = None,
) -> list[str]:
    """O que nesta concessao excede o chamador. Lista vazia = pode conceder.

    Cada violacao NOMEIA o campo e os dois lados — recusa que so diz "forbidden"
    manda o operador adivinhar qual dos 44 campos o barrou.

    ⚠️ `atual` = o config que o ALVO ja tem, e ele existe por um modo de falha
    concreto: `PUT /users/{id}/module-config` SUBSTITUI o config inteiro, entao o
    formulario reenvia tambem os campos que ninguem tocou. Sem comparar com o atual,
    um delegado ficaria impedido de editar qualquer pessoa que tenha UM grant acima
    do dele — inclusive para mexer num campo que ele alcanca — e o sintoma seria
    "nao consigo salvar", sem dizer que a culpa e de um campo que ele nem viu.
    O que se julga e o AUMENTO: campo que ja estava naquele nivel nao esta sendo
    concedido agora. Manter o que existe nunca eleva ninguem; e quem tenta MEXER
    para cima continua barrado.
    """
    if e_master(claims):
        return []

    meu = (claims or {}).get("module_config") or {}
    fora: list[str] = []

    pretendido = config_efetivo(module_config, roles, presets)
    for modulo, campos in pretendido.items():
        for campo, entrada in campos.items():
            quer = entrada.get("access", "none")
            if _rank(quer) == 0:
                continue  # conceder `none` nao concede nada
            if atual is not None and _rank(quer) <= _rank(_acesso_do(atual, modulo, campo)):
                continue  # ja estava neste nivel: manter nao e conceder
            tenho = _acesso_do(meu, modulo, campo)
            if _rank(quer) > _rank(tenho):
                fora.append(
                    f"{modulo}.{campo}: concederia '{quer}', voce tem '{tenho}'"
                )
                continue
            # Escopo do GRANT: `[]` = GLOBAL. Conceder global exigindo recorte seria
            # dar alcance maior que o proprio; o inverso (recorte dentro do meu) passa.
            meu_escopo = {_norm_pool(x) for x in _escopo_do(meu, modulo, campo)}
            quer_escopo = {_norm_pool(x) for x in (entrada.get("scope") or [])}
            if meu_escopo and (not quer_escopo or not quer_escopo <= meu_escopo):
                alvo = "GLOBAL" if not quer_escopo else sorted(quer_escopo)
                fora.append(
                    f"{modulo}.{campo}: escopo {alvo} excede o seu {sorted(meu_escopo)}"
                )

    # `accessible_pools`: `[]` = NENHUM pool desde a AUT-03. Semantica OPOSTA a do
    # `scope` acima, e as duas viajam no mesmo formulario — ver o cabecalho.
    if accessible_pools is not None:
        meus_pools = {_norm_pool(x) for x in ((claims or {}).get("accessible_pools") or [])}
        quer_pools = {_norm_pool(x) for x in accessible_pools}
        excede = sorted(quer_pools - meus_pools)
        if excede:
            fora.append(
                f"accessible_pools: {excede} fora do seu escopo ({sorted(meus_pools) or 'nenhum'})"
            )

    return fora
