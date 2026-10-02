# -*- coding: utf-8 -*-
"""_ledger_stall_audit.py — as duas formas de o ledger PARAR sem ficar vermelho.

Auxiliar dos ramos G e H de `probe_task_ledger.sh`. Vive separado porque a
pergunta e por-CELULA (qual coluna e o status? o bloqueador e um id?), e em
`grep` isso vira heuristica sobre uma linha que tem `|` no meio do texto.

  ramo G — BLOQUEIO MORTO. Uma ficha `bloqueado por XXX-00` cujo XXX-00 ja
    esta em `done.md`. E a unica das duas com populacao medida: quando este
    ramo nasceu (2026-09-07) havia UMA, a `MOD-07`, bloqueada por uma `AUT-01`
    fechada sete dias antes. Ninguem mente aqui — o bloqueio simplesmente
    sobrevive ao bloqueador, e nada no par de arquivos confere isso.

  ramo H — ADIAMENTO SEM VOLTA. `adiado`/`bloqueado` sem NENHUMA condicao de
    retorno escrita. Populacao hoje: ZERO (38 de 38 declaram) — e por isso ele
    e GUARDA, nao limpeza. Um `adiado` sem gatilho e o INCONCLUSIVO permanente
    do ledger: indistinguivel de abandono, e sem nada que possa ficar vermelho.

⚠️ O ramo H le a FICHA INTEIRA, e isso e correcao de um erro cometido ao
   desenha-lo. A primeira medicao leu so a coluna de status e acusou ONZE
   fichas sem gatilho; a condicao estava escrita em TODAS, so que espalhada:
   18 na coluna de status, 6 na coluna de ancora, 6 no corpo da tarefa. Censo
   desenhado para uma coluna nao prova nada sobre a coluna vizinha — o mesmo
   defeito que a § Security ja cataloga por eixo, aqui por celula. Normalizar
   a coluna seria exigir disciplina; ler a linha inteira remove a exigencia.

⚠️ E o que o ramo H NAO faz: julgar a QUALIDADE do gatilho. Ele reprova a
   ausencia total de condicao, que e verificavel; "este gatilho e bom?" nao e.
   Vocabulario deliberadamente largo — falso NEGATIVO aqui e barato (deixa
   passar um gatilho ruim), falso POSITIVO seria caro (ensina a ignorar o gate).

  ramo I — TABELA PARTIDA. Toda ficha tem exatamente TRES celulas depois do id
    (tarefa, status, ancora), contando so o `|` SEM escape, e fecha com `|`. Nasceu em
    2026-10-02: 14 fichas tinham `|` cru dentro de codigo (`a|b`, `otp` | `none`), o que
    parte a celula no Markdown e desliza o status para a coluna errada na leitura; e a
    NIV-06 tinha perdido o `|` final e o fim do texto numa edicao, sem nada acusar. Os
    ramos G e H nao viam: leem o status como a PENULTIMA celula, que o deslize preserva.

SAIDA: 0 = nada encontrado · 1 = achado (vermelho) · 2 = INCONCLUSIVO
"""
import io
import re
import sys

LINHA = re.compile(r"^\|\s*([A-Z]{3}-\d{2})\s*\|(.*)$")
REFID = re.compile(r"\b([A-Z]{3}-\d{2})\b")
NAO_ABERTA = re.compile(r"adiado|bloquead", re.I)

# Vocabulario de CONDICAO DE RETORNO, extraido das 38 fichas que a declaram.
# Largo de proposito: ver a nota do cabecalho.
COND = re.compile(
    r"gatilho"
    r"|pr[ée]-?requisito"
    r"|depende d"
    r"|bloquead[ao]\W{0,3}por"
    r"|falta [ao] "
    r"|precisa d"
    r"|ainda n[ãa]o existe"
    r"|assim que"
    r"|antes de"
    r"|quando .{0,40}(existir|houver|virar|ganhar)",
    re.I,
)


def celulas(corpo):
    """Colunas da ficha, sem a barra final vazia. [tarefa..., status, ancora]"""
    c = [x.strip() for x in corpo.split("|")]
    while c and not c[-1]:
        c.pop()
    return c


def celulas_md(linha):
    """Celulas como o Markdown as ve: so `|` sem escape separa. Sem o id."""
    c = re.split(r"(?<!\\)\|", linha.rstrip("\n"))[1:-1]
    return c[1:]


def ramo_i():
    achados, total = [], 0
    for path in ("pending.md", "done.md"):
        try:
            texto = io.open(path, encoding="utf-8").read()
        except OSError as e:
            print("INCONCLUSIVO: nao consegui ler %s (%s)" % (path, e))
            return 2
        for n, l in enumerate(texto.split("\n"), 1):
            m = LINHA.match(l)
            if not m:
                continue
            total += 1
            c = celulas_md(l)
            if not l.rstrip().endswith("|"):
                achados.append("%s:%d %s nao fecha com `|` (a linha foi cortada?)" % (path, n, m.group(1)))
            elif len(c) != 3:
                achados.append("%s:%d %s tem %d celulas, nao 3 — `|` cru no texto? escape como `\\|`"
                               % (path, n, m.group(1), len(c)))
    if not total:
        print("INCONCLUSIVO: nenhuma ficha reconhecida")
        return 2
    print("populacao: %d fichas nos dois arquivos" % total)
    for a in achados:
        print("   " + a)
    return 1 if achados else 0


def carrega(path):
    itens = []
    try:
        texto = io.open(path, encoding="utf-8").read()
    except OSError as e:
        print("INCONCLUSIVO: nao consegui ler %s (%s)" % (path, e))
        sys.exit(2)
    for l in texto.split("\n"):
        m = LINHA.match(l)
        if m:
            itens.append({"id": m.group(1), "corpo": m.group(2)})
    return itens


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("G", "H", "I"):
        print("INCONCLUSIVO: uso: %s G|H|I" % sys.argv[0])
        return 2

    ramo = sys.argv[1]
    if ramo == "I":
        return ramo_i()
    pend = carrega("pending.md")
    done = carrega("done.md")
    if not pend:
        print("INCONCLUSIVO: nenhuma ficha reconhecida em pending.md")
        return 2
    ids_done = {i["id"] for i in done}

    nao_abertas = []
    for i in pend:
        c = celulas(i["corpo"])
        st = c[-2] if len(c) >= 2 else ""
        if NAO_ABERTA.search(st):
            i["status"] = st
            nao_abertas.append(i)

    # Zero nao-abertas nao e verde: e amostra ausente. Ver § Postura.
    if not nao_abertas:
        print("SEM AMOSTRA: nenhuma ficha `adiado`/`bloqueado` em %d abertas" % len(pend))
        return 2

    achados = []
    if ramo == "G":
        for i in nao_abertas:
            for r in sorted(set(REFID.findall(i["status"])) - {i["id"]}):
                if r in ids_done:
                    achados.append("%-8s bloqueada por %s, que esta em done.md" % (i["id"], r))
    else:
        for i in nao_abertas:
            if not COND.search(i["corpo"]):
                achados.append("%-8s %s — sem condicao de retorno na ficha inteira"
                               % (i["id"], i["status"][:40]))

    print("populacao: %d nao-abertas em %d fichas (done.md: %d)"
          % (len(nao_abertas), len(pend), len(ids_done)))
    if achados:
        for a in achados:
            print("   " + a)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
