# Catálogo de KPIs — definição única, população declarada

> Estado: catálogo aceito pelo dono em 2026-09-25 · Fichas `KPI-01..08` em `pending.md`, sob este documento.
> Origem: comparação com os concorrentes (CCaaS, fornecedores de agente de IA, ferramentas de análise de
> conversa) e inventário do `analytics-api` em 2026-09-25. Relacionado:
> [`decagon-paralelo-2026-09.md`](decagon-paralelo-2026-09.md) § 5 (camada de analista) e § 9 (mineração de
> processo), [`competitive-analysis-2026-09.md`](competitive-analysis-2026-09.md).

## Regras do catálogo

Todo KPI deste documento segue as mesmas cinco regras. Elas existem porque cada uma já custou um número
plausível e errado neste repositório.

1. **Uma definição, uma casa.** Cada KPI tem UMA fórmula, calculada no `analytics-api`. Duas telas que
   mostram o mesmo KPI chamam a mesma consulta.
2. **População declarada.** Toda linha diz sobre QUAIS sessões ou segmentos o número é calculado e o que
   fica fora. *"Contato"* é o recorte com cliente do outro lado (`spawn_reason` nulo ou `collect`, D11); sessão
   interna não entra em KPI de atendimento.
3. **Ausência não é zero.** KPI sem amostra devolve `null` com o tamanho da amostra, nunca `0`. Coluna
   nova é forward-only e tem **época declarada**, como `deploy_version` e o SLA por segmento.
4. **Nunca somar segmentos para obter tempo de sessão** (segmentos se sobrepõem) e nunca somar
   `available` de pools (a capacidade é do recurso).
5. **Escopo ABAC:** KPI por pool respeita `accessible_pools`; KPI de conta LLM é do tenant e recusa filtro
   de pool, como `/reports/resources/tokens` já faz.

Legenda de estado: **✓** calculado · **◐** o dado existe, falta o cálculo · **✗** falta dado.

---

## 1. Acesso

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| Nível de serviço | esperas encerradas dentro de `sla_target_ms` ÷ esperas com alvo | segmentos `role='queue'`; sem alvo conta em `sla_unstamped`, nunca como violação | `segments`, `/reports/pools/queue` | ✓ |
| Abandono | esperas com `outcome='abandoned'` ÷ esperas | idem; **falta** separar o abandono curto | `segments` | ✓ |
| Abandono curto | abandonos com espera < limiar (default 5 s, config do tenant) | exclui-se do abandono "real" e publica-se à parte | `segments` | ◐ `KPI-01` |
| Tempo até ser atendido (ASA) | média e p90 da espera das esperas **atendidas** | exclui abandonadas; confirmar se `avg_wait_ms` hoje as mistura | `segments` | ◐ `KPI-01` |
| Precisão da espera informada | erro médio e p90 entre `estimated_wait_ms` informado e a espera real | só contatos que receberam estimativa | `queue_events` × `segments` | ◐ `KPI-01` |

## 2. Eficiência da conversa

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| Tempo do caso | `elapsed_time_ms` (inclui esperas) | contatos fechados | `sessions` | ✓ |
| Tempo de agente | Σ `duration_ms` de segmentos `primary`/`specialist` | não é tempo de sessão (regra 4) | `segments` | ✓ |
| Tempo até a 1ª resposta | 1ª mensagem de agente − 1ª mensagem do cliente | canais digitais; voz fora | `messages` | ◐ `KPI-02` |
| Turnos por contato | trocas cliente ↔ agente até o fechamento | canais digitais | `messages` | ◐ `KPI-02` |
| Tempo suspenso · % expirado | duração média das suspensões; suspensões que expiraram ÷ suspensões | sessões com `session_transitions` | `session_transitions` | ◐ `KPI-02` |

⚠️ **1ª resposta e turnos já são calculados** pelo `SessionMetricsExtractor` do evaluation-api, mas só para
sessões AVALIADAS e gravados no Postgres (`evaluation.instances.session_metrics`), a serviço dos critérios
automáticos da nota. A `KPI-02` reaproveita a MESMA definição para todas as sessões — duas fórmulas para o
mesmo KPI é o que a regra 1 proíbe.

## 3. Resolução

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| Resolução declarada | segmentos com `outcome='resolved'` ÷ segmentos | ⚠️ no segmento de IA o dado está comprometido (`SFE-01`, `SFE-02`) | `segments` | ✓ ⚠️ |
| **Recontato em N dias** | contatos do MESMO cliente, pelo MESMO motivo, em até N dias depois de um contato fechado ÷ contatos fechados | identidade por `customer_id` ou journey; N é config (default 7); motivo pela folha do orquestrador / `agent_event` quando houver, senão qualquer motivo, dito na resposta | `sessions`, `journey_aliases`, `agent_business_events` | ◐ `KPI-05` |
| **FCR medido** | 1 − recontato em N dias | mesma população | derivado | ◐ `KPI-05` |
| FCR perguntado | % "sim" na pesquisa | só quem respondeu | `session_signal` | ✓ |
| Transferência | segmentos com `close_reason='agent_transfer'` ÷ segmentos | nunca por `outcome` (TRF-01) | `segments` | ✓ |
| Cadeia de transferência | nº de pools por contato; contatos que voltam a um pool já visitado | contatos com ≥ 2 pools atendidos | `segments` | ◐ `KPI-04` |
| Escalonamento e ranking de motivos | taxa + distribuição de `handoff_reason`/`escalation_reason` | segmentos com motivo | `segments` | ✓ / ◐ `KPI-04` |

