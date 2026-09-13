#!/usr/bin/env python3
"""
_up_state_verdict.py — o veredicto de estado depois do `up -d` (GAT-05).

POR QUE ESTE ARQUIVO EXISTE
---------------------------
O `up.sh` conferia o estado com `grep -v ' running'` e uma lista FIXA de one-shots
excluidos PELO NOME. Isso tinha dois defeitos, e os dois foram medidos em 2026-09-12:

  1. **Seed MORTO lia igual a seed CONCLUIDO.** A exclusao era por nome, sem olhar o
     exit code, e um one-shot so tem dois desfechos. O `auth-seed` saiu com exit 1
     em toda subida desde a MOD-11 (AUT-54), sem semear ninguem depois do admin, e o
     script terminava em "Stack no ar".
  2. **A lista envelheceu no sentido oposto.** O `context-map-seed` (ALW-12,
     2026-09-02) nunca entrou nela: saindo 0, como deve, ele era lido como servico
     CAIDO, e o `up.sh` reprovava toda subida correta. Um vermelho permanente ensina
     a ignorar o script — o ultimo log em `.logs/` era de 2026-08-12.

Uma lista parece completa por ser uma lista. Aqui a classificacao e DERIVADA do
compose, que e onde o fato ja esta declarado:

  one-shot  = servico com `restart: "no"`, OU que algum outro espera com
              `depends_on: {condition: service_completed_successfully}`
              (o segundo criterio cobre o `minio-init`, que nao declara restart)

O julgamento e uma funcao pura (compose + listagem do `ps`) justamente para poder
ser REPROVADA sem subir a stack: `infra/test/probe_up_state_verdict.sh`.

VEREDICTO (exit code)
---------------------
  0  VERDE        long-running em `running`; one-shot `exited` com codigo 0
  1  VERMELHO     long-running fora de `running` (inclusive `exited 0` — servico que
                  saiu nao "concluiu"); one-shot com codigo != 0 ou em estado que nao
                  e `running`/`exited` (ex.: `created`, que e dependencia que falhou)
  2  PENDENTE     nada vermelho, mas one-shot ainda `running` — quem chama espera e
                  pergunta de novo; esgotado o prazo, e INCONCLUSIVO, nunca verde
  3  INCONCLUSIVO nao consegui ler o compose ou a listagem — sem classificacao nao ha
                  veredicto, e cair numa lista fixa seria reabrir o defeito

Uso:
  _up_state_verdict.py <compose.json> <ps.tsv>        # ps: servico<TAB>estado<TAB>exit
  _up_state_verdict.py --oneshots <compose.json>      # so a classificacao
"""
import json
import sys

VERDE, VERMELHO, PENDENTE, INCONCLUSIVO = 0, 1, 2, 3


def one_shots(compose: dict) -> set[str]:
    servicos = compose.get("services")
    if not isinstance(servicos, dict) or not servicos:
        raise ValueError("compose sem `services`")
    out: set[str] = set()
    for nome, s in servicos.items():
        s = s or {}
        if str(s.get("restart", "")).strip('"') == "no":
            out.add(nome)
        deps = s.get("depends_on") or {}
        if isinstance(deps, dict):
            for alvo, spec in deps.items():
                if (spec or {}).get("condition") == "service_completed_successfully":
                    out.add(alvo)
    return out


def ler_ps(texto: str) -> list[tuple[str, str, str]]:
    linhas = []
    for bruta in texto.splitlines():
        if not bruta.strip():
            continue
        partes = bruta.split("\t")
        if len(partes) != 3:
            raise ValueError("linha do ps fora do formato servico<TAB>estado<TAB>exit: %r" % bruta)
        linhas.append((partes[0].strip(), partes[1].strip(), partes[2].strip()))
    if not linhas:
        raise ValueError("listagem do ps vazia — nenhum container para julgar")
    return linhas


def julgar(oneshots: set[str], ps: list[tuple[str, str, str]]) -> tuple[int, list[str]]:
    caidos, falhos, pendentes = [], [], []
    for svc, estado, codigo in ps:
        if svc in oneshots:
            if estado == "running":
                pendentes.append(svc)
            elif estado == "exited" and codigo == "0":
                continue
            else:
                falhos.append("%s %s (exit %s)" % (svc, estado, codigo or "?"))
        elif estado != "running":
            caidos.append("%s %s (exit %s)" % (svc, estado, codigo or "?"))

    msgs: list[str] = []
    if caidos:
        msgs.append("servico(s) long-running fora de 'running':")
        msgs += ["  " + c for c in caidos]
    if falhos:
        msgs.append("one-shot(s) que NAO concluiram com exit 0:")
        msgs += ["  " + f for f in falhos]
    if caidos or falhos:
        return VERMELHO, msgs
    if pendentes:
        return PENDENTE, ["one-shot(s) ainda rodando: " + ", ".join(sorted(pendentes))]
    return VERDE, ["%d container(s): long-running em 'running', %d one-shot(s) concluido(s) com exit 0"
                   % (len(ps), sum(1 for s, _e, _c in ps if s in oneshots))]


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "--oneshots":
            print("\n".join(sorted(one_shots(json.load(open(argv[1], encoding="utf-8"))))))
            return VERDE
        if len(argv) != 2:
            print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
            return INCONCLUSIVO
        compose = json.load(open(argv[0], encoding="utf-8"))
        ps = ler_ps(open(argv[1], encoding="utf-8").read())
        classes = one_shots(compose)
    except Exception as exc:  # noqa: BLE001 — qualquer falha de leitura e INCONCLUSIVO, nomeada
        print("INCONCLUSIVO: %s" % exc)
        return INCONCLUSIVO
    rc, msgs = julgar(classes, ps)
    print("\n".join(msgs))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
