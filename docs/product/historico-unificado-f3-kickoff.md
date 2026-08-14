# Kickoff — Histórico unificado, Fase F3: visão 1 (lista de contatos)

> Cole isto no início da sessão. **Uma sessão = F3.** Esta é a **primeira fase de UI** do arco —
> as anteriores foram todas backend.
> Desenho: [`historico-unificado-telas-design.md`](historico-unificado-telas-design.md) §1 ·
> ADR: [`../adr/adr-historico-unificado-duas-visoes.md`](../adr/adr-historico-unificado-duas-visoes.md) D3/D8/D12/D12b ·
> Plano: [`historico-unificado-plano-execucao.md`](historico-unificado-plano-execucao.md).
> Estado em 2026-08-14: **F0 ✅ · F1 ✅ · F1b ✅ · F2 ✅** (+ dívida da F2 fechada). F3 depende de F1b,
> que está pronta.

---

## ⚠️ Leia isto antes de confiar no resto deste arquivo

O kickoff da F1b errou em **três** pontos, e cada erro custou uma rodada. Ele errou *por escrito*,
que é o único motivo de terem sido pegos. Os erros não foram de raciocínio — foram de **afirmar
estado de código sem medir**: um produtor que "resolvia o pool" e não resolvia, um risco de ABAC dado
como latente que atingia 2 usuários reais, e um gate proposto que **não podia reprovar**.

Este arquivo tem o mesmo defeito potencial. Tudo abaixo marcado com 📏 é **afirmação a verificar**,
não fato estabelecido. O inventário de frontend foi levantado por leitura em 2026-08-14 e já corrigiu
três erros do plano de execução (abaixo) — mas leitura de código não é execução.

---

## O que é, em uma frase

`/analise/sessions` passa a ser a **única** lista de contatos: ganha os dois filtros de pool com nomes
distintos, a coluna de **direção do acesso**, o **chip de processo**, e absorve o que hoje vive em
`/analise/processos`. O processo **nunca** é linha (D3) — chega-se a ele pivotando do chip.

---

## Correções ao plano de execução — medidas, não supostas

O `historico-unificado-plano-execucao.md` §F3 diz *"`/analise/sessions` absorve `/analise/processos`;
`AnaliseProcessosPage.tsx` é código morto e sai; `AnaliseJourneysPage` deixa de ser página"*. Três
imprecisões:

| O plano diz | O código diz (2026-08-14) |
|---|---|
| `AnaliseSessionsPage` | **não existe.** A rota `/analise/sessions` é servida por `SessionsPage` (`modules/contacts/SessionsPage.tsx`, 350 L), e a lista em si é o `ListaTab` (`modules/contacts/tabs/ListaTab.tsx`, 335 L) |
| `AnaliseProcessosPage` é código morto | **confirmado** (`modules/analise/AnaliseProcessosPage.tsx`, 518 L, zero `import` em `src/`). Sai junto o bloco i18n `contacts.processes` (`:753`), consumido só por ela. Sai também `modules/analise/OriginSelector.tsx`, morto pelo mesmo critério |
| `AnaliseJourneysPage` "deixa de ser página" | ela **é** quem serve `/analise/processos` hoje (870 L). Não é remoção — é reenquadramento para nível 2 da rota unificada |

---

## O que JÁ existe — e por isso a fase é menor do que parece

📏 **O backend já devolve os três campos das colunas novas.** `_fetch_sessions` seleciona
`elapsed_time_ms` (`reports_query.py:905`), `spawn_reason` (`:914`) e `root_session_id` (`:915`).
**Não há trabalho de backend previsto nesta fase.** O que falta é tipagem + render:

| Campo | Backend | `ContactRow` (`modules/contacts/types.ts:24-46`) |
|---|---|---|
| `spawn_reason` | ✅ devolve | ❌ não tipado |
| `root_session_id` | ✅ devolve | ❌ não tipado |
| `elapsed_time_ms` | ✅ devolve | ❌ não tipado (a coluna de duração usa `handle_time_ms`) |
| `is_internal` · `total_internal` | ✅ | ✅ já tipados e em uso |
| `window_applied` | ✅ (novo, 2026-08-14) | ❌ nenhum consumidor no front |

