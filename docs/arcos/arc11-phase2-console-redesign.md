# Arc 11 Fase 2 — Console: Redesign de Orquestração

> **Status**: Em especificação — validado. Nenhuma fase implementada.

---

## Objetivo

Evoluir o Console de uma interface de atendimento para uma **superfície de orquestração simétrica**: o agente humano opera com as mesmas capacidades de um agente IA orquestrador — invita especialistas, monitora segmentos de pós-atendimento, consulta contexto e histórico de journey. Simultaneamente, elimina redundância de informações e concentra cada sinal no lugar onde é mais acionável.

---

## Problemas Atuais

| # | Problema | Impacto |
|---|----------|---------|
| P1 | session_id, timer, SLA e pool_id aparecem em 3 lugares simultâneos | Ruído visual, desperdício de espaço |
| P2 | SLA visível apenas para a sessão corrente (barra superior) | Agente não sabe qual das suas sessões ativas é mais urgente |
| P3 | Tempo que o cliente está esperando resposta não existe na UI | Agente não sabe se cliente de outra sessão está esperando há minutos |
| P4 | "Convidar Especialista" e "Delegar Tarefa" são botões separados no header | Fluxo fragmentado, inconsistente com modelo de especialistas |
| P5 | Aba State agrega informações que não têm relação entre si | Não há coerência semântica na aba |
| P6 | Aba History mostra apenas transcript — não conecta ao Journey | Agente não enxerga o histórico de contexto de sessões anteriores no journey |
| P7 | Sentiment e intention aparecem em abas secundárias | Sinais importantes invisíveis durante a conversa |

---

## Modelo de Informação Proposto

Cada informação no lugar onde é **mais acionável**, não onde é tecnicamente disponível:

| Informação | Onde fica | Por quê |
|------------|-----------|---------|
| Timer (tempo total do contato) | Lista esquerda | Comparação entre sessões ativas |
| SLA | Lista esquerda | Priorização entre sessões ativas |
| Tempo esperando resposta | Lista esquerda | Urgência de resposta por sessão |
| pool_id | Lista esquerda | Contexto do contato |
| session_id | Lista esquerda | Identificação do contato |
| Sentimento | Barra superior | Sinal de tom da conversa corrente |
| Botão de ação (Desligar, etc.) | Barra superior | Controles da sessão corrente |
| Agentes na sessão + Adicionar | Aba Agentes | Orquestração |
| Segmentos pós-atendimento | Aba Agentes | Orquestração |
| Intention + ContextStore | Aba Context | Inteligência da sessão |
| Histórico de sessões do Journey | Aba Journey | Visão longitudinal do cliente |

---

## Mudanças Detalhadas

### 1. Lista de Contatos (esquerda) — Enriquecida

Cada item da lista passa a exibir quatro sinais em formato compacto:

```
● 6aca7b5c   retencao_humano
  ⏱ 0:14   [████░░ SLA 68%]

● 3f2b1a9d   retencao_humano   💬 3:12
  ⏱ 1:02   [██░░░░ SLA 31%]   ← cliente esperando, SLA crítico
```

**Campos:**
- **session_id** truncado (6–8 chars) + pool_id
- **⏱ timer** — tempo total do contato (MM:SS)
- **Barra SLA** — progresso colorido (verde → amarelo → vermelho) + percentual
- **💬 tempo de espera de resposta** — aparece SOMENTE quando o último a falar foi o cliente; cor escalona: verde (< 1min) → amarelo (1–3min) → vermelho pulsante (> 3min); some quando agente responde

**Redis novo:** `session:{id}:last_customer_message_at` — timestamp UNIX (ms), atualizado a cada mensagem com `author.type == "customer"` e `visibility == "all"`. Escrito pelo bridge (Core). Cálculo do tempo de espera é client-side (`now - last_customer_message_at`).

**Removidos da lista:** nenhum — informações que estavam em outros lugares são consolidadas aqui.

---

### 2. Barra Superior — Simplificada

**Antes:** session_id | pool | timer | SLA | Transferir | Desligar | Substituir | Colaborar | SLA (duplicado) | Connected

**Depois:**
```
[ ⏱ 0:14 ]  [ 😊 Satisfeito ]  |  [ Transferir ]  [ Desligar ]  [ Pausar ]  [ Connected ]
```

