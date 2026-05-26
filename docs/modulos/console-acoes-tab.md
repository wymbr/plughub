# Console — Aba "Ações": Unificação de Agentes e Processos

> Spec criada em 2026-05-25 · Status: planejado
> Substitui a proposta original de F2 (Adicionar Especialista) e F3 (Delegar Tarefa) do Arc 11.

---

## Motivação

O Console atual tem três superfícies separadas para invocar agentes e processos:

| Onde | Ação | Mecanismo |
|------|------|-----------|
| AgentesTab Seção B | "Convidar" (inline) | @mention sem instrução |
| AgentesTab Seção B | Botão "Delegar Tarefa" | Abre DelegarTarefaDrawer |
| ActionBar | "Iniciar Processo" dropdown | Chama `journey_start` |

Problemas: (a) invite e delegate são mecanicamente idênticos — ambos disparam um agente via @mention; (b) "Iniciar Processo" isolado na ActionBar não tem status inline; (c) o DelegarTarefaDrawer força campo de texto livre obrigatório mesmo para YAMLs sem parâmetros; (d) visibilidade é sempre exibida mesmo quando o YAML a fixa.

**Objetivo**: um único ponto de orquestração — a aba **"Ações"** — com toggle Agentes | Processos, campos dinâmicos YAML-driven, e sem redundância de convite vs delegação.

---

## Resultado esperado

```
Painel direito — tabs
  Estado  |  Capacidades  |  Contexto  |  Histórico  |  Ações  |  Orquestração
                                                           ↑
                                                     (renomeado de "Agentes")

Aba Ações:
┌──────────────────────────────────────────┐
│  Ativos na Sessão                        │  ← inalterado (Seção A)
│  [HumanCard] [AiParticipantCard×N]       │
├──────────────────────────────────────────┤
│  ┌──────────────────┬───────────────┐    │
│  │  Agentes  (N)    │  Processos (M)│    │  ← toggle novo
│  └──────────────────┴───────────────┘    │
│                                          │
│  [Agentes selecionado]                   │
│  ┌─────────────────────────────────┐     │
│  │ 🤖 Agente Retenção              │     │  ← AcaoItemRow
│  │ @retencao · pool retencao_ia    │     │
│  │          [●] Ativo  [Acionar ▼] │     │
│  └─────────────────────────────────┘     │
│  ┌── inline form (se YAML tem campos) ──┐│
│  │  Motivo: [select ▾]                  ││
│  │  [Cancelar]         [Acionar]        ││
│  └──────────────────────────────────────┘│
│                                          │
│  [Processos selecionado]                 │
│  ┌─────────────────────────────────┐     │
│  │ ⚙ Portabilidade                 │     │
│  │ skill_portabilidade_v1          │     │
│  │                     [Iniciar ▼] │     │
│  └─────────────────────────────────┘     │
│  ┌── inline form ────────────────────┐   │
│  │  Nº Protocolo: [________]  *      │   │
│  │  [Cancelar]           [Iniciar]   │   │
│  └───────────────────────────────────┘   │
└──────────────────────────────────────────┘
```

---

## Design de interação

### Toggle Agentes | Processos

- Estado inicial: "Agentes" selecionado (o mais frequente).
- Contador de itens disponíveis no badge de cada botão.
- Se pool não tem `mentionable_journeys`, botão "Processos" fica desabilitado com tooltip.

### AcaoItemRow (unifica invite + delegate)

Cada item na lista tem um único botão de ação, com estados:

| Estado | Dot | Botão |
|--------|-----|-------|
| disponível | cinza | "Acionar" |
| expandido | cinza pulsante | "Cancelar / Acionar" |
| pendente | amarelo pulsante | desabilitado |
| ativo | verde | desabilitado "Ativo" |
| concluído | azul | "Acionar" (permite re-invocar) |

Clicar "Acionar" → expande inline form imediatamente abaixo do card:
- Se YAML tem `delegation_params`: renderiza os campos declarados.
- Se YAML não tem parâmetros: formulário vazio com apenas os botões (Cancelar / Acionar), sem campo de texto obrigatório.
- `delegation_visibility` declarado no YAML → campo visibility omitido do form, valor usado silenciosamente.
- `delegation_visibility` ausente → radio "Somente equipe | Visível ao cliente" com padrão "Somente equipe".

Ctrl+Enter confirma o form aberto (qualquer tipo).

### Remoções da ActionBar

Os três botões abaixo são **removidos** da ActionBar:

- `AdicionarEspecialistaButton` (Fase B Arc 11)
- `DelegarButton` (Fase C Arc 11)
- "Iniciar Processo" dropdown (Arc 10 Fase D)

ActionBar fica com: campo de texto, enviar, substituição, encerrar sessão.

---

## Modelo de dados YAML

### Para agent types — campo `delegation_visibility`

Campo opcional dentro de `capabilities` no YAML do agent type:

