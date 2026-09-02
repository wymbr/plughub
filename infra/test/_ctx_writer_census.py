# -*- coding: utf-8 -*-
"""Censo dos ESCRITORES DIRETOS do ContextStore — o número que dimensiona a ALW-02.

── O CRITÉRIO, declarado (CNS-06, 2026-09-02) ────────────────────────────────

Existiam TRÊS números para a mesma pergunta e nenhum critério escrito: **12** na §1.7 do
ADR (2026-08-26), **16** numa contagem estrutural e **18** numa textual. Número sem
critério não dimensiona nada — e a ALW-02 é a maior tarefa do arco.

A pergunta da ALW-02 é: **quantos pontos precisam passar a chamar um choke point?** Daí:

  CONTA      escrita de campo num hash de ContextStore (`{tenant}:ctx:…`) feita
             DIRETAMENTE, sem passar por um helper compartilhado.
  NÃO CONTA  · teste e fixture — não vão a produção, e incluí-los infla o número com
               trabalho que não existe;
             · `hset` em OUTRO hash (instância, `menu:waiting:`, resume_tokens) — é
               outro dado, e foi contá-lo que produziu o 18;
             · LEITURA (`hget`/`hgetall`) — a ALW-02 é sobre escrita;
             · call site de helper que já centraliza: o helper é UM ponto, e é nele que
               o carimbo entra. Contar os chamadores mediria o ALCANCE do conserto, não
               o seu tamanho.

DUAS unidades, porque respondem a perguntas diferentes e confundi-las foi metade da
confusão: **arquivos** diz quantos módulos abrir; **sítios** diz quantas edições fazer.

── Três modos de falha que este instrumento já teve, e que a forma dele evita ──

1. **Marcador no RECEPTOR, não na chave.** A primeira versão casava a linha inteira, e
   `ctx.redis.hset(waitingKey, …)` passava — o `ctx` ali é o objeto de contexto do step,
   não a chave. Trouxe três falsos positivos do skill-flow-engine, que escrevem no hash
   `menu:waiting:`. **O marcador tem de estar no ARGUMENTO.**
2. **Variável não resolvida.** Metade dos escritores faz `hset(key, …)` com
   `key = f"{t}:ctx:{sid}"` linhas acima. Olhar só o argumento perdia `sentiment_emitter`,
   `copilot_emitter` e o router da evaluation-api — 4 arquivos onde o oráculo tem 9.
3. **Convenção de nome.** `ctx_key` em Python × `ctxKey` em TS; exigir uma só perdia
   `bpm.ts` inteiro. Daí o casamento case-insensitive.

── Duas imprecisões CONHECIDAS, medidas em 2026-09-02 no passo 1 da ALW-02 ───

Nenhuma das duas move o número hoje, e as duas movem se alguém mexer perto. Ficam
escritas porque uma imprecisão silenciosa num instrumento que dimensiona trabalho é a
mesma família do teste que não pode reprovar.

1. **Um falso positivo, na direção declarada.** `sentiment_emitter.py:163` escreve em
   `{tenant}:pool:{p}:sentiment_live`, não no ctx. Entra porque o índice de atribuições é
   de escopo de ARQUIVO e pega a ÚLTIMA `key =` (a `:218`, do ctx). É exatamente o erro
   para o lado de INCLUIR que o critério assume — primeiro caso confirmado. Logo: **21
   contados, 20 a rotear.**

2. **O varredor de TS não resolve variável, e por isso vê 1 dos 2 `hset` de ctx do
   funil.** `writeContextTag` tem dois — o do ramo journey usa a variável `key` (que não
   casa o marcador) e o do ramo sessão tem a chave literal. Por CRITÉRIO o helper conta
   UMA vez, então o número está certo; ele chega ao certo por sorte, não pela regra. Um
   terceiro `hset` com chave literal dentro do funil faria o gate acusar "escritor novo"
   sobre código que é o próprio choke point. Consertar exige índice de atribuições em TS,
   que hoje não se paga: o Python tem 20 dos 21 sítios.

── Instrumento × oráculo ─────────────────────────────────────────────────────

Este arquivo é o INSTRUMENTO. O ORÁCULO é a lista `ESCRITORES` de
`censo_contextstore_cadastro.py`, curada à mão para outro fim. Foram construídos por
caminhos independentes; onde discordarem, **uma das duas está errada e a divergência é o
achado** — foi comparar instrumento com oráculo que pegou a sub-contagem de aliases em
2026-08-30.
"""
import ast
import io
import os
import re
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))       # …/infra/test
RAIZ = os.path.dirname(os.path.dirname(_AQUI))           # raiz do repo
PULA = ("node_modules", ".git", "dist", "__pycache__", ".venv")

#: Marca de que a chave é do ContextStore. Case-insensitive: `ctx_key` (Python) e
#: `ctxKey` (TS) convivem, e exigir uma só perde o outro pacote inteiro.
CHAVE_CTX = re.compile(r":ctx:|ctx", re.IGNORECASE)

