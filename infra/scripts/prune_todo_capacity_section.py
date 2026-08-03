#!/usr/bin/env python3
"""
prune_todo_capacity_section.py — poda a seção de capacidade do TODO.md.

POR QUE UM SCRIPT SEPARADO. `prune_todo_closed.py` remove seções `## ` INTEIRAS, e só serve
para seção 100% fechada. A de capacidade não é: ~500 linhas quase todas ✅, com um resíduo
genuinamente aberto no meio (fatia 4/defeito C, itens independentes da medição). Podar aqui é
SUBSTITUIR o corpo, não deletá-lo — operação diferente, com risco diferente: o erro possível
não é "removeu de menos", é "levou junto o que ainda vale".

PORTÕES (todos abortam antes de gravar). O corte é por ÂNCORA de texto, nunca por número de
linha — o arquivo muda de tamanho a cada commit:

  P1  o cabeçalho da seção existe e é ÚNICO
  P2  o fim é o próximo `## ` (nunca o EOF: se não houver, o corte comeria o resto do arquivo)
  P3  o bloco a remover contém os marcadores esperados — prova que a âncora pegou a seção
      CERTA, e não uma homônima
  P4  o bloco a remover tem o tamanho esperado, ±tolerância — divergência grande significa
      que a seção mudou desde que este texto foi escrito, e aí a substituição está cega

DRY-RUN POR DEFEITO. `--apply` grava, com backup em `TODO.md.bak`.

Uso:
    python3 infra/scripts/prune_todo_capacity_section.py            # dry-run
    python3 infra/scripts/prune_todo_capacity_section.py --apply
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

TODO = Path("TODO.md")

ANCHOR = "## Capacidade, licenças e isolamento entre pools"

# P3 — o bloco removido TEM de conter estes trechos. Se a âncora casar com outra coisa
# (ou se a seção já tiver sido podada), pelo menos um destes falta e o script para.
MUST_CONTAIN = [
    "### Linha de base medida (2026-07-31)",
    "### Pico de ocupação VERDADEIRO — event-driven",
    "### Medições que decidiram o escopo",
    "Alternativas descartadas",
]

# P4 — tamanho medido em 2026-08-03 antes da poda.
EXPECTED_LINES = 501
TOLERANCE = 40

REPLACEMENT = '''## Capacidade, licenças e isolamento entre pools *(A e B ✅ 2026-08-02/03 — histórico no CHANGELOG; resta C + fatia 4)*

> **Podada em 2026-08-03: @@PODA@@.** O as-built das fatias F1–F5b e P1–P3 mora no
> `CHANGELOG.md` (14 entradas, de *"fatia 1: tag de pool no membro do semáforo"* a *"pré-requisito
> da F3, F5 e o fóssil em quarentena"*) e o modelo vigente no `CLAUDE.md` § Operational Visibility
> e § Admissão de sessão. Duas casas para a mesma informação é o defeito que este projeto evita em
> toda parte — e a segunda casa já estava mentindo em dois pontos (registrados no CHANGELOG da
> poda, não aqui: contradição resolvida é histórico).
>
> · Desenho de relatório: [`docs/product/shared-capacity-pool-as-tag-design.md`](docs/product/shared-capacity-pool-as-tag-design.md)
> · ADR de licenciamento: [`docs/adr/adr-agent-licensing-and-pool-isolation.md`](docs/adr/adr-agent-licensing-and-pool-isolation.md)

| | Problema | Estado |
|---|---|---|
| **A** | relatório mente: `available` por pool ignora consumo dos irmãos; KPI soma recurso compartilhado | ✅ F1–F5b + P1–P3 (2026-08-02/03) |
| **B** | teto de licença mistura moedas (`C = ai + human`) e gateia sessão humana | ✅ fatia 3 (2026-08-02) — sobrou `kind:ai ≤ C_ai`, gate único |
| **C** | piso/teto por pool, licenças materializadas, cerimônia de deploy | **ADIADO por medição** — é capacidade NOVA, não conserto |

### O que segue aberto

**1. Fatia 4 / defeito C — adiada, e a medição que a adiou precisa ser REFEITA antes de reabrir.**
`Q1` (IA roda > 1 sessão por instância?) e `Q2` (alguém usa `session_reservation`?) saíram do banco
ERRADO: `plughub_demo.public.pools` era fóssil congelado, o agent-registry vive em
`plughub_registry`. A perna de CÓDIGO de Q1 sustenta a conclusão (`instance_bootstrap.py:1054-1072`
usa *"Concurrent sessions: N"* como número de INSTÂNCIAS, cada uma `max_concurrent=1` ⇒ para IA,
instância == sessão); a perna de DADO não. **Q2 não é re-mensurável** — a coluna foi dropada em
02/08, e as evidências que sobram (`infra/registry/*.yaml` não declara reserva em pool nenhum; o
fóssil também marcava zero) apontam para a mesma conclusão sem serem o banco vivo no momento da
decisão. *O método estava errado mesmo com o resultado provavelmente certo.* O script já aponta
para o banco certo e aborta se não for (portão Q-1, `_prisma_migrations` como discriminador):

```bash
bash infra/test/measure_capacity_licensing_baseline.sh tenant_demo
```

**2. `max_concurrent_sessions` ainda soma as moedas** — hoje só como teto de PROVISIONAMENTO
(`lib/capacity.ts`, `deployViolation`: Σ declarada nos slots ≤ C). A fatia 3 deixou o defeito C mais
VISÍVEL, não menor; trocar esse gate agora seria construir a fatia 4 no meio da 3. Anotado no
docstring do próprio arquivo, para quem chegar nele primeiro não repetir a conta.

**3. Itens independentes achados na medição de 2026-07-31 — DATADOS, não verificados desde.**

| Achado | O que se mediu | Ressalva antes de agir |
|---|---|---|
| vazamento de admissão | 3 sessões presas em `…:admission:shared` (todas `kind:ai`, pool `survey_journey_wf`) com zero instâncias ocupadas; o reconciler não as liberou | o SET `shared` **não existe mais** (fatia 3). O mecanismo sobrevive em `kind:ai`: a liberação depende do marcador `closed` + reconciler. **Re-medir antes de tratar como defeito** |
| pools fantasma | `formfill_demo`, `ramal_test`, `survey_journey_wf` — resíduo de smoke com estado vivo | limpeza, não defeito |
| **`webhook_skill_id` é um pool** | com 3 instâncias: **o nome de um campo virou id de pool** | o mais concreto dos três; bug de seed/provisionamento |
| `retencao_humano-int` fora de `public.pools` | espelho vive só em runtime ⇒ **invisível a validação em tempo de config** | é por desenho (ADR §9.1: pool interno resolve licenciamento no pai). Registro, não defeito |
| `fila_humano` com `agent_kind = ai` | pelo nome deveria ser humano; muda licenciamento e hook | dado de tenant, não código |

**4. Costura única `acquire`/`release`** — arco separado; ver a seção própria mais abaixo.

### O que a poda NÃO pode levar junto

- **Não somar linhas de pool.** `Σ available(pool)` conta o mesmo recurso uma vez por pool e **não é
  corrigível na linha do pool**: a linha está certa (aquele pool alcança mesmo N vagas), somá-la é
  que não pode, e a informação de sobreposição não está lá. Vale igual na série
  `pool_occupancy_peaks` — `__total__` e `__capacity_{kind}__` são deduplicados (F4c), a linha do
  pool **não** é, por construção. E `by_channel` é PROJEÇÃO, não partição: instância que serve 2
  canais conta nos dois, então Σ entre canais excede o total do tipo.
- **Duas descontinuidades a marcar no eixo**, se a série virar base de dimensionamento:
  (1) `peak_concurrency` trocou de fonte (`active_count` → `used_here`, 2026-08-02) — o contador
  antigo derivava para CIMA, então o histórico tende a estar **superestimado**; degrau não medido.
  (2) `admission.shared_series` morreu e `admission.ai_series` começa em 2026-08-02 — não é
  renomeação: o denominador mudou de `370 − Σ reservas` para `C_ai = 360` e o numerador deixou de
  contar sessão humana.
- **`peak_concurrency` nunca responde "ocupação média"** — o registro por minuto já é máximo, e média
  de máximos não é média de ocupação. Média exigiria soma+contagem de amostras por minuto (campo
  novo, não pedido).

### Alternativas descartadas — não reabrir sem argumento novo

Reservar vagas de sessão por pool (fragmenta o recurso — contraria o invariante *"capacidade é do
RECURSO"*); só piso sem teto (sem teto não há limite a impor); empréstimo do piso ocioso (garantia
que exige espera não é garantia); baixar o TTL do snapshot (cura por expiração); métrica única de
"degradação" (valor plausível que esconde privação, espera e atribuição); adotar `current_sessions`
em vez do SET de ocupantes (é da mesma família do contador por pool — trocar um contador por outro
não fecha a classe, só muda qual deles vai mentir depois).

---

'''


def fail(msg: str) -> int:
    print(f"❌ {msg}")
    print("   nada foi gravado.")
    return 2


def main() -> int:
    apply = "--apply" in sys.argv
    if not TODO.exists():
        return fail(f"{TODO} não encontrado — rode da raiz do repositório.")

    lines = TODO.read_text(encoding="utf-8").splitlines(keepends=True)

    # P1 — âncora existe e é única.
    starts = [i for i, ln in enumerate(lines) if ln.startswith(ANCHOR)]
    if len(starts) != 1:
        return fail(f"P1: esperava 1 ocorrência de {ANCHOR!r}, achei {len(starts)}.")
    start = starts[0]

    # P2 — fim é o próximo `## `, nunca o EOF.
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), None)
    if end is None:
        return fail("P2: não há `## ` depois da âncora — o corte comeria o resto do arquivo.")

    block = lines[start:end]
    block_text = "".join(block)

    # P3 — o bloco é mesmo a seção que este texto substitui.
    missing = [m for m in MUST_CONTAIN if m not in block_text]
    if missing:
        return fail(f"P3: bloco não contém {missing} — âncora pegou outra seção, ou já foi podada.")

    # P4 — tamanho dentro do esperado.
    if abs(len(block) - EXPECTED_LINES) > TOLERANCE:
        return fail(
            f"P4: bloco tem {len(block)} linhas, esperava ~{EXPECTED_LINES} (±{TOLERANCE}). "
            "A seção mudou desde que a substituição foi escrita — reveja o texto novo antes."
        )

    # O texto novo declara o tamanho da própria poda. Escrever esse número à mão é como se
    # produz um valor plausível e errado; aqui ele vem da medição, e P5 garante que veio.
    # Substituir o token não muda a CONTAGEM de linhas, só o conteúdo de uma — então medir
    # antes de substituir é seguro.
    new_lines = REPLACEMENT.splitlines(keepends=True)
    filled = REPLACEMENT.replace("@@PODA@@", f"{len(block)} → {len(new_lines)} linhas")
    if "@@PODA@@" in filled:
        return fail("P5: token @@PODA@@ sobreviveu à substituição.")
    new_lines = filled.splitlines(keepends=True)
    result = lines[:start] + new_lines + lines[end:]

    print(f"── TODO.md: {len(lines)} linhas ──")
    print(f"  seção  : linhas {start + 1}–{end} ({len(block)} linhas)")
    print(f"  vira   : {len(new_lines)} linhas")
    print(f"  arquivo: {len(lines)} → {len(result)} linhas (−{len(lines) - len(result)})")
    print("\n  5 portões: OK")
    print("\n  primeiras linhas removidas:")
    for ln in block[:3]:
        print(f"    − {ln.rstrip()[:92]}")
    print("  últimas linhas removidas:")
    for ln in block[-3:]:
        print(f"    − {ln.rstrip()[:92]}")

    if not apply:
        print("\n  DRY-RUN — nada foi gravado. Use --apply para aplicar.")
        return 0

    shutil.copy(TODO, TODO.with_suffix(".md.bak"))
    TODO.write_text("".join(result), encoding="utf-8")
    print(f"\n  ✅ aplicado. Backup em {TODO.with_suffix('.md.bak')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
