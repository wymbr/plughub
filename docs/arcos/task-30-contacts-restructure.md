# Task #30 — Contacts & Navigation Restructure

> ⚠️ Documento histórico — design de planejamento da Task #30 (2026-05-08), superado pela estrutura de navegação atual. Mantido apenas como referência. Ver CLAUDE.md § Frontend Architecture.

> **Status:** Design fechado — pronto para implementação  
> **Data:** 2026-05-08  
> **Módulos afetados:** `platform-ui` (nav, Contacts, Flow, nova seção Análise)

---

## Visão Geral

Reestruturação completa da seção Contacts e do nav lateral. O objetivo é separar claramente três tipos de view:

- **Operacional** — o que está acontecendo agora (Monitor, Sessions, Agents, Processos)
- **Histórico/Pesquisa** — o que aconteceu (Sessions histórico, Events)
- **Gerencial/Analítico** — consolidado para decisão (grupo Análise)

A estrutura anterior misturava esses três tipos dentro de tabs aninhadas em `ContactsPage`, o que limitava espaço, tornava os filtros inadequados e criava confusão sobre onde encontrar cada coisa.

---

## Nova Estrutura de Navegação

```
Atendimento  (grupo colapsável — ícone 📞)
  Sessions
  Agents
  Events

Flow  (grupo colapsável — ícone ⚙️)
  Editor
  Deploy
  Monitor
  Processos        ← novo sub-item

Avaliação  (grupo existente — ícone ✓)
  ...

Análise  (grupo colapsável novo — ícone 📊)
  Contatos
  Agentes
  Processos
  Qualidade

Configuração  (grupo existente — ícone ⚙️)
  ...
```

### Grupos colapsáveis

O padrão de grupo colapsável já existe no nav para Service/Flow. O mesmo componente é reutilizado para Atendimento e Análise. Estado de colapso persistido em `localStorage` por grupo.

---

## Grupo Atendimento

### Sessions (`/contacts/sessions`)

View unificada de sessões de contato — inbound e outbound, ativas e históricas.

**Filtros:**
| Campo | Tipo | Observação |
|---|---|---|
| Período | date range | from_dt / to_dt |
| Pool | select | lista de pools do tenant |
| Canal | select | whatsapp, webchat, voice, email, sms… |
| Tipo | select | inbound / outbound |
| Status | select | active, closed, abandoned |
| Agent | select | agent_type_id |

**Lista — colunas:**
| Coluna | Detalhe |
|---|---|
| ID / início | session_id abreviado + timestamp |
| Canal | ícone + label |
| Pool | pool_id |
| Duração | duração total da sessão |
| Status | badge active / closed / abandoned |
| Processo | ícone/link se houver workflow vinculado |

**Drill-down de sessão:**
- Timeline de eventos (stream canônico)
- Participantes e papéis
- ContextStore snapshot
- **Processos vinculados** — lista de WorkflowInstances onde `origin_session_id = session.id` com link para Flow/Processos

**Notas:**
- Sessions outbound são contatos legítimos — geradas por processos mas com cliente real do outro lado
- Não há "Monitor" separado: sessões ativas aparecem no topo da lista com badge `active`

---

### Agents (`/contacts/agents`)

Duas sub-abas: **Monitor** (live) e **List** (agregado).

#### Sub-aba Monitor

View em tempo real de agentes humanos. Dados do Redis via polling curto (~5s).

**Filtros:** Pool, Status (ready / busy / paused / offline)

**Colunas:**
| Coluna | Detalhe |
|---|---|
| Agente | nome / instance_id |
| Pool | pool_id |
| Status | badge com cor |
| Sessão atual | link para Sessions se `busy` |
| Tempo no status | elapsed desde última mudança |
| Motivo de pausa | reason_label se `paused` |

#### Sub-aba List

Tabela de agentes com métricas consolidadas no período selecionado. Dados do ClickHouse.

**Filtros:** Período, Pool, Agent