**Removidos:** session_id, pool_id, SLA (movidos para lista esquerda)

**Adicionado:** indicador de sentimento compacto (ícone + label colorido, atualiza em tempo real)

Escala de ícone/cor do sentimento:
- 😊 verde — Satisfeito (score ≥ 0.3)
- 😐 cinza — Neutro (-0.3 a 0.3)
- 😤 laranja — Frustrado (-0.6 a -0.3)
- 😡 vermelho — Irritado (< -0.6)

**Botão Substituir:** removido da barra principal. Função mantida como ação secundária no card do agente na aba Agentes ("..." → Substituir).

---

### 3. Aba State → Removida

Todo o conteúdo da aba State é distribuído para locais mais contextuais (ver tabela acima). A aba é eliminada.

**Abas resultantes no painel direito (2):** Agentes · Context

**Abas na área central (2):** Atual · Journey

---

### 4. Aba Agentes (nova — substitui State)

Superfície completa de orquestração de agentes da sessão. Três seções verticais:

#### Seção A — Ativos na Sessão
Lista dos agentes presentes na sessão agora (humanos e IA), herdada dos cards do Arc 11 F1. Cada card mostra: nome, role (specialist/supervisor), estado atual (step do skill flow se IA), tempo no segmento.

Ação secundária por card: `...` → **Substituir** (rebaixado da barra principal) | **Encerrar segmento**.

#### Seção B — Adicionar Agente
Unifica "Convidar Especialista" (Arc 11 F2) e "Delegar Tarefa" (Arc 11 F3) em um único fluxo:

1. Lista de agentes disponíveis derivada de `session.pool.mentionable_pools` (ContextStore)
2. Agente selecionado → botão desabilita imediatamente (previne duplo-convite na mesma sessão)
3. Se o skill/pool declarar `invite_params` → abre popup com formulário de parâmetros
4. Se não houver `invite_params` → convite enviado diretamente
5. Ícone do agente na lista indica estado:
   - ⚪ Disponível para convite
   - 🔄 Aguardando entrada na sessão
   - 🟢 Ativo na sessão
   - ✅ Concluído — botão reabilitado

O tipo de participação (colaborativa vs tarefa async) é determinado pela declaração do agent-type/pool, não por dois botões distintos na UI.

**`invite_params`** — novo campo em pool YAML (opcional):
```yaml
invite_params:
  - id: contexto
    label: Contexto para o agente
    type: textarea
    required: false
  - id: objetivo
    label: Objetivo específico
    type: text
    required: true
```

#### Seção C — Pós-Atendimento
Visível somente quando hooks posatt estão ativos (Arc 14). Mostra os segmentos de pós-atendimento com estado:

```
Wrap-up      🔄 Rodando   0:42
NPS          ⏳ Aguardando cliente
Avaliação    ⚪ Agendada
```

Estados: Aguardando entrada | Rodando | Concluído | Timeout

---

### 5. Aba Context (enriquecida)

Deixa de ser um container vazio e passa a ser o **viewer ao vivo do ContextStore** da sessão corrente.

**Conteúdo:** todas as entries `{tenant}:ctx:{session_id}` agrupadas por namespace:

```
▾ session
    pool.id          retencao_humano
    close_origin     —
    sentimento       Neutro (0.40)
    intention        Portabilidade de número    ← antes era aba State
    copilot.summary  Cliente solicita portabilidade...

▾ caller
    name             João Silva
    phone            ***-1234
    tier             gold

▾ account
    plan             Premium
    since            2023-04
```

Confidence indicada por cor do valor: alto (azul) / médio (cinza) / baixo (itálico).

**Escrita manual:** agente humano pode adicionar ou editar tags via campo inline → chama MCP tool `context_tag_set` → persiste no ContextStore. Torna o agente humano produtor de contexto, não só consumidor.

---

### 6. Área Central — Abas Atual e Journey

O transcript da conversa é conteúdo primário — vive na área central. Sessões passadas do journey são também transcripts e pertencem ao mesmo espaço visual. A área central passa a ter duas abas:

#### Aba Atual
Transcript da sessão corrente — comportamento idêntico ao de hoje. Input de resposta, mensagens em tempo real, cards de menu/botões do skill flow.

