# Journey — Árvore de proveniência: pertença, desfecho e exibição

**Status:** Proposta (2026-07-14), pré-código.
**Motivador:** achado do usuário na Vista Processos — uma journey exibida como `Resolvido`
tendo uma sessão-membro ainda `suspended`.
**Relacionado:** `journey-retorno-modelo-3-niveis-design.md` (modelo D1.5),
`journey-3-niveis-implementation-spec.md` (J1–J5), Arc 19 (modelo unificado de sessão),
`docs/arcos/arc19-unified-session-model.md`.

---

## 1. O que motivou

Duas observações na tela, e a segunda é a que abre o buraco:

1. **O desfecho contradiz o estado.** A journey mostra `Desfecho: Resolvido` e, ao mesmo
   tempo, `Abertas: 1`. O outcome é **provisório** exibido como final.

2. **Qualquer sessão-membro sequestra o desfecho do processo.** A regra é
   `argMaxIf(outcome, opened_at, outcome != '')` — *"o outcome da sessão mais recentemente
   ABERTA que tenha um"*. Numa journey de 3 sessões (processo → workflow de survey →
   contato de survey), quem abre por último é o **contato de survey**. Logo o "Desfecho do
   processo" que o operador lê é, muito provavelmente, **o desfecho da pesquisa**.

   Hoje passa despercebido porque todas fecham `resolved`. Mas um survey que falhe fará a
   Vista Processos declarar que **o processo de negócio falhou** — quando o que falhou foi
   a pesquisa de satisfação *sobre* ele. Um contato auxiliar decidindo o desfecho do
   processo é a inversão exata que o modelo de níveis existe para impedir.

---

## 2. O achado que muda o que é possível

**A aresta da árvore não está nos dados.**

`origin_session_id` (o elo pai→filho) existe no DDL (`_DDL_SESSIONS_MIGRATE_ORIGIN`), o
`parse_inbound` o popula no dict — e o **`_SESSION_COLS` não o inclui no INSERT**
(`clickhouse.py:1127`). Nunca chega à tabela: é sempre `NULL`. A própria spec do J1
registra isto no *as-built* como **"no-op latente"**, e nunca foi corrigido.

Consequência: **o modelo fala em árvore de proveniência, mas só persiste a raiz
achatada.** Sabe-se quais sessões pertencem à journey; perdeu-se **quem gerou quem**. A UI
lista as três como irmãs não por decisão de design, mas porque a hierarquia foi descartada
na escrita.

---

## 3. Reenquadramento: os níveis reais

O desconforto ("N3 contém N2; os dois são jornadas dependendo do ponto de vista") vem de
tratar **N1/N2/N3 como camadas da plataforma**. Não são: são **papéis numa cadeia
específica** (a do survey outbound). Usá-los como camadas produz o paradoxo.

Os níveis da plataforma são três — mas **o do meio é recursivo**:

| nível | o que é | cardinalidade |
|---|---|---|
| **segmento** | janela de participação de um agente | folha |
| **sessão** | **um nó** — a unidade de contato/execução. **Pode gerar outras sessões.** | **recursivo** |
| **journey** | a **componente conexa** — a árvore inteira, nomeada pela raiz | o todo |

Com isso o paradoxo se dissolve:

- *"N3 contém N2"* → uma sessão gerou outra sessão. É o caso normal, não uma exceção.
- *"os dois são journeys"* → **nenhum dos dois é.** Ambos são **nós**. A journey é a árvore.
  O que muda com o ponto de vista é a **subárvore** observada, não a natureza da coisa.

A profundidade é **emergente**, não modelada. Exemplo real (survey outbound):

```
P  processo            (webhook)   ← raiz = a journey
└── W  survey outbound (webhook)   ← criado por workflow_trigger
    └── S  contato survey (webchat) ← criado pelo collect
```

Árvore de profundidade 3, exibida hoje como três irmãs numa lista.

---

## 4. Proveniência ≠ pertença

Hoje **uma aresta responde duas perguntas**, e é por isso que ela erra uma:

| pergunta | campo | como se comporta hoje | como deve se comportar |
|---|---|---|---|
| *quem me criou?* | `origin_session_id` | descartado no INSERT | **sempre** o pai — atravesse ou não a fronteira da journey |
| *de que processo faço parte?* | `root_session_id` | herda **incondicionalmente** do chamador | herda por default; **reseta para `self`** quando o disparo declara processo novo |

A pertença é hoje **derivada** da proveniência por propagação automática (J1). É essa
derivação que está errada: **nem toda filha continua o processo do pai.**

### O cenário que a expõe

> Dentro do atendimento de uma journey, o cliente pede algo **sem relação** — que deveria
> abrir um processo próprio.

Hoje o novo processo é **engolido pela journey A**. Não há como dizer "isto é outra coisa".

### A solução: corte declarativo no nascimento