## 4. IA e automação

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| **Contenção** | contatos fechados sem nenhum segmento humano ÷ contatos atendidos por IA | exclui abandonos antes do atendimento | `segments` | ◐ `KPI-03` |
| **Resolução automatizada** | contatos contidos **e** sem recontato em N dias ÷ contatos atendidos por IA | é o número que separa "não pediu humano" de "resolveu" | `KPI-03` × `KPI-05` | ◐ `KPI-05` |
| **Envolvimento humano em sessão de IA** | contatos conduzidos por IA com humano convidado (especialista, supervisor ativo, @mention) ÷ contatos conduzidos por IA; e tempo do humano nesses contatos | só sessão com `primary` de IA | `segments`, `mention_command` | ◐ `KPI-06` |
| Erro e latência de tool | chamadas com falha ÷ chamadas; p50/p95 de duração, por tool | `mcp.tool_call` | `session_timeline` | ◐ `KPI-03` |
| Latência de LLM | p50/p95/p99 por conta × modelo, e taxa de throttle | todas as tentativas | a criar | ✗ `AIG-02` |
| Fallback de modelo | chamadas em que `model_id` ≠ o modelo do `model_profile` pedido ÷ chamadas | eventos com os dois campos | `usage_events` | ◐ `KPI-03` |
| Custo por contato | tokens × preço por modelo + mensagens cobradas por canal | pós-época de atribuição | `usage_events` | ✗ `USG-03`, `USG-04` |

## 5. Experiência do cliente

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| CSAT, NPS, CES | por pesquisa | só quem respondeu; taxa de resposta ao lado | `session_signal` | ✓ |
| Trajetória de sentimento | score do 1º e do último turno do cliente, delta, e sentimento por segmento | contatos com ≥ 2 medições; `None` = não medido (nunca 0) | `sentiment_events` | ◐ `KPI-02` |
| Satisfação estimada em 100% | nota explicável, decomposta em motivos (esforço, emoção, qualidade da resposta) | todos os contatos fechados | avaliador | ✗ `QMN-01` |

## 6. Força de trabalho

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| **Ocupação** | tempo ocupado ÷ (tempo logado − pausa) | agentes humanos; por agente, pool e período | `/reports/agent-availability` | ◐ `KPI-01` |
| Pausa por motivo | Σ pausa por `reason_id` | agentes humanos | `agent_pause_intervals` | ✓ |
| Uso de capacidade do pool | pico de concorrência ÷ capacidade | por pool | `pool_occupancy_peaks` | ✓ |
| Previsão de demanda, aderência, perdas | — | — | — | ✗ fora de escopo por ora (WFM) |

## 7. Qualidade e voz

| KPI | Definição | População / exclusões | Fonte | Estado |
|---|---|---|---|---|
| Nota de QA (por critério, por deploy) | média de `evaluation_finalized` | avaliações finalizadas | `evaluation_*` | ✓ |
| Flags de conformidade | distribuição e taxa de `compliance_flags` por pool e critério | avaliações finalizadas | `evaluation_results` | ◐ `KPI-04` |
| Queda de chamada | chamadas encerradas por falha ÷ chamadas, por `end_reason` | canais com `call_intervals` | `call_intervals` | ◐ `KPI-04` |
| Qualidade do reconhecimento de fala | ruído, confiança, WER | bot leg | `speech_*` | ✓ |

---

## Análises (acima dos KPIs)

| Análise | O que faz | Por que o PlugHub | Ficha |
|---|---|---|---|
| Descoberta de motivos fora da taxonomia | agrupa contatos cujo motivo NÃO cai numa folha declarada e acompanha o volume de cada grupo | hoje a taxonomia é só a declarada (folhas do orquestrador, `agent_event`) | `KPI-07` |
| Briefing periódico | variação dos KPIs com os marcadores de deploy e uma hipótese de causa, com links para as conversas | os marcadores por pool já existem (Arc 6 Fase 2) | `KPI-08` |
| Colaboração na sessão | quando a IA chama humano, por quê, por quanto tempo e se resolve | só existe onde humano e IA compartilham a sala | `KPI-06` |
| Causa raiz ligada a step, versão e tool | aponta o step de qual `deploy_version` e qual tool | trajetória real persistida + `deploy_version` no segmento | `ANL-02`, `PMN-01` |
| Perguntas em linguagem natural | consulta sobre as conversas no escopo de quem pergunta | — | `ANL-01` |

**Pré-requisito comum:** a verdade do desfecho (`SFE-01`, `SFE-02`). Resolução de IA errada contamina
contenção, resolução automatizada e causa raiz.
