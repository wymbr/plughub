# -*- coding: utf-8 -*-
"""Ramo I do `probe_ctx_read_audience.sh` — a SEGUNDA casa de interpolação.

⚠️ **`steps/suspend.ts` não usa `interpolate()`.** Ele tem um `_interpolate` PRÓPRIO
(`suspend.ts:274`), que resolve `{{resume_token}}`, `{{$.pipeline_state.*}}` e
`{{$.session.*}}` por conta própria — e, por não passar pelo `interpolate`, **não vê o
filtro de plateia da CTX-04**. O `visibility` default dele é `agents_only` (plateia
OPERADOR), mas o campo é declarável: `visibility: all` faz a plateia virar CLIENTE, e ali
não há filtro nenhum.

── Por que isto é um RAMO e não um conserto ────────────────────────────────────

Medido em 2026-09-07: o parque tem **5 steps `suspend` e ZERO declaram `notify`**. Não há
população. Construir o filtro agora seria política contra população zero — o erro que
este repositório já registrou duas vezes (a recusa por escopo da AUT-01, o ramo legado da
evaluation-api). O que fica é o FATO, contado, com reprovação no dia em que deixar de ser
zero — a mesma forma do ramo G da CTX-05, que guarda a plateia `model` no `reason`.

⚠️ **O que este ramo NÃO afirma:** que o `_interpolate` do suspend é seguro. Ele afirma
que ninguém o exercita com plateia de cliente. São duas proposições, e só a segunda é
mensurável hoje.

── Por que AST/estrutura e não `grep` ──────────────────────────────────────────

`notify:` aparece em três lugares diferentes de um YAML de skill (o step `type: notify`,
o `notify` de um `suspend`, e `on_notify` de outros steps). Contar por texto acusaria
inocentes — a mesma razão que fez o censo de fechamento de contato ser AST.

EXIT: 0 população ZERO (ou toda ela é `agents_only`) · 1 há `suspend.notify` ao CLIENTE
      · 3 sem amostra
"""
import io
import os
import sys

try:
    import yaml
except Exception:  # pragma: no cover
    print("   SEM AMOSTRA — PyYAML ausente; o censo estrutural não roda")
    sys.exit(3)

RAIZ = "packages/skill-flow-engine/skills"


def _steps(no):
    """Percorre a árvore e devolve todo dict que pareça um step."""
    if isinstance(no, dict):
        if "type" in no and isinstance(no.get("type"), str):
            yield no
        for v in no.values():
            yield from _steps(v)
    elif isinstance(no, list):
        for v in no:
            yield from _steps(v)


def main() -> int:
    if not os.path.isdir(RAIZ):
        print("   SEM AMOSTRA — não achei %s" % RAIZ)
        return 3

    arquivos = sorted(f for f in os.listdir(RAIZ) if f.endswith((".yaml", ".yml")))
    if not arquivos:
        print("   SEM AMOSTRA — nenhum skill")
        return 3

    total_suspend = 0
    com_notify = []
    for nome in arquivos:
        try:
            doc = yaml.safe_load(io.open(os.path.join(RAIZ, nome), encoding="utf-8"))
        except Exception as exc:
            print("   ⚠️ %s não parseou (%s) — fora do censo" % (nome, exc))
            continue
        for st in _steps(doc):
            if st.get("type") != "suspend":
                continue
            total_suspend += 1
            notify = st.get("notify")
            if not isinstance(notify, dict):
                continue
            vis = notify.get("visibility", "agents_only")
            com_notify.append((nome, st.get("id", "?"), vis,
                               str(notify.get("text", ""))[:60]))

    print("   steps `suspend` no parque: %d" % total_suspend)
    print("   com `notify` declarado ...: %d" % len(com_notify))
    ao_cliente = [c for c in com_notify if c[2] != "agents_only"]
    for nome, sid, vis, texto in com_notify:
        marca = "CLIENTE" if vis != "agents_only" else "operador"
        print("   %-8s %-34s %-22s visibility=%s" % (marca, nome, sid, vis))

    if ao_cliente:
        print("   VEREDICTO: FALHA — %d `suspend.notify` com plateia de CLIENTE."
              % len(ao_cliente))
        print("              O `_interpolate` do suspend NÃO passa pelo filtro da")
        print("              CTX-04: o valor sai CRU e nada fica vermelho. A")
        print("              população deixou de ser zero — agora o filtro precisa")
        print("              existir, ou o `notify` do suspend precisa sair.")
        return 1

    if total_suspend == 0:
        print("   SEM AMOSTRA — nenhum step `suspend` para julgar")
        return 3

    print("   VEREDICTO: OK — nenhum `suspend.notify` alcança o cliente")
    print("              (a segunda casa de interpolação segue sem população;")
    print("               isto CONTA o fato, não afirma que ela é segura)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