O disparo (`workflow_trigger` / `delegate` / `collect`) ganha um parâmetro
**`journey: inherit | new`** (default `inherit`). Com `new`:

- a filha nasce com `root_session_id = ela mesma` → **journey B**, com seu próprio
  `@ctx.journey.*`, seu próprio desfecho, sua própria linha na Vista Processos;
- **mantém `origin_session_id` apontando para a sessão de A** → o fio de proveniência
  sobrevive, atravessando a fronteira.

**Quem decide é o skill, não a plataforma.** É conhecimento de negócio ("o cliente pediu
outra coisa"); a plataforma não tem como inferir. Mesma filosofia do grão do survey (S2) e
da escolha do segmento (S3): **política mora no skill**.

### A simetria — e o que ela NÃO fecha

| operação | quando | efeito |
|---|---|---|
| **`journey: new`** | **no nascimento** — "isto é outro processo" | corta |
| **`journey_merge`** | **depois** — "afinal são o mesmo" | une |

Os dois **compõem**: cortou cedo demais e descobriu que era o mesmo assunto? `journey_merge`
une. O erro é recuperável **nessa direção**.

**A direção contrária não existe: não há split retroativo.** Uma vez que N sessões nasceram
sob a mesma raiz, não há como desmembrá-las depois — o mapa de aliases é uma **união**, e
união não tem inverso. É o `merge/split` que a spec proíbe reviver, e por bom motivo: um
split retroativo reescreveria a pertença de linhas já gravadas e **todo relatório histórico
mudaria de valor**.

**Consequência prática (custo assumido):** o corte precisa ser decidido **na hora**. Isso
empurra o desenho para *"na dúvida, corte"* — porque unir depois é possível e separar não é.

---

## 5. Desfecho do processo

Uma vez que sessão é **nó** e journey é **árvore**, a regra cai sozinha:

> **Cada nó tem o seu próprio outcome. O desfecho do PROCESSO é o da RAIZ.**

Um filho **nunca** sobrescreve o pai. Uma pesquisa que falha não faz o processo falhar. Não
é preciso inventar "sessão auxiliar × espinha": **a árvore já diz quem é quem** — a raiz é o
processo, o resto é o que ele gerou.

E enquanto houver sessão aberta (`open_count > 0`), o desfecho é **provisório** e deve ser
exibido como tal (marcador/estilo), nunca como final.

*(Fraqueza conhecida: se o processo continuar numa sessão-filha — retomada —, a raiz não tem
a última palavra. Na modelagem atual isso não ocorre: retomada **reabre a mesma sessão**,
não cria outra. Se um dia ocorrer, a regra vira "último nó da espinha", o que exige marcar o
papel do nó.)*

---

## 6. O que exibir

> **A journey é a unidade que se MEDE; a árvore completa é a unidade que se RASTREIA.**
> Medição precisa de fronteira. Rastreio não.

### Processos (operacional) — **só a subárvore da journey**

Expandir todas as criações transitivamente **desfaria o corte** que o operador acabou de
pedir: o `journey: new` viraria decorativo.

E, decisivo: **a árvore completa não tem fronteira.** Um atendimento pode gerar uma
portabilidade, que gera uma cobrança, que gera uma pesquisa, ao longo de meses. Esse objeto
**cresce para sempre e não tem dono** — não se lhe atribui duração, desfecho ou NPS, nem
cabe numa linha de tabela. A journey, por construção, **tem** fronteira; por isso é
mensurável.

Terceiro motivo: com o merge (J3), a "árvore completa" **deixa de ser árvore** — passa a ter
dois tipos de aresta com semânticas distintas (proveniência × alias). Pedir ao operador que
as distinga de relance, num desenho só, é entregar uma ferramenta forense fantasiada de
painel.

**Arestas que cruzam a fronteira NÃO expandem — viram marcadores com link:**

```
Processo PRC-ba2f4613
├── P  processo             webhook   Resolvido
│     └── ↗ originou o processo PRC-9f3a1c        (link — não expande)
└── W  survey outbound      webhook   Suspenso
    └── S  contato survey   webchat   Resolvido
```

E a journey B abre com `← originada no atendimento X do processo PRC-ba2f4613`.

Ganho ergonômico: o operador **percorre** o grafo completo navegando pelos links, em vez de
**encarar** tudo de uma vez. Acesso ao grafo inteiro, sem que nenhuma tela precise
renderizá-lo.

### Rastro / auditoria (forense) — a cadeia completa, sob demanda

A partir de uma sessão: *"o que isto gerou, em cascata"* — atravessando fronteiras de
journey, com o rótulo de cada aresta (`trigger` / `delegate` / `collect`) e as fronteiras
marcadas. Aqui a pergunta não é *"como vai o processo?"* mas *"o que aconteceu a partir
daqui?"*, e a falta de fronteira é aceitável porque **ninguém está medindo**.

**Ressalva honesta:** essa separação só se sustenta se o `journey: new` for **usado**. Se os
skills herdarem por preguiça, a journey vira o balde sem fundo que este desenho evita — e
sem nem o link para navegar. **O corte é o que dá sentido à fronteira**; sem ele, a fronteira
é acidental.

---

## 7. Identidade da journey

**Mantida: a journey é identificada pela RAIZ CANÔNICA, valorada num `session_id`.** Não se
cunha id próprio.

Conceitualmente `journey_id` **já é** um identificador próprio — apenas *valorado* num
`session_id`, como um branch do git é identificado por um hash de commit (branch ≠ commit).

**Por que não cunhar um id opaco:**

- Não compra estabilidade. Num merge, **uma das duas identidades morre** de qualquer jeito
  (ou se cunha uma terceira, e morrem as duas). Um id opaco só muda onde o problema aparece.
- O que salva os links é o **mapa de aliases** (union-find na leitura): um id antigo resolve
  para o canônico, para sempre. Isso já existe e independe de id próprio.
- Um id próprio quer uma **entidade** — tabela, ciclo de vida, criação, sincronização com a
  pertença. É a Journey do **Arc 10**, removida por isso. Hoje a raiz **já existe** no
  nascimento da primeira sessão: nada a criar, nada a sincronizar, e zero chance de o "id"
  divergir da "pertença" (um é derivado da outra).

**Custo assumido:** sob merge a identidade **muda** (sobrevive a mais antiga). Links antigos
não quebram (o alias resolve), mas o id **não é imutável**. Concessão consciente do D1.5.

**Correção de apresentação (barata, alto valor):** exibir a journey com **prefixo** —
`PRC-45d390fe…` — em vez do UUID cru idêntico ao da sessão. Elimina a confusão *"o processo
é a mesma coisa que a sessão?"* sem introduzir entidade nenhuma.

### Gatilho para reabrir a decisão

> Quando uma journey precisar **existir antes da primeira sessão** (processo aberto por um
> operador, sem contato ainda) ou **carregar atributos próprios** (tipo, nome, SLA, dono),
> a raiz-como-id quebra: não há raiz, e não há onde pendurar os atributos.

Aí ela volta a ser entidade — reabrindo o Arc 10 **de olhos abertos**, e não por efeito
colateral de querer um id mais bonito.

---

## 8. Não-objetivos

- **Split retroativo** — não existe e não deve existir (§4).
- **Entidade Journey** (Arc 10) — só sob o gatilho do §7.
- **Renderizar a árvore completa numa tela** — é rastro, não painel (§6).
- **Reescrever histórico** — sessões já gravadas têm `origin_session_id` nulo e continuarão
  achatadas. **A árvore só existe para o que for gravado daqui em diante.**

---

## 9. Fatiamento

| Fatia | Entrega | Depende de |
|---|---|---|
| **T1** | **Persistir `origin_session_id`** — `_SESSION_COLS` + `_session_row` (a coluna e o parser já existem; só o INSERT a descarta). **Destrava tudo o mais.** | — |
| **T2** | **Desfecho = o da RAIZ** (`argMaxIf(... , s.session_id = journey_id)`) + **provisório** enquanto `open_count > 0` (marcador na UI). Corrige o defeito observado. | T1 (não estrito) |
| **T3** | **`journey: inherit \| new`** no `workflow_trigger`/`delegate`/`collect` (schemas + channel-gateway + engine). Reseta a raiz, preserva a proveniência. | T1 |
| **T4** | **Rótulo da aresta** — persistir o que criou o filho (`trigger`/`delegate`/`collect`; o `trigger_type` já viaja no evento). É o que torna a árvore legível. | T1 |
| **T5** | **UI: L2 vira árvore** (indentação por `origin_session_id`) + **marcadores de aresta cruzando** (link, não expande) + **prefixo `PRC-`** no id da journey. | T1, T4 |
| **T6** | **Rastro forense** — cadeia completa a partir de uma sessão, atravessando journeys, com rótulos de aresta e fronteiras marcadas. Superfície separada. | T4 |

Ordem recomendada: **T1 → T2** (pequenos, corrigem o defeito visível) → **T3** → **T4 + T5**
→ T6.

---

## 10. Riscos e custos honestos

- **Não é retroativo** (§8). A árvore nasce vazia e se preenche com o tráfego novo.
- **O corte depende de disciplina dos skills** (§6). Sem `journey: new` usado, a fronteira é
  acidental.
- **A decisão do corte é irreversível na direção de separar** (§4). Empurra para "na dúvida,
  corte".
- **T2 muda números de relatório já exibidos.** O desfecho de journeys existentes vai mudar
  (deixa de ser o da última sessão aberta e passa a ser o da raiz). É uma **correção**, mas
  quebra comparação com telas/prints anteriores — vale anunciar, não fazer em silêncio.