**Primeira coisa a fazer na sessão** é confirmar isso com uma leitura real do endpoint, não com o
`grep` acima:

```bash
curl -s "http://localhost:3500/reports/sessions?tenant_id=tenant_demo&page_size=1" \
  | jq '.data[0] | {spawn_reason, root_session_id, elapsed_time_ms, is_internal}'
```

Previsão: os quatro presentes, `spawn_reason` provavelmente `null` (sessão de topo). Se algum vier
**ausente** (≠ `null`), o alias não sai do SQL e a fase ganha uma fatia de backend.

Outros reaproveitáveis:

- **O chip de processo já existe**, noutro módulo: `agent-assist/components/tabs/HistoricoTab.tsx:204-215`
  (botão-chip `GitBranch` + rótulo da raiz que navega para `/analise/processos?journey=…`). Extrair,
  não reinventar.
- `Badge` (`components/ui/Badge.tsx`) é o único chip do design system — 18 variantes, **sem**
  `onClick` e sem variante de processo.
- `SegmentList.tsx` (`modules/service/components/`, 420 L) já funde segmentos + sessões-filhas numa
  timeline única ordenada por tempo, e já classifica filha em `internal|process|contact`. É a base da
  F4, não da F3 — mas a função `childKind()` (`:207-211`) é onde a classificação vive.

---

## ⛔ Bloqueia o código: a decisão aberta #2

O chip **conta o processo inteiro, não a fatia filtrada** (§1 do desenho): uma janela que pega 2 de 3
contatos mostra `· 3`. Isso **vai parecer defeito**, e a mitigação prevista é um rótulo explícito no
rodapé da tabela — cujo texto **nunca foi escrito**.

Não começar o chip antes de fechar esse texto. Duas coisas o texto tem de dizer ao mesmo tempo, sem
mentir: (a) o número do chip ignora o filtro de período; (b) por que isso é o certo (o processo não
encolhe porque você olhou uma semana dele).

E ele agora tem um aliado que não existia quando a decisão foi aberta: **`meta.window_applied`**
(entregue em 2026-08-14). O rodapé pode ser condicional em vez de permanente.

---

## Armadilha de nomenclatura — a colisão está no i18n, não no código

`contacts.lista.columns.origin` **já significa ANI** (`ListaTab.tsx:309-311`), e
`columns.destination` significa DNIS. O vínculo de proveniência já se chama `parent`
(`:107`, `:297-308`).

Se a coluna de **direção do acesso** (D8: inbound ⇣ · outbound ⇡ · interno ⚙) entrar chamada
`origin`, ela colide com a chave existente e o operador lê um e recebe o outro — **exatamente o erro
que este arco existe para corrigir**, cometido de novo na mesma tela. Escolher outro nome (`direcao`
→ chave `columns.direction`) e conferir os dois locales antes do PR.

Lembrete relacionado: as colunas `origin (ANI)` e `destination (DNIS)` **saem e não voltam** (achados
1 e 3: permanentemente vazias nos dois canais existentes). Sai com elas o filtro `filter.ani`/
`filter.dnis` (`contacts.json:147-148`).

---

## O trabalho, em fatias

### F3.1 — a lista (sem chip)

Tipar os 3 campos em `ContactRow`; trocar a coluna de duração para `elapsed_time_ms` (**nunca**
`agent_time_ms`, nunca Σ segmentos — ADR §D9); acrescentar a coluna de direção derivada de
`spawn_reason` + canal; remover ANI/DNIS (colunas e filtros).

Desempate da direção, do desenho: `NULL`→inbound · `collect`→outbound · `trigger`/`delegate`→interno,
com `customer_id` de prefixo `sys:` como critério auxiliar. 📏 **Verificar se `sys:` existe no
ambiente** antes de codificar o desempate — pode ser regra sem população.

### F3.2 — os dois filtros de pool

`entrou por` = `sessions.pool_id` (o que a F1b definiu) · `atendido por` = subconsulta em `segments`.

