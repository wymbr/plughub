"""
_route_baseline_judge.py — AUT-58: a medição ANÔNIMA de cada rota bate com a tabela declarada?

Entrada: a saída de `_route_anon_sweep.py` (JSON) e `route_credential_baseline.tsv`.
Cada rota medida precisa de UMA linha, e a linha precisa bater com o que a rota fez:

  fechada            → mediu 401/403. Qualquer outra coisa é REGRESSÃO.
  guard_corpo:<g>    → o handler chama <g> e <g> é guard do serviço (conferido no código), e o
                       anônimo NÃO passou dele: mediu 422 (corpo inválido antes do guard) ou não
                       foi disparada. 2xx/404/400 = o guard falha aberto — reprova.
  isenta             → pública por decisão, com o motivo na linha.
  divida:<FICHA>     → aberta conhecida, com ficha. Se medir FECHADA, reprova: a dívida fechou e
                       a linha tem de ir para `fechada` (e a ficha, para o done).
  <serviço>|*|fora   → a varredura não enxerga o serviço (motivo na linha). Se passar a enxergar,
                       reprova até alguém classificar as rotas.

Rota medida sem linha reprova; linha sem rota medida reprova (órfã). Uma tabela que só
aceitasse o que já está nela não mediria nada — daí o `--autoteste`, que injeta uma regressão e
uma rota nova numa cópia da medição e exige as duas reprovações.
"""
from __future__ import annotations

import collections
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _route_guard_body_check as gbc  # noqa: E402

_CACHE: dict = {}


def _guard_confere(svc: str, rota: str, nome: str) -> str | None:
    if svc not in _CACHE:
        _CACHE[svc] = gbc.carregar(svc)
    guards, rotas = _CACHE[svc]
    metodo, path = rota.split(" ", 1)
    fn = gbc._achar(rotas, metodo, path)
    if fn is None:
        return f"handler não encontrado no código de {svc}"
    if nome not in guards:
        return f"{nome} não é guard de {svc} (não levanta 401/403, nem delega a quem levanta)"
    if nome not in gbc._chamados(fn):
        return f"o handler {fn.name} não chama {nome}"
    return None


def carregar_tabela(caminho: str) -> dict:
    tab = {}
    for n, linha in enumerate(pathlib.Path(caminho).read_text(encoding="utf-8").splitlines(), 1):
        if not linha.strip() or linha.startswith("#"):
            continue
        svc, rota, postura, motivo = (linha.split("|", 3) + [""])[:4]
        tab[(svc, rota)] = (postura, motivo, n)
    return tab


def julgar(sweep: list, tab: dict) -> tuple[list[str], collections.Counter, collections.Counter]:
    erros: list[str] = []
    posturas: collections.Counter = collections.Counter()
    fichas: collections.Counter = collections.Counter()
    vistos = set()
    for s in sweep:
        svc = s["service"]
        if "error" in s:
            if (svc, "*") in tab:
                vistos.add((svc, "*")); posturas["fora"] += 1
            else:
                erros.append(f"{svc}: serviço inalcançável e sem linha `{svc}|*|fora` — {s['error'][:80]}")
            continue
        if (svc, "*") in tab:
            erros.append(f"{svc}: declarado `fora`, mas a varredura agora o enxerga — classifique as rotas")
            vistos.add((svc, "*"))
        for r in s["routes"]:
            k = (svc, r["route"])
            if k not in tab:
                erros.append(f"SEM LINHA: {svc} {r['route']} (mediu {r.get('status', r['class'])})")
                continue
            vistos.add(k)
            postura, motivo, _ = tab[k]
            cls, st = r["class"], r.get("status")
            posturas[postura.split(":")[0]] += 1
            if postura == "fechada":
                if cls != "fechada":
                    erros.append(f"REGRESSÃO: {svc} {r['route']} declarada fechada, anônimo recebeu {st or cls}")
            elif postura.startswith("guard_corpo:"):
                problema = _guard_confere(svc, r["route"], postura.split(":", 1)[1])
                if problema:
                    erros.append(f"GUARD NÃO CONFERE: {svc} {r['route']} — {problema}")
                elif cls == "aberta" and st != 422:
                    erros.append(f"GUARD FALHA ABERTO: {svc} {r['route']} — anônimo recebeu {st}")
                elif cls == "fechada":
                    erros.append(f"MELHORIA NÃO DECLARADA: {svc} {r['route']} agora recusa na porta — mude para fechada")
            elif postura.startswith("divida:"):
                fichas[postura.split(":", 1)[1]] += 1
                if cls == "fechada":
                    erros.append(f"DÍVIDA FECHOU: {svc} {r['route']} ({postura}) recusa anônimo — mude para fechada")
            elif postura == "isenta":
                if not motivo.strip():
                    erros.append(f"ISENTA SEM MOTIVO: {svc} {r['route']}")
                elif cls == "fechada":
                    erros.append(f"ISENTA FECHOU: {svc} {r['route']} recusa anônimo — mude para fechada")
            else:
                erros.append(f"POSTURA DESCONHECIDA: {svc} {r['route']} → {postura}")
    for k, (postura, _, n) in tab.items():
        if k not in vistos:
            erros.append(f"LINHA ÓRFÃ (linha {n}): {k[0]} {k[1]} não existe mais na medição")
    return erros, posturas, fichas


def autoteste(sweep: list, tab: dict) -> list[str]:
    falhas = []
    s2 = copy.deepcopy(sweep)
    alvo = next((r for s in s2 if "routes" in s for r in s["routes"] if r["class"] == "fechada"), None)
    if alvo is None:
        return ["autoteste sem rota fechada para injetar regressão"]
    alvo["class"], alvo["status"] = "aberta", 200
    nova = next(s for s in s2 if "routes" in s)
    nova["routes"].append({"route": "GET /probe/rota-nova-aut58", "class": "aberta", "status": 200})
    erros, _, _ = julgar(s2, tab)
    if not any(e.startswith("REGRESSÃO") for e in erros):
        falhas.append("autoteste: a regressão injetada NÃO reprovou")
    if not any("rota-nova-aut58" in e for e in erros):
        falhas.append("autoteste: a rota nova sem linha NÃO reprovou")
    return falhas


if __name__ == "__main__":
    sweep = json.load(open(sys.argv[1], encoding="utf-8"))
    tab = carregar_tabela(sys.argv[2])
    erros, posturas, fichas = julgar(sweep, tab)
    print("POSTURAS " + " ".join(f"{k}={v}" for k, v in sorted(posturas.items())))
    print("DIVIDA " + " ".join(f"{k}={v}" for k, v in sorted(fichas.items())))
    for e in erros:
        print("ERRO " + e)
    for f in autoteste(sweep, tab):
        print("ERRO " + f)
