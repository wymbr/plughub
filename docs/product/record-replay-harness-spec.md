# Record/Replay Harness — Visão + Especificação

> **Visão:** um "VCR em todas as costuras arquiteturais" — gravar, com timing, o I/O de fronteira de **todos os módulos** envolvidos num contato (channel-gateway, AI Gateway, MCP servers, Kafka) e poder **replayar qualquer costura** como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados). Generaliza o Session Replayer atual (que replaya uma única costura — o stream da sessão — para avaliação) num harness de teste/simulação determinístico.
> **Status:** visão + especificação. Fiel ao código onde marcado (`packages/session-replayer/`, `mcp.audit`, Kafka). **Data:** Junho 2026.
> **Relacionado:** gate de promoção homologação→produção (descritivo §20.1), fila de aprovação no Console, Bancada/Arc 13.

---

## 1. Princípio: cada costura é *driver* ou *mock*

Se todo I/O de fronteira está gravado com timing, cada interface gravada pode ser replayada em dois papéis:

- **Driver** — injeta os inputs gravados *como se* viessem daquela interface (ex.: replayar o inbound do channel-gateway dirige o fluxo do começo).
- **Mock/stub** — devolve as respostas gravadas de uma dependência (ex.: o MCP server "responde" o que respondeu na gravação, sem tocar o backend real).

O harness **escolhe, por teste, quais costuras ficam vivas e quais são replayadas**. Exemplos:

| Cenário | Inbound | Skill-Flow | AI Gateway | MCP domínio | Outbound |
|---|---|---|---|---|---|
| Regressão de lógica de fluxo | driver | **vivo (candidato)** | mock | mock | mock |
| Determinismo total (golden) | driver | vivo | mock | mock | mock |
| Re-execução end-to-end | driver | vivo | **vivo** | **vivo** | mock (sandbox) |
| Repro de bug | driver | vivo | mock | mock | mock |

Regra de ouro: **outbound e costuras com efeito colateral são mockadas por default** (§7).

---

## 2. O que JÁ existe (prior art)

- **`packages/session-replayer/`** (serviço Kafka, ativo): `stream_persister` (`conversations.session_closed` → PostgreSQL), `stream_hydrator` (PG→Redis), `replayer` (monta `ReplayContext` no Redis, TTL), `comparator` (comparação turn-a-turn). Disparado por `evaluation.requested`.
- **`Comparator`** (`comparator.py`, pura computação, sem I/O): Jaccard token-level por turn; `similarity_score`; `divergence_points` (< threshold, default 0.4); `outcome_delta`; `sentiment_delta`; `latency_delta` → `ComparisonReport`.
- **`ReplayContext.comparison_mode`** + `comparison_turns` no `evaluation_submit` (cenário e2e `11_comparison_mode`).
- **Timing:** `ReplayEvent.delta_ms` preserva intervalos originais; `speed_factor` escala (default 10x batch).
- **Kafka como log replayável** por natureza (offsets/retention): `conversations.inbound`, `agent.events`, `usage.events`, etc.
- **`e2e-tests`** (`runner.ts` + cenários `09_session_replayer`, `10_masking`, `11_comparison_mode`, `regressions.ts`) — harness de regressão E2E.

**Hoje o replayer cobre UMA costura** (o stream da sessão, para o avaliador). Esta spec é a generalização para **todas as costuras**, como driver ou mock.

---

## 3. Costuras (tap points) e o que gravar

| Costura | Conteúdo gravado | Papel típico no replay |
|---|---|---|
| **Inbound (channel-gateway)** | `NormalizedInboundEvent` (texto/menu/transcript), `origin_identity`, anexos (refs) | **driver** |
| **AI Gateway** | request (prompt/output_schema/perfil) → completion | **mock** (determinismo) |
| **MCP de domínio** | request (server/tool/args) → response | **mock** (backends) |
| **Kafka cross-módulo** | eventos por tópico + offset | driver/observador |
| **Outbound (channel-gateway)** | render/entrega ao cliente | **mock** (efeito colateral) |
| **Relógio / timers** | timestamps, `delta_ms`, deadlines (`business_hours`) | injetado (clock control) |
| **Aleatório** | UUIDs, `resume_token`, seeds | injetado (seed) |

