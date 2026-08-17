# Kickoff — Segmento que nunca fecha · Volume de sessões inexplicado

> ## ⛔ ESTE KICKOFF FOI EXECUTADO EM 2026-08-17 — não reutilizar como está
>
> A sessão mediu tudo o que este arquivo pedia e **derrubou parte do que ele afirma**. O estado
> vivo passou para:
>
> - [`TODO.md`](../../TODO.md) § *"Segmento que nunca fecha"* e § *"Volume de sessões inexplicado"*
> - [`docs/guias/conference-mechanics.md`](../guias/conference-mechanics.md) § *Problema 34* (família fila)
> - [`CHANGELOG.md`](../../CHANGELOG.md) 2026-08-17 (instrumentação do publish + 6 probes)
>
> **O que este arquivo diz e a medição negou:**
>
> | afirmação daqui | medido |
> |---|---|
> | *"a espera em fila deve contar como tempo de agente? as duas leituras não podem estar certas"* | pergunta **já respondida pelo código**: `agent_time_ms` filtra `role IN ('primary','specialist')` (`reports_query.py:1354`) — `queue` está fora **por papel**, não por `duration_ms IS NOT NULL`. O segmento aberto de fila não custa tempo de agente; custa UI. A perda real de `agent_time_ms` vem dos **7 casos de pools de workflow**, não da fila |
> | *"o participante de fila sai por superação"* | é a forma dos 2 casos de fila, mas **não** dos outros 7 — são 5 formas distintas (A, B1, B2, B3, B4), com posições diferentes do órfão |
> | *"+167 contatos numa execução de e2e"* | **a rajada não existe no dado**: ~1 sessão/minuto, 8 dias, 14 pools; `origin` 300/300 `live`; 4 sessões sem segmento. As duas explicações propostas caem; a terceira (tiers do `/reports/sessions` degradando em silêncio) segue **não medida** |
> | *"9 segmentos abertos"* | 9 **em sessão fechada**. O tenant tem **26** abertos: 15 são seed (`dlz_*`/`sess_epoch_*`) e 2 estão em sessões abertas. Probe sem filtro mede 65% de contaminação |
>
> **Quatro hipóteses eliminadas por medição — estão listadas em `TODO.md` com o porquê de cada uma.**
> Não refazer: timeout/prazo, exceção+retry do dispatcher, empate de `ReplacingMergeTree`,
> concorrência da mesma instância.
>
> A cadeia de medição em tabela (§ "A cadeia medida") continua válida e reusável. O resto é histórico.

> Cole isto no início da sessão. **Uma sessão = estes dois itens**, nesta ordem.
> Os dois foram achados durante a F3 (2026-08-14) e **registrados sem conserto**, de propósito: um
> toca mecânica de conferência, o outro nem foi medido ainda.
> Estado: F3 ✅ na tela e commitada. `TODO.md` tem as duas seções com a cadeia completa.

---

## ⚠️ Leia isto antes de confiar no resto deste arquivo

O kickoff da F3 errou em **três** afirmações de estado de código, cada uma custando uma rodada — e
todas eram do mesmo tipo: *"o código faz X"* escrito sem executar nada. Além disso, o autor deste
arquivo (a sessão da F3) errou **duas previsões** que tinha escrito antes de medir.

Tudo abaixo marcado com 📏 é **afirmação a verificar**, não fato. O que está em tabela de medição
foi rodado em 2026-08-14 contra `tenant_demo` e pode ser reusado; o resto, não.

**Regra que esta sessão herda:** ao propor conserto, escreva primeiro *que número exatamente vai
aparecer na tela* — na unidade que o instrumento imprime, não na unidade em que você pensou a
mudança.

---

## Item 1 — Segmento que nunca fecha (diagnosticado até o produtor)

### O sintoma

Um contato **encerrado** exibe um segmento com `live` + `join`, e o cabeçalho diz `1 active`.
A UI está honesta: `SegmentList.tsx:96` deriva `live` de `ended_at === null`. O defeito é a montante.

### Por que não é cosmético — e a decisão de produto embutida

- **`agent_time_ms` filtra `duration_ms IS NOT NULL`** ⇒ segmento que nunca fecha fica **fora** do
  tempo de agente.
- O **`join`** oferece entrar numa conferência já destruída.
- O contador de ativos mente num contato fechado.

**A pergunta que o conserto obriga a responder:** a espera em fila deve contar como tempo de agente?
Se sim, ela está sumindo dos agregados hoje. Se não, então `role='queue'` não deveria ser um segmento
de agente. **As duas leituras não podem estar certas ao mesmo tempo** — escolher é parte da fatia, não
detalhe de implementação.

### A cadeia medida (2026-08-14, `tenant_demo`, sessão `61dd213c…`) — não refazer

| Passo | Resultado |
|---|---|
| segmentos abertos em sessão FECHADA, por papel | `primary` 5/597 · `queue` 2/11 · `specialist` 2/68 ⇒ **9 em 676 (1,3%)** |
| `queue` no tenant inteiro | **14 fechados**, 2 abertos ⇒ o caminho normal FUNCIONA |
| os 9, nomeados | **9 sessões distintas**; `close_reason` variado (`flow_complete` ×6, `agent_hangup` ×2, `customer_abandon` ×1); 2 canais; 6 skills; 5 dias |
| `segments` **sem `FINAL`** | 1 versão por `segment_id` ⇒ **inconclusivo** (o merge pode ter comido a anterior) |
| `session_timeline` | **vazia** para a sessão ⇒ instrumento inútil aqui |
| `participation_intervals` | fila com `left_at = ∅` ⇒ **o evento nunca foi publicado** |

