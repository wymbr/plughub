# Plano de execução — interop n8n

> **Status: proposto para avaliação conjunta** — 2026-08-17
> **Entrada:** [`n8n-triagem-2026-08-17.md`](n8n-triagem-2026-08-17.md) (55 itens de direção) e
> [`n8n-interop-boundaries-and-seams.md`](n8n-interop-boundaries-and-seams.md) §11 (as fases).
> **Não decide:** cronograma em datas, nem hospedagem do n8n.

---

## 0. Como este plano foi montado

Três escolhas, feitas de propósito, que explicam o formato:

| Escolha | Consequência no plano |
|---|---|
| **Fases 0–2 tarefa a tarefa; 3–6 em blocos** | O detalhe para depois do gate da fase 3 seria planejamento sobre fundação inexistente — e o próprio doc mestre admite que o gate pode revelar impedimento |
| **Defeitos em trilha paralela; só os bloqueantes entram** | Quatro defeitos viram tarefa dentro da fila porque bloqueiam uma fase. Os outros ~24 ficam numa lista própria, priorizada por conta própria |
| **Uma frente por vez** | A fila é estritamente ordenada. É o que o § Protocolo de Sessão do `CLAUDE.md` já prescreve (uma sessão = uma tarefa coerente), e torna o caminho crítico legível |

---

## 1. Antes de escrever código: cinco medições

Todas baratas, todas destravam uma decisão que hoje seria tomada no escuro. **A regra de método do
projeto manda prever o número antes de rodar** — cada uma abaixo traz a pergunta que ela responde, não
só o comando.

| # | Medição | A decisão que ela destrava | Modo de falha se pular |
|---|---|---|---|
| **M1** | **Chamadores reais da rota por pool.** Estático já achado: `scheduler-api/…/dispatcher.py:98`, `orchestrator-bridge/…/main.py:1337`, `mcp-server-plughub/src/tools/workflow.ts:176`, mais ~12 scripts em `infra/test/`. Falta o **volume por chamador em runtime** | Dimensiona a fase 0a — ver §2 | Fechar a rota derruba toda Agenda, todo hook destacado e o **fan-out de campanha outbound**, em silêncio |
| **M2** | **Conflito Camada F** — `CLAUDE.md` diz *"ARCO A–F COMPLETO"*, `TODO.md` mantém E2 e F abertos | Se a Camada E2 do wrap-up entra ou não na fila | Planejar trabalho já feito, ou dar por feito trabalho que não existe. **Resolver por medição, não escolhendo o doc mais recente** |
| **M3** | **M0 do ADR de borda única** — volume de chamada MCP pelo caminho nativo × `external-mcp` | Dimensiona a fase 2b/B2 (costura C). O próprio ADR já a define como primeira fase | Construir a borda única sem saber quanto tráfego ela vai absorver |
| **M4** | **`spawn_reason`** — hoje zero amostras de `collect`/`delegate` no demo (só `NULL` 349, `trigger` 71) | Se a F5/visão 2 tem dado para renderizar | Tela nasce plana, e planura parece resposta |
| **M5** | **`value_label`** — a evidência citada na triagem não existe no arquivo apontado | Se o resíduo do S1 de Customer Surveys é real | Item entra em plano sem existir |

> **Por que medição vem antes de tarefa:** quatro dos oito abortados da primeira versão da triagem
> caíram na auditoria por falta de evidência medida. O custo de medir é uma sessão; o de errar-cortando
> é reconstruir.

---

## 2. A decisão que dimensiona a fase 0 — precisa ser tomada por você

A triagem promoveu *"fechar a rota anônima `POST /v1/channels/webhook/pool/{pool_id}`"* a **primeiro
trabalho de todos**. Ao sequenciar, apareceu um fato que muda o tamanho da tarefa por um fator grande:
**a rota não é anônima e sem uso — ela é anônima e é o barramento interno de disparo do produto.**

Três chamadores de produção:

```
scheduler-api/…/dispatcher.py:98          → toda Agenda que dispara um pool
orchestrator-bridge/…/main.py:1337        → todo hook de finalização destacado
mcp-server-plughub/src/tools/workflow.ts:176 → a tool workflow_trigger = o fan-out de campanha
```

Mais ~12 gates em `infra/test/` que a chamam direto, e um probe (`probe_edge_surface.sh:44`) que
**declara** a rota como anônima e afirma que ela está no schema — a asserção teria de virar do avesso.