> **Gravar no CONTRATO, não no estado interno.** Registrar `NormalizedInboundEvent`, request/response de MCP e do AI Gateway (contratos com schema Zod) faz o replay **sobreviver a refactors**. Gravar `pipeline_state` interno seria frágil.

---

## 4. Contrato de fixture

Uma **fixture** é um contato gravado, autocontido, com todo o I/O de fronteira + timings:

```jsonc
// Fixture (conceitual)
{
  "fixture_id": "fx_...",
  "tenant_id":  "...",
  "recorded_at":"...",
  "channel":    "whatsapp",
  "golden":     true,                 // marcada como caso de referência
  "seams": {
    "inbound":    [ {"t_ms":0,    "event": <NormalizedInboundEvent> }, … ],
    "ai_gateway": [ {"t_ms":840,  "req": {…}, "resp": {…} }, … ],
    "mcp":        [ {"t_ms":1200, "server":"mcp-server-crm","tool":"customer_get","req":{…},"resp":{…}}, … ],
    "outbound":   [ {"t_ms":1500, "render": {…} }, … ]    // referência p/ comparação; mockado no replay
  },
  "clock":   { "start": "...", "deadlines": [...] },       // p/ congelar tempo
  "random":  { "seed": "...", "uuid_sequence": [...] },     // p/ reprodutibilidade
  "expected": { "outcome": "resolved", "final_sentiment": 0.6 }  // golden assertions
}
```

A fixture respeita masking (§6). É derivável do que já é persistido (stream + audit) **quando a captura full-fidelity estiver ligada** (§8).

---

## 5. Dois modos de replay

1. **Estrito (mocka tudo externo)** — AI Gateway, MCP e outbound vêm da fixture; clock e random congelados. **Determinístico** → compara o candidato maçã-com-maçã. Pega regressão de **roteamento/branching/lógica de step**. É o modo do **gate de CI/promoção**.
2. **Re-execução viva (replay só do inbound + clock; LLM/MCP ao vivo)** — testa end-to-end com dependências reais (outbound ainda em sandbox). Não-determinístico → compara por similaridade/deltas (o `Comparator`). Responde "a versão nova ainda resolve esses casos reais?".

---

## 6. Determinismo — o requisito mais profundo

Para o modo estrito comparar maçã-com-maçã, é preciso **congelar as fontes de não-determinismo**:

- **LLM (AI Gateway):** mock pela fixture (req→resp gravado) ou seed/temperatura fixa.
- **Relógio:** clock injetável — timers, `business_hours`, deadlines de `suspend`/`collect` resolvidos contra o tempo gravado, não o atual.
- **Aleatório:** UUIDs/`resume_token` por seed determinístico no modo replay.
- **MCP/externos:** mock pela fixture.

Sem isso, o candidato diverge por motivos que **não são bug** (um UUID diferente, um horário diferente). Estes pontos de injeção (clock/seed) são a parte que **não existe hoje** e precisa ser construída no engine/runtime.

---

## 7. Segurança de efeitos colaterais

**Replay nunca pode causar efeito colateral real.** Por default, todas as costuras *outbound/side-effecting* são **mockadas**: enviar WhatsApp/SMS/e-mail, discar, cobrar no PSP, escrever em backend via MCP de escrita. Só com **opt-in explícito** (e ambiente sandbox) uma costura outbound roda viva. Isso é invariante do harness — senão um replay "cobra o cliente de novo".

---

## 8. Política de gravação — seletiva (decidido)

Gravar full-fidelity **tudo de todos os contatos** é caro e é um honeypot de PII. Política:

- **Golden set** — contatos marcados como referência (curados, ou promovidos de incidentes/avaliações).
- **Amostrado** — % configurável por pool/skill (como a amostragem de avaliação).
- **On-demand** — ligar gravação full-fidelity para um pool/skill/sessão específicos (ex.: durante homologação, ou ao investigar um bug).

O **stream da sessão + `mcp.audit` (metadados)** continuam sempre persistidos; a **captura de payload** (req/resp de MCP e AI Gateway) é o **delta** que se liga seletivamente.

---

## 9. PII / LGPD

A fixture full-fidelity contém PII (texto inbound, payloads MCP/LLM). Mitigações:

