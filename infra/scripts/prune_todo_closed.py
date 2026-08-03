#!/usr/bin/env python3
"""
prune_todo_closed.py — remove seções FECHADAS do TODO.md.

POR QUE EXISTE. `CLAUDE.md` define os papéis: `TODO.md` guarda *"itens genuinamente não
implementados"*; `CHANGELOG.md` guarda o histórico do que foi concluído. Seção fechada que
permanece no TODO não é arquivo morto inofensivo — ela faz alguém refazer trabalho, ou
procurar um defeito que já não existe. Em 2026-08-03, três dos cinco itens atacados estavam
stale, e cada um custou uma rodada de medição para descobrir.

**NÃO cria um terceiro arquivo.** Mover concluídos para um `TODO_DONE.md` daria duas casas
para a mesma informação — o `CHANGELOG` já é a casa. Este script DELETA, e o pré-requisito
de deletar é ter conferido que o essencial está lá (feito à mão antes de rodar: os termos de
cada seção foram procurados no CHANGELOG).

DRY-RUN POR DEFEITO. Imprime o que sairia e quantas linhas, sem tocar no arquivo. `--apply`
grava, com backup em `TODO.md.bak`. A separação existe porque a remoção é irreversível na
prática (o git ajuda, mas só se alguém lembrar de olhar) e porque a lista de seções é
casada por SUBSTRING — um título que mude de forma some da lista em silêncio, e o
dry-run é onde isso aparece.

Uso:
    python3 infra/scripts/prune_todo_closed.py            # dry-run
    python3 infra/scripts/prune_todo_closed.py --apply
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

TODO = Path("TODO.md")

# Já removidas em execuções anteriores. NÃO entram em CLOSED: o aviso "NÃO ENCONTRADO" existe
# para denunciar título que MUDOU, e um padrão que nunca mais vai casar o dispara para sempre.
# Alarme com álibi permanente é equivalente a não ter alarme — a mesma lição do Portão D do
# smoke de ocupação. Ficam aqui como registro do que saiu, sem serem verificadas.
ALREADY_PRUNED = [
    "Suítes VERMELHAS fora do routing-engine",      # 2026-08-03, 1ª rodada
    "Registro original — Zero testes coletados",
    "Registro original — Suítes vermelhas",
    "Varrer o `REDIS_URL` de leitura única",
    "Drop de `Pool.session_reservation`",
    "`available > total` — **ENCERRADO",
    "Registro original — Arc 12",
    "`agent_events` — fatia 2: DROP",
    "`agent_done` de crash-recovery",
    "`bootstrap_placeholder` publica capacidade ZERO",
]

# Substrings que identificam o cabeçalho `## ` de cada seção a remover NESTA execução.
# Conferido em 2026-08-03: o conteúdo essencial de todas está no CHANGELOG.md.
#
# ── 2026-08-03, 3ª sessão ────────────────────────────────────────────────────────────────
# Sete seções tachadas, e TRÊS tinham resíduo VIVO enterrado no corpo — colhido à mão antes
# de entrar nesta lista. É a forma que este script NÃO vê sozinho: o título é tachado, o
# pendente mora no meio, e a remoção é por seção inteira.
#   · `close_reason`   → `_TRANSPORT_TO_CLOSE_REASON` (gated em WARNING) foi para
#                        § Wrap-up unificado — resíduos após a Phase 2
#   · Pricing → quota  → tenant fantasma `smoke_gprobe_pricing` foi para a tabela de
#                        resíduos de smoke da § Capacidade
#   · Suítes vermelhas → 2 das 5 regras de dublê viraram § Erros de método nº 7
# A do Arc 12 saiu por estar STALE de um jeito ativo: afirmava *"não há produtor algum"*, e a
# fatia 3 do wrap-up criou o primeiro produtor no mesmo dia.
CLOSED = [
    "`close_reason` do contato só é persistido",
    "Zero testes coletados em 6 pacotes",
    "Suítes vermelhas — os 4 pacotes restantes",
    "analytics-api — 23 testes vermelhos há tempo",
    "Arc 12 — `segment_id` em `agent_business_events`",
    "evaluation-api — 10 testes de `test_router.py`",
    "Pricing → quota Redis não existe",
]


def split_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """[(cabeçalho|'' para o preâmbulo, linhas)] — preserva o texto antes do 1º `## `."""
    out: list[tuple[str, list[str]]] = []
    cur_head: str = ""
    cur: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            out.append((cur_head, cur))
            cur_head, cur = ln.rstrip("\n"), [ln]
        else:
            cur.append(ln)
    out.append((cur_head, cur))
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    if not TODO.exists():
        print(f"❌ {TODO} não encontrado — rode da raiz do repositório.")
        return 2

    lines = TODO.read_text(encoding="utf-8").splitlines(keepends=True)
    sections = split_sections(lines)

    kept: list[str] = []
    removed_total = 0
    matched: set[str] = set()

    print(f"── TODO.md: {len(lines)} linhas, {len(sections) - 1} seções ──\n")
    for head, body in sections:
        hit = next((p for p in CLOSED if p in head), None)
        if hit:
            matched.add(hit)
            removed_total += len(body)
            print(f"  − {len(body):4d} linhas  {head[:78]}")
        else:
            kept.extend(body)

    # Padrão que não casou = título mudou (ou já foi removido). Silenciar isto faria o
    # script "funcionar" removendo menos do que se pediu, sem ninguém notar.
    for p in CLOSED:
        if p not in matched:
            print(f"  ⚠️  NÃO ENCONTRADO: {p!r} — título mudou ou já saiu?")

    print(f"\n  total a remover: {removed_total} linhas "
          f"({removed_total * 100 // max(len(lines), 1)}% do arquivo)")
    print(f"  resultado:       {len(kept)} linhas")

    if not apply:
        print("\n  DRY-RUN — nada foi gravado. Use --apply para aplicar.")
        return 0

    shutil.copy(TODO, TODO.with_suffix(".md.bak"))
    TODO.write_text("".join(kept), encoding="utf-8")
    print(f"\n  ✅ aplicado. Backup em {TODO.with_suffix('.md.bak')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