#### Aba Journey
Exibe o histórico de sessões do Journey ao qual este contato pertence (Arc 10).

**Layout — painel esquerdo interno (lista de sessões) + painel direito (transcript):**
```
Journey #j-7f3a2b  —  Portabilidade Telco
────────────────────────────────────────
  ✅ 2026-05-12  retencao_humano  12:34   Resolvido    ← selecionada
  ✅ 2026-05-10  retencao_humano   8:21   Escalado
  🔵 2026-05-16  retencao_humano   0:14   Em andamento (atual)
```

Selecionando uma sessão → exibe o transcript daquela sessão na área de leitura central (read-only).

**Efeito no painel Context (direita):** quando uma sessão do journey está selecionada, a aba Context passa a exibir o ContextStore *daquela* sessão (não da corrente). Um indicador sutil na aba mostra qual sessão está ativa: `Context · 2026-05-12`. Ao voltar para a aba Atual, Context retorna à sessão corrente.

**Estado sem journey:** se o contato é standalone (sem journey), a aba Journey exibe "Contato standalone — sem journey associado" e fica em modo simplificado (só mostra a sessão corrente como único item).

---

## Impacto por Componente

| Componente | Impacto | Descrição |
|------------|---------|-----------|
| `platform-ui/AgentAssistContext.tsx` | Alto | Estado de sentimento, last_customer_message_at, posatt segments |
| `platform-ui/Console/ContactList` | Médio | SLA, timer, 💬 indicador na lista |
| `platform-ui/Console/ActionBar` | Médio | Sentimento, remover session_id/SLA/pool |
| `platform-ui/Console/AgentesTab` | Alto | Componente novo — seções A, B, C |
| `platform-ui/Console/ContextTab` | Alto | ContextStore viewer + escrita manual |
| `platform-ui/Console/ChatArea` | Médio | Adicionar abas Atual / Journey na área central |
| `platform-ui/Console/JourneyTab` | Alto | Lista de sessões + transcript viewer + ctx sync com painel direito |
| `orchestrator-bridge/main.py` | Baixo | Escrever `last_customer_message_at` em cada mensagem do cliente |
| `mcp-server-plughub/tools` | Baixo | Expor `context_tag_set` para agentes humanos |
| `@plughub/schemas` | Baixo | `invite_params` em PoolHooksSchema; `last_customer_message_at` |

---

## Fases de Implementação

| Fase | Escopo | Dependências |
|------|--------|--------------|
| **A** | Lista esquerda enriquecida: SLA, timer, pool_id, session_id consolidados + `last_customer_message_at` (💬 indicador) | Nenhuma |
| **B** | Barra superior simplificada: remover redundâncias, adicionar sentimento compacto | Fase A |
| **C** | Aba Agentes: seções Ativos + Adicionar (unificando Convidar + Delegar) com estados de ícone e `invite_params` popup | Nenhuma |
| **D** | Aba Context: ContextStore viewer ao vivo + escrita manual via `context_tag_set` | Nenhuma |
| **E** | Área central — aba Journey: lista de sessões + transcript viewer + sincronização com aba Context | Arc 10 (implementado) |
| **F** | Aba Agentes seção C (Pós-Atendimento): estados dos segmentos posatt | Arc 14 Fase A |

---

## Conexão com Arc 14

A seção C da aba Agentes (Pós-Atendimento) depende do Arc 14 para existir. Sem Arc 14, a aba Agentes tem apenas as seções A e B — funcionalmente completa. A seção C é adicionada quando Arc 14 Fase A for implementada.

---

## Notas de Design

- **Paleta de cores do sentimento** usa os design tokens existentes: `green=#059669` (satisfeito), `warning=#D97706` (frustrado), `red=#DC2626` (irritado), cinza padrão (neutro)
- **SLA bar** na lista esquerda usa a mesma escala: verde ≥ 60%, amarelo 30–60%, vermelho < 30%
- **💬 indicador** usa a mesma escala: verde < 60s, amarelo 60–180s, vermelho pulsante > 180s
- Nenhum inline hex — apenas Tailwind tokens conforme regra do CLAUDE.md

---

*Criado em 2026-05-16. Pronto para iniciar Fase A.*