- **Mascarar na fixture** (mesmo esquema de tokenização do stream) — replay roda sobre valores mascarados quando o teste não precisa do valor real.
- **Store de fixtures com os mesmos gates de role/auditoria** do módulo de Auditoria LGPD (acesso registrado).
- **Anonimização/síntese** para datasets de avaliação compartilháveis.
- Erasure (SAR) deve cascatear para fixtures.

---

## 10. Comparação e gate de promoção

O `Comparator` já produz `ComparisonReport` (similarity, divergence_points, outcome_delta, sentiment_delta, latency_delta). O **gate de promoção** (descritivo §20.1) usa isso como **critério objetivo**:

```
suíte = conjunto de golden fixtures do pool
para cada fixture: replay ESTRITO contra a versão candidata → ComparisonReport
critério de aprovação (configurável), ex.:
  - nenhum outcome_delta.diverged == true        (nenhum desfecho mudou)
  - max(divergence_points) abaixo do limite       (sem resposta materialmente diferente onde não devia)
  - latency_delta.delta_ms <= teto
→ passou: libera a promoção (deploy no pool de produção)
→ falhou: bloqueia + relatório de divergências
```

Amarra os três: **homologação** (pool isolado) valida o candidato; **replay-suite** dá o veredito objetivo; **fila de aprovação** registra a decisão humana final + assinatura.

---

## 11. Aplicações

- **Gate de promoção determinístico** (§10) — CI e homologação→produção.
- **Reprodução de bug** — incidente de produção vira fixture replayável ("replay do contato X").
- **Simulação de carga/soak** — replay de N contatos a `speed_factor` alto para stress-test de versão/capacidade.
- **Datasets de avaliação/calibração** — fixtures rotuladas alimentam o avaliador (Arc 13).
- **What-if** — replayar um contato por outra versão de skill ou outra config de routing.

---

## 12. O que construir (delta sobre o que existe)

| Peça | Novo? |
|---|---|
| Captura de payload no **AI Gateway** (req→resp) | **Novo** — modo de gravação |
| Captura de payload no **MCP** (req→resp) | **Novo** — hoje `mcp.audit` é só metadado |
| **Clock injetável** + **seed determinístico** no engine/runtime (modo replay) | **Novo** — núcleo do determinismo |
| **Harness** que escolhe driver/mock por costura + monta a fixture | **Novo** |
| Mock por default de outbound/side-effects | **Novo** (invariante do harness) |
| Store de fixtures (seletivo) com masking + gates LGPD | **Novo** |
| Gate de promoção consumindo `ComparisonReport` | **Novo** (liga §20.1) |
| Replay do stream da sessão + `Comparator` + `speed_factor`/`delta_ms` | ✅ existe |
| Kafka como log replayável cross-módulo | ✅ existe |
| Harness e2e (`runner.ts` + cenários) | ✅ existe (estende) |

---

## 13. Fases

| Fase | Entrega |
|---|---|
| **A — captura** | Modo de gravação full-fidelity (payload de AI Gateway + MCP), seletivo (golden/amostrado/on-demand); contrato de fixture; masking na fixture. |
| **B — determinismo** | Clock injetável + seed determinístico no engine; mock por default de outbound/side-effects. |
| **C — harness multi-costura** | Driver/mock por costura; replay estrito vs vivo; integração com o `Comparator`. |
| **D — gate de promoção** | Suíte de golden fixtures por pool; critério configurável sobre `ComparisonReport`; bloqueio/liberação do deploy; assinatura via fila de aprovação. |
| **E — extras** | Simulação de carga (speed_factor em massa), repro de bug 1-clique, datasets para Arc 13. |

---

## 14. Questões em aberto

1. **Onde interceptar a captura de payload** — no `McpInterceptor`/proxy sidecar (já é o ponto único de toda chamada MCP) e no AI Gateway (já é o ponto único de LLM). Naturais, mas confirmar overhead e o gate de masking.
2. **Clock injetável** — escopo (engine skill-flow + routing timers + calendar) e como o modo replay sinaliza "use este tempo".
3. **Critério de aprovação do gate** — default conservador (zero outcome_delta) vs. configurável por pool/skill.
4. **Retenção/custo das fixtures** — TTL/arquivamento do golden set; limites por tenant.
5. **Semântica vs léxica** — o `Comparator` é Jaccard (léxico); avaliar um modo de similaridade semântica (embeddings) para reduzir falso-positivo em paráfrases, sem perder determinismo do gate.
