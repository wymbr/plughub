# Passagem de sessão — Histórico unificado, depois de F0

> Sucessor de [`historico-unificado-kickoff.md`](historico-unificado-kickoff.md), que descrevia F0 antes de
> ele ser medido. **Onde os dois divergirem, este vale** — o kickoff contém duas previsões que a medição
> desmentiu, e elas estão nomeadas abaixo.
> Desenho: [`../adr/adr-historico-unificado-duas-visoes.md`](../adr/adr-historico-unificado-duas-visoes.md).

---

## 1. O que ficou provado

**F0.1 — o gate assimétrico do `collect` está fechado.** Medido no que RODA (não no repo), casando o
token da leitura e com testemunha de presença ao lado do contador de ausência:

```
grep -n 'async def handle_\|customer_resumable' …/adapters/webhook.py
  1501, 1689  → handle_delegate            (gate vivo, já existia)
  1814, 1969  → handle_collect             (NOVO — era zero)
  2540, 2713  → handle_delegate_conference (gate vivo, já existia)
```

`handle_collect` recebe `customer_resumable`/`resume_policy` e escreve a dual-write
`pending_by_customer`. O endpoint `/v1/channels/webhook/collect` os descartava porque monta os kwargs à
mão a partir de um dict cru — **o engine sempre os enviou** (`steps/collect.ts:192` →
`skill-flow-service/index.ts:590`). Ganho colateral: survey (J4c) e arco Outbound ganharam retomada
cross-canal que não tinham.

**F0.2 até o parking — smoke 17/0.** `skill_limite_entrega_v1.parquear_resultado` virou `collect`; a
pendência nasce `policy: auto`; o `context_preview` vem `{}` por desenho; o resultado vive em `journey.*`.

**Decisão aberta #1 do ADR — FECHADA.** *"`collect` que expira sem engajamento conta como contato?"*
**Não — porque não existe sessão.** `handle_collect` é LAZY: entrega o convite e suspende sem criar sessão
nem alocar recurso; a sessão só nasce em `handle_collect_engage`, quando o cliente abre o link (é lá que
`spawn_reason='collect'` e `root_session_id` são carimbados). Resposta por **ausência**, não por política:
não precisa de `_apply_contact_scope` nem do status `suspended`.

---

## 2. O que NÃO ficou provado — o risco que já está no ar

**O `invoke journey_merge` no intake nunca executou.** Está publicado no slot `current` do `limite_ia` e
não foi exercido por nada: o smoke é harness do **N3** (dispara o processo direto por `workflow_trigger`)
e o probe se ancora na entrega mais recente, que passou a ser a do smoke.

O modo de falha é o silencioso: `on_failure` do merge segue para `retomar_resultado`, que lê
`@ctx.journey.*` de uma raiz não unificada → **hash vazio → o agente narra valores em branco**.

Julga: **webchat → `limite_ia` → aprovar no Console → voltar pelo webchat.**
Critério: `probe_journey_limite.sh` sai de `INCONCLUSIVO` para 5/0.

---

## 3. Duas previsões do kickoff que a medição desmentiu

| Afirmação | Medido |
|---|---|
| *"depois de F0 a sessão-filha do `collect` aparece, logo o valor esperado é **4**"* | **3 → 3.** O lazy não materializa sessão sem clique, e a entrega real do link é trilha não construída. |
| *"`collect` cria sessão-filho de contato"* (ADR §F0) | Só **no engajamento**. Três das quatro coisas que o ADR credita ao `collect` (perna-como-sessão, direção outbound, proveniência) são condicionadas ao clique. |

Erro meu registrado no caminho: previ **4** para a baseline supondo que o `delegate` do parking criava
sessão contada em `limite_retorno`. Não cria — a baseline sempre foi 3
(`limite_ia`, `aprovacao_credito`, `limite_entrega`).

---

## 4. Decisão de desenho que mudou o escopo

O `CollectStepSchema` **não tem `context`** (o `delegate` tem, `skill.ts:1162`), e era por ele que o
`preview` levava `resultado`/`limite_aprovado`/`parecer` ao acesso 3 — o YAML avisava que era o *único*
caminho de volta. Quatro alternativas foram pesadas; escolhida a **A**: o resultado é fato do **processo**
e passou a viver em `journey.*` (hash da raiz canônica, TTL 30d, migrado pelo `journey_merge`).

Consequências, ambas assumidas:

