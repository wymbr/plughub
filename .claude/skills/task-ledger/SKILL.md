---
name: task-ledger
description: Como registrar, andar e fechar trabalho no PlugHub — ledger pending.md/done.md, fichas com id AAA-NN, CHANGELOG.md, TODO.md, documentação que acompanha a entrega, e commit com sessões paralelas na mesma árvore. Use ao abrir ficha ou tarefa nova, escolher id, mudar status (aberto, bloqueado, adiado), fechar ou mover ficha para done.md, escrever entrada no CHANGELOG.md, registrar achado ou decisão técnica, decidir onde documentar uma feature (docs/arcos, docs/guias, docs/modulos, ADR), e ao fazer commit ou stage (git add, git commit) neste repositório.
---

# task-ledger — trabalho que não se registra se paga duas vezes

Quatro casas, cada uma com UM tipo de fato. Duas casas para o mesmo fato não têm dois valores:
têm o da casa que ninguém confere.

| Casa | Guarda | Nunca guarda |
|---|---|---|
| `pending.md` | trabalho ABERTO, sob o grupo da demanda | narrativa longa de entrega, ✅ |
| `done.md` | ÍNDICE do que fechou: id · tarefa · data · âncora | o porquê (repetiria o CHANGELOG) |
| `CHANGELOG.md` | o porquê de cada entrega, medição, achados | lista de pendências |
| `TODO.md` | raciocínio e medição por assunto (decisão > 3 linhas) | status de tarefa |
| `CLAUDE.md` | invariante e regra arquitetural | tarefa, ✅, histórico |

A decisão original, restaurada palavra por palavra, está em
[`references/regras-originais.md`](references/regras-originais.md) — ela foi apagada do
`CLAUDE.md` pela DOC-02 sem destino, e é por isso que esta skill existe.

## 1. Antes de registrar qualquer coisa: procure

**Achado que não é procurado é achado que se paga duas vezes** — e a segunda pode contradizer a
primeira. Antes de abrir ficha ou escrever descoberta:

```bash
grep -n -i '<sintoma ou termo>' pending.md done.md TODO.md CHANGELOG.md
```

## 2. Abrir ficha

1. **Grupo = documento que existe** (ADR, spec ou arco), no formato `` ## `docs/…/x.md` — título ``.
   Sem documento: balde **`sem-demanda`**, e **incremente o contador** do balde.
2. **Id `AAA-NN`, único através dos DOIS arquivos.** Ids fechados não voltam ao pool:
   ```bash
   grep -ohE '\bPRE-[0-9]+' pending.md done.md TODO.md CHANGELOG.md | sort -u -V | tail -1
   ```
   Olhar só o `pending.md` já fez reusar id fechado (`PUL-03`).
3. **Linha de tabela**: `| id | tarefa | estado | evidência |`. A tarefa diz o que falta e a
   medição que a justifica; a evidência aponta arquivo:linha, gate ou `CHANGELOG.md` § data.
4. **Título NUNCA afirma status** — nem de ficha nem de grupo ("concluído", "completo", ✅).
   Status é coluna. O ramo F do portão reprova em título de GRUPO; no texto da ficha, nada
   confere — é disciplina, e foi num título velho que os nove marcadores mentiram.

## 3. Estados

| estado | exige | sai quando |
|---|---|---|
| `aberto` | — | fecha → `done.md` |
| `bloqueado` | impedimento **NOMEADO** (id do bloqueador, de preferência) | o impedimento cai |
| `adiado` | **gatilho declarado** em alguma coluna da linha | o gatilho dispara |

- `adiado` **não é** `done`: existe para decisão tomada não voltar à mesa.
- Bloqueio cujo bloqueador **já fechou** é ledger parado (ramo G). Ao fechar uma ficha, procure
  quem a citava como bloqueio.
- Corrigir o CORPO de uma ficha por medição é bem-vindo; diga a data e o que mudou
  ("⚠️ Corpo corrigido em AAAA-MM-DD por medição: …").

## 4. Fechar

Fechar é **MOVER**, e as três metades vão no **mesmo commit**:

1. **`CHANGELOG.md`**: entrada no topo, `## AAAA-MM-DD (n) — ID: frase do que mudou`
   (`n` sequencial no dia). Traz o porquê, a medição, o gate e o que ficou de fora.
2. **`done.md`**: apague a linha do `pending.md` e escreva no MESMO grupo do `done.md`
   `| id | tarefa (curta) | data | CHANGELOG.md § AAAA-MM-DD (n) |`. Índice, não narrativa.
3. **Documentação do que mudou**: rota de UI → `docs/modulos/`; arco ou backend significativo →
   `docs/arcos/`; padrão transversal → `docs/guias/`; decisão estrutural → `docs/adr/`.
   **Mudança no mecanismo de conferência** (lifecycle, chaves Redis, eventos, posatt, teardown)
   → `docs/guias/conference-mechanics.md` **e** entrada em § Histórico de Problemas e Correções.
4. Ficou trabalho para depois? **Ficha nova** (§2), citada na entrada do CHANGELOG
   ("Deixou ficha: XXX-NN"). Nunca "pendente" em prosa.

Portão, que não precisa de stack de pé:

```bash
wsl.exe -d ubuntu -- bash -lc 'cd /home/a1/projects/plughub && bash infra/test/probe_task_ledger.sh'
```

Ramos: A id bem formado · B grupo tem documento · C id em um arquivo só · D nenhum id sumiu
contra `HEAD` · E linha de `done` cita âncora · F título sem status · G bloqueio com bloqueador
fechado · H `adiado` sem gatilho.

## 5. Onde registrar cada decisão

| Tipo | Onde, imediatamente |
|---|---|
| Nova tarefa planejada | linha em `pending.md`, sob o grupo da demanda |
| Decisão técnica (> 3 linhas) | `TODO.md`, com o raciocínio |
| Invariante ou regra arquitetural | `CLAUDE.md` (resumo) — detalhe em `docs/` |
| Implementação concluída | `CHANGELOG.md` + linha em `done.md` |
| Feature grande (> 50 linhas de doc) | `docs/arcos/{nome}.md` + resumo de 15–20 linhas no `CLAUDE.md` |

## 6. Commit

Há **sessões paralelas na mesma árvore**. O que uma deixa sem commit é dela.

1. `git status --short` — veja o que é seu e o que não é.
2. **Índice vazio antes**: `git diff --cached --name-only` não pode listar nada.
3. **Stage e commit por caminho explícito**, nunca `git add -A` / `git add .`:
   ```bash
   git add -- <caminho> <caminho>
   git commit -F <arquivo-de-mensagem> -- <caminho> <caminho>
   ```
4. `.git/index.lock` presente = a outra sessão está commitando. **Espere e repita; nunca apague.**
5. Mensagem: assunto `ID: frase do que passou a ser verdade` (sem acento, como o histórico;
   `CLAUDE.md: …` ou `<pacote>: …` quando não há ficha); corpo com o porquê e a medição.
   Mensagem vai para arquivo no scratchpad — `-m` com `$`/crase atravessando o `wsl.exe` é
   comido pelo shell de fora (skill `deployment` § 6).
6. `CHANGELOG.md`, `pending.md` e `done.md` editados pela outra sessão: **não os inclua** no seu
   commit, mesmo que você também precise deles — espere ela commitar e edite sobre o `HEAD`.
7. Commit e push só quando o usuário pedir.
