# -*- coding: utf-8 -*-
"""_declared_script_texts.py — extrai os TEXTOS de roteiro das formas publicadas.

Le o JSON de uma forma (`GET /v1/dialog/forms/{id}`) no stdin e imprime um JSON
`[{form_id, node_id, texto}, ...]` no stdout.

⚠️ **Anda a arvore inteira, nao um punhado de campos nomeados.** Uma lista de campos
(`text`, `prompt`, `label`) envelhece no primeiro campo novo do schema, e o que faltasse
sairia do censo em SILENCIO — a familia "uma lista parece completa por ser uma lista"
que o CLAUDE.md cataloga. Aqui a regra e por FORMA do dado: toda folha string de tamanho
util e texto candidato, e o `node_id` e o ancestral mais proximo que tiver `id`.
"""
import json
import sys

MIN = 4  # abaixo disso nao ha frase; `id`, `pt-BR` e afins nao sao roteiro


def anda(no, node_id, saida):
    if isinstance(no, dict):
        prox = no.get("id") if isinstance(no.get("id"), str) else node_id
        for k, v in no.items():
            if k == "id":
                continue
            anda(v, prox, saida)
    elif isinstance(no, list):
        for item in no:
            anda(item, node_id, saida)
    elif isinstance(no, str) and len(no) >= MIN:
        saida.append((node_id, no))


def main():
    try:
        doc = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("JSON_INVALIDO %s\n" % e)
        return 2
    form_id = doc.get("form_id") or doc.get("id") or "?"
    pares = []
    anda(doc.get("form") or doc, None, pares)
    vistos = set()
    fora = []
    for node_id, texto in pares:
        chave = (node_id, texto)
        if chave in vistos:
            continue
        vistos.add(chave)
        fora.append({"form_id": form_id, "node_id": node_id or "?", "texto": texto})
    sys.stdout.write(json.dumps(fora, ensure_ascii=False))
    return 0


sys.exit(main())