ORACULO = "infra/test/censo_contextstore_cadastro.py"


def _e_teste(rel: str) -> bool:
    q = rel.replace("\\", "/")
    return (
        "/tests/" in q
        or "/test_" in q
        or q.endswith("_test.py")
        or ".test.ts" in q
        or ".spec.ts" in q
        or "/e2e-tests/" in q
        or "/__tests__/" in q
    )


def _trecho(node, src: str) -> str:
    try:
        return ast.get_source_segment(src, node) or ""
    except Exception:
        return ""


def varre_python(rel: str):
    src = io.open(os.path.join(RAIZ, rel), encoding="utf-8", errors="ignore").read()
    try:
        arvore = ast.parse(src)
    except SyntaxError:
        return []

    # Índice nome → fonte da última atribuição. Escopo de ARQUIVO, não de função: é
    # aproximação DECLARADA, e erra para o lado de INCLUIR. Preferível ao inverso — o
    # número dimensiona trabalho, e omitir um escritor é o erro que custa caro.
    atribuicoes = {}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    atribuicoes[alvo.id] = _trecho(no.value, src)
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name) and no.value:
            atribuicoes[no.target.id] = _trecho(no.value, src)

    achados = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call) or not isinstance(no.func, ast.Attribute):
            continue
        if no.func.attr != "hset" or not no.args:
            continue
        chave = no.args[0]
        t = _trecho(chave, src)
        ok = bool(CHAVE_CTX.search(t))
        if not ok and isinstance(chave, ast.Name):
            ok = bool(CHAVE_CTX.search(atribuicoes.get(chave.id, "")))
        if ok:
            achados.append(no.lineno)
    return sorted(achados)


def varre_ts(rel: str):
    """Sem AST aqui — casamento por regex, e isso está declarado. Mas sobre o ARGUMENTO
    e sobre a chamada JUNTA: `redis.hset(\\n  ctxKey,` existe, e a versão linha-a-linha
    a perdia enquanto casava o `ctx.` de `ctx.redis.hset(...)`, que é o receptor."""
    s = io.open(os.path.join(RAIZ, rel), encoding="utf-8", errors="ignore").read()
    achados = []
    for m in re.finditer(r"\.hset\s*\(", s):
        cabeca = s[m.end():m.end() + 160].split(",")[0]
        if CHAVE_CTX.search(cabeca):
            achados.append(s[:m.start()].count("\n") + 1)
    return sorted(achados)


def censo():
    por_arquivo = {}
    for dp, dn, fn in os.walk(RAIZ):
        if any(x in dp.replace("\\", "/") for x in PULA):
            continue
        for f in fn:
            rel = os.path.relpath(os.path.join(dp, f), RAIZ).replace("\\", "/")
            if not rel.startswith("packages/") or _e_teste(rel):
                continue
            if f.endswith(".py"):
                linhas = varre_python(rel)
            elif f.endswith(".ts"):
                linhas = varre_ts(rel)
            else:
                continue
            if linhas:
                por_arquivo[rel] = linhas
    return por_arquivo


def le_oraculo():
    src = io.open(os.path.join(RAIZ, ORACULO), encoding="utf-8").read()
    bloco = src[src.index("ESCRITORES = ["):]
    bloco = bloco[: bloco.index("]") + 1]
    return set(re.findall(r'"([^"]+)"', bloco))


def main() -> int:
    por_arquivo = censo()
    sitios = sum(len(v) for v in por_arquivo.values())
    print("ESCRITORES DIRETOS DO CONTEXTSTORE — critério no cabeçalho deste arquivo")
    # Linha legível por MÁQUINA, sem acento de propósito: o gate a extrai por `sed`, e
    # casar acento no shell depende de locale — que é a família de defeito que este
    # próprio arquivo documenta (a bancada decidindo o veredicto em vez do dado).
    print(f"RESUMO arquivos={len(por_arquivo)} sitios={sitios}")
    print(f"  arquivos: {len(por_arquivo)}   sítios: {sitios}")
    for rel in sorted(por_arquivo):
        print(f"    {len(por_arquivo[rel]):3d}  {rel}  {por_arquivo[rel]}")

    oraculo = le_oraculo()
    so_inst = sorted(set(por_arquivo) - oraculo)
    so_orac = sorted(oraculo - set(por_arquivo))
    print(f"\n  ORÁCULO ({ORACULO}): {len(oraculo)} arquivos")
    print(f"  só no INSTRUMENTO: {len(so_inst)}")
    for r in so_inst:
        print(f"     + {r}")
    print(f"  só no ORÁCULO:     {len(so_orac)}")
    for r in so_orac:
        print(f"     - {r}")
    if not so_inst and not so_orac:
        print("  CONCORDAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
