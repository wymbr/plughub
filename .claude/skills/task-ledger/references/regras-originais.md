# Regras originais — texto restaurado de `CLAUDE.md` anterior à DOC-02

> **Por que este arquivo existe.** A DOC-02 (commit `76b272b0`, 2026-09-06) apagou do
> `CLAUDE.md` cinco subseções que NÃO foram movidas para lugar nenhum — o commit declara
> "0 perdidas" porque conferiu só as seções de arco que moveu. Dois ponteiros ficaram
> apontando para o vazio (`CLAUDE.md` § *Pending* e o cabeçalho do `pending.md`, ambos citando
> "`CLAUDE.md` § *Ledger de tarefas*"). Achado e restaurado em 2026-09-16.
>
> Abaixo, o texto **exato** de `git show 76b272b0^:CLAUDE.md`, extraído por script, sem edição.
> A forma operacional das mesmas regras está no `SKILL.md`; onde divergirem, **esta** é a
> decisão original e a skill é que precisa ser corrigida.

---

### Como adicionar uma nova feature

1. **Feature pequena** (< 20 linhas): inline na seção H2 existente mais próxima.
2. **Feature média** (20–50 linhas): subseção `###` dentro da seção H2 mais próxima.
3. **Feature grande** (> 50 linhas): criar `docs/arcos/{nome}.md`; adicionar resumo de 15–20 linhas aqui.
4. **Fase pendente concluída**: mover a linha de `pending.md` para `done.md` (o índice) e escrever o porquê no `CHANGELOG.md`; **nunca deixar ✅ aqui**.

### Regra de persistência de planejamento

| Tipo de decisão | Onde registrar imediatamente |
|---|---|
| Nova tarefa planejada | Linha em `pending.md`, **sob o grupo da demanda** |
| Decisão técnica (> 3 linhas) | Entrada em `TODO.md` com raciocínio |
| Invariante ou regra arquitetural | Seção neste arquivo |
| Implementação concluída | `CHANGELOG.md` (o porquê) **+** linha em `done.md` (o índice) |

### Ledger de tarefas — `pending.md` / `done.md`

> **Nasceu em 2026-08-31.** O `TODO.md` acumulou 127 seções e nenhuma citava um ADR no título; a
> vinculação entre tarefa, demanda e histórico só existia em prosa. Medido no mesmo dia: **nove
> marcadores desatualizados**, e em todos o **corpo estava certo e o título velho** — porque quem
> lista pendências lê título. O tracker que esta tabela mandava usar (`TaskCreate`) **nunca recebeu
> uma linha**: destino sem mecanismo não se cumpre, e é por isso que a regra abaixo vem com portão.

1. **Toda tarefa nasce sob um grupo**, e o grupo titula um documento que existe (ADR, spec ou arco).
   Sem documento, vai para o balde **`sem-demanda`**, que é **contado** — se cresce, está entrando
   trabalho sem decisão por trás.
2. **Todo id é `AAA-NN`, único através dos DOIS arquivos.** É a chave de junção que não existia.
3. **Título nunca afirma status** — status é coluna. Isso remove a possibilidade do defeito em vez
   de exigir vigilância, que já falhou nove vezes.
4. **Três estados abertos:** `aberto` · `bloqueado` (impedimento nomeado) · `adiado` (decidido não
   agora, com **gatilho** declarado). `adiado` existe para que decisão tomada não volte à mesa.
5. **`done.md` é índice, nunca narrativa** — id, tarefa, data e âncora no `CHANGELOG.md`. O porquê
   mora lá; repetir aqui criaria mais uma casa afirmando o mesmo fato.
6. **Fechar é MOVER**, e a mudança é conferida: nenhum id nos dois arquivos, nenhum id sumido.

Portão: **`infra/test/probe_task_ledger.sh`** (6 ramos; A/B/C/E/F provados falseáveis por mutação).
Ele existe porque o modo de falha do desenho de dois arquivos — **tarefa perdida na mudança** — é
mais silencioso que o status velho que ele substitui.

### Convenção de pastas de documentação

| Pasta | Conteúdo | Quando criar arquivo aqui |
|---|---|---|
| `docs/modulos/` | Docs de páginas e features da UI | Nova rota/módulo de interface |
| `docs/arcos/` | Docs de implementação por Arc | Arc novo ou refactoring de backend significativo |
| `docs/guias/` | Padrões transversais a múltiplos pacotes | Novo padrão (mascaramento, @mention, hooks, etc.) |
| `docs/adr/` | Decisões arquiteturais com trade-offs | Toda decisão estrutural relevante |
| `docs/pacotes/` | Contratos públicos de cada pacote | Novo pacote no monorepo |

### Regra de atualização de documentação

> Toda entrada em `CHANGELOG.md` deve ter um doc correspondente **criado ou atualizado** antes de ser considerada concluída. Se a feature afeta uma rota de UI → atualizar `docs/modulos/`. Se é um Arc ou backend significativo → atualizar ou criar `docs/arcos/`. Se é um padrão transversal → atualizar `docs/guias/`.

> **Conference mechanics**: qualquer mudança no mecanismo de conferência (lifecycle, Redis keys, eventos Kafka/pub-sub, lógica de posatt, filtros no mcp-server, regras de teardown no platform-ui) **deve atualizar `docs/guias/conference-mechanics.md` e adicionar uma entrada em § Histórico de Problemas e Correções** antes de ser considerada concluída.

