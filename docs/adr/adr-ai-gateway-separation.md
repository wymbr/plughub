# ADR: Separação de AI Gateway — Carga Operacional vs Avaliação

**Status:** Proposto  
**Data:** 2026-04-20  
**Contexto:** Session Replayer / Evaluator (seção pending — CLAUDE.md)

---

## Contexto

O AI Gateway é o componente responsável por inferência LLM stateless no PlugHub.
Com a introdução do Session Replayer e do agente Evaluator para avaliação de qualidade
pós-sessão, surge a questão de se avaliações devem compartilhar o mesmo deployment de
AI Gateway usado pelas sessões operacionais ativas.

## Decisão

Separar em dois deployments do AI Gateway (mesmo código, configurações distintas),
apontados por `GatewayConfig` diferentes no Agent Registry:

- `ai-gateway-operational` — usado por todos os agent types de atendimento
- `ai-gateway-evaluation` — usado exclusivamente por agent types de avaliação (ex: `agente_avaliador_v1`)

O isolamento é declarativo: nenhuma mudança no AI Gateway em si, nenhuma mudança
no modelo de dados. A separação é feita por configuração no Agent Registry.

## Justificativa

### 1. Isolamento de rate limit (razão principal)

Operação e avaliação compartilhando a mesma API key do provider LLM significa que
um spike de avaliações (fim de turno, batch noturno de qualidade) consome o budget
de requisições por minuto e pode atrasar respostas de agentes em sessões ativas.
Um agente em sessão de retenção esperando 10s por resposta LLM é um problema de
negócio, não apenas de performance.

Com API keys separadas, avaliações não afetam o budget operacional sob nenhuma
circunstância — incluindo falhas de provider que derrubem uma key específica.

### 2. Modelo LLM diferente por propósito

Sessões operacionais requerem latência baixa → Claude Sonnet ou Haiku.
Avaliação de qualidade se beneficia de raciocínio mais profundo → Claude Opus.

Com gateways separados, cada um tem seu modelo configurado independentemente.
Com gateway compartilhado, seria necessário lógica de seleção de modelo por chamada,
introduzindo conhecimento de negócio no AI Gateway — violação de responsabilidade única.

### 3. Política de retry diferente

- Sessão ativa: falhou → erro imediato, o agente trata o fallback
- Avaliação: falhou → retry com backoff exponencial, sem urgência de SLA

Misturar as duas políticas no mesmo gateway complica o código sem ganho.

### 4. Custo separável por propósito

LLM é a linha de custo mais visível no modelo de operação. Com gateways ou API keys
separadas, é possível atribuir custo exato por propósito:
operação vs programa de qualidade. Relevante para billing por tenant.

## Alternativas descartadas

**Fila de prioridade dentro do mesmo gateway**: não resolve o problema fundamental.
O budget de rate limit ainda é compartilhado e o ponto de falha ainda é único.
Se o provider suspender a API key por excesso de requisições, operação e avaliação
caem simultaneamente.

**Seleção de modelo por chamada**: introduz lógica de negócio no gateway, viola o
princípio de responsabilidade única do componente.

## Impacto na implementação

- Nenhuma mudança no código do `ai-gateway`
- Nenhuma mudança no modelo de dados
- Dois registros de `GatewayConfig` no Agent Registry
- Agent type `agente_avaliador_v*` configurado com `gateway_url` do segundo deployment
- Variáveis de ambiente distintas por deployment (API key, modelo padrão, limites)

## Referências

- CLAUDE.md — "Pending: Session Replayer"
- Spec v1 — seção AI Gateway (stateless inference)
- Spec v1 — seção Agent Registry / GatewayConfig
