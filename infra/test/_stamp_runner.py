# -*- coding: utf-8 -*-
"""Runner Python da fixture de paridade do carimbo — ALW-02 passo 2.

Lê `fixtures/context_stamp_cases.json`, roda `stamp_context_entry` em cada caso e imprime
uma linha JSON por caso, com as chaves ORDENADAS.

Chaves ordenadas de propósito: a ordem de inserção não faz parte do contrato (ninguém
compara bytes de uma entrada do Redis — todo leitor desserializa), então compará-la
transformaria uma diferença cosmética num vermelho que ensina a ignorar o gate. O que o
gate mede é CONTEÚDO.

Par: `_stamp_runner.ts`. Os dois têm de imprimir exatamente as mesmas linhas.
"""
import io
import json
import os
import sys

# ⚠️ Bancada, nao contrato: o `print()` do python de WINDOWS traduz LF para CRLF em modo
# texto (o `CLAUDE.md` ja cataloga o gemeo disto para ESCRITA DE ARQUIVO). A metade TS roda
# em container Linux e emite LF, entao sem isto as duas saidas divergem em TODAS as linhas
# por um byte e a divergencia real fica enterrada no ruido — foi o que aconteceu na primeira
# execucao deste gate, e eu normalizei a metade ERRADA por ter presumido a causa em vez de
# medi-la.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(newline="\n")  # type: ignore[union-attr]

_AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(_AQUI))

sys.path.insert(0, os.path.join(RAIZ, "packages", "py-contextstore", "src"))
from plughub_contextstore import build_context_tag_index, stamp_context_entry  # noqa: E402

#: Caminho por ARGUMENTO, com default co-localizado. O gate passa explicitamente porque a
#: metade TS roda BUNDLADA e o `__dirname` dela vira o diretório do bundle, não o do fonte
#: — resolver isso por caminho relativo seria consertar de um lado só.
FIXTURE = (
    sys.argv[1] if len(sys.argv) > 1
    else os.path.join(_AQUI, "fixtures", "context_stamp_cases.json")
)

#: A entrada é fixa e irrelevante ao carimbo. Ela existe para a testemunha de PASSAGEM —
#: "o carimbo não toca nos campos do escritor" —, que é medida DENTRO de cada runner e
#: reportada como booleano.
#:
#: ⚠️ Por que booleano, e não a entrada inteira na comparação: a primeira versão deste gate
#: emitia o objeto completo e acusou divergência nos 15 casos — `"confidence":1.0` em Python
#: contra `"confidence":1` em JS, que não tem inteiro e float separados. Os 15 `atributo`
#: eram byte a byte iguais. Era o INSTRUMENTO divergindo, não o produto, e a lição é a de
#: sempre: duas proposições numa comparação só, e a barulhenta esconde a que importa.
#: Comparar representação de número não é o contrato; comparar o carimbo é.
ENTRADA = {
    "value": "123.456.789-00",
    "confidence": 1.0,
    "source": "parity",
    "visibility": "agents_only",
    "updated_at": "2026-09-02T00:00:00.000Z",
}


def main() -> int:
    fx = json.load(io.open(FIXTURE, encoding="utf-8"))
    indices = {nome: build_context_tag_index(m) for nome, m in fx["maps"].items()}

    for caso in fx["cases"]:
        saida = stamp_context_entry(
            ENTRADA, caso["tag"], indices[caso["map"]], bool(caso["fallback"]),
        )
        # Igualdade NATIVA, dentro do runner — nunca atravessa JSON.
        passagem = all(saida.get(k) == v for k, v in ENTRADA.items())
        # E a entrada recebida não pode ter sido mutada.
        intacta = "atributo" not in ENTRADA
        print(json.dumps(
            {
                "name": caso["name"],
                "atributo": saida["atributo"],
                "passagem_ok": bool(passagem),
                "entrada_intacta": bool(intacta),
            },
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