1. **F1 foi feito junto com F0** — a leitura por `@ctx.journey.*` exige a raiz unificada, então o
   `invoke journey_merge` entrou no intake. O que resta do F1 do ADR é só o F1b.
2. **F5 (`ContextStorePersister`) subiu de prioridade.** O payload de negócio saiu de um preview em Redis
   (TTL 7d) para um hash em Redis (TTL 30d) — melhorou, mas o **conteúdo da entrega** agora depende de
   store efêmero. Era higiene; virou dependência.

Cadeia eliminada: `context_json` → ctx da entrega → `delegate.context` → `context_preview` → pendência →
intake. O mesmo fato era copiado por quatro lugares porque o `delegate` não tinha onde guardá-lo.

---

## 5. Achados laterais medidos (não redescobrir)

- **`sessions.pool_id` mente, ao vivo.** A sessão do processo aparece como `aprovacao_credito`, não
  `limite_processo` — é o último pool que escreveu. Confirma o achado M3 do ADR e é o que **F1b** conserta.
- **`session_transitions` tem 70 linhas, todas `suspend_reason='input'`.** A tabela não é fantasma: o par
  suspend→resume está persistido e não aparece em lugar nenhum da UI (o ADR já dizia; agora está contado).
- **`probe_journey_limite.sh` acusava a coisa errada.** `origin_session_id` vazio fazia a variável de
  origem receber o `pool_id`, e o veredicto anunciava *"raízes DIVERGEM (A=aprovacao_credito)"* — nome de
  pool onde ia session_id. **`IFS=$'\t'` não conserta**: tab é *IFS whitespace* e runs são dobrados mesmo
  declarados (`printf 'a\t\tb\n' | { IFS=$'\t' read -r x y z; }` → `x=[a] y=[b] z=[]`). Conserto: delimitador
  não-branco montado na query (`concat(a,'|',b,'|',c)` + `IFS='|'`). O probe ganhou terceiro ramo:
  ausência de intake = `INCONCLUSIVO`, exit 2.

---

## 6. Próximas tarefas, em ordem de dependência

1. **E2E do acesso 3** — bloqueia tudo. Ver §2. Única coisa que exercita o `journey_merge` e a leitura
   `@ctx.journey.*`, ambos já em produção.
2. **Corrigir a documentação desmentida** — ADR (§F0 ainda diz que `handle_collect` não honra os campos),
   kickoff (previsão `3 → 4`) e a nota do `CLAUDE.md` sobre o gate. `CHANGELOG.md` **só depois do item 1**:
   registrar como concluído um arco cuja peça central não rodou é o "valor plausível" que a postura de
   engenharia recusa.
3. **F1b — `entrou por`** (first-write-wins em `sessions.pool_id`). Independente de F0. Antes de virar a
   chave, **medir quem lê a coluna hoje**: dar-lhe significado é definir uma coluna que hoje é acidente, e
   quem depende do acidente quebra em silêncio.
4. **F2 — `root_session_id` em `/reports/segments`** (D10). Pré-requisito de backend do drill de 3 níveis.
5. **F3/F4 — as duas visões.** Só depois de 1, 3 e 4, senão a tela renderiza dado que ainda mente.
6. **F5 — `ContextStorePersister`.** Ver §4.2.
7. **Testes das peças novas.** `_resolve_pending_customer` tem três ramos (id nativo → `caller.customer_id`
   → âncoras do ctx) e o smoke exerce um; o ramo de aviso (`customer_resumable=true` sem cliente resolvido)
   nunca rodou. E falta um harness que entre pelo **N2** — a ausência dele é o motivo de o probe ter
   precisado do terceiro ramo.

---

## 7. Armadilhas confirmadas nesta sessão

- O caminho do arquivo no container é `/app/packages/channel-gateway/src/plughub_channel_gateway/…`
  (o kickoff dizia `/app/src/…`, e o `grep` devolveu *No such file* — inconclusivo, não "confirmado").
- **Deploy de skill exige `deploy_skill_to_slot.sh`** com âncora que só existe no flow novo. Usadas aqui:
  `journey.resultado` (limite_processo), `channel_policy` (limite_entrega), `journey_merge` (limite_ia).
- **O smoke não passa pelo N2.** Rodar o probe logo depois dele ancora no caso sintético — por isso o
  terceiro ramo. Para julgar propagação de raiz, rodar o probe depois de um caso REAL.
- `e2e` apaga `tenant_demo:*` e `session:*` antes de cada cenário — não rodar no meio de uma medição de
  journey.
