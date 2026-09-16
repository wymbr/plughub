---
name: claude-md-maintenance
description: Como acrescentar, corrigir, mover ou apagar texto no CLAUDE.md do PlugHub (raiz ou packages/<pacote>/CLAUDE.md) sem criar segunda casa para o mesmo fato, afirmação que o código não sustenta, regra perdida na mudança ou estouro do orçamento de linhas. Use ao editar CLAUDE.md, criar ou apertar seção de arco, registrar invariante novo, corrigir frase medida falsa, decidir se algo vai para CLAUDE.md, skill, docs/arcos, pending.md, TODO.md ou CHANGELOG.md, e ao mover regra do CLAUDE.md para uma skill.
---

# claude-md-maintenance — o CLAUDE.md é lido como verdade, então cada linha tem de ser

O CLAUDE.md raiz entra em **toda** sessão. Uma frase errada ali não fica parada: a próxima sessão
age com base nela. O custo já foi pago mais de uma vez. Casos e medição estão em
[`references/casos-medidos.md`](references/casos-medidos.md).

## 1. Onde o fato mora — decida ANTES de escrever

| O fato é… | Casa | No CLAUDE.md |
|---|---|---|
| regra que tem de valer mesmo quando ninguém pede (invariante, "never") | **CLAUDE.md** | o texto inteiro, curto |
| método, procedimento, checklist, caso medido que justifica a regra | **skill** (`.claude/skills/`) | ponteiro de uma linha |
| detalhe de arco (> 20 linhas, UI, snippet > 10 linhas) | `docs/arcos/<arc>.md` | resumo de 15–20 linhas + link |
| trabalho aberto | `pending.md` (skill `task-ledger`) | **nada**; a § Pending só aponta |
| o porquê de uma entrega, histórico, ✅, testes X/Y | `CHANGELOG.md` + `done.md` | nada |
| raciocínio e medição por assunto | `TODO.md` | nada |
| regra que só vale dentro de um pacote | `packages/<p>/CLAUDE.md` | nada, ou uma linha se cruza pacotes |

**Duas casas para o mesmo fato não têm dois valores; têm o valor da casa que ninguém confere.**
Antes de escrever, faça `grep` do fato no CLAUDE.md, nos 11 CLAUDE.md de pacote e em `docs/`. Se
ele já mora em outro lugar, aponte para lá em vez de copiar.

## 2. Escrever uma afirmação

1. **Meça antes, no código ou no estado vivo, nunca na fonte declarativa.** YAML de
   `infra/registry/` é seed-if-absent; para config viva pergunte à API. Um docstring não é
   mecanismo.
2. **Diga qual mecanismo impõe a regra** (gate, teste, validador) ou deixe claro que é só prosa.
   Invariante sem mecanismo é promessa (§ Postura).
3. **Número sobre coleção viva envelhece calado** (`11 módulos`, `73 tools`). Prefira apontar o
   instrumento que conta. Se precisar do número, date: *"medido em AAAA-MM-DD"*.
4. **Não escreva status no título nem no parágrafo** (`✅`, "completo", "pendente"). Status mora no
   ledger. Nota de status em prosa sobrevive ao fato, e o `probe_task_ledger.sh` não lê prosa.
5. Identificadores em inglês; português só no texto (§ Language Rule).

## 3. Corrigir uma frase medida falsa

- **Formato da casa:** `*Correção AAAA-MM-DD:* dizia X; medido Y (onde).` Deixe o erro visível
  quando é provável que alguém ainda acredite na versão velha (ela está em doc, em memória, em
  outra skill). Quando ninguém mais a repete, a correção vira só o texto certo, e o histórico vai
  ao `CHANGELOG.md`.
- **Corrija TODAS as casas no mesmo commit:** `git grep -n "<trecho da frase>"` no repositório
  inteiro, incluindo CLAUDE.md de pacote, `docs/`, `.claude/skills/` e a memória do usuário.
- **Ao corrigir, meça outra vez.** Uma correção já foi feita lendo o YAML e trocou uma frase certa
  por uma errada.
- Achou a frase falsa, mas o conserto é código? Corrija a frase **e** registre ficha. Não deixe a
  frase de pé esperando o código.

## 4. Mover ou apagar texto

1. **Nunca apague sem destino.** Liste cada regra do trecho e confira, item por item, que ela
   existe na casa nova **antes** de remover (precedente DOC-01). A DOC-02 apagou as regras do
   ledger dizendo que elas *"continuavam abaixo"*, e elas ficaram sumidas por dez dias.
2. **Mover para skill: o texto vai inteiro** para `references/`, sem resumo. No CLAUDE.md ficam
   os invariantes e um ponteiro. Resumir na mudança é como uma regra se perde.
3. **Cabeçalho é endereço.** Há ~150 citações `CLAUDE.md § <seção>` no repositório (medido
   2026-09-16). Antes de renomear ou remover um `##`/`###`, faça
   `git grep -n "CLAUDE.md.*§ *<nome>"`. Em arquivo vivo (doc, skill, gate), atualize a citação.
   Em `CHANGELOG.md`/`done.md`, deixe como está: é histórico.
4. **Nenhum gate lê o conteúdo do CLAUDE.md raiz** (medido 2026-09-16: 26 citam o arquivo, todos
   em comentário ou mensagem). Mover texto não quebra instrumento. Confira de novo se isso mudar.
5. Seção nova de arco: **15–20 linhas + link** para `docs/arcos/`. Se não couber, o detalhe é do doc.

## 5. Orçamento e verificação

- **Alvo: ≤ 1 750 linhas** na raiz (o porquê do número está nas referências). Está acima? Aplique
  a tabela do § 1 ao que já está lá antes de subir o alvo. Subir o alvo é decisão do dono.
- **Verificador de forma:**

  ```bash
  wsl.exe -d ubuntu -- bash -lc 'cd /home/a1/projects/plughub && python3 .claude/skills/claude-md-maintenance/scripts/check_claude_md.py'
  ```

  - **ERRO**, que reprova: `orcamento` · `link-quebrado`.
  - **ATENÇÃO:** `arco-longo` · `status-check` · `historico` · `contagem`.
  - `--selftest` prova que cada regra dispara e que os 5 casos limpos não disparam.
  - Na primeira rodada (2026-09-16), a raiz deu 0 ERRO e 14 ATENÇÃO, incluindo Arc 7 com 47
    linhas, Arc 15 com 32 e ✅ nas seções D14 e Arc 19. **Não conserte o que não é seu no mesmo
    commit**: registre.
- ⚠️ **O verificador mede FORMA, não verdade.** Verde não prova que o arquivo está certo; quase
  todo erro medido do CLAUDE.md foi de conteúdo (§ 2 e § 3).
- **Sessões paralelas editam o CLAUDE.md.** Rode `git status CLAUDE.md` antes de editar. Se ele
  estiver modificado por outra sessão, não sobrescreva: espere, ou edite só o trecho seu. O commit
  vai por caminho explícito.
- Mexeu no critério de divisão ou no índice de skills? Atualize a § *Onde a documentação mora*.