```yaml
# infra/registry/agente_retencao_v1.yaml
capabilities:
  description: "Especialista em retenção de clientes"
  delegation_visibility: agents_only   # "all" | "agents_only" | omitido
  # delegation_input já existe (DelegationSchema), continua inalterado
  delegation_input:
    fields:
      - id: motivo
        label: "Motivo"
        type: select
        options:
          - { value: desconto, label: "Desconto" }
          - { value: cancelamento, label: "Cancelamento" }
        required: true
```

Regras:
- `delegation_visibility: agents_only` → radio omitido, agente sempre interno.
- `delegation_visibility: all` → radio omitido, agente sempre visível ao cliente.
- Omitido → radio exibido, padrão `agents_only`.

### Para skills — campo `delegation_params`

Novo campo top-level no YAML da skill (Tier 2 / processo):

```yaml
# infra/registry/skills/skill_portabilidade_v1.yaml
name: "Portabilidade"
description: "Processo de portabilidade de linha"
delegation_params:
  - id: numero_protocolo
    label: "Nº Protocolo"
    type: text
    required: true
    placeholder: "Ex: BR2025-0042831"
  - id: operadora_origem
    label: "Operadora atual"
    type: select
    options:
      - { value: claro,  label: "Claro" }
      - { value: vivo,   label: "Vivo" }
      - { value: tim,    label: "TIM" }
      - { value: oi,     label: "Oi" }
    required: false
delegation_visibility: agents_only   # mesmo schema que agents
steps:
  - ...
```

---

## API

### A. `GET /v1/agent-types/:id/delegation-schema` — estender

**Pacote**: `agent-registry`
**Já existe** — adicionar `delegation_visibility` à resposta.

Resposta atual:
```json
{ "fields": [ ... ] }
```

Resposta nova:
```json
{
  "fields": [ ... ],
  "delegation_visibility": "agents_only"   // ou null quando omitido no YAML
}
```

Leitura: `(capabilities as any)?.delegation_visibility ?? null`.

### B. `GET /v1/pools/:poolId/mentionable-processes` — novo

**Pacote**: `agent-registry`
**Responsabilidade**: retornar lista de processos (skills) disponíveis para acionamento manual no pool.

Fonte: `pool.mentionable_journeys: Record<alias, skill_id>` — campo já existente (`Pool.mentionable_journeys` em Prisma).

```
GET /v1/pools/retencao_humano/mentionable-processes

Response:
{
  "processes": [
    {
      "alias":              "portabilidade",
      "skill_id":           "skill_portabilidade_v1",
      "label":              "Portabilidade",
      "description":        "Processo de portabilidade de linha",
      "delegation_params":  [ { id, label, type, options?, required, placeholder? } ],
      "delegation_visibility": "agents_only"   // ou null
    }
  ]
}
```

Implementação:
1. Ler `pool.mentionable_journeys` — `Record<alias, skill_id>`.
2. Para cada `skill_id`: ler o skill YAML armazenado na tabela `Skill` (campo `definition` JSON) ou buscar via `GET /v1/skills/:id`.
3. Extrair `name`, `description`, `delegation_params`, `delegation_visibility` do YAML.
4. Retornar lista ordenada por `alias`.

---

## Frontend — platform-ui

### Arquivos modificados / criados

| Arquivo | Ação |
|---------|------|
| `components/tabs/AgentesTab.tsx` | Renomear para `AcoesTab.tsx`; refatorar conteúdo |
| `components/DelegarTarefaDrawer.tsx` | Manter nome; remover visibilidade hard-coded; tornar instrução opcional |
| `hooks/useMentionableAgents.ts` | Inalterado |
| `hooks/useDelegationSchema.ts` | Adicionar `delegation_visibility` ao tipo retornado |
| `hooks/useMentionableProcesses.ts` | **NOVO** |
| `components/ActionBar.tsx` | Remover botões Delegar, Adicionar Especialista, Iniciar Processo |
| `RightPanel.tsx` | Renomear tab "Agentes" → "Ações" |
| `AgentAssistPage.tsx` | Atualizar wiring dos handlers |
| `types.ts` | Adicionar `MentionableProcess`, atualizar `DelegationSchema` |
| `i18n/agentAssist.json` (en + pt-BR) | Adicionar chaves `acoes.*` |

### Novos tipos em `types.ts`

```typescript
// Atualizar DelegationSchema — adicionar delegation_visibility
export interface DelegationSchema {
  fields: DelegationField[]
  delegation_visibility: "all" | "agents_only" | null
}

// Novo
export interface MentionableProcess {
  alias:               string
  skill_id:            string
  label:               string
  description?:        string
  delegation_params:   DelegationField[]
  delegation_visibility: "all" | "agents_only" | null
}
```

### `AcoesTab.tsx` — estrutura

```
AcoesTab
  ├── Seção A: AtivosSection (inalterada — HumanAgentCard + AiParticipantCard)
  ├── Toggle: [Agentes (N)] [Processos (M)]
  └── Seção B: lista dinâmica
        ├── modo=agents → AcaoItemRow× (agent)
        └── modo=processes → AcaoItemRow× (process)
```