### As duas saídas, e elas não custam o mesmo

| | **Opção A — credencial na rota** | **Opção B — n8n entra pela porta registrada** |
|---|---|---|
| O que muda | A rota passa a exigir token de serviço; `tenant_id` sai do corpo e vem da credencial | A rota **continua interna**; o n8n usa `/channel/webhook/{slug}`, que **já tem** `auth_required`, token, hash e rotação |
| Custo | 3 serviços + ~12 gates + 1 probe invertido | Registrar um endpoint. Perto de zero de código |
| O que fecha | O risco de verdade **e** a defesa em profundidade | O risco de verdade, se — e só se — a borda de topologia for enforçada |
| O que NÃO fecha | — | O `/v1` continua publicável por engano, e **não existe borda versionada no repositório** (sem nginx.conf; nada verifica o que o deploy publica) |

**A recomendação é B + um gate de topologia**, não B sozinho. Motivo: o `CLAUDE.md` já classifica `/v1`
como interno e `/channel` como publicável, mas a separação é **de código, não de topologia** — as duas
rotas vivem no mesmo app, na mesma porta, e o que as separa é um `allowed_origins={"external"}`. Sem um
gate que verifique o que o deploy realmente publica, a opção B protege por convenção.

> **Enquanto esta decisão não for tomada, a fila abaixo começa em 0a.0 e não em 0a.1.**

---

## 3. A fila — fases 0 a 2, uma frente por vez

Ordem estrita. Cada linha é uma sessão de trabalho coerente.

### Fase 0a — fechar a borda de entrada *(bloqueia todo o resto)*

| # | Tarefa | Depende de |
|---|---|---|
| **0a.0** | **Decidir A × B** (§2) e escrever a decisão como ADR curto | M1 |
| **0a.1** | Implementar a decisão de 0a.0 na borda de entrada | 0a.0 |
| **0a.2** | Gate de topologia: probe que reprova se `/v1`, `/docs`, `/openapi.json` estiverem publicados. Hoje os três respondem `200` e **nada verifica o que o deploy publica** | 0a.1 |
| **0a.3** | **Posse do item no resume externo.** A porta pública passa `approver=None` por construção, então o gate de `channel-gateway/…/adapters/webhook.py:1163` nunca roda e um resume externo descarta trabalho humano em curso. Obstáculo mecânico conhecido: `_routing_work_task_holder` exige `pool_id`, que a porta externa não recebe — derivar do ledger `work_task` | 0a.1 |
| **0a.4** | **`source` do resume deixa de ser asserido pelo cliente.** Cuidado registrado: downgrade cego derruba o expire legítimo do supervisor | 0a.3 |

### Fase 0b — costuras A + E, e o contrato de fronteira

| # | Tarefa | Depende de |
|---|---|---|
| **0b.1** | **Modelo journey/session/segment — spec Fases 0/1.** Declarar a borda externa. Subiu de fosso para pré-requisito: é onde o n8n aterrissa | 0a |
| **0b.2** | **Contrato de propagação de `root_session_id`** em disparo externo + **0c**: logar o auto-mint. Risco que isto cobre: *journey partida*, cujo sinal é `root_session_id == session_id` em disparo externo | 0b.1 |
| **0b.3** | **Tópicos Kafka órfãos — fechar ANTES de expor a costura E.** Decidir destino de `rules.escalation.events` e `rules.shadow.events`; dar schema Zod a `conversations.events` (hoje 5 produtores × 6 consumidores, sem contrato). A costura E congela contrato Kafka — expor tópico fantasma como contrato é o risco §13 nomeado | 0b.1 |
| **0b.4** | **Sub-workflow template n8n** (precursor barato da costura D). É nele que mora a disciplina de propagação | 0b.2 |
| **0b.5** | **Throttle de downstream em pool webhook.** Deixou de ser *"deferred até haver caso de uso"*: o §2.1 exige capacidade n8n ≥ Σ `max_concurrent` dos pools que delegam | 0b.2 |
| **0b.6** | **F5 — `ContextStorePersister` mascarado.** Onde o contexto do processo é persistido no close; contraparte direta do risco de PII com execução fora | 0b.1, M4 |

### Fase 1 — superfície de resultado honesta

