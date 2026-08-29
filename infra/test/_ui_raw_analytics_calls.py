#!/usr/bin/env python3
"""
_ui_raw_analytics_calls.py — varredura DERIVADA das chamadas do browser a analytics.

Por que existe (2026-08-28)
---------------------------
O `probe_ui_credential_coverage.sh` nasceu com inventario A MAO: quatro arquivos
listados dentro do proprio script. Ele estava VERDE no dia em que a Home e o Monitor
ficaram sem dado, porque os arquivos que quebraram (`dashboard/CardRenderer.tsx`,
`modules/service/api/hooks.ts`, `service/components/WorkflowTraceList.tsx`) nao
estavam na lista. Inventario a mao mede *"os quatro que eu conheco mandam
credencial?"* — proposicao ADJACENTE a que se queria: *"alguma chamada do browser a
endpoint gateado sai sem credencial?"*.

Esta varredura responde a segunda, e cresce sozinha quando alguem escreve um `fetch`
novo. Ela NAO decide se aquilo e defeito: so lista os candidatos e o path a testar.
Quem separa QUEBRADO (endpoint exige credencial) de EXPOSTO (ainda nao exige) e o
probe, medindo AO VIVO — ler o codigo do backend diria qual dependencia o handler
declara, nao o que o deploy responde, e e o segundo que derruba a tela.

O que conta como credencial
---------------------------
  `apiFetch(...)`                          -> anexa o Bearer do token em memoria
  `Authorization` no proprio call site      -> header montado a mao
  helper do MESMO arquivo que monta o header (`bearerHeaders()`, `_authHeaders()`, …)
     -> resolvido por LEITURA, nao por lista de nomes: uma lista envelheceria e o
        proximo helper entraria como falso positivo.

`EventSource` fica FORA de proposito: nao aceita header, e o token dele vai por
`?token=` — regra diferente, medida em outro lugar.

LIMITE CONHECIDO, declarado e nao escondido
-------------------------------------------
So e decidivel aqui o `fetch` cujo 1o argumento e um LITERAL — ou um IDENTIFICADOR que
esta varredura consegue resolver (ver abaixo). `fetch(montarUrl())` continua sem dizer
para onde vai. Esses casos saem como linhas `UNDECIDABLE|…`: nao sao defeito nem
cobertura, sao o pedaco que esta varredura NAO mede, e o probe os imprime como nota.
Omiti-los faria a cobertura parecer maior do que e — que e exatamente o erro que este
arquivo existe para nao repetir.

RESOLUCAO DE `fetch(url)` (2026-08-29) — o balde de nao-medidos tinha defeito dentro
------------------------------------------------------------------------------------
Ate aqui, uma `const url` montada com template literal seguida de `fetch(url)` caia no
balde UNDECIDABLE, e o probe imprimia "15 chamadas fora do alcance" com o resto
VERDE. Ao gatear as rotas descobertas da analytics-api, DUAS delas foram achadas A MAO
dentro desse balde, e as duas eram `fetch` cru contra endpoint que passou a exigir
credencial — `useCustomer360.ts` (aba Cliente) e `AddCardModal.tsx` (opcoes do editor
de cartao). Ou seja: o balde nao continha so ruido, continha as duas telas que
quebrariam, e o numero "15" as tornava invisiveis contando-as.

Agora, quando o 1o argumento e um identificador simples, procuramos a ULTIMA atribuicao
`const <ident> = <literal>` antes da chamada e decidimos sobre ela. Nao resolve
identificador vindo de parametro, de props ou de outra funcao — esses continuam
UNDECIDABLE, e continuam contados.

Saida: `HIT|arquivo|linha|path_de_teste|trecho`   (decidivel, candidato)
       `UNDECIDABLE|arquivo|linha|trecho`         (URL nao-literal)
rc 0 = nenhum HIT · rc 1 = ha HIT · rc 2 = nao consegui varrer.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Prefixos servidos pela analytics-api (ver `location ~ ^/(dashboard|sessions|supervisor|reports)/`
# no nginx do platform-ui). `/supervisor/` entra tambem: e escrita, exige token, e o
# reconhecimento de header montado a mao evita o falso positivo.
PREFIXES = ("/reports/", "/dashboard/", "/sessions/", "/analytics/", "/supervisor/")

# `fetch(` que nao seja `apiFetch(`/`safeFetch(`/etc: o caractere anterior nao pode
# fazer parte de um identificador.
CALL_RE = re.compile(r"(?<![A-Za-z0-9_$.])fetch\s*\(")

# Janela depois da abertura do parenteses onde o argumento (e um eventual header) e
# procurado. 400 chars cobrem os call sites multi-linha do repo.
WINDOW = 400

# Helpers definidos no proprio arquivo cujo corpo escreve `Authorization`.
DEF_RE = re.compile(r"(?:function|const)\s+([A-Za-z_$][\w$]*)\s*[\(=]")

# `const BASE = ''` e afins: prefixo de URL que mora numa constante do arquivo.
CONST_STR_RE = re.compile(r"const\s+([A-Za-z_$][\w$]*)\s*=\s*(['\"])(.*?)\2")

# `fetch(url)` — 1o argumento e um identificador simples, sem chamada nem propriedade.
BARE_IDENT_RE = re.compile(r"^([A-Za-z_$][\w$]*)\s*[,)]")

# `const url = ` — a atribuicao cujo lado direito queremos decidir.
CONST_ASSIGN_RE = r"const\s+%s\s*=\s*"


COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def _mask_comments(text: str) -> str:
    """Comentarios viram espacos, PRESERVANDO offsets (e portanto os numeros de linha).

    Por que (2026-08-29): a varredura casava `fetch(` dentro de PROSA. Das 15 linhas
    UNDECIDABLE medidas antes desta mudanca, pelo menos duas eram comentario
    (`dialog-hooks.ts:4` "fetch (mirrors …", `useCopilotState.ts:23` "fetch (e.g.
    Date.now())"), e uma terceira apareceu no instante em que alguem escreveu um
    comentario explicando um `fetch(url)`. Um contador de nao-medidos que conta prosa
    infla justamente o numero que serve para dizer o tamanho do ponto cego.

    Substituir por espacos (e nao remover) mantem `str.count('\\n')` e os offsets do
    `finditer` exatos — o numero de linha reportado continua sendo o do arquivo real.
    """
    def _branco(m: re.Match) -> str:
        return "".join("\n" if c == "\n" else " " for c in m.group(0))
    return COMMENT_RE.sub(_branco, text)


def _credential_helpers(text: str) -> set[str]:
    """Nomes de funcoes DESTE arquivo que montam um header `Authorization`."""
    names: set[str] = set()
    for m in DEF_RE.finditer(text):
        body = text[m.end(): m.end() + 600]
        if "Authorization" in body:
            names.add(m.group(1))
    return names


def _string_consts(text: str) -> dict[str, str]:
    return {m.group(1): m.group(3) for m in CONST_STR_RE.finditer(text)}


def _test_path(window: str, consts: dict[str, str]) -> str | None:
    """
    Extrai o 1o argumento como path testavel.

    `${IDENT}` cujo IDENT e uma const string do arquivo e SUBSTITUIDO pelo valor —
    e o caso do `const BASE = ''` em `service/api/hooks.ts`, sem o qual toda chamada
    daquele arquivo (o Console inteiro) sairia da varredura sem ninguem notar.
    Qualquer outra interpolacao vira `__probe__`.
    """
    arg = window.lstrip()
    quote = arg[:1]
    if quote not in ("`", "'", '"'):
        return None
    body: list[str] = []
    i = 1
    while i < len(arg):
        c = arg[i]
        if quote == "`" and arg.startswith("${", i):
            depth, j = 1, i + 2
            while j < len(arg) and depth:
                if arg[j] == "{":
                    depth += 1
                elif arg[j] == "}":
                    depth -= 1
                j += 1
            expr = arg[i + 2: j - 1].strip()
            body.append(consts.get(expr, "__probe__") if expr in consts else "__probe__")
            i = j
            continue
        if c == quote:
            break
        body.append(c)
        i += 1
    path = "".join(body)
    return path if path.startswith(PREFIXES) else None


def scan(root: Path) -> list[str]:
    hits: list[str] = []
    undecidable: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".ts", ".tsx") or not path.is_file():
            continue
        text = _mask_comments(path.read_text(encoding="utf-8", errors="replace"))
        helpers = _credential_helpers(text)
        consts = _string_consts(text)
        for m in CALL_RE.finditer(text):
            window = text[m.end(): m.end() + WINDOW]
            if "Authorization" in window:
                continue
            if any(re.search(r"(?<![\w$])" + re.escape(h) + r"\s*\(", window) for h in helpers):
                continue
            line = text.count("\n", 0, m.start()) + 1
            snippet = text[m.start(): m.start() + 60].split("\n")[0].strip()
            rel = path.relative_to(root).as_posix()
            if window.lstrip()[:1] not in ("`", "'", '"'):
                # `fetch(url)`: tenta a ULTIMA `const url = <literal>` antes daqui.
                # Sem isto o caso mais comum de URL montada em duas linhas some do
                # relatorio como "fora do alcance" — e foi la que estavam as duas
                # chamadas que quebrariam ao gatear as rotas (2026-08-29).
                ident = BARE_IDENT_RE.match(window.lstrip())
                rhs = None
                if ident:
                    antes = text[: m.start()]
                    ult = None
                    for a in re.finditer(CONST_ASSIGN_RE % re.escape(ident.group(1)), antes):
                        ult = a
                    if ult is not None:
                        rhs = antes[ult.end():]
                if rhs is not None and rhs.lstrip()[:1] in ("`", "'", '"'):
                    # RESOLVIDO. `None` aqui significa "aponta para outro servico",
                    # nao "nao consegui ler" — e os dois nao podem cair no mesmo balde,
                    # senao o numero de nao-medidos passa a contar chamadas que foram
                    # medidas e absolvidas (`/api/work_queue/`, `/v1/workflow/`).
                    alvo = _test_path(rhs, consts)
                    if alvo is not None:
                        hits.append(f"HIT|{rel}|{line}|{alvo}|{snippet}")
                    continue
                undecidable.append(f"UNDECIDABLE|{rel}|{line}|{snippet}")
                continue
            test_path = _test_path(window, consts)
            if test_path is None:
                continue   # literal, mas nao aponta para analytics
            hits.append(f"HIT|{rel}|{line}|{test_path}|{snippet}")
    return hits + undecidable


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: _ui_raw_analytics_calls.py <platform-ui/src>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"diretorio inexistente: {root}", file=sys.stderr)
        return 2
    rows = scan(root)
    for r in rows:
        print(r)
    return 1 if any(r.startswith("HIT|") for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
