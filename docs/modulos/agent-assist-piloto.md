# Agent Assist — Protótipo UI (Piloto)

> Spec de referência: v24.0 seções 3.2a, 4.5
> Módulo pai: [agent-assist.md](agent-assist.md)
> Escopo: protótipo funcional para o piloto — interface de atendimento
> do agente humano com assistência em tempo real. Não é produto final.

---

## O que é e o que não é

**É:** uma interface funcional suficiente para demonstrar os conceitos do piloto
numa conferência — atendimento real via chat, sentimento em tempo real, sugestões
de agentes IA, conferência ativa, insights do cliente.

**Não é:** produto de produção com autenticação robusta, multi-tenant, resiliência
total, acessibilidade completa ou design system finalizado. Esses aspectos são
deixados para o produto após validação dos conceitos.

---

## Stack técnica

```
React 18 + TypeScript
Vite                    ← build e dev server
Tailwind CSS            ← estilização utilitária
shadcn/ui               ← componentes base (Badge, Card, Button, Progress, Alert)
Recharts                ← gráfico de trajetória de sentimento
WebSocket nativo        ← conexão com Channel Gateway (chat ao vivo)
fetch + polling         ← chamadas às supervisor tools via API REST do mcp-server
```

O mcp-server-plughub expõe as supervisor tools como endpoints REST para consumo
pelo protótipo — sem overhead de implementar cliente MCP completo no frontend.

---

## Layout geral

```
┌─────────────────────────────────────────────────────────────────┐
│  HEADER: nome do agente · pool · session_id · SLA bar           │
├──────────────────────────┬──────────────────────────────────────┤
│                          │  PAINEL DIREITO (tabs)               │
│  ÁREA DE CHAT            │                                      │
│                          │  [Estado] [Capacidades] [Contexto]   │
│  mensagens do cliente    │                                      │
│  mensagens do agente     │  conteúdo da tab ativa               │
│  mensagens do assistente │                                      │
│  (quando em conferência) │                                      │
│                          │                                      │
├──────────────────────────┤                                      │
│  INPUT DO AGENTE         │                                      │
│  [ Digite sua resposta ] │                                      │
│  [Enviar]  [Encerrar]    │                                      │
└──────────────────────────┴──────────────────────────────────────┘
```

Divisão: 60% chat / 40% painel de assistência.
O painel direito usa tabs para alternar entre os três contextos de informação
sem sobrecarregar a interface visualmente.

---

## Área de chat

### Bolhas de mensagem

Cada mensagem é renderizada com identidade visual distinta por autor:

| Autor | Alinhamento | Cor de fundo | Label |
|---|---|---|---|
| Cliente | Esquerda | Cinza claro | — |
| Agente humano | Direita | Azul | "Você" |
| Agente IA (conferência) | Esquerda | Verde claro | "Assistente" |
| Sistema | Centro | Amarelo claro | itálico |

### Indicador de entrada em conferência

Quando `agent_join_conference` é acionado, aparece uma mensagem de sistema
no chat:

```
── Assistente entrou na conversa ──
```

Quando o agente IA encerra via `agent_done`:

```
── Assistente saiu da conversa ──
```

### Input do agente

Campo de texto com envio por Enter (Shift+Enter para nova linha) e botão
"Enviar". Botão "Encerrar Atendimento" abre modal de confirmação com campos
`issue_status` (obrigatório) e `handoff_reason` (obrigatório se outcome ≠ resolved).

---

## Tab: Estado da Conversa

Atualizada via `supervisor_state` a cada mensagem enviada ou recebida.

### Sentimento

```
┌─────────────────────────────────────┐
│ SENTIMENTO                          │
│                                     │
│  ████████░░  -0.35   ▼ declining    │ ← cor: vermelho quando alert: true
│                                     │
│  [gráfico de linha — trajectory]    │
│   turnos: 1  3  5  7  8             │
└─────────────────────────────────────┘
```

- Gauge de -1 a +1 com cor progressiva (verde → amarelo → vermelho)
- Linha do tempo de sentimento (Recharts LineChart) com os últimos N pontos
  da `trajectory`
- Badge `▼ declining` / `▲ improving` / `→ stable` com cor correspondente
- Quando `alert: true`: borda vermelha no card + badge "⚠ Atenção" pulsando

### Intent e flags

```
┌─────────────────────────────────────┐
│ INTENT                              │
│ portability_check  (87%)            │
│                                     │
│ Histórico: billing_query →          │
│            portability_check        │
│                                     │
│ FLAGS ATIVAS                        │
│ [churn_signal]  [authentication...] │ ← badges coloridos por severidade
└─────────────────────────────────────┘
```

### SLA

```
┌─────────────────────────────────────┐
│ SLA                    4:00 / 8:00  │
│ ████████░░░░░░░  50%                │ ← amarelo > 70%, vermelho se breach_imminent
└─────────────────────────────────────┘
```

Barra de progresso com cor dinâmica. Quando `breach_imminent: true`: barra
vermelha pulsando + toast de alerta no topo da tela.

---

## Tab: Capacidades