**Colunas:**
| Coluna | Detalhe |
|---|---|
| Agente | agent_type_id |
| Pool | pool_id |
| Volume | total de sessões no período |
| TMA | tempo médio de atendimento |
| Resolução | taxa de resolução (%) |
| Pausas | count de pausas no período |
| Tempo em pausa | total_pause_ms formatado |

Avaliação média **não** aparece na lista — disponível somente no drill-down do agente.

**Drill-down de agente:**
- Métricas detalhadas
- Histórico de pausas com motivos
- Sessões atendidas (link para Sessions)
- Avaliação média + breakdown por critério

---

### Events (`/contacts/events`)

Stream plano de eventos do canal canônico. Útil para debug, auditoria e investigação pontual.

**Filtros:**
| Campo | Tipo | Observação |
|---|---|---|
| Período | date range | |
| Pool | select | |
| Canal | select | |
| Tipo de evento | multiselect | session_opened, message_sent, agent_done… |
| session_id | text | busca exata — chave para investigação pontual |

`outcome` **não** é filtro — está disponível no drill-down da sessão vinculada.

**Colunas:** timestamp, session_id (link), tipo, agente/canal, resumo

---

## Grupo Flow — adições

### Processos (`/flow/processos`)

Instâncias de workflow — todos os estados, todos os tipos. Substitui o scope toggle "Processos" que existia dentro de Flow/Monitor.

**Filtros:**
| Campo | Tipo |
|---|---|
| Período | date range |
| Flow | select (skill_id) |
| Status | select: running, suspended, completed, failed, cancelled |
| Pool | select |
| Tem sessão vinculada | toggle |

**Colunas:**
| Coluna | Detalhe |
|---|---|
| ID | workflow_instance_id abreviado |
| Flow | skill_id |
| Status | badge |
| Iniciado em | timestamp |
| Duração / Em curso | elapsed ou duração total |
| Sessão vinculada | link para Sessions se `origin_session_id` presente |

**Drill-down de instância:**
- Steps executados com duração por step
- Estado atual do pipeline
- Sessão vinculada (link)
- Botões de ação conforme status: cancelar, retomar (se suspended)

**Flow/Monitor** passa a mostrar apenas sessões ativas conduzidas por skill flows (view em tempo real) — sem o toggle anterior.

---

## Grupo Análise (novo)

Grupo gerencial separado dos módulos operacionais. Destinado a supervisores e gestores para consolidar dados, identificar tendências e exportar relatórios.

Nenhum módulo operacional (Atendimento, Flow, Avaliação) tem sub-aba "Analysis" embutida — esse papel migra inteiramente para este grupo.

### Contatos (`/analise/contatos`)

Métricas de interação com clientes.

**Períodos:** Dia (intervalos 15min, 96 pontos) · Semana (por dia, 7 pontos) · Mês (por dia, 30 pontos) · Ano (por mês, 12 pontos)

**Filtros:** Período, Pool, Canal, Tipo (inbound/outbound)

**Métricas:**
- Volume de sessões ao longo do tempo (gráfico de linha/área)
- Taxa de resolução (%)
- TMA médio
- SLA cumprido (%)
- Distribuição por canal (pizza ou barras)
- Distribuição por close_reason
- Exportação CSV

---

### Agentes (`/analise/agentes`)

Métricas de performance e disponibilidade de agentes humanos.

**Períodos:** mesmos de Contatos

**Filtros:** Período, Pool, Agent

**Métricas:**
- Disponibilidade × pausa ao longo do tempo (gráfico empilhado)
- Pivot table agente × data com heatmap de tempo em pausa (existente em `AgentsTab`)
- Breakdown de pausas por motivo
- TMA por agente
- Taxa de resolução por agente
- Exportação CSV

---

### Processos (`/analise/processos`)

Métricas de performance dos workflows.

**Períodos:** mesmos de Contatos

**Filtros:** Período, Flow (skill_id), Pool, Status

