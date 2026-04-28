# Pool Lifecycle Hooks — Guia do Desenvolvedor

> **Versão:** PlugHub Arc 3 (Fase A ✅) / Arc 3 (Fase B ✅) / Arc 3 (Fase C ⏳)  
> **Fonte de verdade técnica:** `CLAUDE.md § Pool Lifecycle Hooks`

---

## O que são Pool Lifecycle Hooks

Pool Lifecycle Hooks permitem que pools humanos declarem agentes especialistas que são
ativados **automaticamente** em pontos específicos do ciclo de atendimento — sem código
adicional no Agent Assist UI e sem lógica hardcoded no bridge.

O mecanismo reutiliza 100% da infraestrutura existente de conferência e `@mention`:
o bridge simplesmente publica um `ConversationInboundEvent` sintético com `conference_id`,
e o Routing Engine o trata como qualquer outra alocação.

---

## Hooks disponíveis

| Hook | Status | Quando dispara |
|------|--------|---------------|
| `on_human_start` | ✅ Fase A | Agente humano entra na sessão (após `activate_human_agent`) |
| `on_human_end` | ✅ Fase B | Último agente humano chama `agent_done` |
| `post_human` | ⏳ Fase C | Após todos os `on_human_end` concluírem |

---

## Configuração

### Schema YAML (infra/registry/*.yaml)

```yaml
pools:
  - pool_id: retencao_humano
    channel_types: [webchat, whatsapp, voice]
    sla_target_ms: 300000

    hooks:
      on_human_start:
        - pool: copilot_retencao    # ativa co-pilot ao humano entrar
      on_human_end:
        - pool: finalizacao_ia      # ativa NPS + encerramento ao humano sair
      post_human: []               # Fase C — pendente

  - pool_id: copilot_retencao
    channel_types: [webchat, whatsapp, voice]
    sla_target_ms: 60000

  - pool_id: finalizacao_ia
    channel_types: [webchat, whatsapp]
    sla_target_ms: 120000
```

### Schema TypeScript (@plughub/schemas)

```typescript
PoolHookEntry { pool: string }   // pool_id do especialista

PoolHooks {
  on_human_start: PoolHookEntry[]
  on_human_end:   PoolHookEntry[]
  post_human:     PoolHookEntry[]
}
```

Declarado em `PoolRegistrationSchema.hooks?: PoolHooksSchema`.

> **Deprecação:** o campo `copilot_skill_id` foi substituído por `hooks.on_human_start`.
> Ainda é lido pelo bridge por retrocompatibilidade, mas não deve ser usado em novas
> configurações.

---

## Fase A — `on_human_start`

### O que acontece

Quando o Routing Engine aloca um agente humano (`process_routed` no bridge), após
`activate_human_agent()` concluir com sucesso, o bridge verifica se o pool tem hooks
`on_human_start` configurados e os dispara.

### Fluxo

```
Routing Engine → conversations.routed (human pool)
  → bridge: process_routed()
      → activate_human_agent()            ← agente humano ativo
      → pool_config = get_pool_config()
      → hooks = pool_config.hooks.on_human_start
      → para cada hook em hooks:
          → fire_pool_hooks("on_human_start")
              → publica conversations.inbound com conference_id
              → Routing Engine aloca instância do pool do hook
              → bridge: process_routed() com conference_id
              → activate_native_agent()   ← especialista (co-pilot) ativo
```

### Exemplo de uso: co-pilot IA

O agente humano entra na sessão e, automaticamente, um agente co-pilot IA entra em
conferência para assistir, sugerir respostas e monitorar sentimento.

```yaml
hooks:
  on_human_start:
    - pool: copilot_retencao
```

O co-pilot fica ativo enquanto o agente humano estiver na sessão. Para que o co-pilot
se encerre quando o humano sair, use um `mention_command` de `terminate_self` ou configure
`on_human_end` para um agente de finalização que não reconfigure o co-pilot.

---

## Fase B — `on_human_end` e separação `agent_done` / `contact_close`

### Motivação

Antes da Fase B, `agent_done` do último agente humano imediatamente fechava o WebSocket
do cliente. Isso impedia que agentes adicionais (ex.: NPS, qualidade) continuassem
interagindo com o cliente após o atendimento humano.

A Fase B separa as duas ações:

- **`agent_done`**: o agente humano liberou a sessão (sinalizado via REST `/agent_done`)
- **`contact_close`**: o WebSocket do cliente é fechado e a sessão é encerrada

O bridge agora é o único dono do `contact_close`. Ele o adia até que todos os agentes
de `on_human_end` concluam.

### Fluxo completo

```
1. Agente humano clica "Encerrar"
   → mcp-server POST /agent_done
   → publica contact_closed reason="agent_closed" em conversations.events

2. bridge: process_contact_event(agent_closed)
   → identifica o agente que saiu
   → contabiliza agentes humanos restantes na sessão
   → se remaining <= 0 (último humano):
       → busca pool_config do pool do agente
       → lê hooks.on_human_end
       → SE hooks existem:
           → fire_pool_hooks("on_human_end")  ← dispara agentes de finalização
           → NÃO fecha o WebSocket do cliente ainda
       → SE sem hooks:
           → _trigger_contact_close()          ← fecha imediatamente

3. fire_pool_hooks("on_human_end"):
   → para cada hook:
       → publica conversations.inbound com conference_id único
       → SETEX session:{id}:hook_conf:{conference_id} 4h "on_human_end"
   → SETEX session:{id}:hook_pending:on_human_end 4h <N>  (N = número de hooks)

4. Routing Engine aloca agente do pool de finalização
   → bridge: process_routed() com conference_id
   → activate_native_agent() — ex.: agente_finalizacao_v1 executa
       → envia agradecimento
       → coleta NPS (menu button)
       → registra score
       → complete resolved
   → ao retornar:
       → GETDEL session:{id}:hook_conf:{conference_id}  → retorna "on_human_end"
       → DECR session:{id}:hook_pending:on_human_end
       → se remaining == 0:
           → _trigger_contact_close()

5. _trigger_contact_close():
   → publica conversations.outbound session.closed
     → channel-gateway fecha WebSocket do cliente
   → publica conversations.events contact_closed reason="agent_done"
     → bridge executa limpeza completa da sessão no Redis
```

