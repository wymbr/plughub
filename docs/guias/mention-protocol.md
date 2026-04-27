# Protocolo @mention — Endereçamento de Participantes em Conferência

> Spec de referência: v1.0 · Módulos: `mcp-server-plughub`, `agent-registry`, `skill-flow-engine`, `agent-assist-ui`

---

## O que é

O protocolo `@mention` permite que um agente humano envie comandos diretamente para qualquer agente especialista participante (ou disponível para convite) em uma sessão de conferência, usando uma sintaxe natural baseada em alias.

É um protocolo de **coordenação entre participantes** — não uma feature específica do Co-pilot. Qualquer agente especialista configurado no pool pode ser endereçado pelo agente humano via `@alias`.

---

## Sintaxe

```
@<alias> [texto livre] [chave=valor ...] [@ctx.<campo>|"fallback"] ...
```

Exemplos:

```
@copilot ativa
@copilot pausa
@billing conta=@ctx.caller.account_id motivo=@ctx.caller.motivo_contato
@captura campos=@ctx.caller.campos_ausentes|"cpf,telefone"
@suporte cliente tem plano @ctx.caller.plano_atual, sentimento @ctx.session.sentimento.categoria
@billing @suporte analise o contexto    ← múltiplos destinatários
```

A mensagem é enviada com `visibility: "agents_only"`. Todos os participantes a recebem (transparência da coordenação). O(s) agente(s) endereçado(s) recebem adicionalmente um evento de roteamento específico.

---

## Permissões — quem pode emitir

Apenas participantes com `role: primary` ou `role: human` podem emitir `@mention`.

Agentes IA em conferência **não** podem usar `@mention`. Para convidar especialistas ou coordenar outros agentes, agentes IA utilizam o `task` step com `mode: assist` — que tem controle de fluxo próprio e auditável.

Esta restrição é aplicada pelo mcp-server-plughub antes do parse do mention. Se um participante IA tentar enviar uma mensagem `agents_only` com prefixo `@`, ela é entregue normalmente sem roteamento especial.

---

## Configuração de pools disponíveis

O pool de origem declara quais agentes podem ser endereçados via `@mention` naquela fila. Domínio fechado — o administrador controla o que pode ser convidado.

```yaml
# infra/registry/tenant_demo.yaml
pools:
  - id: retencao_humano
    agent_type_id: agente_retencao_humano_v1
    mentionable_pools:
      copilot:   copilot_retencao      # @copilot → pool copilot_retencao
      billing:   billing_especialista  # @billing → pool billing_especialista
      suporte:   suporte_tecnico       # @suporte → pool suporte_tecnico

  - id: billing_humano
    agent_type_id: agente_billing_humano_v1
    mentionable_pools:
      copilot:   copilot_billing       # Co-pilot específico desta fila
      # sem outros aliases — domínio fechado
```

`mentionable_pools` é um mapa `alias → pool_id`. Se o alias não está no mapa do pool de origem, o mention não é roteado. A mensagem é entregue normalmente como texto `agents_only` sem efeito de roteamento.

---

## Resolução de alias — fluxo completo

```
mcp-server recebe message_send:
  visibility: "agents_only"
  text: "@billing conta=@ctx.caller.account_id"

1. Verifica permissão do remetente (role: primary | human)
   → não autorizado: entrega sem roteamento

2. Detecta prefixo "@" → extrai aliases e texto do comando
   aliases detectados: ["billing"]
   texto do comando:   "conta=@ctx.caller.account_id"

3. Interpola referências @ctx.* no texto do comando
   → lê ContextStore: account_id = "ACC-00291"
   texto resolvido: "conta=ACC-00291"

4. Para cada alias:
   a. Consulta mentionable_pools do pool da sessão
      → "billing" → pool_id = "billing_especialista"

   b. Participante com agent_type de "billing_especialista" está na sessão?
      SIM → publica em agent:events:{session_id} com campo mention_target + comando resolvido
      NÃO → aciona routing engine para convidar agente do pool "billing_especialista"
            (equivale a task step mode:assist — o join do agente é a confirmação)

5. Entrega a mensagem normalmente (visibility: agents_only) para todos os participantes
```

---

## Interpolação de `@ctx.*` no comando

Antes de rotear o comando, o mcp-server-plughub resolve referências `@ctx.*` usando a mesma lógica do `interpolate.ts` do skill-flow-engine (função movida para `@plughub/sdk` para uso compartilhado).

### Sintaxe de fallback

```
@ctx.<namespace>.<campo>|"valor default"
```

Se o campo não existe no ContextStore, o fallback é usado. Se não há fallback e o campo está ausente, o valor resolve para string vazia `""`.

Exemplos:

