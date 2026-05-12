# Agentes de Avaliação de Qualidade — Modelos Arquiteturais

Três arquétipos de agentes para avaliação de qualidade de interações, com graus diferentes de intervenção no atendimento e requisitos de implementação distintos.

---

## Agente 1 — Avaliador por Formulário (Post-Session)

**Função**: Avalia uma sessão encerrada contra um formulário de avaliação configurado numa campanha.

**Tipo**: Skill-flow YAML (`skill_avaliacao_formulario_v1`)

**Ativação**: Triggered pelo `evaluation-api` via `POST /v1/workflow/trigger` após `session_closed` → Replayer → Hydrator → `evaluation.requested`.

### Fluxo

```yaml
steps:
  - id: load_context
    type: invoke
    tool: evaluation_context_get    # MCP tool → evaluation-api + mcp-server-knowledge
    # Retorna: session_transcript, evaluation_form, campaign_context, knowledge_snippets (top-5)

  - id: score_session
    type: reason
    model_profile: evaluation       # Haiku isolado do perfil realtime
    output_schema:
      type: object
      properties:
        criteria_responses:
          type: array
          items:
            criterion_id: string
            score: number | "pass" | "fail" | "na"
            evidence: string        # trecho da sessão que fundamenta a nota
            notes: string
        overall_score: number
        highlights: string[]
        improvements: string[]
        flags: string[]
        general_observation: string

  - id: submit_result
    type: invoke
    tool: evaluation_submit         # MCP tool → evaluation-api POST /v1/results
    inputs:
      instance_id: "@step.load_context.instance_id"
      result: "@step.score_session.output"

  - id: done
    type: complete
    outcome: resolved
```

### Notas

- **LLM único por sessão**: toda a avaliação acontece em uma única chamada `reason`. O prompt do sistema é composto pelo description dos critérios + knowledge snippets RAG. O `output_schema` força retorno estruturado via tool use do AI Gateway — sem parsing frágil de texto livre.
- **Evidências por critério**: o campo `evidence` ancora a nota a um trecho real da transcrição, facilitando a revisão humana e a contestação.
- **Delegação a especialista**: se um critério exigir análise de domínio profundo (ex: validação jurídica), o step `score_session` pode usar `task` para delegar a um sub-agente especialista antes de agregar o resultado. Esse padrão usa `assist` mode — o especialista retorna um `agent_done` com output estruturado.
- **Nutrido pelo Replayer**: `evaluation_context_get` recupera a sessão do Redis (`{tenant}:replay:{session_id}:context`) hidratado pelo Replayer. Nunca acessa PostgreSQL diretamente.
- **Não é adequado para avaliação online**: o Replayer opera em `session_closed`. Para monitoramento em tempo real, usar Agente 2.

---

## Agente 2 — Supervisor Evaluator (Online, Observação Passiva)

**Função**: Observa uma sessão ativa em tempo real e envia alertas internos para o agente humano quando detecta padrões de risco (frustração, desvio de protocolo, oportunidade perdida, etc.).

**Tipo**: Agente nativo SDK (Python ou TypeScript `@plughub/sdk`) — **não** skill-flow.  
**Razão**: requer loop contínuo de escuta de mensagens da sessão; skill-flow não tem primitiva `receive` (passo que suspende aguardando qualquer mensagem do stream). O `menu` step suspende aguardando input do *cliente via canal*, não funciona para monitorar mensagens de agentes.

### Modelo de participação

O agente entra na sessão com role `supervisor`:

```python
# agent_login
session.join(
    session_id=session_id,
    role="supervisor",
    visibility_subscribe="all"      # recebe todas as mensagens
)
```

Ao detectar um padrão, envia uma nota interna endereçada somente ao agente humano:

```python
session.send_message(
    content="⚠️ Cliente demonstra frustração há 3 turnos. Considere oferecer crédito.",
    visibility=["<participant_id do agente humano primário>"]  # agents_only não basta — precisa ser privado
)
```

### Loop principal

```python
async def run(session_id: str, human_participant_id: str):
    async with session.subscribe(session_id, role="supervisor") as stream:
        window: list[Message] = []

        async for message in stream:
            window.append(message)
            if len(window) > WINDOW_SIZE:
                window.pop(0)

            analysis = await llm.invoke(
                prompt=build_prompt(window),
                output_schema=SupervisorAlert
            )

            if analysis.alert_level >= THRESHOLD:
                await session.send_message(
                    content=analysis.message,
                    visibility=[human_participant_id]
                )
```

### Notas

