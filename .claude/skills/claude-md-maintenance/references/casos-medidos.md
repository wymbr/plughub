# Casos medidos — base da skill `claude-md-maintenance`

> Levantado em 2026-09-16. Linhas do CLAUDE.md mudam; use a citação como ponto de partida e abra o
> arquivo antes de repetir um número.

## O orçamento de linhas — texto movido do CLAUDE.md § *Saúde do CLAUDE.md*

Movido **na íntegra** em 2026-09-16. Lá ficou o alvo e um ponteiro para cá.

> **Por que 800 caiu, e é aritmética, não gosto.** O arquivo estava em 1 883; as três maiores seções
> somam 567 linhas (*Saúde* · *Security* · *Postura*), então **apagar as três inteiras daria 1 316** —
> ainda 116 acima de 800. Um alvo abaixo do piso que as próprias regras protegem é promessa que o
> arquivo não pode cumprir, e a próxima sessão que o lesse tentaria cortar o catálogo para alcançá-lo.
> As outras 51 seções somavam 1 175 linhas, média de **23** — já no formato de resumo que a tabela
> abaixo pede. O 800 foi fixado antes de *Postura* e *Security* crescerem para carregar o catálogo
> medido; ele descrevia um arquivo que não existe mais.
>
> **1 750 nasce cumprido, com folga estreita de propósito:** os dois movimentos que o alcançaram
> (índice de docs → `docs/INDEX.md`; cinco seções de arco apertadas ao formato) esgotaram o que havia
> a mover. A folga é o orçamento de crescimento — seção de arco nova que passe de 20 linhas o estoura,
> que é exatamente o que ele deve cobrar.

**Estado em 2026-09-16:** 1 505 linhas. A folga "estreita" deixou de ser verdade quando método e
casos medidos foram para as skills (1 778 → ~1 505). **O alvo não foi baixado**: isso é decisão do
dono, e o § 5 da skill manda não subir nem descer o número por conta própria.

## Como as 9 skills foram feitas — a base do § 6 (2026-09-16)

- **Medição que corrigiu a skill antes do commit:**
  - `deployment`: a afirmação *"arquivo novo precisa de `--no-cache`"* veio copiada de um comentário
    do `rebuild-all.sh`. Medida de três formas, foi refutada, e o comentário também foi corrigido;
  - `skill-flow-authoring`: o `curl` do validate mandava o YAML plano, mas o corpo é metadados +
    `flow: {entry, steps}`;
  - `platform-ui-change`: `npx tsc` saía `0` sem compilar o projeto. Trocado pelo `tsc` local com
    contagem de arquivos.
- **Testes em sessão nova:** canário e controle negativo nas 9 skills, mais sobreposição
  (`platform-ui-change` + `security-boundaries`). Todos passaram. Três achados vieram das
  próprias sessões de teste:
  - a sessão de sobreposição achou a AUD-05;
  - a de `claude-md-maintenance` refutou a premissa da pergunta (12 → 11 módulos);
  - a de `plughub-review` achou 6 pontos no commit revisado.
- **Defeitos registrados como ficha, não consertados:** TRF-01, ALW-20, PUI-01, SES-01, AIG-01,
  SFE-01, AUT-57, AUD-05.
- **Checagem de skills do `check_claude_md.py`:** a primeira rodada contra as skills reais deu 2
  ERRO/ATENÇÃO, os dois **falsos positivos do verificador**:
  - `deployment` "fora do índice", porque a regex exigia hífen no nome;
  - `infra/tool-guard.ts` "sumiu", mas era atalho relativo a pacote e colidia com `infra/` da raiz.

  Os dois viraram casos do `--selftest`. Um terceiro apareceu quando esta própria skill acrescentou
  uma frase ao parágrafo do índice: com a regex afrouxada, o `` `name` `` da frase passou a contar
  como skill. Agora só conta a lista separada por `·`, e o caso também está no selftest.
- **Resultado:** 9 skills, 9 no índice, 93 caminhos conferidos (19 com `:linha`), 0 sumidos.
  Mutação no CLAUDE.md real (tirar `task-ledger`, `plughub-review` e `claude-md-maintenance` do
  índice) deixou o verificador VERMELHO nas três.
- **Lição:** o selftest sintético passava antes de o verificador rodar contra o repositório real,
  e só as rodadas reais acharam os três erros. Em uma delas, o `rc=0` que parecia verde era o shell
  de fora comendo o `$?` (skill `deployment` § 5). Com script em arquivo, o código real era `1`.

## Afirmações que o CLAUDE.md fez e o código não sustentava

