#!/usr/bin/env python3
"""_ask_when_extract — recorta as TRÊS implementações vivas de `ask_when` dos
seus arquivos REAIS, para que o probe de paridade as execute como elas embarcam.

POR QUE RECORTAR, EM VEZ DE IMPORTAR
────────────────────────────────────
Nenhuma das três é importável de fora:

  · `@plughub/schemas/dialog.ts` é o módulo canônico, mas o arquivo inteiro é
    Zod — importá-lo puxaria a dependência para dentro do gate;
  · `DialogFormRenderer.tsx` é um componente React com imports `@/...`; a função
    é privada do módulo;
  · `survey_web.py` não tem função nenhuma para importar: a implementação é
    **JavaScript dentro de uma string Python**, o script inline da página web.

Copiar os corpos PARA DENTRO do gate seria a quarta cópia — e um gate que testa a
própria cópia é verde garantido. Por isso o recorte é feito do fonte, a cada
execução: se alguém editar qualquer uma das três, é a versão editada que roda.

Falha de recorte é INCONCLUSIVO no chamador, nunca verde: função renomeada tem de
parar o gate, não passá-lo por ausência.

Uso:  python3 _ask_when_extract.py <raiz_do_repo> <dir_de_saida>
"""
import re
import sys
from pathlib import Path

# (rótulo, caminho, nomes a recortar, nome exportado, arquivo, assinatura)
#
# ⚠️ A assinatura NÃO é a mesma nas três, e isso é achado, não detalhe de recorte:
# a cópia da web é `awEval(g)` — ela FECHA sobre o `answers` da página em vez de
# recebê-lo. Duas implementações da mesma regra com aridade diferente não são
# substituíveis uma pela outra nem por acidente; o recorte envolve a de closure
# numa fábrica, para executá-la com as MESMAS entradas das outras duas.
ALVOS = [
    (
        "canonico",
        "packages/schemas/src/dialog.ts",
        ["evaluateAskWhen", "_num", "_eq"],
        "evaluateAskWhen",
        "canon.ts",
        "direta",
    ),
    (
        "console",
        "packages/platform-ui/src/modules/agent-assist/components/DialogFormRenderer.tsx",
        ["evalAskWhen", "awNum", "awEq"],
        "evalAskWhen",
        "console.ts",
        "direta",
    ),
    (
        "web",
        "packages/channel-gateway/src/plughub_channel_gateway/survey_web.py",
        ["awEval", "awNum", "awEq"],
        "awEval",
        "web.js",
        "closure",
    ),
]

# O tipo da guarda só aparece na assinatura; declará-lo aqui não é reimplementar
# comportamento — é o que permite transpilar o recorte isoladamente.
PRELUDIO_TS = (
    "type AskWhen = { field: string; op: string; value: unknown }" "\n"
    "type AnswerValue = unknown" "\n"
)

NL = chr(10)


def recorta(fonte: str, nome: str) -> str:
    """Corpo completo de `function <nome>(...)`, por casamento de chaves."""
    m = re.search(r"^[ \t]*(?:export\s+)?function\s+" + re.escape(nome) + r"\s*\(",
                  fonte, re.M)
    if not m:
        return ""
    i = fonte.index("{", m.end() - 1)
    nivel, j = 0, i
    while j < len(fonte):
        if fonte[j] == "{":
            nivel += 1
        elif fonte[j] == "}":
            nivel -= 1
            if nivel == 0:
                return fonte[m.start():j + 1]
        j += 1
    return ""


def main() -> int:
    raiz, saida = Path(sys.argv[1]), Path(sys.argv[2])
    saida.mkdir(parents=True, exist_ok=True)
    falhou = False

    for rotulo, rel, nomes, exportado, arquivo, assinatura in ALVOS:
        caminho = raiz / rel
        if not caminho.exists():
            print("FALHA " + rotulo + ": " + rel + " nao existe")
            falhou = True
            continue
        fonte = caminho.read_text(encoding="utf-8")
        partes, ausentes = [], []
        for nome in nomes:
            corpo = recorta(fonte, nome)
            if not corpo:
                ausentes.append(nome)
            else:
                partes.append(corpo)
        if ausentes:
            print("FALHA " + rotulo + ": nao achei " + ", ".join(ausentes) + " em " + rel)
            falhou = True
            continue

        corpo = (NL + NL).join(partes)
        if assinatura == "closure":
            # `answers` vem do escopo da página; a fábrica o injeta para que a
            # cópia rode com as MESMAS entradas das outras duas.
            indentado = NL.join("  " + l for l in corpo.split(NL))
            texto = ("function avalia(guarda, answers) {" + NL
                     + indentado + NL
                     + "  return " + exportado + "(guarda);" + NL
                     + "}" + NL + NL
                     + "export { avalia };" + NL)
        else:
            prel = PRELUDIO_TS if arquivo.endswith(".ts") else ""
            texto = (prel + corpo + NL + NL
                     + "export { " + exportado + " as avalia };" + NL)

        (saida / arquivo).write_text(texto, encoding="utf-8")
        linhas = sum(p.count(NL) + 1 for p in partes)
        print("OK " + rotulo + ": " + str(len(nomes)) + " funcao(oes), "
              + str(linhas) + " linhas · " + rel)

    return 1 if falhou else 0


if __name__ == "__main__":
    sys.exit(main())