- **Visibilidade de entrada**: `"all"` — recebe mensagens do cliente E do agente. Essencial para avaliar a qualidade das *respostas* do agente, não só o humor do cliente.
- **Visibilidade de saída**: `[human_participant_id]` — somente o agente humano vê os alertas. O cliente nunca vê.
- **`receive` step (planejado)**: quando o engine do skill-flow suportar step `receive` + DAG cíclico, este agente poderá ser reimplementado como YAML. Ver TODO.md.
- **Janela deslizante**: analisa os últimos N turnos, não a sessão inteira. Mantém latência baixa e custo LLM controlado.
- **Ativação**: campanha com `online_supervisor_enabled: true` triggera o agente via `evaluation.events → orchestrator-bridge` quando a sessão é roteada para um pool monitorado.

---

## Agente 3 — Copilot / Especialista (Online, Intervenção Ativa)

**Função**: Responde a demandas do agente humano (via `@mention`) ou reage proativamente a contexto específico, enviando sugestões de resposta, buscando informações em sistemas externos, ou até assumindo parte do atendimento em modo `assist`.

**Tipo**: Agente nativo SDK ou skill-flow, dependendo da complexidade.

**Distinção do Agente 2**: enquanto o Supervisor apenas observa e alerta o humano, o Copilot pode atuar — buscar informações em MCPs, compor rascunhos de resposta, realizar ações em sistemas (abrir ticket, verificar estoque, etc.).

### Modelo de ativação

**Por `@mention` (humano solicita)**:
```
@copilot-crm: qual o histórico de compras deste cliente?
```
O bridge interpreta o `@mention`, cria um segmento `specialist` para o copilot e passa o contexto. O copilot usa `invoke` para chamar `customer_get`, compõe a resposta e a envia com `visibility: ["<human_participant_id>"]`.

**Proativo (regra de campanha)**:
O copilot pode monitorar `session.copilot.*` tags no ContextStore (escritas pelo AI Gateway a cada mensagem do cliente) e reagir quando `session.copilot.intent` bate uma condição configurada.

### Skill-flow como Copilot

Quando o fluxo é previsível (busca + formatar + responder), skill-flow YAML é suficiente:

```yaml
steps:
  - id: fetch_customer
    type: invoke
    tool: customer_get
    inputs:
      customer_id: "@ctx.caller.customer_id"

  - id: compose_reply
    type: reason
    model_profile: balanced
    prompt: |
      Com base no histórico abaixo, responda a dúvida do agente: {{mention_text}}
      Histórico: {{step.fetch_customer.output}}
    output_schema:
      type: object
      properties:
        reply: string

  - id: send_reply
    type: notify
    content: "@step.compose_reply.output.reply"
    visibility: ["@ctx.session.human_agent_participant_id"]

  - id: done
    type: complete
    outcome: resolved
```

### Equivalência com o Copilot do AI Gateway

O Copilot do AI Gateway (`session.copilot.*` tags) é **proativo e fire-and-forget** — ele analisa cada mensagem do cliente e escreve tags no ContextStore, sem enviar mensagens. O Agente 3 é a camada de **ação** sobre essas tags: lê o contexto, decide intervir, envia mensagem ou realiza ação. São complementares, não redundantes.

---

## Comparativo

| | Agente 1 — Avaliador | Agente 2 — Supervisor | Agente 3 — Copilot |
|---|---|---|---|
| **Timing** | Post-session | Online (passivo) | Online (ativo) |
| **Role na sessão** | `evaluator` | `supervisor` | `specialist` |
| **Visibilidade entrada** | N/A (Replayer) | `all` | `all` ou `agents_only` |
| **Visibilidade saída** | N/A (API direta) | `[human_participant_id]` | `[human_participant_id]` |
| **Tipo de implementação** | Skill-flow YAML | Agente nativo SDK | Skill-flow YAML ou SDK |
| **LLM calls por sessão** | 1 (+ sub-agentes opcionais) | N (janela deslizante) | 1 por ativação |
| **Nutrido por** | Session Replayer | Stream da sessão ativa | ContextStore + @mention |
| **Output** | `EvaluationResult` (API) | Nota interna ao humano | Mensagem interna / ação MCP |

---

## Evolução planejada

- **`receive` step no Skill-flow Engine**: nova primitiva que suspende o workflow até a próxima mensagem do stream da sessão (qualquer remetente, qualquer visibilidade). Permitiria reimplementar o Agente 2 como YAML sem SDK. Requer suporte a DAG cíclico no engine. Ver TODO.md.
- **Delegação de critérios a especialistas (Agente 1)**: step `task` antes de `submit_result`, com agregação de scores parciais. Não requer mudança de arquitetura — apenas YAML mais elaborado.