Todos estes casos têm a correção registrada no próprio CLAUDE.md ou no ledger. O padrão se repete:
a frase era plausível, ninguém a conferia, e a próxima sessão agia com base nela.

| Caso | O que dizia | O que era | Lição |
|---|---|---|---|
| Abertura (2026-08-13) | "connects agents … from any origin" | borda de terceiro só existe se o operador subir o sidecar | afirmação de produto também se mede |
| Config seed-if-absent (2026-08-22) | "o demo está em `dispatch: inline`" | a config viva dizia `detached`; o erro foi lido do YAML **ao corrigir** | meça no estado vivo, nunca na fonte declarativa |
| Arc 19 perfis (CTR-01, 2026-09-06) | "validado em parse do YAML + guard no engine" | não havia allowlist em lugar nenhum | "é validado" pede o nome do validador |
| Arc 19 TMA (D9) | "TMA webhook = `SUM(segment.duration_ms)`" como implementação | o código fazia wall-clock; a soma era conceitualmente errada | descrever intenção como implementação |
| Repository Structure (2026-08-31) | tabela com 21 pacotes | 35 pacotes | lista incompleta parece completa |
| Arc 7 (2026-09-08) | "8 módulos" | 12 no catálogo | contagem em prosa envelhece calada |
| Audit LGPD (AUT-41) | campos `audit.*` "no catálogo" | `audit` não estava em `modules.yaml` nem no registry vivo | três casas afirmando, zero conferindo |
| Instance Bootstrap (2026-09-14) | modelo de rascunho `flow_draft` + `x-skill-publish` | abandonado em 2026-07-13 | texto de fase antiga sobrevive à fase |
| Mention (MEN-01, 2026-09-12) | "Never allow AI agents to emit @mention" | a IA que conduz é `primary`; `role: human` nunca existiu | regra escrita sobre um eixo que o domínio não tem |

## Mudanças de casa que perderam ou duplicaram regra

- **§ Pending (DOC-01, 2026-09-05).** Ela era uma segunda lista de trabalho aberto, ao lado do
  `pending.md`. Seis itens de Customer History ficaram "abertos" por seis semanas depois de
  fechados. A remoção foi conferida **item a item**: sete já estavam no ledger, e os que não estavam
  foram escritos lá no mesmo commit. É o modelo de como mover.
- **Índice de docs (DOC-02, 2026-09-06).** As 95 linhas foram para `docs/INDEX.md`, mas as regras
  de onde ESCREVER (ledger, onde registrar decisão, convenção de pastas) saíram junto **sem
  destino**. O parágrafo que ficou dizia que elas "continuavam abaixo". Foram restauradas em
  2026-09-16 na skill `task-ledger`, com o texto exato de `76b272b0^`.
- **Nota de status em prosa (pending.md, AUT-03).** Uma nota dizia `bloqueado` oito dias depois de
  a ficha fechar. O `probe_task_ledger.sh` lê tabela, não parágrafo.

## Medições de 2026-09-16

- Nenhum gate de `infra/test/` nem `infra/*.py` lê o **conteúdo** do CLAUDE.md raiz. Os 26 que o
  citam fazem isso em comentário ou mensagem (`grep` por `grep|cat|sed|open(` + `CLAUDE.md`). O
  `probe_mcp_tool_guard_census.sh` cita um invariante de `packages/mcp-server-plughub/CLAUDE.md`,
  mas só na mensagem: não lê o arquivo.
- 148 ocorrências de `CLAUDE.md § …` no repositório (`git grep -E 'CLAUDE\.md`? ?§'`). As que não
  casam com cabeçalho atual estão quase todas em `CHANGELOG.md`/`done.md`/docs de passagem, ou seja,
  são históricas. Uma está em arquivo vivo e já foi tratada: `§ Ledger de tarefas`, citada em
  `task-ledger/references/regras-originais.md` como o nome antigo.
- 11 CLAUDE.md de pacote, com 1 596 linhas no total. O maior é `orchestrator-bridge` (372).
- Primeira rodada do `check_claude_md.py`: 0 ERRO e 21 ATENÇÃO (14 na raiz, 7 nos pacotes).
  - Arcos longos: Arc 7 com 47 linhas, Arc 15 com 32, Arc 6 Fase 2 com 24, Arc 19 com 21.
  - ✅ de status: D14 (`:851`), Arc 19 (`:1394`, `:1398`) e dois em `mcp-server-plughub/CLAUDE.md`.
  - O `mcp-server-plughub/CLAUDE.md` diz **73** tools (`:44`, remedido em 09-14) e **72** (`:85`,
    num trecho de 09-01). Não é contradição, é contagem dentro de um trecho datado. Por isso
    `contagem` é ATENÇÃO, não ERRO.