**Métricas:**
- Volume de instâncias ao longo do tempo
- Taxa de conclusão (completed / total)
- Taxa de falha
- Duração média por step (breakdown por flow)
- Distribuição por status final
- Exportação CSV

---

### Qualidade (`/analise/qualidade`)

Consolidado do módulo de avaliação.

**Períodos:** mesmos de Contatos

**Filtros:** Período, Campanha, Pool, Avaliador

**Métricas:**
- Score médio de qualidade ao longo do tempo
- Taxa de contestação
- Distribuição de scores por critério
- Comparativo por pool / agente
- Exportação CSV

---

## Análise tab em ContactsPage — migração

A aba `Analysis` que existe hoje dentro de `ContactsPage` é removida. Seu conteúdo migra para `/analise/contatos` com filtros revisados e mais espaço.

O `AgentsTab` existente (`AgentsTab.tsx` — sub-abas Availability/Pauses) migra para `/analise/agentes`.

---

## Cruzamento entre módulos

A navegação entre módulos acontece via links explícitos no drill-down — nunca por duplicação de dados em dois lugares.

| De | Para | Gatilho |
|---|---|---|
| Sessions drill-down | Flow/Processos | workflow instance vinculado |
| Flow/Processos drill-down | Sessions | `origin_session_id` presente |
| Agents drill-down | Sessions | sessões do agente no período |
| Análise/Agentes | Atendimento/Agents | link no nome do agente |

---

## Rotas

| Path | Componente |
|---|---|
| `/contacts/sessions` | `SessionsPage` |
| `/contacts/agents` | `AgentsPage` (sub-abas Monitor / List) |
| `/contacts/events` | `EventsPage` |
| `/flow/processos` | `ProcessosPage` |
| `/analise/contatos` | `AnaliseContatosPage` |
| `/analise/agentes` | `AnaliseAgentesPage` |
| `/analise/processos` | `AnaliseProcessosPage` |
| `/analise/qualidade` | `AnaliseQualidadePage` |

**Redirects legados:**
- `/contacts` → `/contacts/sessions`
- `/contacts?tab=analise` → `/analise/contatos`
- `/contacts?tab=agents` → `/contacts/agents`

---

## ABAC

| Rota | Módulo | Campo | Acesso mínimo |
|---|---|---|---|
| `/contacts/*` | `contacts` | `operacao` | `read_only` |
| `/analise/*` | `contacts` | `visualizar` | `read_only` |
| `/flow/processos` | `workflows` | `operacao` | `read_only` |

Roles `admin`, `supervisor`, `developer` têm acesso irrestrito (bypass do check ABAC, já implementado).

---

## Componentes a criar / migrar

| Componente | Ação | Origem |
|---|---|---|
| `SessionsPage.tsx` | Criar | substitui lista de contatos atual |
| `AgentsPage.tsx` | Criar | sub-abas Monitor + List |
| `EventsPage.tsx` | Criar | novo |
| `ProcessosPage.tsx` | Criar | extrai scope "Processos" de MonitorTab |
| `AnaliseContatosPage.tsx` | Criar | migra Analysis tab de ContactsPage |
| `AnaliseAgentesPage.tsx` | Migrar | baseado em `AgentsTab.tsx` existente |
| `AnaliseProcessosPage.tsx` | Criar | novo |
| `AnaliseQualidadePage.tsx` | Criar | novo |
| `ContactsPage.tsx` | Remover | substituída pelas páginas acima |
| Nav lateral | Atualizar | adicionar grupo Análise, sub-item Processos em Flow |

---

## Fora de escopo desta tarefa

- Implementação dos backends de Analytics (ClickHouse queries para Análise/Processos, Análise/Qualidade) — backend pendente separado
- Arc 8 backend (agent_pause schema, Config API seed, analytics endpoint) — pendente separado (ver TODO.md)
- Filtros avançados por segmento em Sessions — fase posterior
- Exportação XLSX — fase posterior (CSV basta por ora)