Atualizada via `supervisor_capabilities` quando o intent muda ou a cada
5 turnos (configurável).

### Agentes sugeridos

```
┌─────────────────────────────────────┐
│ AGENTES SUGERIDOS                   │
│                                     │
│ ● agente_portabilidade_v2           │
│   Alta relevância · background      │
│   5 instâncias disponíveis          │
│   [Acionar em background]           │
│                                     │
│ ○ agente_autenticacao_v1            │
│   Média relevância · conferência    │
│   12 instâncias disponíveis         │
│   [Chamar Assistente]               │
└─────────────────────────────────────┘
```

- `interaction_model: background` → botão "Acionar em background"
  (executa sem aparecer ao cliente)
- `interaction_model: conference` → botão "Chamar Assistente"
  (aciona `agent_join_conference`)
- Quando `auto_join: true` e `relevance: high` → o Agent Assist aciona
  automaticamente e exibe toast: "Assistente acionado em background"
- Botão desabilitado quando `circuit_breaker: open`

### Escalações recomendadas

```
┌─────────────────────────────────────┐
│ ESCALAÇÃO RECOMENDADA               │ ← card destacado quando recommended: true
│                                     │
│ → especialista_retencao             │
│   churn_signal + declining_sentiment│
│   Espera estimada: 45s              │
│   [Escalar agora]                   │
└─────────────────────────────────────┘
```

Quando `recommended: true`, o card recebe destaque visual (borda laranja)
e aparece independente da tab ativa como banner colapsável no topo do painel.

---

## Tab: Contexto do Cliente

Populada com `customer_context` do `supervisor_state`.

### Insights históricos

```
┌─────────────────────────────────────┐
│ HISTÓRICO DO CLIENTE                │
│ (últimos 30 dias)                   │
│                                     │
│ • Solicitou portabilidade em jan/26 │
│   última ocorrência: 15/01/2026     │
│                                     │
│ • Plano atual: Pós-pago 50GB        │
│   última ocorrência: 03/03/2026     │
│                                     │
│ • 2 reclamações de cobrança em 2026 │
│   última ocorrência: 20/02/2026     │
└─────────────────────────────────────┘
```

### Insights da conversa atual

```
┌─────────────────────────────────────┐
│ ESTA CONVERSA                       │
│                                     │
│ • Mencionou concorrente (turno 4)   │
│   confiança: 0.91                   │
│                                     │
│ • Insatisfação com preço (turno 6)  │
│   confiança: 0.85                   │
└─────────────────────────────────────┘
```

A separação visual entre histórico e conversa atual é explícita — o agente
sabe o que é memória de longo prazo e o que foi identificado agora.
Insights da conversa atual são adicionados progressivamente a cada turno.

---

## Conexão e polling

### WebSocket (chat ao vivo)

```
conecta em → ws://mcp-server-plughub/agent/ws/{session_id}
```

Recebe mensagens do cliente e confirmações de envio em tempo real.
Envia mensagens do agente humano.

### Polling das supervisor tools

| Tool | Frequência | Trigger |
|---|---|---|
| `supervisor_state` | A cada mensagem (inbound ou outbound) | WebSocket event |
| `supervisor_capabilities` | Quando intent muda + a cada 5 turnos | Comparação com estado anterior |

O polling é orientado a eventos — não há `setInterval` fixo. O `supervisor_state`
é chamado sempre que o WebSocket recebe uma mensagem nova. O `supervisor_capabilities`
é chamado quando `intent.current` muda em relação ao último estado conhecido.

### Indicador de staleness

Quando `is_stale: true` no `supervisor_state`, o painel direito exibe um
banner discreto: "Dados podem estar desatualizados" — sem bloquear a interface.

---

## Estados globais da interface

| Estado | Trigger | Manifestação visual |
|---|---|---|
| `sentiment.alert` | `alert: true` | Card de sentimento com borda vermelha + badge pulsando |
| `breach_imminent` | SLA `breach_imminent: true` | Barra de SLA vermelha + toast persistente |
| `escalation.recommended` | `recommended: true` | Banner laranja colapsável no topo do painel |
| `conference.active` | Após `agent_join_conference` | Badge "Assistente na conversa" no header |
| `is_stale` | `is_stale: true` | Banner cinza discreto no painel |

---

## O que fica fora do escopo do protótipo

- Autenticação do agente humano (login/senha) — agente entra direto com session_id para demo
- Gestão de múltiplas conversas simultâneas — uma conversa por vez
- Notificações sonoras
- Modo escuro
- Responsividade mobile
- Internacionalização
- Acessibilidade (WCAG)
- Histórico de conversas anteriores (lista de atendimentos)

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `mcp-server-plughub` | Fonte das supervisor tools via REST — `supervisor_state`, `supervisor_capabilities`, `agent_join_conference` |
| `channel-gateway` (webchat) | WebSocket para receber/enviar mensagens do cliente |
| `ai-gateway` | Produtor indireto — mantém o estado que `supervisor_state` lê |
| `agent-registry` | Configuração do `supervisor_config` do pool |
