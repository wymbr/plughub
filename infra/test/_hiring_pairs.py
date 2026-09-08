#!/usr/bin/env python3
"""
_hiring_pairs.py — `preset(contratado) ⊆ preset(contratante)`, par a par.

Auxiliar de `probe_hiring_pairs_subset.sh` (MOD-08 / fase G1b do
`adr-abac-module-granularity-and-delegation.md`).

O QUE ELE PROVA
---------------
Que a contratacao declarada em `hiring_pairs:` CONTINUA possivel sob o guard de RANK
da MOD-02 (`rank(concedido) <= rank(detido)`, campo a campo). Sem ele, a proxima
edicao de `role_defaults` quebra a contratacao em silencio — e o sintoma nao se
parece com "preset errado", parece "a tela nao deixa".

POR QUE ELE NAO E UMA HIERARQUIA
--------------------------------
A cadeia `admin > developer > supervisor > operator` foi proposta em 2026-09-08 e
refutada pelo proprio catalogo: nenhum par adjacente estava ordenado (`operator` tem
`evaluation.contestar` e `approvals.*` que `supervisor` nao tinha, e isso e de
proposito — o operador CONTESTA a propria avaliacao, o supervisor REVISA). Papeis sao
funcoes, nao niveis. Por isso os pares sao um GRAFO DECLARADO, e este arquivo so
confere a propriedade que cada aresta declarada exige.

O QUE ELE NAO MEDE, DE PROPOSITO
--------------------------------
**Grants VIVOS.** Ele compara declaracao com declaracao (os `role_defaults` do
catalogo). Um contratante real pode ter recebido menos que o preset — a tela revoga
depois do nascimento —, e ai a contratacao falha no runtime com o gate aqui verde.
Esse eixo e do CENSO (`probe_config_permissions_census.sh`, classe C), e junta-los
faria um gate reprovar por uso normal da tela, que e como se ensina a ignorar um
vermelho.

SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
"""
from __future__ import annotations

import sys

VERDE, VERMELHO, INCONCLUSIVO = 0, 1, 2

# stdout em UTF-8 e independente do console: sem isto, um `⊆` no relatorio
# derruba o helper por `UnicodeEncodeError` em terminal cp1252 — instrumento que
# falha por AMBIENTE, exatamente o que ele existe para nao ser.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# Tabela canonica em `plughub_authz.ACCESS_RANK`. Repetida aqui porque este helper e
# estatico (le YAML, nao importa o pacote), e a copia e conferida pelo ramo B abaixo:
# um valor de `role_defaults` fora deste dominio reprova em vez de virar rank 0 — que
# era a "divergencia 4" que fazia typo de `min_access` liberar tudo.
RANK = {"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}


def presets_por_papel(catalogo: dict) -> dict[str, dict[str, str]]:
    """`{papel: {"modulo.campo": access}}` a partir dos `role_defaults` do catalogo."""
    out: dict[str, dict[str, str]] = {}
    for m in catalogo.get("modules", []):
        mid = m.get("module_id")
        for campo, d in (m.get("permission_schema") or {}).items():
            for papel, acesso in ((d or {}).get("role_defaults") or {}).items():
                out.setdefault(papel, {})[f"{mid}.{campo}"] = acesso
    return out


def main() -> int:
    caminho = sys.argv[1] if len(sys.argv) > 1 else "infra/modules.yaml"
    try:
        import yaml  # type: ignore
        with open(caminho, encoding="utf-8") as fh:
            cat = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"INCONCLUSIVO — nao consegui ler {caminho}: {exc}")
        return INCONCLUSIVO

    pares = cat.get("hiring_pairs")
    if not pares:
        print("INCONCLUSIVO — `hiring_pairs:` ausente do catalogo. Sem pares declarados")
        print("  este gate nao mede nada, e verde por lista vazia seria o pior resultado:")
        print("  ele diria 'toda contratacao possivel' tendo conferido zero arestas.")
        return INCONCLUSIVO

    presets = presets_por_papel(cat)
    falhas = 0

    # ── ramo B: todo valor de `role_defaults` esta no dominio do rank ─────────
    fora = sorted({f"{p}:{c}={a}" for p, campos in presets.items()
                   for c, a in campos.items() if a not in RANK})
    if fora:
        print(f"B. VERMELHO — {len(fora)} preset(s) com `access` fora do dominio "
              f"conhecido: {', '.join(fora)}")
        print("   Um valor desconhecido viraria rank 0 numa comparacao ingenua, e o")
        print("   subconjunto passaria por acidente.")
        falhas += 1
    else:
        print(f"B. verde — os {sum(len(v) for v in presets.values())} presets usam so "
              f"valores do dominio do rank")

    # ── ramo A: o subconjunto, aresta a aresta ────────────────────────────────
    print("\nA. `preset(contratado)` ⊆ `preset(contratante)`, por aresta declarada:")
    arestas = 0
    for par in pares:
        contratante = par.get("contratante")
        if contratante not in presets:
            print(f"   VERMELHO — papel contratante `{contratante}` nao tem preset "
                  f"nenhum no catalogo")
            falhas += 1
            continue
        meu = presets[contratante]
        for contratado in par.get("contratados") or []:
            arestas += 1
            if contratado not in presets:
                print(f"   VERMELHO — {contratante} -> {contratado}: o contratado nao "
                      f"tem preset no catalogo (papel novo sem `role_defaults`?)")
                falhas += 1
                continue
            gaps = [(c, a, meu.get(c, "none")) for c, a in presets[contratado].items()
                    if RANK[a] > RANK.get(meu.get(c, "none"), 0)]
            if gaps:
                print(f"   VERMELHO — {contratante} -> {contratado}: {len(gaps)} campo(s) "
                      f"que o contratante NAO alcanca")
                for c, quer, tem in sorted(gaps):
                    print(f"        {c}: contratado nasce `{quer}`, contratante tem `{tem}`")
                falhas += 1
            else:
                print(f"   verde — {contratante} -> {contratado} "
                      f"({len(presets[contratado])} campo(s) cobertos)")

    if arestas == 0:
        print("   INCONCLUSIVO — nenhuma aresta conferida")
        return INCONCLUSIVO

    print(f"\n== {arestas} aresta(s) declarada(s) · {falhas} ramo(s) reprovado(s) ==")
    return VERMELHO if falhas else VERDE


if __name__ == "__main__":
    sys.exit(main())
