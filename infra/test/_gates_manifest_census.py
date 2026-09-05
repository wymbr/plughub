# -*- coding: utf-8 -*-
"""_gates_manifest_census.py — casa os scripts de `infra/test/` com o
`gates.manifest`, e diz de cada um em que CLASSE ele esta declarado.

POR QUE ELE EXISTE (GAT-01, 2026-09-04)
---------------------------------------
O `run_gates.sh` garante que tudo o que o manifesto CITA foi executado. Nao
garante — e nao tem como — que o manifesto cite tudo o que existe. Media-se em
2026-09-02: 37 dos 103 scripts com cara de gate estavam la, e os outros 66
ninguem rodava. O modo de falha e o mesmo que o runner foi escrito para
impedir, uma casa acima: **uma lista parece completa por ser uma lista**.

A POPULACAO E TODO `.sh` DE `infra/test/` — E ISSO E CONSEQUENCIA DE MEDICAO
---------------------------------------------------------------------------
A primeira versao cobrava so os scripts com "cara de gate", por um criterio
textual. Ele foi refutado DUAS vezes, e nas duas o erro foi um falso NEGATIVO
— a familia de erro que nao aparece em contagem nenhuma, porque o script
simplesmente deixa de ser cobrado e a lista continua parecendo completa:

  · `exit "$FAIL"` nao casava `exit [1-9]`, e por isso
    `probe_internal_service_callers.sh` — gate citado por nome no CLAUDE.md —
    ficava de fora;
  · os 35 `test_*` julgam e saem 1, mas se pronunciam com `✅`/`❌` em vez do
    vocabulario desta casa, e sumiam em bloco.

Dois sistematicos em duas tentativas dizem que o problema nao e a regex, e sim
a forma: **um criterio que decide QUEM PRECISA SER DECLARADO pode esconder
arquivo, e essa e exatamente a falha que a GAT-01 existe para fechar.** Por
isso o manifesto passou a prestar contas de TODOS os `.sh`, sem criterio no
meio: quem nao roda diz por que nao roda. Nao ha falso negativo possivel
quando a populacao e "tudo".

O criterio textual SOBREVIVEU, rebaixado a INFORMACAO: ele nao decide mais
quem entra, so ordena a fila de triagem ("destes nao-triados, N tem cara de
gate"). Um criterio errado agora custa prioridade, nunca cobertura.

    candidato := tem `exit 2`  OU  (vocabulario de veredicto E `exit` != 0)

`exit 2` e o mais especifico dos sinais: e a convencao de INCONCLUSIVO desta
casa, e so a escreve quem pensou em "nao consegui medir" nao ser "passou".
Ele e CALIBRADO contra os 44 que ja estavam no manifesto e mantem os 44 — a
tentativa que contava `$1` como argumento posicional perdia 41 deles, porque
`$1` em `awk` e referencia de CAMPO. Um criterio que exclui 93% da populacao
conhecida esta refutado, nao apertado.

Ficam de fora do inventario os arquivos que **nao sao unidade executavel pelo
runner**: `.py`/`.ts`/`.mjs` sao censos e harnesses invocados POR um `.sh`, e
declara-los faria o manifesto listar as pecas de dentro dos gates.

Saida (TSV, uma linha por script .sh):
    nome <TAB> candidato(1|0) <TAB> classe(AUTO|ASSISTIDO|ISENTO|NAOTRIADO|-)
"""
import io
import os
import re
import sys

D = os.path.dirname(os.path.abspath(__file__))
# `GATE_MANIFEST` existe para que a bateria de mutacao aponte para um manifesto
# de mentira sem tocar no de verdade — mesma variavel que o `run_gates.sh` ja le.
MAN = os.environ.get("GATE_MANIFEST") or os.path.join(D, "gates.manifest")

# Vocabulario de veredicto: as palavras com que os gates desta casa se
# pronunciam. Deliberadamente NAO inclui "OK" sozinho — `echo OK` aparece em
# script de provisionamento e alargaria o criterio ate ele nao discriminar mais.
VOCAB = re.compile(
    r"(VERMELHO|FALHOU|INCONCLUSIVO|VEREDICTO|RESULTADO:|TUDO VERDE|\bVERDE\b)")
# `exit 1` e `exit "$FAIL"` sao a MESMA intencao. A primeira versao so via a
# literal e por isso perdia `probe_internal_service_callers.sh` — um gate que
# o proprio CLAUDE.md cita por nome, e que termina em `exit "$FAIL"`. Falso
# NEGATIVO de criterio nao aparece em contagem nenhuma: o script simplesmente
# nao e cobrado, e a lista continua parecendo completa.
EXIT_NAO_ZERO = re.compile(r"exit\s+(?:[1-9]|\"?\$)")
EXIT_DOIS = re.compile(r"exit\s+2\b")

CLASSES = ("AUTO", "ASSISTIDO", "ISENTO", "NAOTRIADO")


def ler(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def sem_comentario(t):
    return "\n".join(l for l in t.split("\n") if not l.strip().startswith("#"))


def e_candidato(texto):
    corpo = sem_comentario(texto)
    return bool(EXIT_DOIS.search(corpo)
                or (VOCAB.search(texto) and EXIT_NAO_ZERO.search(corpo)))


def parse_manifesto(caminho=MAN):
    """-> {nome: (classe, resto)}. Nome repetido e devolvido como conflito."""
    decl, conflitos = {}, []
    for linha in ler(caminho).split("\n"):
        linha = linha.split("#")[0].strip()
        if not linha:
            continue
        if linha.startswith("!"):
            classe, resto = "ASSISTIDO", linha[1:].strip()
        elif linha.startswith("="):
            classe, resto = "ISENTO", linha[1:].strip()
        elif linha.startswith("?"):
            classe, resto = "NAOTRIADO", linha[1:].strip()
        else:
            # linha AUTO pode levar prefixo de env: `VAR=v script.sh`
            classe, resto = "AUTO", linha
        partes = resto.split()
        nome = partes[-1] if classe == "AUTO" else partes[0]
        cauda = " ".join(partes[1:]) if classe != "AUTO" else ""
        if nome in decl:
            conflitos.append(nome)
        decl[nome] = (classe, cauda)
    return decl, conflitos


def main():
    decl, conflitos = parse_manifesto()
    scripts = sorted(f for f in os.listdir(D) if f.endswith(".sh"))
    for f in scripts:
        cand = e_candidato(ler(os.path.join(D, f)))
        classe = decl.get(f, ("-", ""))[0]
        sys.stdout.write("%s\t%d\t%s\n" % (f, 1 if cand else 0, classe))
    # citados pelo manifesto que nao existem em disco
    for nome in sorted(set(decl) - set(scripts)):
        sys.stdout.write("%s\t0\t%s:AUSENTE\n" % (nome, decl[nome][0]))
    for nome in sorted(set(conflitos)):
        sys.stdout.write("%s\t0\tCONFLITO\n" % nome)


if __name__ == "__main__":
    main()