| # | Tarefa | Depende de |
|---|---|---|
| **1.1** | Status de 3 estados + artefato buscável. O net-new real da interop **não é o protocolo, é o artefato**: hoje o trigger devolve só `{session_id}` e `get_status` responde `"closed"` quando a chave não existe — *"não sei"* indistinguível de *"terminou"* | 0b.2 |

### Fase 2 — a infraestrutura MCP *(onde está o maior retorno)*

| # | Tarefa | Depende de |
|---|---|---|
| **2a.1** | **Promover `skill-flow-service` a pacote de primeira classe.** O runtime de produção dos skills vive em `packages/e2e-tests/services/skill-flow-service/`, cujo cabeçalho diz *"thin HTTP wrapper for E2E testing"*, e é `service_healthy` de três serviços | — *(pode começar em paralelo à 0a se a decisão de §2 demorar)* |
| **2b.1** | **Catálogo `mcp_servers` no config-api**, substituindo o mapa hardcoded de 2 entradas (`index.ts:35-38`) e a convenção de env `MCP_SERVER_{NOME}_URL`, que **não está definida em lugar nenhum** | 2a.1, M3 |
| **2b.2** | **Remover o fallback mudo** (`index.ts:142-145`): servidor desconhecido vai ao `mcp-server-plughub` sem log e sem throw. Produz o erro errado e desvia o diagnóstico | 2b.1 |
| **2b.3** | **B1 — pool de clientes de domínio com health-check e retry.** O ADR de borda única o declara *"pré-requisito, não follow-up"* | 2b.1 |
| **2b.4** | **B2 — `mcpCall` nativo roteando pela borda única = a costura C.** Hoje chama o domain server direto, sem `permissions[]`, sem guard, sem `AuditRecord`. **Gate:** contagem de `mcp.audit` do caminho nativo > 0 — hoje é 0, e 0 é o valor que o gate tem de reprovar | 2b.3 |
| **2b.5** | **Masking — consolidar os 5 mecanismos.** Subiu: o mecanismo #2 (ContextStore field-level) é o que decide **o que o n8n consegue ler**, e o invariante §12.9 (*"valor mascarado nunca atravessa o n8n"*) não tem enforcement sem isto. Junto: dar escritor a `masked_input_fields`, hoje sempre `[]` | 2b.4 |
| **2c.1** | **`tools/list` + snapshot no slot + pin de `versionId` e reconcile.** Hoje `tools/list` **não é chamado em nenhum ponto do repositório** — zero discovery | 2b.2 |
| **2c.2** | **Descongelar o binding skill↔pool** (`PoolSkillSlot` × `SkillDeployment.pool_ids`). Só aqui, porque é a 2c que muda a carga do snapshot | 2c.1 |
| **2d.1** | **`session_id`/`root_session_id` no envelope MCP + `execution_id` em `mcp_audit_log`.** É o que torna a execução do n8n correlacionável sem virar `session` nem journey (invariante §12.4) | 2b.4 |
| **2e.1** | **Seletor `server → tool` na tela de config do skill.** É a tela que substitui a tela de fluxo (§5.1) | 2c.1 |

### 3-gate — a condição de prosseguir

| # | Tarefa | Depende de |
|---|---|---|
| **G.1** | **Instrumentar latência de turno e contagem de travessias no perfil `workflow`.** O gate não pergunta *"devemos prosseguir?"* — o alvo está decidido. Pergunta *"batemos num impedimento?"*. A estimativa de "5–8 round-trips" é palpite, não medição | 2b.1, **D.2** |

---

## 4. Fases 3 a 6 — blocos, não tarefas

Declarados com a dependência; quebrados em tarefa **depois** do gate.

| Bloco | Conteúdo | Bloqueado por |
|---|---|---|
| **3** | **Costura B — principal externo.** ⚠️ **Fusão obrigatória:** a fase A2 do ADR de A2A (`a2a_client`) e o item `Agent Principal` (`agent_principals`) são duas tabelas no **mesmo serviço** para a mesma pergunta. Construir as duas viola o invariante §13. A fusão precisa preservar o que a A2 não cobre e a 2d exige: `origin: native` e `principal_id` no `AuditRecord` | G.1 |
| **4** | Migrar o perfil `workflow`; fachada OpenAI no ai-gateway (`/v1/chat/completions` — o `openai_provider.py` já converte na saída, a fachada de entrada é largamente reverter isso) | 3 |
| **5** | **Dois bloqueantes, ambos frentes novas sem dono até a triagem:** (a) promover o interpretador genérico a serviço de código — arrasta hooks, NPS, wrap-up, survey, OTP; (b) mapeadores de `flow_definition`/`pipeline_state` — sem eles a avaliação de IA degrada **em bloco** e **em silêncio**. Só então migrar o perfil `agent` | 4, **D.1** |
| **6** | Remover o editor `agent-flow`; executar os 4 descartes da triagem; reavaliar o que sobrou da §10 | 5 |