`AcaoItemRow` é genérico — aceita `AcaoItem`:
```typescript
type AcaoItem =
  | { type: "agent";   alias: string; agent_type_id: string; description?: string; status: InviteState }
  | { type: "process"; alias: string; skill_id: string;     label: string; description?: string }
```

O componente renderiza: ícone, nome, alias, botão "Acionar"/"Iniciar" → expande inline form com `DelegationField[]`.

### `DelegarTarefaDrawer.tsx` — mudanças

Alterações mínimas para não quebrar o drawer (usado em outros contextos, ex: seleção de mensagens no transcript):

1. **Visibilidade condicional**: recebe prop `lockedVisibility?: "all" | "agents_only"`. Se preenchido, esconder radio e usar o valor fixo.
2. **Texto livre opcional**: quando schema existe e nenhum campo tem `required: true`, o botão "Acionar" fica habilitado sem texto. Remover `freeText.trim().length > 0` do `isValid()` quando há schema.
3. **Quando schema é null e não há `prefilledContext`**: não exibir campo de texto. Botão "Acionar" fica habilitado imediatamente após escolher agente.

### `useMentionableProcesses.ts` — novo

```typescript
// hooks/useMentionableProcesses.ts
export function useMentionableProcesses(poolId: string | null): {
  processes: MentionableProcess[]
  loading:   boolean
}
```

Chama `GET /v1/pools/:poolId/mentionable-processes`. Retorna lista vazia se poolId null ou endpoint retornar vazio.

### `ActionBar.tsx` — remoções

Remover os três handlers/componentes:
- `onDelegar` → remover prop e botão "Delegar"
- `onAddSpecialist` → remover prop e botão "Adicionar Especialista"  
- Dropdown "Iniciar Processo" → remover completamente

A ActionBar fica apenas com: campo de texto, enviar, substituição de cartão, encerrar sessão.

### Fluxo de submissão

**Modo Agentes** (igual ao atual):
```
AcaoItemRow.handleConfirm(alias, params, visibility)
  → instruction = serializeFields(params) // mesmo que hoje: "[campo: valor]"
  → onAddSpecialist(alias, instruction)   // @alias instruction via WS
```

**Modo Processos**:
```
AcaoItemRow.handleConfirm(alias, params, visibility)
  → onStartProcess(skill_id, params)
  → POST /api/journey/start { skill_id, session_id, metadata: params }
  // ou via MCP tool journey_start (já existe)
```

O `onStartProcess` é novo handler em `AgentAssistContext` — chama `journey_start` via API REST `POST /v1/journeys/start` (Arc 10 endpoint já existente, renomear para clareza ou manter `/trigger`).

---

## Fases de implementação

### Fase A — Backend (estimativa: 2–3h)

1. `agent-registry/src/routes/agent-types.ts` — endpoint `/delegation-schema` retorna `delegation_visibility`.
2. `agent-registry/src/routes/pools.ts` — novo endpoint `GET /:pool_id/mentionable-processes`.
3. Atualizar skill YAMLs demo com `delegation_params` onde aplicável.

### Fase B — Frontend Agentes (estimativa: 4–6h)

1. `types.ts` — atualizar `DelegationSchema`, adicionar `MentionableProcess`.
2. `useDelegationSchema.ts` — mapear `delegation_visibility` da resposta.
3. `DelegarTarefaDrawer.tsx` — `lockedVisibility` prop + instrução opcional.
4. Refatorar `AgentesTab.tsx` → `AcoesTab.tsx`:
   - Adicionar toggle Agentes | Processos.
   - Substituir `AgentInviteRow` por `AcaoItemRow` genérico.
   - Remover botão separado "Delegar Tarefa".
5. `RightPanel.tsx` — renomear tab.
6. `ActionBar.tsx` — remover três botões.
7. i18n `agentAssist.json` (en + pt-BR).

### Fase C — Frontend Processos (estimativa: 2–3h)

1. `useMentionableProcesses.ts` — novo hook.
2. `AcoesTab.tsx` — conectar modo processos, `ProcessItemRow`, form dinâmico.
3. `AgentAssistContext.tsx` — adicionar `onStartProcess` handler.
4. i18n processo.

---

## Permissões (sem mudança)

| Ação | Role mínima | ABAC |
|------|-------------|------|
| Ver aba Ações | operator | `agent_assist.operacao` |
| Acionar agente | operator | `agent_assist.operacao` |
| Iniciar processo | operator | `agent_assist.operacao` |

---

## Itens fora de escopo

- **Remoção do `DelegarTarefaDrawer`** para o fluxo de seleção de mensagens (Arc 11 F3 context menu): o drawer continua existindo para esse caso de uso. O ponto de entrada muda (não vem mais da ActionBar), mas o componente é mantido.
- **Histórico de processos iniciados na sessão**: mostrar processos ativos como cards na seção A (equivalente aos AiParticipantCard) — fase posterior quando `supervisor_state` expuser journeys ativos vinculados à sessão.