📏 **O backend já tem os dois?** O parâmetro `pool_id` de `/reports/sessions` **já é** a subconsulta
em `segments` (`reports_query.py:633-640`) — ou seja, o filtro existente é *"atendido por"*, e o que
**falta é o filtro de entrada**, não o contrário. Confirmar antes de planejar: se faltar, é a única
fatia de backend da fase (um parâmetro novo, `entry_pool_id`, sobre `s.pool_id`).

Os dois nunca se chamam "Pool" na tela.

### F3.3 — o chip + a unificação das rotas

Chip `PRC-{root[:4]} · N`, só quando `N > 1`, computado sobre a página retornada. `/analise/processos`
vira nível 2 da mesma rota; `AnaliseProcessosPage.tsx` + `OriginSelector.tsx` + bloco i18n
`contacts.processes` são deletados.

---

## Gate — e o que o faria reprovar

UI não tem gate de shell. O que **pode** ser afirmado programaticamente:

1. **Contrato**: o `curl` acima, com asserção sobre os 4 campos (é o que impede a fase inteira de ser
   construída sobre um campo que o endpoint não manda).
2. **i18n**: nenhuma chave nova só num locale. Um `jq` comparando as árvores de `en/contacts.json` e
   `pt-BR/contacts.json` — a invariante do `CLAUDE.md` diz "os dois locales antes do PR", e o modo de
   falha é a tela em inglês no meio do português, que passa despercebido em review.
3. **Código morto**: depois da remoção, `grep -r AnaliseProcessosPage packages/platform-ui/src` = 0.

O que **não** dá para afirmar por script (direção correta na linha, chip contando o processo inteiro)
precisa de conferência na tela, com uma sessão de cada tipo à mão. A journey de referência é
**`d62d7121-07b9-43dd-99ff-c5785d520e58`** (3 sessões: intake `limite_ia` → análise `limite_processo`
→ entrega `limite_entrega`), criada em 14/08 e validada por `probe_journey_limite.sh` 5/0. Ela tem
`N=3`, então o chip deve aparecer; e tem uma perna de cada direção.

⚠️ **Não rodar `smoke_limite_tres_acessos.sh` para "gerar dados"** sem necessidade: ele cria cadeias
disparadas direto no N3 (sem intake) e **desloca a amostra** que o `probe_journey_limite.sh` julga —
foi o que o deixou INCONCLUSIVO em 14/08. Se rodar, revalidar aquele probe com um caso real depois.

---

## Fora de escopo nesta sessão

- **F4** (visão 2 — pivô, lentes A/B, internas dobradas). O chip *leva* à visão 2; construí-la é a
  próxima fase.
- **F5** (`ContextStorePersister`).
- A dívida de UI que a unificação **herda** e não deve tentar limpar de passagem
  (telas-design §4: hex hardcoded, `text-[10px]`).
- O resíduo do `session_id` + janela (ver `TODO.md`) — backend, e mexe na testemunha de outro gate.
- Os dois achados órfãos abertos pela F1b: o overload de `pool_id` no whatsapp e as 15 sessões que
  existem em `segments` e não em `sessions`.

---

## Armadilhas de ambiente

- **`platform-ui` NÃO tem volume mount** — toda mudança de frontend exige `build`, não `restart`.
  Editar e recarregar o browser não mostra nada, e a ausência de mudança parece bug de código.
- **Arquivo NOVO exige `build --no-cache`**; edição de arquivo existente entra no build normal.
- **i18n**: toda string visível nos **dois** locales, sempre por `t()`. Namespace `contacts` já
  registrado em `src/i18n/index.ts:8,37,68,97,127`.
- **e2e apaga `tenant_demo:*` e `session:*`** antes de cada cenário — levaria junto a journey de
  referência.
- Portas: analytics-api **3500** · platform-ui **5174** · auth-api **3202** (3200 é o ai-gateway).
- Não existe cliente de API tipado: cada tela monta `URLSearchParams` e chama `apiFetch`. Se a fase
  criar um, isso é refactor — declarar, não fazer de contrabando.