> **Economia registrada:** o trace de execução do n8n que o mapeador de `pipeline_state` (bloco 5)
> precisa é **o mesmo** que o Record/Replay Harness precisa. Construir uma vez. E o Record/Replay é o
> único gate capaz de pegar o risco *"avaliação tier-2 apagando sem alarme"*.

---

## 5. Trilha de defeitos

### Os quatro que entram na fila porque bloqueiam uma fase

| # | Defeito | Bloqueia | Onde entra |
|---|---|---|---|
| **D.1** | **`sequence_index` apagado pelo `participant_left`.** Calculado em `orchestrator-bridge/…/main.py:915`, nunca persistido; o `left` grava `0` e o `ReplacingMergeTree` substitui a linha do join. Quebra 5 `argMax` em `reports_query.py:2183-2209` | O mapeador da fase 5 depende de atribuição por segmento correta | **antes do bloco 5**; e vale por si hoje |
| **D.2** | **Seis serviços rodam sem logging configurado** — todo `logger.info` invisível | O gate da fase 3 é uma **medição**, e medir com metade dos serviços mudos dá número que parece resposta | **antes de G.1** |
| **D.3** | **Fallback mudo do resolvedor MCP** | É a própria 2b.2 | já está na fila |
| **D.4** | **`masked_input_fields` sem escritor** — contador de ausência sem testemunha, no endpoint de auditoria LGPD | A prova de política ao DPO (invariante §12.6) | já está na 2b.5 |

### Os que ficam fora da fila

~24 seções marcadas `defeito` no passo 0 da triagem, priorizadas por conta própria: segmento que nunca
fecha, `phone_number_id` dentro de `pool_id`, `voice.py` com métodos inexistentes, seeds sem `origin`,
15 `session_id` órfãos, `llm_tokens_*` não emitido no `/v1/reason`, hint de backfill que mente, e o
resto. **Nenhum bloqueia a interop**; alguns são mais graves que tarefas da fila, e essa tensão é real —
o plano não a resolve, apenas a torna visível.

---

## 6. Riscos deste sequenciamento

| Risco | Sinal de que aconteceu | Mitigação no plano |
|---|---|---|
| A decisão de §2 empurrar tudo | Fila parada em 0a.0 por semanas | **2a.1 não depende dela** — é a válvula de escape declarada |
| Fase 0 fechar a borda e derrubar produção | Agenda, hook destacado ou fan-out de campanha param | **M1 antes de 0a.1**, e os 3 chamadores já nomeados |
| Contrato Kafka congelado cedo demais | Mudança de schema interno quebra automação de cliente | **0b.3 antes de expor a costura E** |
| Throughput de campanha regredindo | Campanha grande passa a levar horas | Loop Over Items é **sequencial por construção**; paralelizar no n8n é decisão explícita, não default |
| Avaliação tier-2 apagando sem alarme | Faithfulness/tool-correctness ficam planas após a migração | Bloco 5 é bloqueante, não opcional; e D.1 vem antes |
| Reconstruir o n8n por dentro | Aparece tela de "automações" no PlugHub | Invariante §12.3 |
| DialogForm virando linguagem de fluxo | Pedidos de `next` condicional, laço ou variável no form | A guarda `ask_when` é **load-bearing**, não tarefa |

---

## 7. O que este plano NÃO cobre

- **Datas.** A fila é ordenada, não estimada. Estimar exige orçar por linhas ou commits, e a medição
  do doc mestre declara que contagem de arquivo não é esforço.
- **Hospedagem do n8n** — fora do escopo do doc mestre.
- **Os 14 itens em `Congela`.** Cada um tem, na tabela de triagem, a medição que o descongela. Fazer a
  medição é trabalho válido; investir no item, não.
- **A fase 6 em detalhe.** Aposentar código vivo é gated por paridade, e a paridade não existe até o
  bloco 5 rodar.
