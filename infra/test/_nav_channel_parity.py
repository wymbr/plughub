# -*- coding: utf-8 -*-
"""Ramo I do `probe_orchestrator_tree_nav` — o CANAL e o FLUXO falam a mesma lingua.

O canal (Python, `option_tree.flatten_to_sections`) decide como desenhar a arvore e
que id cada linha carrega. O fluxo (TypeScript, `optionsAtPath`) decide o que aquele
id significa. **Sao duas implementacoes em duas linguagens**, e nada as obriga a
concordar — nao ha como compartilhar codigo entre elas.

⚠️ O modo de falha e mudo dos dois lados. Se o canal emitir `sac|info_plano` em vez
de `sac.info_plano`, ou se o fluxo deixar de dividir por ponto, a linha continua
aparecendo bonita para o cliente e a projecao devolve `found: false` — a navegacao
REINICIA parecendo certa. Ninguem fica vermelho.

Este script produz a metade PYTHON: os ids que o canal poria nas linhas. A metade
TypeScript e exercida pelo gate, que resolve cada um deles com `optionsAtPath` e
exige folha.
"""
import io
import json
import os
import sys

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(RAIZ, "packages", "channel-gateway", "src"))

try:
    from plughub_channel_gateway.option_tree import flatten_to_sections, is_tree
except Exception as exc:  # pragma: no cover - o gate trata
    print("SEM_MODULO %s" % exc)
    raise SystemExit


def main() -> None:
    bruto = sys.stdin.read()
    try:
        form = json.loads(bruto)
    except Exception:
        print("FORMA_ILEGIVEL")
        return

    # A pergunta do canal e sobre o NIVEL que ele recebe, entao o recorte e o
    # `options` da pergunta com arvore — o mesmo que o `dialog_tree_level` projeta
    # para `path: []`.
    q = None
    for n in form.get("nodes") or []:
        if n.get("kind") == "question" and any(
            isinstance(o, dict) and o.get("options") for o in (n.get("options") or [])
        ):
            q = n
            break
    if q is None:
        print("SEM_ARVORE")
        return

    opts = q["options"]
    if not is_tree(opts):
        print("PY_NAO_VE_ARVORE")
        return

    secoes = flatten_to_sections(opts)
    if secoes is None:
        # Recusa e resposta legitima (fundo demais, ou acima do teto de linhas) —
        # mas entao o canal NAO desenha, e nao ha ids a conferir.
        print("PY_RECUSA_DESENHAR")
        return

    ids = [r["id"] for s in secoes for r in s["rows"]]
    print("IDS " + " ".join(ids))


if __name__ == "__main__":
    main()