```
@ctx.caller.account_id|"não identificado"
@ctx.caller.campos_ausentes|"cpf,telefone"
@ctx.session.sentimento.categoria|"neutro"
```

---

## Múltiplos destinatários

Todos os aliases detectados **antes do primeiro token que não é um alias** recebem o mesmo comando resolvido:

```
"@billing @suporte analise o contexto"
  → aliases: ["billing", "suporte"]
  → comando: "analise o contexto"
  → ambos recebem o evento de roteamento
```

Não existe destinatário "principal" — todos os aliases são tratados simetricamente.

---

## Confirmação de recebimento

Não existe ack explícito do mcp-server. A confirmação de que o agente especialista foi convidado e chegou é o evento `participant_joined` publicado no stream, que o Agent Assist UI já renderiza como indicador de presença.

Para comandos enviados a agentes já presentes (ex: `@copilot ativa`), o Co-pilot pode opcionalmente responder com uma mensagem `agents_only` de confirmação — definido no `mention_commands` do skill, não pelo protocolo em si.

---

## `mention_commands` — declaração no skill YAML

Cada agente especialista declara os comandos que reconhece no seu skill YAML:

```yaml
# copilot_retencao_v1.yaml
mention_commands:
  ativa:
    description: "Ativa o Co-pilot para falar diretamente com o cliente"
    action:
      set_context:
        session.copilot.mode: "active"
    acknowledge: true          # responde com confirmação agents_only

  pausa:
    description: "Silencia o Co-pilot para o cliente (continua em background)"
    action:
      set_context:
        session.copilot.mode: "passive"
    acknowledge: true

  resumo:
    description: "Gera resumo da conversa até o momento"
    action:
      trigger_step: gerar_resumo
    acknowledge: false         # o step responde por conta própria

  para:
    description: "Encerra a participação do Co-pilot na sessão"
    action:
      terminate_self: true
    acknowledge: false
```

Comandos não reconhecidos são ignorados silenciosamente. O texto do comando pode ser texto livre — nesse caso o skill pode alimentar um `reason` step para interpretação.

### Ações disponíveis em `mention_commands`

| Ação | Efeito |
|---|---|
| `set_context: { tag: value }` | Escreve no ContextStore (fire-and-forget) |
| `trigger_step: <step_id>` | Salta para o step declarado no skill flow |
| `terminate_self: true` | Agente sai da conferência via `agent_done` |

---

## Alias não resolvido — comportamento

Se o alias não está em `mentionable_pools` do pool de origem:

- A mensagem é entregue normalmente como `agents_only`
- Nenhum roteamento especial ocorre
- O Agent Assist UI exibe o alias em cinza (não sublinhado como link) indicando que não foi resolvido
- Nenhum erro é gerado — o agente humano pode ter digitado um alias incorreto

---

## Auto-invite — quando o especialista não está na sessão

Quando o alias está em `mentionable_pools` mas o agente não está na conferência:

1. mcp-server-plughub publica um `session_invite` request para o routing engine com `pool_id` do alias
2. O routing engine aloca uma instância do pool e convida para a sessão
3. O agente entra com `role: specialist`
4. O evento `participant_joined` confirma a chegada
5. O mcp-server entrega o comando ao agente recém-chegado

O auto-invite é equivalente a um `task` step `mode: assist` executado manualmente. O comportamento de fila (SLA, disponibilidade) é o mesmo.

---

## Superfície de implementação

| Componente | Mudança |
|---|---|
| `@plughub/schemas / agent-registry.ts` | campo `mentionable_pools: Record<string, string>` em `Pool` |
| `@plughub/schemas / skill.ts` | seção `mention_commands` em `SkillDefinition` |
| `@plughub/sdk / interpolate.ts` | mover função de interpolação de `skill-flow-engine` para `sdk` (uso compartilhado) |
| `mcp-server-plughub / message_send` | parser de `@mention`: permissão, extração de aliases, interpolação, roteamento |
| `agent-registry` | persistência de `mentionable_pools` no pool; API de leitura para mcp-server |
| `skill-flow-engine` | processamento de `mention_commands` recebidos; ações `set_context`, `trigger_step`, `terminate_self` |
| `agent-assist-ui` | autocomplete de `@alias` no input interno (lê `mentionable_pools` do pool ativo); indicador visual de alias resolvido vs não resolvido |

---

## Invariantes

- `@mention` só é roteado em mensagens com `visibility: "agents_only"`
- Apenas `role: primary` ou `role: human` podem emitir mentions com efeito de roteamento
- O domínio de aliases possíveis é sempre fechado pela configuração `mentionable_pools` do pool
- A mensagem original é sempre entregue a todos os participantes `agents_only`, independente do roteamento
- Aliases não resolvidos nunca geram erro — são texto inerte
- Agentes IA nunca emitem mentions — usam `task` step para coordenação