### Quatro hipóteses JÁ DESCARTADAS — não redescobrir

1. *"a fila nunca fecha"* — fecha em 14 de 16.
2. *"é específico do papel `queue`"* — `primary` e `specialist` também têm casos.
3. *"corrida de ordenação entre tópicos"* — `segments` é escrita pelo par
   `participant_joined`/`participant_left` do **mesmo** tópico `conversations.participants`
   (`clickhouse.py:376`), não por dois. *(Esta hipótese foi afirmada e depois retirada na própria
   sessão da F3, por leitura do DDL.)*
4. *"sobrescrita `ReplacingMergeTree`"* — não sustentada: a linha nunca recebeu o rewrite **e** o
   evento também falta em `participation_intervals`. O fato não existiu.

### O diagnóstico

O participante de fila **nasce da saída do agente anterior** (`sac_ia` sai `16:09:28.912`, a fila
entra `16:09:28.965` — 53 ms depois) e **desaparece quando o humano assume** (`16:09:41`). Ele sai por
**superação**, não por término negociado — e esse caminho não publica `participant_left`.

### Onde mexer, e o que isso obriga

📏 Produtor do `conversations.participants` — orchestrator-bridge / routing, no caminho de alocação
que **esvazia a fila**. Verificar antes de escrever qualquer coisa.

⚠️ Toca mecânica de conferência ⇒ pelo `CLAUDE.md`, **exige** atualizar
`docs/guias/conference-mechanics.md` § Histórico de Problemas e Correções antes de considerar
concluído.

### Gate — e o que o faria reprovar

Não basta "o segmento fecha". O gate precisa de **testemunha**: os **14 que já fecham** têm de
continuar fechando. Um conserto que feche o segmento de fila fechando *todos* os segmentos no
`session_closed` passaria numa asserção ingênua e destruiria a distinção entre segmento fechado por
`agent_done` e segmento fechado à força.

Sugestão de forma: contagem de `ended_at IS NULL` em sessões fechadas **antes e depois**, com as três
linhas de papel lado a lado — e o número previsto por extenso (hoje: `primary` 5, `queue` 2,
`specialist` 2).

---

## Item 2 — +167 contatos numa execução de e2e (ainda NÃO medido)

Duas leituras de `/analise/sessions` com **a mesma janela** (07/08→14/08), **mesmo escopo**
(`contacts`, toggle desligado) e **o mesmo build**, separadas por uma execução de e2e:

| | `meta.total_contacts` |
|---|---|
| antes | **118** (3 páginas) |
| depois | **285** (6 páginas) |

O e2e deveria criar **uma** sessão com 5 segmentos — no máximo ~10 somando as internas. Apareceram
**+167**.

**Duas explicações, consequências opostas:** a suíte rodou muito mais do que um cenário (operação
normal, item morre) **ou** algo produz contato sozinho (contamina contagem, TMA e atribuição por pool
de todo o ambiente de demo, inclusive as medições que a F1b e a F3 usaram como base).

Primeiro corte — quem são, por minuto e por pool/canal:

```
SELECT toStartOfMinute(opened_at) AS m, pool_id, channel, count()
FROM plughub_demo.sessions FINAL
WHERE tenant_id='tenant_demo' AND opened_at >= '2026-08-14 18:00:00'
GROUP BY m, pool_id, channel ORDER BY m DESC LIMIT 40
```

Concentradas em poucos minutos e num pool de teste ⇒ é a suíte. Espalhadas no tempo ⇒ há produtor
ativo, e o alvo é quem publica `conversations.inbound` sem contato real.

⚠️ **Conferir `origin` no mesmo passe.** Já é achado conhecido (`telas-design` §5) que
`infra/test/seed_*.sh` inserem direto no ClickHouse **sem a coluna `origin`**, caindo no default
`live` — exatamente o que inflaria esta contagem, e exatamente o que o discriminador `origin` existe
para impedir.

---

## Fora de escopo nesta sessão

- **F4** (visão 2 — pivô, lentes A/B, internas dobradas) e **F5** (`ContextStorePersister`).
- Os resíduos declarados da F3 (`TODO.md`): filtro por direção, `?session_id=` não honrado em
  `/analise/sessions`, `ContactsPage.tsx`/`AnaliseContatosPage.tsx` mortas, e a query de divergência
  de pool antes/depois do deploy da F1b.

---

## Armadilhas de ambiente

- **O sandbox não alcança o WSL** — Claude edita, o operador roda e cola.
- **e2e apaga `tenant_demo:*` e `session:*`** antes de CADA cenário; contra o demo leva junto
  instâncias logadas, snapshots, ContextStore, claims e sessões vivas. **E é o suspeito do item 2** —
  rodá-lo para "gerar dado" pode duplicar o próprio fenômeno em investigação.
- **`platform-ui` não tem volume mount** — mudança de frontend exige `build`, não `restart`.
- Arquivo NOVO exige `build --no-cache`; edição de existente entra no build normal.
- ClickHouse: banco é **`plughub_demo`** (não `analytics`); `FROM t AS s FINAL`.
- Portas: analytics-api **3500** · platform-ui **5174** · auth-api **3202**.
- Alias de agregado NUNCA repete nome de coluna real (`ILLEGAL_AGGREGATION`, code 184).