### Redis keys de controle

| Chave | TTL | Descrição |
|-------|-----|-----------|
| `session:{id}:hook_pending:on_human_end` | 4h | Counter de hooks pendentes. Decrementado ao concluir cada hook. `_trigger_contact_close()` dispara quando chega a 0. |
| `session:{id}:hook_conf:{conference_id}` | 4h | Marca que o agente com `conference_id` foi criado por um hook. Usado no `process_routed` para detectar conclusão. |

### Mudança no mcp-server `/agent_done`

Com a Fase B, o endpoint `/agent_done` em `mcp-server-plughub` **não publica mais**
`conversations.outbound session.closed`. Essa publicação foi movida para `_trigger_contact_close()`
no bridge. O bridge é o único componente que fecha o WebSocket do cliente após `on_human_end`.

---

## Agente de finalização — `agente_finalizacao_v1`

O agente padrão para uso em `on_human_end`. Executa após o atendimento humano, coleta NPS
e encerra a sessão do cliente.

**Skill:** `packages/skill-flow-engine/skills/agente_finalizacao_v1.yaml`

**Fluxo:**

```
agradecimento (notify)
  → "Obrigado pelo contato! Como você avalia nosso atendimento hoje?"

solicitar_nps (menu button, timeout 60s)
  → opções: ⭐⭐⭐⭐⭐ / ⭐⭐⭐⭐ / ⭐⭐⭐ / ⭐⭐ / ⭐

registrar_nps (notify)
  → context_tag: session.nps_score_raw ← resultado do menu
  → "Obrigado pela avaliação! Até a próxima."

encerrar (complete resolved)

[timeout / failure] → encerrar (complete resolved)   ← nunca bloqueia
```

**Exemplo de ativação via hook:**

```yaml
pools:
  - pool_id: retencao_humano
    hooks:
      on_human_end:
        - pool: finalizacao_ia

  - pool_id: finalizacao_ia
    channel_types: [webchat, whatsapp]
    sla_target_ms: 120000
    # agent_type associado com skill: agente_finalizacao_v1
```

---

## Criando um agente de hook personalizado

Qualquer pool com um agente pode ser usado como hook. O agente deve:

1. **Terminar com `complete`** — o bridge detecta a conclusão e decrementa o counter
2. **Nunca bloquear indefinidamente** — use `timeout` em steps `menu`, `suspend` e `collect`
3. **Usar `on_failure` em todos os steps críticos** — o agente de finalização não deve
   travar o encerramento da sessão

**Estrutura mínima:**

```yaml
id: skill_meu_finalizador_v1
entry: meu_step
steps:
  - id: meu_step
    type: notify
    message: "Mensagem ao cliente"
    on_success: encerrar

  - id: encerrar
    type: complete
    outcome: resolved
    issue_status: finalized
```

---

## Observações sobre Fase C (`post_human`)

A Fase C (status: ⏳ pendente) ativará hooks declarados em `post_human` somente após
**todos** os agentes de `on_human_end` concluírem. O mecanismo é o mesmo: quando
`hook_pending:on_human_end` chegar a 0, em vez de chamar `_trigger_contact_close()`
diretamente, o bridge verificará se existem hooks `post_human` e os disparará antes.

Casos de uso esperados para `post_human`:
- Avaliação de qualidade (QA) automática com acesso à transcrição completa
- Persistência de resumo da conversa no CRM
- Notificação ao supervisor se SLA foi violado

---

## Implementação — referência de código

| Arquivo | Função |
|---------|--------|
| `packages/orchestrator-bridge/src/.../main.py` | `fire_pool_hooks()` — disparo de hooks |
| `packages/orchestrator-bridge/src/.../main.py` | `process_contact_event()` — detecção de `agent_closed` e decisão hook/close |
| `packages/orchestrator-bridge/src/.../main.py` | `process_routed()` — detecção de conclusão de hook via `hook_conf` key |
| `packages/orchestrator-bridge/src/.../main.py` | `_trigger_contact_close()` — fechamento do WebSocket e limpeza de sessão |
| `packages/schemas/src/agent-registry.ts` | `PoolHooksSchema`, `PoolHookEntry` |
| `infra/registry/tenant_demo.yaml` | Exemplo de configuração com `retencao_humano` + `finalizacao_ia` |
| `packages/skill-flow-engine/skills/agente_finalizacao_v1.yaml` | Skill de referência para `on_human_end` |

---

## Referências

- `CLAUDE.md § Pool Lifecycle Hooks` — especificação completa
- `docs/adr/adr-contact-segments.md` — modelo de segmentos e o papel dos hooks na criação de segmentos
- `docs/guias/mention-protocol.md` — `@mention` e `mention_commands` (mecanismo relacionado)
- `docs/guias/context-store.md` — como hooks lêem e escrevem contexto via ContextStore
