"""
_route_guard_body_check.py — AUT-58: o handler de uma rota CHAMA um guard do próprio serviço?

A varredura anônima (`_route_anon_sweep.py`) não enxerga guard que decide DENTRO do handler
(depois da validação do corpo): com corpo inválido, ela vê 422 e acusa "aberta". Para essas
rotas a tabela declara `guard_corpo:<nome>`, e este verificador confere — por AST, no código —
que o handler daquela rota chama `<nome>` e que `<nome>` é uma função do serviço que levanta
401/403. Sem as duas, a declaração é só prosa.

Também serve de DESCOBERTA (`--sugerir`): para cada rota, os guards que o handler chama.

Uso:
  python3 _route_guard_body_check.py <pacote> "<MÉTODO> <path>" <nome>   → exit 0 se confere
  python3 _route_guard_body_check.py <pacote> --sugerir                   → JSON rota→guards
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
METODOS = {"get", "post", "put", "delete", "patch"}
# Subárvores de um pacote que são OUTRO serviço (outro contêiner, outro app). Sem isto, duas
# rotas de mesmo método e path em apps diferentes empatam, e o guard de uma absolve a outra.
OUTRO_SERVICO = {"channel-gateway": ("speech_check/",)}


def _levanta_auth(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            s = ast.unparse(n)
            if ("HTTPException" in s or "JSONResponse" in s) and any(k in s for k in ("401", "403", "HTTP_401", "HTTP_403")):
                return True
    return False


def _prefixos(arvore: ast.Module) -> list[str]:
    out = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "APIRouter":
            for kw in no.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    out.append(str(kw.value.value))
    return out or [""]


def carregar(pacote: str):
    src = RAIZ / "packages" / pacote / "src"
    fora = OUTRO_SERVICO.get(pacote, ())
    arquivos = [f for f in src.rglob("*.py") if "/tests/" not in f.as_posix() and not f.name.startswith("test_")
                and not any(x in f.as_posix() for x in fora)]
    guards: set[str] = set()
    helpers: dict[str, ast.AST] = {}
    rotas: list[tuple[str, str, ast.AST]] = []
    for f in arquivos:
        arv = ast.parse(f.read_text(encoding="utf-8"))
        prefixos = _prefixos(arv)
        for fn in ast.walk(arv):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rota = None
            for d in fn.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in METODOS
                        and d.args and isinstance(d.args[0], ast.Constant)):
                    rota = (d.func.attr.upper(), str(d.args[0].value))
            if rota:
                for p in prefixos:
                    rotas.append((rota[0], p + rota[1], fn))
            else:
                helpers[fn.name] = fn
                if _levanta_auth(fn):
                    guards.add(fn.name)
    # Fecho transitivo: guard que DELEGA a recusa (ex.: `_identity_caller` → `identity_principal`)
    # também é guard. Sem isso, a recusa por delegação ficava invisível e a rota era acusada.
    mudou = True
    while mudou:
        mudou = False
        for nome, fn in helpers.items():
            if nome not in guards and guards & _chamados(fn):
                guards.add(nome)
                mudou = True
    return guards, rotas


def _chamados(fn: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | \
           {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}


def _achar(rotas, metodo: str, path: str):
    # o path medido vem com o prefixo de montagem do app; casa pelo sufixo mais longo
    cands = [(m, p, fn) for m, p, fn in rotas if m == metodo and path.endswith(p) and p]
    cands.sort(key=lambda c: len(c[1]), reverse=True)
    return cands[0][2] if cands else None


if __name__ == "__main__":
    pacote = sys.argv[1]
    guards, rotas = carregar(pacote)
    if sys.argv[2] == "--sugerir":
        print(json.dumps({f"{m} {p}": sorted(guards & _chamados(fn)) for m, p, fn in rotas}, ensure_ascii=False))
        sys.exit(0)
    metodo, path = sys.argv[2].split(" ", 1)
    nome = sys.argv[3]
    fn = _achar(rotas, metodo, path)
    if fn is None:
        print(f"handler de {metodo} {path} não encontrado em packages/{pacote}"); sys.exit(1)
    if nome not in guards:
        print(f"{nome} não é função do serviço que levanta 401/403"); sys.exit(1)
    if nome not in _chamados(fn):
        print(f"o handler {fn.name} não chama {nome}"); sys.exit(1)
    sys.exit(0)
