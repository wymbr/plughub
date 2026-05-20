# CHANGELOG — PlugHub Implementações Concluídas

---

## Channel Gateway — Phase 1 Refactoring Multi-Canal (2026-05-20)

Refactoring de base para suporte a múltiplos canais no `channel-gateway`. Sem breaking changes — comportamento do webchat inalterado.

### Arquivos criados

- **`adapters/__init__.py`**: pacote adapters.
- **`adapters/base.py`**: `ChannelAdapter` ABC com 4 métodos abstratos (`deliver_text`, `deliver_menu`, `deliver_typing`, `deliver_session_closed`). `channel: ClassVar[str]` identifica o canal. Um singleton por canal registrado no `OutboundConsumer`.
- **`adapters/webchat_channel.py`**: `WebchatChannelAdapter(ChannelAdapter)` — singleton de entrega para webchat. Extrai lógica de dispatch do `OutboundConsumer` anterior. `deliver_text` persiste histórico (sem WS send — hybrid stream model). `deliver_menu` registra `masked_fields` no `SessionRegistry`. `deliver_typing` e `deliver_session_closed` enviam via WebSocket.

### Arquivos modificados

- **`models.py`**: `channel: Literal["webchat"]` → `channel: str = "webchat"` em `NormalizedInboundEvent`, `ContactOpenEvent`, `ContactClosedEvent`. Adicionado `content_type: Literal["text", "audio_transcript", "image", "document", "video"] = "text"` em `NormalizedInboundEvent`. Adicionado `channel_session_id: str | None = None` em `ContactOpenEvent` (wamid, CallSid, Message-ID etc.).
- **`outbound_consumer.py`**: reescrito com `_adapters: dict[str, ChannelAdapter]`. Elimina `if channel != "webchat"` hardcoded — delega por `self._adapters.get(channel)`. Novos canais adicionados registrando o adapter no `main.py`, sem tocar o consumer.
- **`main.py`**: instancia `WebchatChannelAdapter` e passa `adapters={"webchat": ...}` para `OutboundConsumer`.
- **`tests/test_outbound_consumer.py`**: atualizado para nova API. Testa routing do consumer, `WebchatChannelAdapter` por tipo de mensagem, e tratamento de erros.

### Spec

→ [`docs/arcos/channel-gateway-multi-channel.md`](docs/arcos/channel-gateway-multi-channel.md)

---

## Retry + Dead-Letter Queue nos consumers Kafka críticos (2026-05-20)

Implementado padrão de retry com backoff exponencial + DLQ (`events.dead_letter`) nos três consumers Kafka críticos da plataforma, eliminando perda silenciosa de eventos em caso de falha transiente.

### skill-flow-worker (`packages/skill-flow-worker/`)

- **`config.ts`**: adicionado `kafkaDlqTopic: string` em `WorkerSettings` (env `KAFKA_DLQ_TOPIC`, default `events.dead_letter`).
- **`worker.ts`**: reescrito com `DLQ producer` KafkaJS, método `_handleWithRetry()` (3 tentativas, backoff 500 ms → 1 000 ms) e `_publishDlq()`. Fire-and-forget de `runInstance()` preservado via `_inflight` Set — apenas o dispatch layer é retried. Erros de JSON skip imediato (sem retry). `DlqPayload`: `event_id`, `source_topic`, `consumer_group`, `service`, `error`, `attempt_count`, `payload_raw`, `failed_at`.

### analytics-api (`packages/analytics-api/`)

- **`config.py`**: adicionado `kafka_dlq_topic: str = "events.dead_letter"`.
- **`consumer.py`**: adicionado `AIOKafkaProducer` (DLQ producer), constantes `MAX_ATTEMPTS=3`/`BACKOFF_BASE_MS=500`, funções `_process_with_retry()` e `_publish_dlq_analytics()`. `_process_message()` agora propaga exceções (sem `except Exception` externo); `_write_row()` já propagava. JSON malformado: skip imediato. Commit de offset após batch completo — mensagens DLQ-roteadas também commitadas.

### orchestrator-bridge (`packages/orchestrator-bridge/`)

- **`main.py`**: adicionado `KAFKA_DLQ_TOPIC` (env var), `_MAX_DISPATCH_ATTEMPTS=3`/`_DISPATCH_BACKOFF_BASE_MS=500`, função `_publish_dlq_bridge()` (usa `_kafka_producer` global já existente). `_dispatch()` reescrito como wrapper de retry; lógica movida para `_dispatch_once()`. Fire-and-forget via `asyncio.create_task` preservado — retries ocorrem dentro da task, sem bloquear o consumer loop.

### Contrato DLQ uniforme

Todos os três consumers publicam o mesmo formato `DlqPayload` no tópico `events.dead_letter`:
`event_id`, `source_topic`, `consumer_group`, `service`, `error`, `attempt_count`, `payload_raw`, `failed_at`.

---

## Channel Endpoints Layer 2 + Analytics Agents expandido (2026-05-20)

### Channel Endpoints — Layer 2: channel-gateway endpoint resolver

Implementado o lookup de channel endpoints no hot-path do WebSocket, completando a Layer 2 da pilha de roteamento do canal.

**agent-registry** (`packages/agent-registry/src/routes/channel-endpoints.ts`):
- `GET /v1/channel-endpoints` agora aceita o query param `identifier` como filtro; sem ele, retorna todos os endpoints do tenant/channel — backward compatible.

**channel-gateway** (`packages/channel-gateway/`):
- `pyproject.toml`: `httpx>=0.27.0` movido de `[dev]` para dependências principais.
- `config.py`: dois novos settings com prefixo `PLUGHUB_`:
  - `agent_registry_url` (default `http://localhost:3000`)
  - `endpoint_cache_ttl_s` (default `30`)
- Novo módulo `endpoint_resolver.py`:
  - Cache em memória `dict[(tenant_id, channel, identifier), (pool_id, expires_at)]` com TTL configurável; evita round-trips no connect path.
  - Double-check lock (asyncio.Lock) para prevenir cache stampede em conexões concorrentes.
  - Negative caching: `None` também é armazenado com TTL para evitar bombardear o registry com slugs desconhecidos.
  - `resolve_pool(channel, identifier, tenant_id, agent_registry_url, cache_ttl_s, http_timeout_s)` — retorna `pool_id | None`; erros de rede são logados e retornam `None` (nunca quebram o WebSocket accept).
  - `invalidate(tenant_id, channel?)` — exposta para futura integração com consumer `registry.changed`.
- `main.py` — `websocket_endpoint`:
  - Tenta resolver via `resolve_pool('webchat', pool_id_param, …)`.
  - Se retornar `None` (sem registro ativo ou registry indisponível), cai no fallback: `pool_id_param or settings.entry_point_pool_id` — 100% backward compatible.

**Pendente (operacional)**: executar `prisma migrate dev --name add_channel_endpoint` no agent-registry para criar a tabela `channel_endpoints` em produção.

### Analytics Agents — página expandida com abas Humanos / IA

`AnaliseAgentesPage.tsx` reescrita com duas abas de primeiro nível:

- **Humanos**: KPI strip (Sessões, TMO, Resolução, Escalação) + gráfico de tendência diária (Recharts LineChart) + seção de disponibilidade/pausas existente (Arc 8).
- **IA**: KPI strip + gráfico de tendência + tabela de performance por `agent_type_id × pool` com badges coloridos de resolution/escalation.
- Hooks: `useAgentPerformance` (`GET /analytics/reports/agents/performance`) + `useAgentPerformanceDaily` (`GET /analytics/reports/agent-performance/daily`).
- Namespace i18n `agentReports` — locale files `en/agentReports.json` + `pt-BR/agentReports.json` completos.

---

## platform-ui: i18n Console + remoção do modal de wrap-up manual (2026-05-19)

Cobertura i18n completa do módulo agent-assist e remoção do fluxo manual de wrap-up
que foi substituído pelos hook agents do Arc 14.

### i18n — namespace `agentAssist` completo

Todos os componentes do Console agora usam `useTranslation('agentAssist')` + `t(key)`.
Nenhuma string hardcoded de PT-BR ou EN permanece no módulo:

- `AgentAssistPage.tsx` — toasts, labels de tab central (Atual/Journey)
- `AgentInput.tsx` — botão "Enviar"
- `CannedPhrasesPalette.tsx` — empty state de busca
- `Header.tsx` — fix interpolação `count` (reservado i18next) → `pools`
- `AgentesTab.tsx` — title "Opções"
- `ContextoTab.tsx` — refactoring completo: helpers `confidenceLabel`, `sourceLabel`,
  `tagLabel`, `groupByNamespace` passam `t` como parâmetro; `FieldRowProps.t` e
  `CtxFieldRowProps.t` tipados como `(key, opts?) => string`
- `HistoricoTab.tsx` — locale de `formatDate` hardcode PT-BR → `undefined` (browser locale)
- Locale files `en/agentAssist.json` + `pt-BR/agentAssist.json`:
  - Adicionadas seções: `message.*`, `agentes.*`, `contexto.*`, `centerTab.*`, `canned.*`
  - Fix: `comboPools` interpolation `{{count}}` → `{{pools}}`

### Remoção do CloseModal — wrap-up delegado ao Arc 14

- `CloseModal` removido de `AgentAssistPage.tsx` (import + state + JSX)
- `onEncerrar` agora chama `handleClose` diretamente com defaults
  `{ issue_status: "closed", outcome: "resolved" }` — sem modal
- `pendingCloseModal` (cliente desconectou antes do agente receber o contato):
  `useEffect` auto-dispara `agent_done` com `{ issue_status: t("clientDisconnected"), outcome: "abandoned" }`
- Pós-atendimento (wrap-up, NPS, formulários) tratado exclusivamente pelos
  hook agents do Arc 14 (`side: agent | customer`)

---

## Microcopy Review — PT-BR (2026-05-18)

Revisão sistemática de copy UX em todas as superfícies principais. Critérios: idioma PT-BR consistente, tokens semânticos, clareza de CTAs, mensagens de erro empáticas e úteis, ellipsis correto.

### `auth/LoginPage.tsx`
- Toda a string de erro em inglês → PT-BR: `"E-mail ou senha incorretos."`, `"Sua conta está inativa. Entre em contato com o administrador."`, etc.
- Labels "Email" → "E-mail", "Password" → "Senha"
- Placeholder `"you@example.com"` → `"voce@empresa.com.br"`
- CTA `"Sign In"` → `"Entrar"`, `"Signing in…"` → `"Entrando…"`
- Subtítulo `"Enterprise Orchestration Platform"` → `"Plataforma de Orquestração Empresarial"`
- Erro alert: tokens `bg-red-50 border-red-400 text-red-700` → `bg-red-light border-red text-red-text`
- Removido hint de credencial de dev (segurança)

### `components/ui/EmptyState.tsx`
- `text-gray` → `text-muted` (token semântico correto)

### `modules/agent-assist/components/ToastContainer.tsx`
- `bg-indigo-600` → `bg-info` / `bg-warning` / `bg-red` (tokens semânticos)
- `z-40` → `z-toast` (token de z-index)
- `shadow-lg` → `shadow-toast`
- Dismiss button: `tabIndex={-1}` removido; `aria-label="Fechar notificação"` adicionado
- Adicionado `role="region" aria-label="Notificações" aria-live="polite"`

### `modules/agent-assist/components/CannedPhrasesPalette.tsx`
- `text-indigo-600` / `bg-indigo-100` / `bg-indigo-50` / `text-indigo-500` → tokens `primary`
- Label seção "Especialistas (@mention)" → "Especialistas · via @menção" (menos técnico)
- `border-gray-100` → `border-border`

### `modules/agent-assist/components/CloseModal.tsx`
- `focus:ring-indigo-500` → `focus:ring-primary`
- Outcome buttons: `bg-indigo-600 border-indigo-600` → `bg-primary border-primary`; `hover:border-indigo-400` → `hover:border-primary`
- CTA "Confirmar encerramento" → "Encerrar atendimento" — alinha com o título do modal e segue o padrão verbo+substantivo
- `bg-red-600 hover:bg-red-700` → `bg-red hover:bg-red-text`

### `modules/agent-assist/components/DelegarTarefaDrawer.tsx`
- Visibilidade "🔒 Interno (agents_only)" → "🔒 Somente equipe" (remove ID técnico)
- Visibilidade "🌐 Visível ao cliente (all)" → "🌐 Visível ao cliente"
- Shortcut hint "⌘↵ para delegar" → "Ctrl + Enter para delegar" (Windows-compatible)
- `text-gray-400` → `text-muted`

### `modules/_placeholder/PlaceholderPage.tsx`
- `"Back to Home"` → `"Ir para o início"` (PT-BR)

### `i18n/locales/pt-BR/common.json`
- `"Carregando..."` → `"Carregando…"` (ellipsis Unicode correto em ambas ocorrências)
- `"Salvando..."` → `"Salvando…"`

---

## Accessibility Audit — WCAG 2.1 AA (2026-05-18)

Auditoria completa e correções críticas/major. 9 Critical + 14 Major + 10 Minor identificados. Resolvidos nesta sessão: todos os Critical e a maioria dos Major.

### `index.html`
- `lang="en"` → `lang="pt-BR"` — corrige WCAG 3.1.1 (Language of Page)

### `index.css`
- `*:focus-visible` com `ring-2 ring-primary ring-offset-1` — focus ring visível para teclado (WCAG 2.4.7)
- `@media (prefers-reduced-motion: reduce)` — zera todas animações/transições (WCAG 2.3.3)

### `shell/Sidebar.tsx`
- `<nav aria-label="Navegação principal">` em ambos modos collapsed/expanded (WCAG 1.3.1)
- `aria-expanded` + `aria-controls` nos botões de grupo — estado comunicado ao SR (WCAG 4.1.2)
- `<span aria-hidden="true">` em todos os emojis decorativos — elimina anúncios duplos (WCAG 1.3.1)
- `aria-current="page"` no item ativo — localização comunicada (WCAG 2.4.8)
- `aria-label` em vez de `title` nos botões Expandir/Recolher menu
- Painel de filhos usa `hidden` nativo em vez de renderização condicional — semanticamente correto

### `shell/TopBar.tsx`
- `<div>` → `<header>` — landmark `banner` ARIA (WCAG 1.3.1)
- `border-lightGray` → `border-border` / `text-gray` → `text-muted` — tokens corretos
- Skip-navigation link: "Ir para o conteúdo principal" → `#main-content` (WCAG 2.4.1)
- `aria-label` descritivo no botão de idioma (WCAG 4.1.2)
- `aria-label` no bloco de info do usuário

### `shell/Shell.tsx`
- `<main id="main-content" tabIndex={-1}>` — alvo do skip-link, recebe foco programático (WCAG 2.4.1)

### `components/ui/Table.tsx`
- Linhas clicáveis: `tabIndex={0}`, `onKeyDown` (Enter/Space), `focus-visible:ring-2` (WCAG 2.1.1)
- `scope="col"` nos cabeçalhos (WCAG 1.3.1)
- `role="grid"` na tabela com linhas interativas (WCAG 4.1.2)
- `aria-label` por linha com `rowActionLabel` configurável
- `aria-busy="true"` no skeleton state
- `motion-safe:animate-pulse` no skeleton — respeita prefers-reduced-motion
- "No data available" → "Nenhum dado disponível" (idioma correto)

### `modules/agent-assist/components/AgentInput.tsx` *(já aplicado em sessão anterior)*
- `<label>` com `sr-only` + `htmlFor` na textarea (WCAG 1.3.1, 3.3.2)
- `aria-label` descritivo no botão "/" (WCAG 4.1.2)
- `aria-expanded` + `aria-controls` no botão de paleta (WCAG 4.1.2)
- Remoção do soft focus-trap em `handleBlur` (WCAG 2.1.2)
- Touch targets 44×44px no botão "/" e Enviar (WCAG 2.5.5)

### `modules/agent-assist/components/ChatArea.tsx` *(já aplicado)*
- `role="log"` + `aria-live="polite"` na lista de mensagens (WCAG 4.1.3)
- `role="status"` no strip de sentimento com `aria-label` completo (WCAG 4.1.3)
- `aria-label` na seta de tendência (WCAG 1.1.1)
- `aria-hidden="true"` em spans decorativos
- `motion-safe:animate-bounce` nos dots do typing indicator (WCAG 2.3.3)
- Tokens: `bg-red-light border-red/20`, `bg-ai-light text-ai` (sem hardcoded indigo)

### `modules/agent-assist/components/MessageBubble.tsx` *(já aplicado)*
- `text-[10px]` → `text-xs` no rótulo de autor — corrige falha de tamanho de fonte (WCAG 1.4.4)
- Checkbox de seleção: `w-11 h-11` touch target, `aria-pressed`, `aria-label`, `focus-visible:ring` (WCAG 2.5.5, 4.1.2)
- Sempre focusável via teclado (não `opacity-0` para keyboard users)
- SVG checkmark em vez de ✓ texto literal

### Documentação
- Criado `docs/arcos/accessibility-audit.md` — 33 findings com WCAG criterion, severidade, recomendação
- Criado `docs/arcos/design-system-audit.md` — score 30/100 com sprint sequence

---

## Design System — Sprint 2: Missing Components (2026-05-18)

Componentes compartilhados que substituem ~25 implementações duplicadas espalhadas pelo código.

### Novo: `packages/platform-ui/src/components/ui/Drawer.tsx` (Task #108)

Right slide-over reutilizável. Substitui implementações independentes em DelegarTarefaDrawer, GroupsPage, CurationReview drawer, CalibrationDashboard e outros.
- Props: `isOpen`, `onClose`, `title`, `children`, `footer?`, `size` (sm/md/lg/xl), `disableBackdropClose?`, `description?`
- Body scroll lock via `useEffect`
- Escape key handler via `useEffect`
- Focus trap completo (Tab / Shift+Tab ficam dentro do painel)
- `role="dialog"`, `aria-modal`, `aria-labelledby`, `aria-describedby`
- `z-overlay` (token de z-index = 30)
- Backdrop com `aria-hidden="true"`
- Botão de fechar com `aria-label="Fechar painel"`
- Footer sticky opcional com `border-t border-border`

### Novo: `packages/platform-ui/src/components/ui/Tabs.tsx` (Task #109)

Tab bar horizontal acessível. Substitui implementações independentes em AgentAssistPage (5 tabs), ContactsPage, AuditPage, EvaluationForms e outros.
- Props: `tabs: TabItem[]`, `activeTab`, `onChange`, `variant` (underline | pill), `panels?`, `aria-label?`
- `TabItem`: `key`, `label`, `count?` (badge numérico), `disabled?`
- ARIA completo: `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected`, `aria-controls`
- Navegação por teclado: ArrowLeft / ArrowRight / Home / End — foco DOM sincronizado
- `tabIndex={0}` apenas na tab ativa (roving tabindex pattern)
- Variante `underline`: underline animado com `::after`, `text-primary` na ativa
- Variante `pill`: background `bg-surface` com sombra `shadow-card` na ativa
- Badge de contagem (`count`) com estilo contextual (ativo vs inativo)
- Exporta tipo `TabItem`

### Novo: `packages/platform-ui/src/components/ui/Textarea.tsx` (Task #110)

Primitive de formulário para texto multi-linha. Espelha API do Input.
- Props: `label?`, `error?`, `hint?`, `maxLength?`, `showCount?` + todos os attrs nativos
- `id` automático via `useId()` para associação label↔input sem props extra
- Character counter com `aria-live="polite"` e `aria-atomic`; vira `text-warning font-semibold` nos últimos 10%
- `aria-invalid` e `aria-describedby` para erro/hint
- `resize-y` por padrão; `rows` padrão 3
- `role="alert"` no error
- Border `border-red bg-red-light/20` no estado de erro

### Novo: `packages/platform-ui/src/components/ui/Checkbox.tsx` (Task #110)

Input de seleção acessível. Substitui `<input type="checkbox">` com styling ad-hoc em AccessPage, ModulePermissionForm, FormsPage e outros.
- Props: `label?`, `error?`, `indeterminate?` + todos os attrs nativos (exceto `type`)
- `indeterminate` aplicado imperativamente via ref callback (não suportado por HTML attribute)
- `focus-visible:ring-2` com offset
- `group-has-[:disabled]` para opacidade do label quando input está disabled
- `aria-invalid` e `aria-describedby` para erro

### Novo: `packages/platform-ui/src/components/ui/Switch.tsx` (Task #110)

Toggle acessível com label e description. Substitui implementações ad-hoc em config pages.
- Props: `checked`, `onChange`, `label?`, `description?`, `disabled?`
- `role="switch"`, `aria-checked`, `aria-describedby`
- Transição `translate-x-5` / `translate-x-0` via CSS transition
- `focus-visible:ring-2` com `ring-offset-2`
- `htmlFor` no label apontando para o botão via id

### Atualizado: `packages/platform-ui/src/components/ui/index.ts`

Barrel agora exporta 17 componentes + 2 tipos (`BadgeVariant`, `TabItem`). Todos os novos componentes incluídos: `Checkbox`, `Drawer`, `Switch`, `Tabs`, `Textarea`.

---

## Design System — Sprint 1: Token Hygiene + Component Foundation (2026-05-18)

Primeira fase do trabalho de design system identificado no audit (score 30/100).
Ver auditoria completa em `docs/arcos/design-system-audit.md`.

### `packages/platform-ui/tailwind.config.ts` — Extensão do sistema de tokens (Task #104)

- Adicionados **tokens de hover**: `primary-dark` (#163F70), `secondary-dark` (#2484BE)
- Adicionado **token de superfície principal**: `surface-muted` (#F9FAFB) — substitui `bg-gray-50` disperso
- Adicionados **tokens de superfície**: `surface`, `surface-alt`, `border`, `border-strong`
- Adicionados **aliases sem camelCase**: `light-gray` (alias de `lightGray`), `muted` (alias de `gray`), `muted-light`, `border`
- Adicionadas **variantes de escala semântica**:
  - `green-light` / `green-text` — backgrounds e texto success
  - `warning-light` / `warning-text` — backgrounds e texto warning
  - `red-light` / `red-text` — backgrounds e texto error
  - `info` / `info-light` / `info-text` — cor info (mapeada ao secondary)
  - `primary-light` — tint azul para seleções e badges
- Adicionados **tokens de domínio** (previamente off-palette):
  - `contested` / `contested-light` / `contested-text` — laranja, estado de contestação
  - `revised` / `revised-light` / `revised-text` — teal, decisão de revisão com ajuste
  - `ai` / `ai-light` / `ai-text` — indigo, indicadores de agente AI
- Adicionada **escala de z-index**: `dropdown(10)`, `sticky(20)`, `overlay(30)`, `modal(40)`, `toast(50)`, `tooltip(60)`
- Adicionada **escala de sombras**: `card`, `panel`, `modal`, `toast`

### `packages/platform-ui/src/components/ui/Button.tsx` — Hover tokens + loading state (Task #105)

- Substituídos `hover:bg-blue-900` / `hover:bg-blue-500` / `hover:bg-gray-200` por tokens: `hover:bg-primary-dark`, `hover:bg-secondary-dark`, `hover:bg-surface-alt`
- Adicionado estado `loading` com spinner inline e `aria-busy`
- Adicionados slots `leftIcon` / `rightIcon` com `aria-hidden`
- `disabled` agora herda de `loading` (ambos bloqueiam interação)
- `opacity-60` no lugar de `disabled:bg-gray-400` para não sobrescrever a cor do variant
- `inline-flex items-center` adicionado para correta exibição de ícones

### `packages/platform-ui/src/components/ui/Badge.tsx` — Cobertura de domínio (Task #106)

- Exportado tipo `BadgeVariant` — 18 variantes total (era 4)
- **Novas variantes genéricas**: `pending`, `processing`, `completed`, `cancelled`, `success`, `warning`, `error`, `info`
- **Novas variantes de avaliação**: `approved`, `contested`, `rejected`, `revised`
- **Novas variantes de tipo de agente**: `ai`, `human`
- Prop `dot?: boolean` — prepend indicador colorido antes do label
- Todas as variantes usam tokens semânticos (`bg-green-light`, `text-green-text`, etc.)

### `packages/platform-ui/src/shell/Shell.tsx` + `Modal.tsx` (Task #107)

- `Shell.tsx`: `bg-gray-50` → `bg-surface-muted` (único token no lugar de classe Tailwind direta)
- `Modal.tsx` reescrito:
  - Prop `size?: 'sm' | 'md' | 'lg' | 'xl'` (antes fixo em `max-w-md`)
  - Prop `loading?: boolean` — overlay de loading sobre o body
  - Prop `disableBackdropClose?: boolean`
  - Escape key handler via `useEffect` + `keydown` listener
  - Body scroll lock (`overflow: hidden`) via `useEffect`
  - Focus management: `dialogRef.focus()` no open via `requestAnimationFrame`
  - `role="dialog"`, `aria-modal="true"`, `aria-labelledby="modal-title"` no container
  - Close button com `aria-label="Fechar"` e `aria-hidden="true"` no SVG
  - Backdrop com `aria-hidden="true"`
  - `z-modal` usando token de z-index em vez de `z-50` hardcoded
  - Sombra `shadow-modal` em vez de `shadow-xl`
  - Bordas usando token `border-border` em vez de `border-lightGray`

### `packages/platform-ui/src/components/ui/PageHeader.tsx`

- `<a href>` → `<Link to>` do react-router-dom (evita reload de página)
- `<nav>` com `aria-label="Breadcrumb"`
- `aria-current="page"` no crumb ativo
- `aria-hidden="true"` no separador `/`
- `text-gray` → `text-muted` (token)

### Novo: `packages/platform-ui/src/components/ui/Alert.tsx`

Componente compartilhado de feedback inline — substitui o padrão `div bg-red-50 border border-red-100` presente em 15+ arquivos.
- 4 variantes: `info`, `success`, `warning`, `error`
- Props: `title?`, `onDismiss?` (botão X), `className`
- `role="alert"` para leitores de tela
- Ícones SVG semânticos por variante
- Usa exclusivamente tokens semânticos do design system

### Novo: `packages/platform-ui/src/components/ui/index.ts`

Barrel de exports para `@/components/ui` — 12 componentes exportados com nomes explícitos (sem `export *`). Inclui export de tipo `BadgeVariant`.

---

## Security & Bug Fixes — Code Review Round 2 (2026-05-18)

Correções críticas identificadas em code review completo do projeto.

### `packages/mcp-server-plughub/src/server.ts` — JWT auth em endpoints UI

- **Task #98**: Adicionado `requireJwtRole()` com verificação de assinatura JWT via `jsonwebtoken.verify()` em todos os endpoints de UI:
  - `GET /api/supervisor_state/:sessionId` → roles: operator, supervisor, admin, developer
  - `POST /api/inject-context/:sessionId` → roles: operator, supervisor, admin, developer
  - `POST /api/force-complete/:sessionId` → roles: supervisor, admin (mais restritivo)
  - `PUT /api/agent-pause` → roles: operator, supervisor, admin
  - `PUT /api/agent-resume` → roles: operator, supervisor, admin
- `extractJwtRole()` (decode sem verificação de assinatura) removido de `supervisor_state` — substituído por `payload["role"]` do JWT verificado.

### `packages/evaluation-api` — Arc 13 bugs

**Task #99** — `contestation_state` errado na finalização de agente AI:
- `router.py`: path `ai_agent` (Fluxo 2) usava `contestation_state="contestation_open"` em `finalize_result` e `emit_evaluation_finalized`. Corrigido para `"auto_finalized"`. Removido campo `initial_state` que nunca era usado.

**Task #100** — `calibration_reviewed` não emitido para decisão `"approved"`:
- `contestation_router.py` `resolve_curation`: `emit_calibration_reviewed` estava dentro de `if calibration_note:` — que é `None` para `approved`. Refatorado para buscar `campaign_id` e `evaluation_instance_id` do DB incondicionalmente; `emit_calibration_reviewed` emitido para todos os status (`approved`, `recalibrated`, `bias_flagged`). Calibration dashboard agora registra aprovações corretamente.

**Task #101** — `max_rounds` nunca verificado, `current_round` nunca incrementado:
- `db.py` `set_contestation_state`: adicionado parâmetro `current_round: int | None = None` com UPDATE condicional.
- `contestation_router.py` `file_contestation`: chama `set_contestation_state` com `current_round=current_round` para persistir o round após cada contestação.
- `contestation_router.py` `submit_review`: busca `contestation_policy.max_rounds` da campanha; verifica `cycles_completed = (review_round - 1) // 2 >= max_rounds`; se excedido → `next_state = "closed_max_rounds"` em vez de `"contestation_open"`; persiste `current_round=review_round`. Loops infinitos de contestação não são mais possíveis.

### `packages/orchestrator-bridge` — Arc 14 Fase C wrap_up_pending

**Task #102** — `wrap_up_pending` nunca bloqueava routing-engine:
- `fire_pool_hooks`: key de `wrap_up_pending` agora usa `f"human-{pool_id}"` diretamente (pattern deterministico idêntico ao que routing-engine usa para indexar pool instances), em vez de `_fixed_pid` do ContextStore (que podia ser `None` se `_write_pre_hook_context` tivesse falhado).
- Formato de `hook_conf` estendido de `"{hook_type}:{target_pool}:{side}"` para `"{hook_type}:{target_pool}:{side}:{origin_pool}"`. Parser atualizado para `split(":", 3)`.
- Cleanup em `process_routed`: usa `_hook_origin_pool` (4° campo do `hook_conf`) para derivar `human-{origin_pool}` — não depende mais de ContextStore. Condição adicionada: `completed_hook_type == "on_human_end"` antes de limpar o flag.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase H: Feedback Loop RAG / Curation Module (2026-05-18)

Fecha o loop completo de melhoria contínua do avaliador AI: `sampling_engine.py` faz curadoria amostral pós-`evaluation_finalized` para Fluxo 2 (AI avaliado), `CuradoriaPage` expõe a fila de curadoria ao curador humano, e `CalibrationNote` é publicada no knowledge namespace do `mcp-server-knowledge` para fechar o RAG feedback loop ao `agente_avaliacao_v1`.

### `packages/evaluation-api` — sampling engine

**`sampling_engine.py`** (novo):
- `run_curation_sampling(pool, *, instance_id, tenant_id, campaign_id, normalized_score)` — entry point async chamado em background task após `evaluation_finalized` para Fluxo 2.
- Helpers inline sem circular import: `_get_campaign_score_stats`, `_count_finalized_instances`, `_count_na_criteria`.
- Avalia 5 regras (pula `reviewer_signal` explicitamente para Fluxo 2): `score_extremes`, `random_baseline` (hash determinístico do `instance_id`), `deploy_baseline`, `score_outlier` (min 5 amostras), `na_excess`.
- Trigger composto: regras ativas concatenadas como string, ex. `"score_extremes,random_baseline"`.
- Cria um único `CurationReview` por instância (insert-or-skip se já existe).

**`router.py`** — após `emit_evaluation_finalized` no path `ai_agent`:
- Importou `asyncio` e `run_curation_sampling`.
- `asyncio.create_task(run_curation_sampling(...), name=f"curation-sampling-{body.instance_id}")` — não bloqueia resposta HTTP.

**`config.py`**: campo `knowledge_api_url: str = "http://localhost:3401"` adicionado.

### `packages/evaluation-api` — contestation_router (CalibrationNote → KB)

**`contestation_router.py`** — `resolve_curation` endpoint:
- Após criar/resolver `CurationReview`, se `status == "recalibrated"` ou `status == "bias_flagged"` com `calibration_note_text`:
  - `httpx.AsyncClient.post(knowledge_api_url + "/v1/knowledge/snippets")` com namespace `evaluation:calibration:{campaign_id}`, conteúdo + metadados de dimensão/severidade.
  - On success (200/201): `mark_calibration_note_published(note_id, kb_source_ref)` + emite `calibration_note_published` ao `evaluation.events`.
  - Erros de I/O capturados — `kb_published=False` retornado sem falhar a ação do curador.
- Emite `calibration_reviewed` ao topic `calibration.events` em todos os casos.
- Retorna `{ ..., "kb_published": bool }`.

**`db.py`** — `list_curation_reviews` reescrita:
- Sempre faz JOIN com `evaluation.instances` para retornar `campaign_id`.
- Correlated subquery retorna o `calibration_signal` mais recente do `pre_reviewer_ai` nos threads de contestação.
- Parse defensivo: converte `calibration_signal` de JSON string para dict quando necessário (asyncpg JSONB variante).

### `packages/platform-ui` — hooks + CuradoriaPage

**`evaluation-hooks.ts`** — novos exports:
- Interfaces: `CurationReview`, `CurationResolvePayload`.
- `useCurationQueue(tenantId, opts, pollMs)` — fetch `GET /evaluation/curation-reviews`, polling configurável (padrão 15s), retorna `{ reviews, total, loading, error, reload }`.
- `resolveCuration(reviewId, tenantId, userId, payload)` — `POST /evaluation/curation-reviews/{id}/resolve`.

**`CuradoriaPage.tsx`** (novo — `/evaluation/curadoria`):
- KPI strip: Aguardando, Total, label do filtro ativo.
- Filtros: status (pending/approved/recalibrated/bias_flagged), campanha.
- `CurationCard`: trigger badges coloridos, preview do `calibration_signal` do revisor AI, 3 botões (Aprovar / Recalibrar / Viés).
- `RecalibrateDrawer`: pré-preenche observação do sinal AI; campos `noteText`, `curatorNotes`, `dimensionId`, `evaluatorId`, `skillVersion`, `severity`. Submit → `resolveCuration` com status `recalibrated` ou `bias_flagged`.
- Polling 15s.

**`routes.tsx`**: `{ path: 'evaluation/curadoria', element: <CuradoriaPage /> }`.

**`Sidebar.tsx`**: nav item `{ label: t('nav.eval.curadoria'), href: '/evaluation/curadoria', icon: '🔍', roles: ['supervisor', 'admin'] }`.

**i18n** (`shell.json` en + pt-BR): chave `nav.eval.curadoria` → `"Curation"` / `"Curadoria"`.

→ Ver [`docs/arcos/arc13-review-contestation.md`](docs/arcos/arc13-review-contestation.md)

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase G: Calibration Dashboard (2026-05-18)

Implementa o pipeline completo de dados e a tela de Calibration Dashboard: Kafka consumer para `calibration.events`, ClickHouse DDL, endpoint `GET /reports/evaluator-calibration` na analytics-api, hook `useEvaluatorCalibration` e página `CalibrationDashboard` na platform-ui.

### `packages/analytics-api` — ClickHouse DDL + consumer

**`clickhouse.py`**:
- `_DDL_CALIBRATION_EVENTS` — nova tabela `calibration_events` (ReplacingMergeTree por `(tenant_id, event_id)`): `event_id`, `tenant_id`, `campaign_id`, `evaluator_id`, `skill_version`, `decision` (LowCardinality: approved/recalibrated/bias_flagged), `dimension_id`, `severity`, `curator_id?`, `note_id?`, `event_time`, `date`.
- `_calibration_event_row(d)` — row builder para inserção.
- `AnalyticsStore.insert_calibration_event()` + `_CALIBRATION_EVENT_COLS`.
- Adicionada à lista `_ALL_DDL`.

**`models.py`**:
- `parse_calibration_event(payload)` — parser do topic `calibration.events`. Aceita `calibration_reviewed` (retorna row para ClickHouse); ignora `calibration_note_published` (informacional).

**`consumer.py`**:
- Topic `calibration.events` adicionado a `_TOPICS` e `_PARSERS`.
- `_write_row`: routing `calibration_events` → `store.insert_calibration_event`.
- Docstring atualizado.

### `packages/analytics-api` — endpoint

**`reports_query.py`** — `query_evaluator_calibration` (async wrapper + `_fetch_evaluator_calibration` sync):
- Parâmetros: `campaign_id`, `evaluator_id`, `skill_version`, `granularity` (day/week).
- Query time-series: `calibration_score = countIf(decision='approved') * 100 / count()` por `(period, skill_version, evaluator_id)`.
- Query summary: agregação total do período.
- Retorna: `{ data, summary, meta }`.

**`reports.py`** — `GET /reports/evaluator-calibration`:
- Query params: `tenant_id`, `from_dt`, `to_dt`, `campaign_id`, `evaluator_id`, `skill_version`, `granularity`.
- Delega a `query_evaluator_calibration`.

### `packages/platform-ui` — hook + página

**`evaluation-hooks.ts`** — `useEvaluatorCalibration`:
- Interfaces exportadas: `CalibrationPoint`, `CalibrationSummary`, `CalibrationResult`.
- Hook com `fetch` a `${ANALYTICS_BASE}/evaluator-calibration`, estado `{ data, summary, meta, loading, error, reload }`, polling opcional.

**`CalibrationDashboard.tsx`** (novo — `/evaluation/calibration`):
- Filtros: campanha (select), evaluator_id (input), granularidade (day/week).
- KPI strip: Calibration Score geral, Aprovadas %, Recalibradas %, Viés %.
- LineChart (Recharts): `calibration_score (%)` × tempo, uma série por `skill_version`, paleta rotativa de 7 cores. ReferenceLine em 90% (verde tracejada).
- Tabela de dados brutos com score colorido (verde ≥ 90%, amarelo ≥ 75%, vermelho abaixo).
- Estado vazio, loading e erro tratados.

**`routes.tsx`**: rota `evaluation/calibration` → `<CalibrationDashboard />`.

**`Sidebar.tsx`**: nav item "Calibração" (📐) em `/evaluation/calibration`, roles `supervisor|admin`.

**i18n** `shell.json` (en + pt-BR): chave `nav.eval.calibration`.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase F: Campaign Config UI + Curation Sampling Rules (2026-05-18)

Implementa a interface de configuração de campanha para Arc 13 na `CampaignsPage` da platform-ui: novos campos na `ContestationPolicy`, editor de regras de curadoria amostral (`CurationSamplingRule`) e painel de detalhe de campanha atualizado.

### `packages/platform-ui/src/types/index.ts`

Novos campos opcionais em `ContestationPolicy` (Arc 13):
- `reviewer_type?: 'ai' | 'human' | 'ai_then_human'` — roteamento de resolução de contestação.
- `contest_deadline_hours?: number` — prazo em horas para o avaliado contestar.
- `use_business_hours?: boolean` — usa horário comercial (calendar-api) nos deadlines.
- `pre_review_enabled?: boolean` — habilita gate de qualidade por AI pré-publicação.
- `pre_review_agent_pool?: string | null` — pool do agente pré-revisor.

Novos tipos de regras de curadoria amostral:
- `CurationRuleType` — union `'score_extremes' | 'deploy_baseline' | 'score_outlier' | 'na_excess' | 'random_baseline' | 'reviewer_signal'`.
- `CurationRuleParams` — campos opcionais: `threshold_low/high`, `sample_pct`, `sample_n`, `std_devs`, `na_threshold_pct`, `rate`.
- `CurationSamplingRule` — entidade completa com `rule_id?`, `campaign_id`, `rule_type`, `enabled`, `priority`, `params`.

### `packages/platform-ui/src/api/evaluation-hooks.ts`

Novos hooks Arc 13 Fase F:
- `useCurationSamplingRules(campaignId)` — `GET /v1/evaluation/campaigns/{id}/sampling-rules`; retorna `{ rules, loading, error, reload }`.
- `saveCurationSamplingRules(campaignId, rules, token?)` — `PUT /v1/evaluation/campaigns/{id}/sampling-rules`; retorna a lista salva.

### `packages/platform-ui/src/modules/evaluation/CampaignsPage.tsx`

**Constantes novas:**
- `REVIEWER_TYPE_OPTIONS` — 3 opções: `human`, `ai`, `ai_then_human` com labels descritivos.
- `DEFAULT_CURATION_RULES` — 6 regras pré-configuradas (score_extremes e reviewer_signal habilitadas por padrão; score_outlier e deploy_baseline habilitadas; na_excess e random_baseline desabilitadas).

**Novos componentes:**
- `CurationRuleRow` — editor de uma regra: toggle enabled, campo priority, inputs condicionais por `rule_type` (threshold_low/high para score_extremes, sample_n para deploy_baseline, std_devs+sample_pct para score_outlier, na_threshold_pct para na_excess, rate para random_baseline — reviewer_signal sem params).
- `CurationSamplingRulesEditor` — lista de `CurationRuleRow` em ordem de priority com drag-free reordering via flechas.
- `CurationSamplingRulesDetailPanel` — painel de detalhe de campanha: modo leitura (bullet list por regra com resumo de params) + modo edição (chama `saveCurationSamplingRules`). Exibido apenas para campanhas com `review_workflow_skill_id === 'skill_revisao_treplica_v1'`.

**`CreateModal` estendido (Arc 13 fields):**
- Novos campos de estado: `contestDeadlineHours` (default '72'), `reviewerType` ('ai_then_human'), `useBusinessHours`, `preReviewEnabled`, `preReviewPool`, `curationRules` (DEFAULT_CURATION_RULES), `showCurationRules`.
- `isArc13Skill = workflowSkillId === 'skill_revisao_treplica_v1'` — gatea seções Arc 13 no modal.
- Quando `isArc13Skill`: exibe select `reviewer_type`, campos de deadline com toggle `use_business_hours`, toggle `pre_review_enabled` + input do pool, acordeão "Regras de curadoria amostral" com `CurationSamplingRulesEditor`.
- `submit()`: passa novos campos em `contestation_policy`; após criar campanha, chama `saveCurationSamplingRules` quando `isArc13Skill && curationRules.length > 0`.

**Painel de detalhe atualizado:**
- Card de contestation policy exibe Arc 13 fields como grid de badges coloridos: reviewer_type (azul), pre_review (verde/cinza), use_business_hours (teal), auto_lock (âmbar).
- `<CurationSamplingRulesDetailPanel>` renderizado abaixo do card de política para campanhas Arc 13.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase E: Human Review UX (2026-05-18)

Implementa a interface de revisão humana com threads por dimensão na `AvaliacoesPage` da platform-ui. Suporte completo a Arc 13 (dimension threads) com fallback transparente para Arc 6 (criterion list).

### `packages/platform-ui/src/types/index.ts`

Novos tipos Arc 13:
- `DimensionState` — union type dos estados visuais por dimensão (`neutral|pre_reviewed|contested|upheld|revised|timeout`).
- `EvidenceEntry` — evidência individual em um thread entry (`stream_entry_id`, `excerpt`, `relevance_note`).
- `ContestationThreadEntry` — entrada append-only de um thread: `round`, `author_role`, `action?`, `score`, `justification`, `evidence_entries[]`, `submitted_at`.
- `ContestationThread` — thread completo de uma dimensão: `dimension_id`, `dimension_label?`, `current_state`, `original_score`, `current_score`, `entries[]`.
- `InstanceThreads` — resposta de `GET /v1/evaluation/instances/{id}/threads`.
- `HumanDimensionDecision` — payload de decisão por dimensão para o revisor humano.
- `HumanReviewResponse` — resposta de `POST /v1/evaluation/instances/{id}/review`.
- `DimensionContestationPayload` / `DimensionContestationResponse` — payload/resposta de `POST /v1/evaluation/instances/{id}/contest`.

### `packages/platform-ui/src/api/evaluation-hooks.ts`

Novos hooks e funções Arc 13:
- `fetchContestationThreads(instanceId, accessToken?)` — `GET /v1/evaluation/instances/{id}/threads`, normaliza resposta.
- `useContestationThreads(instanceId, accessToken?, pollMs?)` — React hook com polling opcional; retorna `{ data, loading, error, reload }`.
- `submitHumanReview(instanceId, body, jwtToken)` — `POST /v1/evaluation/instances/{id}/review` para o revisor humano.
- `submitDimensionContestation(instanceId, body, jwtToken)` — `POST /v1/evaluation/instances/{id}/contest` para o agente avaliado.

### `packages/platform-ui/src/modules/evaluation/AvaliacoesPage.tsx`

**Novos componentes Arc 13:**
- `DimensionStateIndicator` — dot colorido + label por estado (`DIM_STATE_META` map).
- `DimensionThreadCard` — card expansível por dimensão: header com estado/score, entradas por round com `ROUND_ROLE_LABELS`, evidências formatadas. Expandido por padrão quando `contested` ou `revised`.
- `HumanReviewPanel` — revisor humano decide por dimensão contestada: upheld/revised radio, `score_override` (apenas revised, 0–10), `justification` (≥ 20 palavras, contador em tempo real). Chama `submitHumanReview`.
- `DimensionContestPanel13` — agente avaliado contesta por dimensão (apenas `neutral` ou `pre_reviewed`): checkbox por dimensão, justification (≥ 10 palavras). Chama `submitDimensionContestation` com anti-replay `round`.

**`DetailPanel` atualizado:**
- Chama `useContestationThreads(result.instance_id)` em cada abertura.
- Detecta `isArc13 = threads.length > 0` — ativo automaticamente quando a instância tem threads.
- Modo Arc 13: exibe `DimensionThreadCard` list + `HumanReviewPanel` (review) ou `DimensionContestPanel13` (contest).
- Modo Arc 6 (fallback): mantém `CriterionRow` list + `ReviewPanel`/`ContestPanel` sem alteração.
- Badge "Arc 13" no header quando threads disponíveis.

**Invariante de compatibilidade**: instâncias criadas antes do Arc 13 (sem threads) continuam funcionando exatamente como antes — sem alteração de comportamento.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase D: Revisor Pós-Contestação + Workflow (2026-05-18)

Implementa o árbitro AI pós-contestação (`agente_revisor_v1`) e o motor de estado de revisão (`skill_revisao_treplica_v1` v2.0) que roteia para AI ou humano com base em `reviewer_type` da campanha.

### `mcp-server-plughub` — `src/tools/evaluation.ts`

**`evaluation_review_submit`** (novo):
- Input: `session_token`, `instance_id`, `dimension_decisions[]`, `reviewer_id?`.
- `DimensionDecisionSchema`: `dimension_id`, `decision` ("upheld"|"revised"), `score_override?` (obrigatório se revised), `evidence_entries[]?` (obrigatório se revised), `justification` (obrigatório).
- Chama `POST /v1/evaluation/instances/{id}/review` na evaluation-api.
- Retorna: `dimensions_upheld`, `dimensions_revised`, `contestation_state`, `current_round`, `finalized`.

### `skill-flow-engine` — `skills/agente_revisor_v1.yaml` (novo — v1.0)

Árbitro AI pós-contestação. Fluxo de 5 steps: `get_context` → `get_threads` → `filter_contested` → `review` (reason, prompt `post_contestation_rubric_v1`) → `submit_review`.

- Só arbitra dimensões com `round=2` (contestadas pelo humano avaliado). Não toca dimensões não contestadas.
- `output_schema.dimension_decisions[]`: `decision` (upheld|revised) + `score_override?` + `evidence_entries[]?` + `justification`.
- `decision=revised` obriga `score_override` diferente do original + `evidence_entries` (mínimo 1).
- `decision=upheld` requer apenas `justification` explicando por que a contestação não é procedente.
- Lê `calibration_notes` antes de decidir (RAG de calibração).
- Não emite `calibration_signal` — o revisor árbitro não é calibrador.
- `complete_skip` quando sem dimensões contestadas (edge case).

### `skill-flow-engine` — `skills/skill_revisao_treplica_v1.yaml` (v1.0 → v2.0)

Motor de estado do ciclo de revisão. Atualizado com roteamento Arc 13.

**Novo fluxo v2.0** (ciclo por round):
1. `aguardar_contestacao` (suspend, `contest_deadline_hours`) — aguarda o agente humano contestar; timeout → `congelar_resultado`.
2. `verificar_contestacao` (choice) — se `review_decision="contested"` → incrementa round; se não → `encerrar_aprovado`.
3. `verificar_limite_rounds` (choice) — `current_round > max_rounds` → `congelar_resultado`.
4. `rotear_revisor` (choice) — `reviewer_type="ai"` ou `"ai_then_human"` → `dispatch_revisor_ai`; default → `aguardar_revisao_humana`.
5. `dispatch_revisor_ai` (task, `skill_revisao_v1`) — A2A para `agente_revisor_v1`; `on_failure` → `fallback_para_humano`.
6. `fallback_para_humano` (choice) — só suspende para humano se `reviewer_type="ai_then_human"`; caso contrário `congelar_resultado`.
7. `aguardar_revisao_humana` (suspend, `review_deadline_hours`) — deadline para revisor humano; timeout → `congelar_resultado`.
8. `aguardar_proxima_contestacao` (suspend) — resultado publicado; aguarda próxima contestação do avaliado; timeout → `congelar_resultado`; recomeça o ciclo.

Contexto esperado no ContextStore (escrito pela evaluation-api antes do trigger): `instance_id`, `result_id`, `reviewer_type`, `max_rounds`, `contest_deadline_hours`, `review_deadline_hours`, `use_business_hours`, `current_round` (init=0).

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase C: Revisor AI Pré-Publicação (2026-05-18)

Implementa o `agente_pre_revisor_v1` — gate de qualidade que atua antes do resultado ser publicado ao agente humano avaliado. Verifica evidências, calibração de notas e emite sinais de calibração para padrões sistemáticos do avaliador.

### `mcp-server-plughub` — `src/tools/evaluation.ts`

**`evaluation_threads_get`** (novo):
- Input: `session_token`, `instance_id`.
- Chama `GET /v1/evaluation/instances/{id}/threads` na evaluation-api.
- Retorna `threads[]` — ContestationThreads round=1 do avaliador por dimensão.
- Usado pelo `agente_pre_revisor_v1` para ler o que o avaliador produziu antes de revisar.

**`evaluation_pre_review_submit`** (novo):
- Input: `session_token`, `instance_id`, `dimension_reviews[]`, `calibration_signal?`.
- `DimensionReviewSchema`: `dimension_id`, `action` ("approve"|"adjust"), `score_override?`, `revised_evidence[]?`, `justification` (obrigatório).
- `CalibrationSignalPreReviewSchema`: `severity` ("low"|"medium"|"high"), `dimension_id`, `observation`.
- Chama `POST /v1/evaluation/instances/{id}/pre-review` na evaluation-api.
- `calibration_signal` presente → `curation_review_created: true` na resposta (assíncrono).
- Retorna: `dimensions_adjusted`, `dimensions_approved`, `contestation_state`, `curation_review_created`.

### `skill-flow-engine` — `skills/agente_pre_revisor_v1.yaml` (novo — v1.0)

Fluxo de 5 steps: `get_context` → `get_threads` → `check_has_threads` → `review` (reason) → `submit_pre_review`.

- `get_context`: `evaluation_context_get` — ReplayContext + form + calibration_notes (RAG).
- `get_threads`: `evaluation_threads_get` — ContestationThreads round=1 do avaliador.
- `check_has_threads`: choice — se sem threads, avança para `complete_skip` (avaliação legacy sem dimension_threads).
- `review` (`pre_review_rubric_v1`): LLM revisa cada dimensão:
  - Lê `evaluator_threads`, `replay_events`, `evaluation_form`, `knowledge_snippets`, `calibration_notes`.
  - `output_schema.dimension_reviews[]`: `action` + `score_override?` + `revised_evidence[]?` + `justification`.
  - `output_schema.calibration_signal?`: emitido apenas para padrões sistemáticos do avaliador (não discordância pontual).
  - `action=adjust` obriga `score_override` + `revised_evidence[]` (mínimo 1 entry).
- `submit_pre_review`: `evaluation_pre_review_submit` — persiste threads + avança estado da instância.

**Invariantes da skill:**
- Nunca abre contestação — é gate pré-publicação, não árbitro.
- `calibration_signal` emitido apenas para padrões sistemáticos (severidade low/medium/high).
- `complete_skip` quando threads vazios — não bloqueia o fluxo para avaliações sem dimension_threads.

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase B: Session Metrics + Evidence Threads (2026-05-18)

Completa o pipeline de avaliação com extração automática de métricas de sessão, threads de evidência estruturadas por dimensão, e integração do agente avaliador com notas de calibração do curador.

### `evaluation-api`

**`db.py`**:
- `evaluation.instances`: novo campo `session_metrics JSONB` — armazena métricas extraídas pelo `SessionMetricsExtractor` (tempo, mensagens, escalação, custo LLM).
- `set_instance_session_metrics()`: nova função CRUD para persistir métricas após `session_closed`.

**`session_metrics_extractor.py`** (novo):
- `SessionMetricsExtractor`: computa `session_metric.*` para uma `EvaluationInstance` após `session_closed`.
  - `_compute_time_metrics`: `first_response_time_s`, `total_session_duration_s` — lê `stream_events` no PostgreSQL.
  - `_compute_message_metrics`: `total_messages`, `agent_messages`, `customer_messages`, percentuais, `avg_agent_message_length`, `turns_to_resolution`.
  - `_compute_outcome_metrics`: `escalated` (bool), `escalation_reason` — lê evento `agent_done` com `handoff_reason`.
  - `_compute_llm_metrics`: `llm_calls_total`, `tokens_input_total`, `tokens_output_total` — lê `usage_events`.
  - Todas as métricas são best-effort: falha individual não aborta as demais.
- `compute_auto_criterion_score()`: interpolação linear entre `threshold_pass` e `threshold_fail` para critérios `auto_computed`.
- `fill_auto_computed_criteria()`: preenche `criterion_responses` com scores auto-calculados antes do submit, eliminando a necessidade de o LLM avaliar esses critérios.

**`router.py`**:
- `IngestBody`: novos campos `dimension_threads: list[dict]` e `evaluated_agent_type: str`.
- `ingest_result()`:
  - Cria `ContestationThread` round=1 para cada dimensão em `dimension_threads` (com `evidence_entries[]`).
  - Fluxo 2 (ai_agent): chama `finalize_result()` + emite `evaluation_finalized` imediatamente.
  - Fluxo 1 (human_agent): verifica `pre_review_enabled` → define estado `pre_review_pending` ou `contestation_open`.
  - Retorna `contestation_threads_created`, `contestation_state`, `evaluated_agent_type`.

### `mcp-server-plughub`

**`src/tools/evaluation.ts`**:
- `EvidenceEntryInputSchema` (novo): `stream_entry_id`, `excerpt`, `relevance_note`.
- `DimensionThreadInputSchema` (novo): `dimension_id`, `score`, `justification`, `evidence_entries[]` (mínimo 1).
- `EvaluationSubmitInputSchema`: novos campos `dimension_threads[]` e `evaluated_agent_type`.
- Handler `evaluation_submit`: repassa `dimension_threads` e `evaluated_agent_type` no payload enviado ao ingest.
- Handler `evaluation_context_get`: busca `CalibrationNotes` publicadas da evaluation-api (`/v1/evaluation/calibration-notes?published_to_kb=true`) e retorna em `calibration_notes[]` — consumidas pelo agente avaliador via RAG.

### `skill-flow-engine`

**`skills/agente_avaliacao_v1.yaml`** (v2.0 → v3.0):
- `evaluate` step (`evaluation_rubric_v2` → `evaluation_rubric_v3`):
  - Nova instrução: ignorar critérios com `type=auto_computed` (preenchidos pelo `SessionMetricsExtractor`).
  - Novo input: `calibration_notes` do `eval_context` — guia o LLM com feedback anterior do curador.
  - Novo campo `output_schema.dimension_threads[]`: thread por dimensão com `dimension_id`, `score`, `justification` e `evidence_entries[]` (stream_entry_id + excerpt + relevance_note, mínimo 1 por dimensão).
- `submit_result` step: passa `dimension_threads` e `evaluated_agent_type` para `evaluation_submit`.
- Comentários atualizados para refletir dois fluxos (ai_agent → finalização imediata; human_agent → contestação).

---

## Arc 13 — Evaluation Review, Contestation & Calibration — Fase A: Data Model (2026-05-18)

Implementa o modelo de dados e endpoints base para o ciclo completo de qualidade: contestação estruturada por dimensão para agentes humanos, curadoria amostral para agentes AI, e infraestrutura de calibração contínua.

### `@plughub/schemas` — `src/evaluation.ts`

- `EvaluationCriterionTypeSchema`: novo valor `"auto_computed"` — critérios computados automaticamente de `session_metric.*` sem LLM.
- `EvaluationCriterionSchema`: novos campos `dimension_label`, `computation_source`, `threshold_pass`, `threshold_fail`, `comparison` (suporte ao tipo `auto_computed`).
- `ContestationPolicySchema` (novo): `max_rounds` (padrão 3, máx 5), `contest_deadline_hours`, `use_business_hours`, `reviewer_type` ("ai"|"human"|"ai_then_human"), `pre_review_enabled`, `pre_review_agent_pool`, `review_deadline_hours`.
- `ContestationStateSchema` (novo): state machine de contestação — 7 estados: `pre_review_pending`, `contestation_open`, `under_review`, `timeout_contestation`, `timeout_review`, `closed_upheld`, `closed_revised`.
- `EvidenceEntrySchema` (novo): `stream_entry_id`, `excerpt`, `relevance_note` — formato Arc 13, diferente do `EvidenceRefSchema` Arc 6.
- `CalibrationSignalSchema` (novo): `severity`, `dimension_id`, `observation`, `evaluator_id`, `skill_version`.
- `ContestationThreadSchema` (novo): registro append-only por dimensão com `round`, `author_type`, `decision`, `score_override`, `evidence_entries`, `calibration_signal`.
- `CurationReviewSchema` + `CurationReviewStatusSchema` (novos): fila de curadoria com `trigger`, `curator_id`, `status`, `calibration_note_id`.
- `CalibrationNoteSchema` (novo): nota de calibração do avaliador com `published_to_kb`.
- `CurationSamplingRuleSchema` + `CurationSamplingRuleTypeSchema` (novos): 6 regras configuráveis por campanha.
- Novos Kafka events: `EvalFinalizedSchema` (`evaluation_finalized`), `CalibrationReviewedSchema` (`calibration_reviewed`), `CalibrationNotePublishedSchema` (`calibration_note_published`).

### `evaluation-api`

**`db.py`** — migrations DDL (idempotentes):
- `evaluation.campaigns`: `pre_review_enabled BOOLEAN`, `pre_review_agent_pool TEXT`.
- `evaluation.results`: `contestation_state TEXT`, `pre_review_complete BOOLEAN`, `evaluated_agent_type TEXT`, `finalized_at TIMESTAMPTZ`, `final_score NUMERIC`, `process_duration_ms BIGINT`.
- Nova tabela `evaluation.contestation_threads` (append-only, indexed por instance+dimension+round).
- Nova tabela `evaluation.curation_reviews` (fila de curadoria, indexed por tenant+status).
- Nova tabela `evaluation.calibration_notes` (notas do curador, indexed por campaign+evaluator).
- Nova tabela `evaluation.curation_sampling_rules` (regras por campanha, indexed por campaign+priority).

**`db.py`** — funções CRUD:
- `create_contestation_thread`, `list_contestation_threads`
- `create_curation_review`, `resolve_curation_review`, `list_curation_reviews`
- `create_calibration_note`, `mark_calibration_note_published`, `list_calibration_notes`
- `create_sampling_rule`, `list_sampling_rules`, `update_sampling_rule`, `delete_sampling_rule`
- `finalize_result`, `set_contestation_state`

**`contestation_router.py`** (novo — `contestation_router` registrado em `main.py`):
- `GET  /v1/evaluation/instances/{id}/threads` — lista threads por dimensão
- `POST /v1/evaluation/instances/{id}/contest` — agente humano contesta dimensão
- `POST /v1/evaluation/instances/{id}/review` — revisor submete decisão upheld/revised
- `POST /v1/evaluation/instances/{id}/pre-review` — revisor AI pré-publicação; se `calibration_signal` → cria `CurationReview` automaticamente
- `GET  /v1/evaluation/curations` — fila de curadoria (filtros: campaign_id, status)
- `POST /v1/evaluation/curations/{id}/resolve` — curador aprova/recalibra/flag viés; cria `CalibrationNote` e emite `calibration_reviewed`
- `GET  /v1/evaluation/calibration-notes` — histórico de notas de calibração
- `POST /v1/evaluation/calibration-notes/{id}/publish` — marca nota como publicada no KB; emite `calibration_note_published`
- `GET  /v1/evaluation/campaigns/{id}/sampling-rules` — lista regras de curadoria
- `POST /v1/evaluation/campaigns/{id}/sampling-rules` — cria regra
- `PUT  /v1/evaluation/campaigns/{id}/sampling-rules/{rid}` — atualiza regra
- `DELETE /v1/evaluation/campaigns/{id}/sampling-rules/{rid}` — remove regra

**`kafka_emitter.py`** — novos emitters:
- `emit_calibration_reviewed` → topic `calibration.events`
- `emit_calibration_note_published` → topic `evaluation.events`
- `emit_evaluation_finalized` → topic `evaluation.events`

**`main.py`**: `contestation_router` registrado no app FastAPI.

---

## Arc 10 — Journey — Fase F: Split de Jornadas (2026-05-18)

Permite extrair sessões collect de uma journey para uma nova journey independente. Caso de uso: durante a execução de um processo, descobre-se que algumas sessões pertencem a um processo diferente.

### `@plughub/schemas` — `src/journey.ts`

- `JourneySchema`: novo campo `split_from_journey_id: UUID nullable` rastreia proveniência de journeys derivadas de split.
- `JourneyEventTypeSchema`: `journey_split` removido o comentário `(future)` — agora ativo.
- `JourneyEventSchema`: novos campos `source_journey_id`, `new_journey_id`, `session_ids[]`, `session_count`, `split_from_journey_id`.
- Novos schemas de tool: `JourneySplitInputSchema` / `JourneySplitOutputSchema`.

### `workflow-api`

**`db.py`**:
- Migration idempotente: `ALTER TABLE workflow.journeys ADD COLUMN IF NOT EXISTS split_from_journey_id UUID`.
- `_row_to_journey`: serializa o novo campo.
- `db_split_journey()`: cria nova journey com `split_from_journey_id`, re-linka `collect_instances` via JOIN em `instances.session_id`.

**`kafka_emitter.py`**: `emit_journey_split()` publica `journey_split` no topic `journey.events` com `source_journey_id`, `new_journey_id`, `session_ids[]`, `session_count`.

**`journey_router.py`**:
- `GET /v1/journeys/{id}/collect-sessions`: retorna session IDs das collect_instances da journey para o picker do Monitor.
- `POST /v1/journeys/{id}/split`: valida restrições (origin protegida, somente collect sessions, merged read-only), cria nova journey, re-linka sessões, opcionalmente dispara novo workflow, publica `journey_split` no Kafka.

### `mcp-server-plughub` — `src/tools/journey.ts`

Nova tool `journey_split(journey_id, session_ids[], skill_id?, metadata?)`. Chama `POST /v1/journeys/{id}/split`. Interceptada pelo McpInterceptor (auditoria automática).

### `analytics-api`

- `models.py`: `_JOURNEY_STATUS_MAP` inclui `journey_split: None` (audit event sem transição de status). `parse_journey_event` retorna `split_from_journey_id`, `new_journey_id`, `session_count`.
- `clickhouse.py`: DDL de `journey_events` com 3 novas colunas (`split_from_journey_id`, `new_journey_id`, `session_count`). `_JOURNEY_EVENT_COLS` e `_journey_event_row` atualizados.

### `platform-ui` — `ProcessosPage.tsx`

- Novo componente `SplitDrawer`: modal com checklist de sessões collect (carregadas via `GET /v1/journeys/{id}/collect-sessions`), campo opcional de `skill_id`, validação client-side (origin bloqueada). Após split: fecha drawer, atualiza lista e seleciona a nova journey automaticamente.
- Botão "✂️ Separar em nova jornada" no footer do painel de detalhe (visível para journeys `active`/`suspended`).

### Invariants

- `journey_split` é irreversível — use `journey_merge` para reagrupar.
- `origin_session_id` da journey origem é protegido — retorna `400 origin_session_cannot_be_split` se incluído.
- Somente sessões collect (`collect_instances.journey_id = source_journey_id`) são elegíveis.
- Journey com `status: merged` retorna `409`.

---

## workflow-api — Fix: timeout scanner rodava antes do schema ser aplicado (2026-05-17)

### Problema
Em `main.py`, a chamada `ensure_schema(pool)` estava dentro de um `try/except` que silenciava a exceção com `logger.warning(...)` e continuava normalmente. Se o PostgreSQL ainda não estivesse pronto para aceitar DDL no momento em que o pool conectava (race condition frequente em docker-compose), o schema nunca era criado. Após `timeout_scan_interval_s` segundos (default 60), o scanner rodava `db_timeout_expired_instances()` e recebia `relation "workflow.instances" does not exist`.

### Fix — `packages/workflow-api/src/plughub_workflow_api/main.py`
Substituído o `try/except` silencioso por um retry loop com backoff exponencial (até 7 tentativas, 2s × attempt entre cada uma, total ~56s). Se todas as tentativas falharem, o lifespan levanta `RuntimeError` — o container falha e o Docker policy de restart reinicia até o PG estar pronto. O scanner só é criado **após** `ensure_schema()` ter sido confirmado com sucesso.

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase C: Bloqueio do Agente Humano (2026-05-17)

Impede que o routing-engine aloque um novo contato ao agente humano enquanto o segmento de wrap-up ainda está ativo.

### `routing-engine` — `src/plughub_routing/registry.py`

- Documentação do novo Redis key `{tenant_id}:instance:{instance_id}:wrap_up_pending` adicionada ao header.
- **`get_ready_instances()`**: antes de incluir um candidato com `state=ready`, verifica a existência de `{tenant_id}:instance:{instance_id}:wrap_up_pending`. Se presente → `continue` (agente excluído do pool de candidatos). Falha no check é não-fatal (agente incluído como fallback).

### `orchestrator-bridge` — `main.py`

**`fire_pool_hooks()`** — quando `hook_type=on_human_end` e `hook_side=agent`:
  - Após registrar o `_fixed_pid` no posatt participants SET (Fase B), escreve `{tenant_id}:instance:{human_instance_id}:wrap_up_pending = session_id` com TTL = `_HOOK_TIMEOUT_S + 300` (auto-expira como safety net em caso de crash da bridge).

**`process_routed()` — hook completion** — quando `_hook_side == "agent"`:
  - Após o publish do targeted `session.closed` (Fase B), lê `session.human_agent_participant_id` do ContextStore e deleta `{tenant_id}:instance:{human_instance_id}:wrap_up_pending`.
  - Na próxima avaliação de candidatos, `get_ready_instances()` incluirá o agente normalmente.

**Mecanismo**: não usa `agent_paused` event (que seria sobrescrito por heartbeats). Em vez disso, a flag Redis é verificada diretamente pelo routing-engine em tempo de roteamento — zero interferência com o ciclo de vida do agente e sem modificação no kafka_listener.

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase D: nps_on_disconnect (2026-05-17)

Implementa o comportamento configurável do segmento NPS quando o cliente se desconecta antes de ser atendido.

### `@plughub/schemas` — `src/agent-registry.ts`

- Campo `nps_on_disconnect: z.enum(["skip", "timeout"]).default("timeout")` adicionado a `PoolHookEntrySchema`.
  - `"skip"` → hook customer-side não é despachado quando `close_origin == "customer_disconnect"`. Segmento é pulado silenciosamente (sem INCR `posatt:active`, sem Kafka).
  - `"timeout"` → despachado normalmente; skill YAML pode encerrar via branch `@ctx.session.close_origin` ou aguardar `_HOOK_TIMEOUT_S`. Default backward-compat.

### `orchestrator-bridge` — `main.py`

**`fire_pool_hooks()`**: antes de criar cada `conference_id` para uma entrada `side=customer`, verifica:
  1. `entry.nps_on_disconnect == "skip"`.
  2. Lê `session.close_origin` do ContextStore (`{tenant}:ctx:{session_id}` hash).
  3. Se `close_origin == "customer_disconnect"` → `continue` (skip da entrada, sem dispatch, sem INCR `posatt:active`).
  
Leitura `nps_on_disconnect` via `entry.get("nps_on_disconnect", "timeout")` — backward compat para hooks sem o campo.

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase B: session.closed Targeted (2026-05-17)

Implementa o fechamento direcionado por segmento: cada posatt segment publica `session.closed` apenas para os participantes que fazem parte daquele segmento, em vez de broadcast para todos.

### `@plughub/schemas` — `src/stream.ts`

- Campo `recipients?: z.array(z.string()).nullable().optional()` adicionado a `SessionClosedPayloadSchema`.
  - `null` / ausente → broadcast (comportamento anterior preservado).
  - `string[]` → teardown restrito aos `participant_id`s listados.

### `orchestrator-bridge` — `main.py`

**`fire_pool_hooks()`**: Ao disparar cada hook `on_human_end` / `post_human`, registra o participante do lado fixo no SET `session:{id}:posatt:{conf_id}:participants` (TTL 4h):
  - `side=customer` → lê `session:{id}:customer_participant_id` (STRING Redis).
  - `side=agent` → lê `session.human_agent_participant_id` do ContextStore (`{tenant}:ctx:{session_id}` hash).

**`process_routed()` — hook agent join**: Quando um hook agent entra na conferência (`_is_hook_agent = True`), adiciona seu `native_instance_id` ao SET `posatt:{conf_id}:participants`.

**`process_routed()` — hook completion**: Ao detectar conclusão de um segmento posatt:
  1. Lê SMEMBERS `session:{id}:posatt:{conf_id}:participants`.
  2. Publica `session.closed` com `reason=posatt_segment_complete` e `recipients=[...]` em `agent:events:{session_id}`.
  3. Deleta o SET (cleanup).
  O broadcast final (`reason=conference_destroyed`) de `_destroy_conference()` continua como sinal global de teardown quando todos os segmentos terminam.

### `mcp-server-plughub` — `src/server.ts`

Handler `session.closed` refatorado com lógica de três paths:
  - `posatt_segment_complete` + `recipients` → teardown apenas se `agentInstanceId` estiver em `recipients`. NPS completion não afeta o agente humano; wrap-up completion encerra a view do agente humano.
  - `conference_destroyed` → teardown incondicional (broadcast final).
  - `agent_done` → teardown incondicional (path legado / backward compat).
  - Qualquer outro reason → mantém canal aberto (hooks ainda podem estar em execução).

---

## Arc 14 — Pós-Atendimento: Segmentos Independentes — Fase A: Core Split (2026-05-17)

Implementa a separação das camadas de encerramento de contato (Layer 1 × Layer 3) e o rastreamento de segmentos posatt independentes via `posatt:active` counter.

### `@plughub/schemas` — `src/agent-registry.ts`

- Campo `side: z.enum(["agent", "customer"]).default("agent")` adicionado a `PoolHookEntrySchema`.
  - `"agent"` → hook interage com agente humano (wrap-up, resumo automático). Default backward-compat.
  - `"customer"` → hook interage com cliente (NPS, pesquisa de satisfação).

### `orchestrator-bridge` — `main.py`

**Novas funções:**

- **`_close_contact_layer(redis_client, session_id)`**: Layer 1 close — fecha o WebSocket do cliente imediatamente quando o contato encerra. Publica `conversations.outbound session.closed` + `conversations.events contact_closed` (analytics com `ended_at` do `_mark_contact_ended()`). Guard: `contact_close_fired` NX.

- **`_destroy_conference(redis_client, session_id)`**: Layer 3 destroy — limpa infraestrutura da conferência quando o último segmento posatt terminar. Deleta `human_agent`/`human_agents` keys (que DEVEM permanecer vivas durante os hooks para roteamento de mensagens). Publica `session.closed` broadcast em `agent:events:{session_id}` (Arc 14 Fase B tornará isso targeted). Guard: `close_fired` NX (reutiliza a chave existente).

- **`_trigger_contact_close(redis_client, session_id)`**: mantida como wrapper backward-compat que chama `_close_contact_layer()` + `_destroy_conference()` sequencialmente. Usado no no-hook path, watchdog, e paths de AI primary.

**`fire_pool_hooks()` changes:**
- Lê `side` de cada hook entry (`entry.get("side", "agent")`, default `"agent"`).
- `INCR session:{id}:posatt:active` (TTL 4h) para cada hook `on_human_end` ou `post_human` disparado. Decrementado no completion em `process_routed()`.
- Valor de `hook_conf` estendido de `"{hook_type}:{pool}"` para `"{hook_type}:{pool}:{side}"` — backward compat: parse usa `split(":", 2)` e missing side defaults para `"agent"`.
- Log de `hook_type`, `side`, `origin_pool`, `target_pool` por hook disparado.

**Hook path (human agent_done com on_human_end hooks):**
- Chama `_close_contact_layer()` imediatamente após `_mark_contact_ended()`, ANTES de `fire_pool_hooks()`.
- Antes (P2): cliente aguardava WS aberto até TODOS os hooks terminarem.
- Depois (Arc 14 A): WS do cliente fecha imediatamente; hooks rodam em paralelo.
- NPS e wrap-up descobrem que o cliente saiu via `@ctx.session.close_origin` e agem conforme YAML (decisão D2 do spec).

**`process_routed()` hook completion (Arc 14 algorithm):**
- Parseia `side` do valor estendido de `hook_conf` (`_hl_parts[2]`, default `"agent"`).
- Dispatch post_human (se aplicável) ANTES de DECR `posatt:active` — garante que os INCRs de post_human precedam o DECR deste segmento (evita transição espúria a 0).
- `DECR session:{id}:posatt:active` para cada hook completion, independente do tipo.
- Quando `posatt:active == 0` e nenhum novo segmento foi despachado: `_destroy_conference()`.
- Substitui as chamadas a `_trigger_contact_close()` no completion block (que era Layer 1 + Layer 3 juntos).

**Comportamento resultante (P1 e P2 resolvidos):**
- NPS e wrap-up são posatt segments independentes — cada um tem seu `posatt:active` contribution.
- Cliente vê seu WS fechar imediatamente ao fim do contato (não mais aguarda wrap-up).
- Wrap-up e NPS rodam em paralelo sem esperar um pelo outro.
- Console do agente permanece aberto até o último posatt segment terminar (`_destroy_conference()`).
- P3 (bloqueio do agente para próximo contato) e Fase B (targeted session.closed) permanecem como Arc 14 Fases C e B respectivamente — ver TODO.md.

---

## Sistema Dinâmico de Mascaramento ContextStore — Fase C: UI (2026-05-17)

Seção 6 "Regras de Context Store" adicionada à `MaskingPage.tsx` — interface completa para configurar `ContextMaskingConfig` via Config API.

### `platform-ui` — `src/modules/masking/MaskingPage.tsx`

**Tipos inline** (sem import de schemas para isolar dependência):
- `ContextMaskingType` union dos 9 valores visuais
- `ContextMaskingRule` interface `{ pattern, role, type, label? }`
- `ContextMaskingConfig` interface `{ rules[], default_unmatched_operator }`

**Constante `MASKING_TYPE_INFO`**: mapa de cada `ContextMaskingType` para `{ label, sample }` — usado em selects e preview na tabela.

**Em `MaskingPage()`**:
- Lê `maskingEntries['context_rules']` do namespace `masking` (já carregado por `useNamespace`)
- `saveContextRules(config)` → `putConfig('masking', 'context_rules', config, tenantId, adminToken)` → Redis `plughub:cfg:{tenantId}:masking:context_rules`

**`ContextRulesSection` sub-component**:
- Tabela com colunas: Padrão, Role, Tipo de máscara, Prévia (preview do sample), Label, Ações
- Edição inline por linha (click ✏️ abre row edit in-place, ✓/✕ confirma/cancela)
- Exclusão por linha (✕ vermelho)
- Linha "adicionar nova regra" expansível (botão "+ Nova regra")
- Select de `default_unmatched_operator` com preview inline
- Botão "Salvar Regras" com badge "⚠ Alterações não salvas" quando há mudanças locais não persistidas
- Hint de prioridade de regras: exact > glob > wildcard; role exato > `*`
- Sincroniza state com `config` prop via `useEffect` (suporta reload após save)

**Persistência**: `putConfig('masking', 'context_rules', { rules, default_unmatched_operator }, tenantId, adminToken)` escreve no Config API (port 3600) que propaga para Redis. `MaskingService.loadContextMaskingConfig()` em `mcp-server-plughub` lê a chave no próximo request (TTL cache 60s).

---

## Sistema Dinâmico de Mascaramento ContextStore — Fase B: Backend Dinâmico (2026-05-17)

Substitui o `TAG_PII_CATEGORY` hardcoded em `server.ts` pelo algoritmo dinâmico de resolução de regras baseado em `ContextMaskingConfig` carregado do Config API.

### `mcp-server-plughub` — `src/server.ts`

**Removido**: `TAG_PII_CATEGORY` (mapa estático tag → categoria) e `maskPiiValue()` (switch por categoria).

**Adicionado**:

- **Cache em memória**: `contextMaskingConfigCache: Map<string, CachedMaskingConfig>` com TTL de 60s por tenant. `getContextMaskingConfig(redis, tenantId)` — retorna cache hit se válido, senão chama `MaskingService.loadContextMaskingConfig()` e armazena. `invalidateContextMaskingCache(tenantId)` — disponível para eventos futuros de `config.changed`.

- **Algoritmo de especificidade** (`ruleSpecificity()`): pontua cada regra candidata:
  - Pattern: exact = 20 pontos; glob (`caller.*`) = 10; wildcard (`*`) = 0; sem match = `null`
  - Role: match exato da categoria do caller = +2; `*` = +0; categoria diferente = `null`

- **`resolveContextMaskingRule(tag, callerRole, config)`**: varre todas as regras, seleciona a de maior pontuação. Traduz roles em duas categorias: `operator` (default) e `supervisor` (supervisor/admin/evaluator/reviewer). Retorna `null` quando nenhuma regra casa.

- **`applyMaskingTypeToValue(raw, type)`**: aplica os 9 tipos visuais:
  - `plain` → valor intacto; `hidden` → `""` (sinal de omissão); `full` → `"***"`
  - `last_2` / `last_4` → preserva últimos N dígitos; `first_1` → preserva primeiro caractere; `first_word` → preserva primeira palavra
  - `email_domain` → `X***@domain.com`; `financial` → `"R$ ****,**"`

- **`applyContextMaskingDynamic(rawHash, role, allowedNs, redis, tenantId)`** (async): substitui o antigo `applyContextMasking()` síncrono. Fluxo:
  1. Filtra `agent.*` (sempre omitido)
  2. Filtra namespaces fora de `allowedNs` para operator
  3. Chama `resolveContextMaskingRule()` → obtém `maskType`
  4. Fallback: `config.default_unmatched_operator` para operator; `"plain"` para supervisor
  5. Aplica: `hidden` → omite campo; `plain` → campo intacto; outros → aplica `applyMaskingTypeToValue()` e anota `{ pii: true, masked: true, category: maskType }`

- **Handler `GET /api/supervisor_state`**: chamada atualizada para `await applyContextMaskingDynamic(hash, viewerRole, operatorNamespaces, redis, tenantId)`.

**Import adicionado** no topo: `MaskingService` de `"./lib/masking"` e `ContextMaskingConfig` de `"@plughub/schemas"`.

---

## Sistema Dinâmico de Mascaramento ContextStore — Fase A: Schemas e Bootstrap (2026-05-17)

Implementa a infraestrutura de tipos para o mecanismo 3D de mascaramento (tag × role × tipo) conforme especificado em `docs/guias/context-masking-rules.md`.

### `@plughub/schemas` — `src/audit.ts`

- `ContextMaskingTypeSchema` (z.enum): 9 tipos visuais de mascaramento — `plain`, `hidden`, `full`, `last_2`, `last_4`, `first_1`, `first_word`, `email_domain`, `financial`. Puramente semântica visual, sem vínculo a tipo de dado.
- `ContextMaskingRuleSchema`: mapa `{ pattern, role, type, label? }`. `pattern` aceita nome exato ou glob com `*`. `role` = `"operator" | "supervisor" | "*"`. Resolução por especificidade: exact > glob > `*`; role específico > `*`.
- `ContextMaskingConfigSchema`: `{ rules[], default_unmatched_operator }` com default `"plain"` (permissivo — maioria dos tags do ContextStore não são PII).
- `DEFAULT_CONTEXT_MASKING_CONFIG`: fallback hardcoded que converte exatamente o `TAG_PII_CATEGORY` anterior, incluindo `account.limite_credito → hidden` (campo oculto para operator). Inclui catch-alls `caller.* → last_4` e `account.* → financial`.

### `@plughub/schemas` — `src/index.ts`

- Exports adicionados: `ContextMaskingTypeSchema`, `ContextMaskingRuleSchema`, `ContextMaskingConfigSchema`, `DEFAULT_CONTEXT_MASKING_CONFIG` (valor) + tipos `ContextMaskingType`, `ContextMaskingRule`, `ContextMaskingConfig`.

### `mcp-server-plughub` — `src/lib/masking.ts`

- `MaskingService.loadContextMaskingConfig(redis, tenantId)`: lookup chain em 3 tiers — `plughub:cfg:{tenantId}:masking:context_rules` → `plughub:cfg:__global__:masking:context_rules` → `DEFAULT_CONTEXT_MASKING_CONFIG`. Valida com `ContextMaskingConfigSchema.safeParse()` — schema inválido cai para o próximo tier.
- `MaskingService.saveContextMaskingConfig(redis, scope, config)`: persiste configuração. `scope = "global"` escreve em `__global__`; string de tenant escreve override por-tenant.

### `infra/config-seed/masking-context-rules.json`

- Arquivo de seed criado com as regras globais padrão. Pode ser carregado por `saveContextMaskingConfig(redis, "global", ...)` durante bootstrap de novos ambientes.

---

## ContextStore Taxonomy — Mascaramento PII e Visibilidade por Role (2026-05-17)

Implementa controle formal de acesso e mascaramento de dados PII na aba Contexto do Console, conforme `docs/guias/context-store-taxonomy.md`.

### Taxonomia (documento)
- **`docs/guias/context-store-taxonomy.md`** (criado): 7 namespaces (`caller`, `account`, `service`, `journey`, `session`, `agent`, `history`) com catálogo de tags por namespace, categorias PII por tag, matriz de visibilidade por role (operator/supervisor/admin/evaluator), `context_visibility.operator_namespaces` configurável por pool, `TAG_PII_CATEGORY` mapping, e plano de 4 fases.

### Backend — Fase 1: mascaramento em supervisor_state

**`mcp-server-plughub` `src/server.ts`**
- Funções helper adicionadas antes de `startServer`: `extractJwtRole()` (decodifica role do Bearer JWT sem verificação — auth middleware já validou), `TAG_PII_CATEGORY` (mapa tag → categoria PII), `maskPiiValue()` (padrões por categoria: cpf, cnpj, phone, email_addr, financial), `DEFAULT_OPERATOR_NAMESPACES = ["service","journey","session"]`, `applyContextMasking()` (filtra namespaces por role + mascara PII para operator).
- `GET /api/supervisor_state/:sessionId`: extrai `viewerRole` do header `Authorization`; lê `poolId` do session meta; busca `context_visibility.operator_namespaces` do pool_config Redis; lê ContextStore hash e aplica `applyContextMasking()`; resposta inclui `context_snapshot` filtrado/mascarado (entries PII têm `masked:true`, `pii:true`, `category`); `contact_context` (legacy) suprimido quando `context_snapshot` presente.

### Backend — Fase 2: validação de namespace no inject-context

**`mcp-server-plughub` `src/server.ts`**
- `POST /api/inject-context/:sessionId`: extrai role do JWT; valida namespace da `key` — `operator` pode escrever apenas em `agent.*` e `service.*`; outros namespaces retornam `HTTP 403 forbidden_namespace`.

### Schemas + Agent Registry + Routing Engine — Fase 3

**`@plughub/schemas` `agent-registry.ts`**
- `PoolRegistrationSchema`: novo campo `context_visibility?: { operator_namespaces: string[] }` com JSDoc explicando defaults e comportamento PII.

**`agent-registry`**
- `prisma/schema.prisma`: `context_visibility Json?` no modelo `Pool`
- `prisma/migrations/20260517000000_add_pool_context_visibility/migration.sql`: `ALTER TABLE pools ADD COLUMN context_visibility JSONB`
- `routes/pools.ts`: `create` inclui `context_visibility ?? DbNull`; `update` spread condicional. `_formatPool` já é dinâmico — propagado automaticamente no Kafka `pool.registered`.

**`routing-engine`**
- `models.py` `PoolConfig`: campo `context_visibility: dict | None = None`
- `kafka_listener.py`: propaga `pool_data.get("context_visibility")` para `PoolConfig`
- `save_pool_config()` usa `model_dump()` — `context_visibility` serializado automaticamente no Redis

### Frontend — Fase 4

**`platform-ui`**
- `types/index.ts` `Pool`: campo `context_visibility?: { operator_namespaces: string[] } | null`
- `modules/agent-assist/types.ts` `ContextEntry`: campos `pii?`, `masked?`, `category?` — set pelo backend quando valor mascarado
- `ContextoTab.tsx`:
  - `CtxFieldRow`: badge "🔒 PII" em amber para entradas mascaradas; valor exibido em `font-mono text-gray-400`; confidence badge suprimido quando mascarado
  - `groupByNamespace()`: ordem canônica atualizada para `caller → account → service → journey → session → agent → history` + labels para novos namespaces
  - `ManualTagForm`: aceita `viewerRole` prop; datalist filtrado — operator vê `agent.`, `service.`; supervisor+ vê todos; erro 403 exibe mensagem do backend em vez de "HTTP 403"
  - `ContextoTab`: prop `viewerRole?: string` (default "operator"); propagado para `ContextSnapshotCard` e `ManualTagForm`
- `RightPanel.tsx`: importa `useAuth`; lê `currentUser.role`; passa `viewerRole` para `ContextoTab`
- `config-recursos/PoolsPage.tsx`: campo "Visibilidade do Context Store" no formulário de pool — input de texto com namespaces separados por vírgula; lido/gravado como `context_visibility.operator_namespaces`

---

## max_reply_time_ms — SLA de resposta por mensagem (2026-05-16)

Campo opcional `max_reply_time_ms` adicionado ao Pool para definir o tempo máximo de resposta do agente a cada mensagem do cliente, independente do SLA total de sessão (`sla_target_ms`).

### Backend

**`@plughub/schemas` `agent-registry.ts`**
- `PoolRegistrationSchema`: novo campo `max_reply_time_ms: z.number().int().positive().optional()`

**`agent-registry` Prisma + routes**
- `prisma/schema.prisma`: `max_reply_time_ms Int?` no modelo `Pool`
- `prisma/migrations/20260516000000_add_pool_max_reply_time_ms/migration.sql`: `ALTER TABLE pools ADD COLUMN max_reply_time_ms INTEGER`
- `routes/pools.ts`: incluído no `create` (`?? null`) e no `update` (spread condicional). `_formatPool` já é dinâmico — sem alteração necessária.

**`routing-engine`**
- `models.py` `PoolConfig`: campo `max_reply_time_ms: int | None = None`
- `registry.py` `write_pool_snapshot()`: parâmetro `max_reply_time_ms` opcional; escrito no snapshot Redis apenas quando não-nulo
- `kafka_listener.py` `_handle_pool_event()`: propaga `pool_data.get("max_reply_time_ms")` para `PoolConfig`
- `main.py` `_write_pool_context()`: lê `max_reply_time_ms` do pool_config Redis e escreve `session.pool.max_reply_time_ms` no ContextStore quando não-nulo

**`mcp-server-plughub` `tools/supervisor.ts`**
- `supervisor_state`: lê `pool_id` do `session:meta`, busca `{tenantId}:pool_config:{poolId}` no Redis, extrai `sla_target_ms` e `max_reply_time_ms`
- Resposta `sla`: inclui `max_reply_time_ms` (null quando não configurado)

### Frontend

**`platform-ui`**
- `types/index.ts` `Pool`: campo `max_reply_time_ms?: number | null`
- `modules/agent-assist/types.ts` `PoolInfo`: campo `max_reply_time_ms: number | null`
- `modules/agent-assist/types.ts` `ContactSession`: campo `maxReplyTimeMs: number | null`
- `AgentAssistContext.tsx`: `fetchPools` mapeia `max_reply_time_ms`; `makeContact` inicializa `maxReplyTimeMs: null`; contact criado na chegada recebe `maxReplyTimeMs` do `poolInfo`
- `ContactList.tsx` `waitLevel()`: agora aceita `maxReplyTimeMs` como segundo parâmetro. Com limite: 50% = atenção, 100% = urgente. Sem limite: fallback hardcoded 60s/180s.
- `ActionBar.tsx`: novo componente `ReplySlaChip` — mostra `💬 {elapsed}` colorido (verde/âmbar/vermelho pulsante) quando sessão aberta + cliente aguardando + pool tem `maxReplyTimeMs` configurado. Limiar: 70% = warning, 100% = breach.
- `config-recursos/PoolsPage.tsx`: SLA e tempo máx. de resposta agora exibidos lado a lado no formulário de pool. Campo `max_reply_time_ms` opcional, `placeholder="Sem limite"`.

---

## Arc 11 Fase 2 — Fase E: Área central abas Atual + Journey (2026-05-16)

### Mudanças

**`components/JourneyPanel.tsx`** (novo)
- Painel do journey para a aba central. Layout: strip superior com dados do journey + dois painéis (lista de sessões | transcript read-only).
- `useCustomerJourneys(tenantId, customerId)`: busca journeys do cliente via `GET /analytics/reports/journeys?customer_id=X`. Prioridade: journey com `origin_session_id === currentSessionId` → journey ativo/suspenso mais recente → primeiro da lista.
- `useCustomerSessions(customerId)`: busca histórico via `GET /analytics/sessions/customer/:id` (mesmo endpoint do `useCustomerHistory`). Filtra sessões abertas após `journey.created_at - 1min`.
- `useTranscript(sessionId)`: busca transcript via `GET /api/conversation_history/:sessionId` (endpoint existente).
- `SessionListItem`: item da lista de sessões com ícone de outcome, badge "atual" / "origem", data e duração.
- `TranscriptViewer`: exibe mensagens read-only com banner "🔒 somente leitura", auto-scroll to bottom, bubble colors por author.
- Estados: sem customer → "Cliente não identificado"; sem journey → "Contato standalone" (mensagem explicativa); com journey → view completa.
- Auto-seleciona a sessão atual ao abrir o painel.

**`AgentAssistPage.tsx`**
- Import `JourneyPanel`
- Estado `centralTab: "current" | "journey"` (default `"current"`, reset em `selectedSessionId` change)
- Tab switcher "Atual · Journey" renderizado como barra compacta acima do conteúdo central (quando `selected` existe)
- `centralTab === "journey"` → `<JourneyPanel>` (hidei ChatArea + CopilotBanner + AgentInput + ParticipantFilterBar)
- `centralTab === "current"` → comportamento idêntico ao anterior

---

## Arc 11 Fase 2 — Fase D: Aba Contexto enriquecida (2026-05-16)

### Mudanças

**`components/tabs/ContextoTab.tsx`** — reescrita parcial
- Props adicionadas: `sessionId?: string | null`, `supervisorState?: SupervisorState | null`
- **`IntentFlagsCard`** (novo): card violeta exibindo `intent.current` (com % de confiança) + `flags[]` como chips laranja. Migrado do EstadoTab (removido na Fase C). Aparece apenas quando `supervisorState` fornecido.
- **`ManualTagForm`** (novo): form inline com campo chave (datalist com `caller.` / `account.` / `session.`), textarea de valor, select de confiança (0.5–1.0). Submit → `POST /api/inject-context/:sessionId` com `{ key, value, confidence }`. Feedback visual: "Salvando…" / "✓ Tag salva" com auto-limpa após 1.5s. ESC cancela. ⌘↵ submete.
- **`ContextSnapshotCard`**: botão "+" no header abre/fecha `ManualTagForm` inline. Exibe mesmo quando snapshot vazio. Props: `sessionId`, `onTagSaved`.
- `sourceLabel()` ampliado: `human_agent` → "agente", `routing_engine` → "roteamento".
- Quando `context_snapshot` ausente mas `sessionId` presente: exibe card teal com form aberto (sempre visível).
- Ordem de renderização: IntentFlagsCard → ContextSnapshotCard+form → legacy ContactContextCard → Insights histórico → Insights conversa.

**`components/RightPanel.tsx`**
- Prop adicionada: `sessionId?: string | null`
- `ContextoTab` recebe `sessionId` e `supervisorState`
- Docstring atualizada para Fase D

**`AgentAssistPage.tsx`**
- `RightPanel`: prop `sessionId={selected?.sessionId ?? null}` adicionada

---

## Arc 11 Fase 2 — Fase C: Aba Agentes (substitui State) (2026-05-16)

### Mudanças

**`types.ts`** — `ActiveTab`: `"estado"` → `"agentes"`

**`components/tabs/AgentesTab.tsx`** (novo)
- **Seção A — Ativos na Sessão**: `HumanAgentCard` (blue, primary, "● Atendendo", `···` → Substituir inline menu) + `AiParticipantCard` existente por AI participant; texto "Nenhum agente AI" quando vazio
- **Seção B — Adicionar Agente**: `AgentInviteRow` por agente mentionável com estado ⚪→🔄→🟢→✅ derivado de `ai_participants`; convite expande textarea inline; pending aliases rastreados em state local e limpos quando agente entra em `ai_participants`; botão "📤 Delegar Tarefa" abre drawer existente
- **Seção C — Pós-Atendimento**: oculto (Arc 14 pendente)
- Prop `substitutionMode` + `onToggleSubstitutionMode` migradas de ActionBar para `HumanAgentCard.···` menu

**`components/RightPanel.tsx`**
- Import `AgentesTab` (substitui `EstadoTab` no render)
- Interface ampliada: `agentName`, `substitutionMode`, `onToggleSubstitutionMode`, `mentionableAgents`, `onAddSpecialist`, `onDelegar`, `sessionClosed`

**`AgentAssistPage.tsx`**
- `activeTab` default: `"estado"` → `"agentes"`
- `handleSelectContact`: `setActiveTab("agentes")`
- `rightTabLabels`: chave `estado` → `agentes`; defaultValue `"Agentes"`
- Tab array: `["estado","contexto","historico"]` → `["agentes","contexto","historico"]`
- `RightPanel`: 7 novas props passadas

---

## Arc 11 Fase 2 — Fase B: Barra superior simplificada + sentimento compacto (2026-05-16)

### Mudanças

**`ActionBar.tsx`**
- Removida a seção de identidade do contato (channel icon, displayId, pool badge) — migrada definitivamente para a lista esquerda (Fase A)
- Removida a `SlaBar` e seu import de `SlaState` — SLA já está na lista esquerda
- Removido o botão "Substituir" da barra principal — moverá para Aba Agentes (Fase C); props `substitutionMode`/`onToggleSubstitutionMode` mantidas na interface para não quebrar o pai
- Adicionado `SentimentChip`: indicador compacto com emoji + label colorido derivado de `contact.supervisorState?.sentiment.current`; escala: 😊 Satisfeito (≥0.3) · 😐 Neutro (-0.3–0.3) · 😤 Frustrado (-0.6– -0.3) · 😡 Irritado (<-0.6); usa tokens Tailwind green-700/orange-600/red-600/gray-500; só aparece quando sessão aberta e dado disponível
- Banner "⚠️ Sessão encerrada" simplificado (antes dependia de `!sla`)

**`Header.tsx`**
- Removida sub-linha com `poolId + sessionId` — só exibe estado ready/offline do agente
- Removido bloco de timer de sessão (`sessionStartedAt` + `handleMs` state + `useEffect`)
- Removido bloco de SLA (`sla`, `slaPercent`, `slaColor`)
- Removido `formatElapsed()` (ficou órfão após remoção do timer)
- Removido `SlaState` do import de tipos
- Props `poolId?`, `sessionId?`, `sla?`, `sessionStartedAt?` marcadas como `@deprecated` e mantidas na interface para não quebrar o pai
- Row 1 final: avatar + nome + estado ready/offline | badge contatos + botão pausa + WS status

---

## Fix: Mensagens do wrap-up não apareciam no Console após F5 do cliente (2026-05-15)

### Causa raiz
`fire_pool_hooks()` no bridge publica `conversations.inbound` via Kafka para os agentes de hook (wrap-up, NPS) **depois** que `session:{id}:closed` já foi setado (linha 2802 do bridge, antes de `fire_pool_hooks()` na linha 3129). O routing engine, ao receber esses eventos, verificava `session:{id}:closed` no guard de `_process_message()` → retornava imediatamente → `conversations.routed` nunca publicado → `activate_native_agent()` nunca chamado → wrap-up nunca iniciava → nenhuma mensagem aparecia no Console.

O banner "Wrap-up em andamento" aparecia porque é gerado pelo `hook_pending` key, independente do routing. O Agente Wrapup V1 aparecia no painel Estado mas com status inicial sem avançar.

### Fix
`packages/routing-engine/src/plughub_routing/main.py` — `_process_message()`:
- O guard `is_closing` agora é condicionado a `not event.conference_id`
- Eventos de conferência (`conference_id` setado por `fire_pool_hooks()` e routing de `@mention`) são isentos do guard — são ativações legítimas em sessões já encerrando
- Seguro: o router já passa `session_id=None` para `mark_busy()` quando `conference_id` está presente, então nunca incrementa `active_count` nem atualiza o serving-pool key

### Containers afetados
- `routing-engine` (único rebuild necessário)

## Fix: active_count stuck após reconexão webchat (F5) + guard janela de hooks (2026-05-15)

### Causa raiz — Bug 1 (active_count stuck)
Ao recarregar a página (F5) durante uma sessão ativa, o webchat cliente publicava novo `conversations.inbound`. O serving-pool key `{tenant}:session:pool:{session_id}` havia sido deletado por `remove_conversation()` no fechamento do WS anterior, então `mark_busy()` Guard 0 não detectava a sessão como ativa → `INCR active_count` → contador travado em 1 permanentemente.

### Fix — Bug 1
`packages/channel-gateway/src/plughub_channel_gateway/adapters/webchat.py`:
- Guard expandido para 5 chaves: `{tenant}:session:pool:{session_id}`, `session:{id}:closed`, `session:{id}:close_fired`, `session:{id}:hook_pending:on_human_end`, `session:{id}:hook_pending:post_human`
- Cobre a janela de reconexão durante hooks ativos (até 180s)

### Containers afetados
- `channel-gateway`

## Pool Context Enrichment — Campos agent_groups e mentionable_journeys (2026-05-14)

Completa o Pool Context Enrichment adicionando os dois campos que estavam bloqueados por falta de suporte no schema.

### packages/schemas/src/agent-registry.ts
- `PoolRegistrationSchema`: adicionados `mentionable_journeys: z.record(z.string()).optional()` e `agent_groups: z.array(z.string()).optional()`

### packages/agent-registry/prisma/schema.prisma
- Model `Pool`: adicionados `mentionable_journeys Json?` e `agent_groups String[] @default([])`
- Requer `prisma migrate dev --name add_pool_groups_journeys` ao conectar

### packages/agent-registry/src/routes/pools.ts
- `POST /v1/pools`: persiste `mentionable_journeys` e `agent_groups` no create
- `PUT /v1/pools/:pool_id`: atualiza `mentionable_journeys` e `agent_groups` no update
- Ambos incluídos no payload do evento `pool.registered`/`pool.updated` via `_formatPool`

### packages/routing-engine/src/plughub_routing/models.py
- `PoolConfig`: adicionados `mentionable_journeys: dict[str, str] | None = None` e `agent_groups: list[str] = Field(default_factory=list)`

### packages/routing-engine/src/plughub_routing/kafka_listener.py
- `_handle_pool_event`: lê `mentionable_journeys` e `agent_groups` do payload e popula o `PoolConfig`
- Log atualizado para incluir `agent_groups`

### packages/routing-engine/src/plughub_routing/main.py
- `_write_pool_context`: extrai `mentionable_journeys` e `agent_groups` do Redis cache do pool
- Escreve `session.pool.mentionable_journeys` (condicional, quando presente) e `session.pool.agent_groups` (condicional, quando não vazio) no ContextStore
- Skill-flows podem agora acessar `@ctx.session.pool.mentionable_journeys` e `@ctx.session.pool.agent_groups`

---

## Arc 12 Fase E — Integração do MetricSelector com Análise de Qualidade (2026-05-14)

Fecha o ciclo de observabilidade do Arc 12: `agent_event` KPIs agora sobreponíveis em todas as telas de análise de qualidade, com seletor dinâmico de categorias.

### analytics-api — reports_query.py
- `_fetch_agent_event_slice(category, from_dt, to_dt, ...)`: query de AVG(value) na `agent_business_events` para uma categoria; retorna valor ou null
- `_fetch_agent_event_timeseries(category, from_dt, to_dt, granularity, ...)`: série temporal de AVG(value) por período
- `query_quality_comparison()`: aceita `metrics: list[str]` — para cada `agent_event:{category}`, chama `_fetch_agent_event_slice()` em paralelo e injeta no dict de métricas de cada fatia
- `query_quality_timeseries()`: aceita `metrics: list[str]` — para cada `agent_event:{category}`, chama `_fetch_agent_event_timeseries()` e mescla na série
- `query_quality_metrics()`: single-slice, mesma expansão de `metrics[]`
- `_compute_delta()`: refatorado para ser key-agnostic — itera sobre todas as chaves presentes, não apenas as 4 base

### analytics-api — reports.py
- `GET /reports/quality-comparison`: parâmetro `metrics: list[str] = Query(default=[])` — aceita `metrics[]=agent_event:{category}` repetível
- `GET /reports/quality-timeseries`: idem
- `GET /reports/quality-metrics`: idem
- `GET /reports/agent-events/categories`: novo endpoint — retorna lista de categorias distintas com contagem; usado pelo AddCardModal e MetricSelector

### platform-ui — catalog.ts
- `agent-event-timeseries`: endpoint `/reports/display/agent-event-timeseries`; `compatible_tools: ['line_chart', 'bar_chart']`; `configurable_params.category` com `options_from: '/reports/agent-events/categories'`
- `agent-event-summary`: endpoint `/reports/display/agent-event-summary`; `compatible_tools: ['metric_card', 'table']`; mesmo `options_from`
- `ConfigurableParam.options_from?: string` — nova propriedade opcional

### platform-ui — AddCardModal.tsx
- `StepConfigure`: prop `tenantId: string` adicionada
- `paramOptions: Record<string, string[]>` via `useState`
- `useEffect` por `endpoint.id + tenantId`: loop sobre params com `options_from`, fetch assíncrono, popula `paramOptions`
- Render condicional: `paramOptions[param.key]` presente → `<select>` com categorias; ausente → `<input type="text">` original
- `<select>` desabilitado com "Carregando categorias…" enquanto array está vazio; opção vazia para params opcionais

### platform-ui — MetricSelector.tsx (novo arquivo)

Componente compartilhado em `src/modules/analise/MetricSelector.tsx`:

- `MetricDef`: `{ key: string, label, format, higherIsBetter, color? }` — `key` é `string` (aceita `agent_event:*`)
- `BASE_METRIC_DEFS`: 4 defs (evaluation_score #1B4F8A, resolution_rate #059669, escalation_rate #DC2626, aht_ms #D97706)
- `BASE_METRIC_KEYS`: `BASE_METRIC_DEFS.map(d => d.key)`
- `makeAgentEventDef(category, idx)`: cria def com cor de `AGENT_EVENT_COLORS` (paleta de 6 cores violet/cyan/pink/green/amber/purple)
- `buildMetricDefs(selectedMetrics)`: filtra base + map agent_event entries → `MetricDef[]`
- `MetricSelector`: pills toggle para 4 base + pills removíveis para agent_event ativos + picker "+ Evento" com lazy-fetch de `/reports/agent-events/categories`; fechamento por clique externo via `useRef`

### platform-ui — AnaliseComparacaoPage.tsx
- Removidas definições inline de `MetricDef`, `BASE_METRIC_DEFS`, `buildMetricDefs`; importadas de `./MetricSelector`
- `SliceMetrics.metrics: Record<string, number | null>` (era struct tipada com 4 chaves fixas)
- Estado `selectedMetrics: string[]` + derivado `metricDefs = buildMetricDefs(selectedMetrics)`
- `fetchSlice()`: append `&metrics[]={k}` para cada `agent_event:*` selecionado
- `MetricSelector` adicionado na área de filtros globais
- `GroupedBarChart` e `MetricTable` recebem `metricDefs: MetricDef[]` e iteram dinamicamente
- `metricToChartValue(key, raw)`: `agent_event:*` → raw; `aht_ms` → `/60000`; outros → `×100`

### platform-ui — AnaliseQualidadePage.tsx

**TimeseriesView**:
- `selectedMetrics` (default `['evaluation_score']`), `metricDefs = buildMetricDefs(selectedMetrics)`
- `load()` append `metrics[]` para agent_event; `selectedMetrics` em deps
- `chartData`: `score` (evaluation_score ×100) + `agent_event:*` keys raw
- `YAxis` sem `domain` fixo — auto-escala
- `<Legend>` adicionado; `<Line>` para evaluation_score + `{metricDefs.filter(agent_event).map(d => <Line dataKey={d.key}>)}` dinâmico
- Tooltip dinâmico: label da MetricDef; `%` apenas para `score`
- `MetricSelector` abaixo do filtro de datas

**ComparisonView**:
- `selectedMetrics` (default `BASE_METRIC_KEYS`), `metricDefs = buildMetricDefs(selectedMetrics)`
- `run()` append `metrics[]`; `selectedMetrics` em `useCallback` deps
- `metricDefs.map(def => <MetricComparisonRow>)` substitui 4 chamadas hardcoded
- `MetricSelector` adicionado após os SliceForms

**Doc:** `docs/arcos/arc12-agent-business-events.md`

---

## Arc 6 Fase 2 — Observabilidade de Mudanças e Comparação por Deploy (2026-05-14)

Todas as fases A–D implementadas. Spec: [`docs/arcos/arc6-phase2-observability.md`](docs/arcos/arc6-phase2-observability.md).

**Fase A — Infraestrutura Deploy Events**
- `analytics.deploy_events` ClickHouse table (ReplacingMergeTree por `deploy_id`) em `clickhouse.py`.
- `agent-registry/src/infra/kafka.ts`: `publishSkillDeployed()` chamado em `POST /v1/skills/:id/deploy`.
- `analytics-api/consumer.py`: consumer `registry.changed` com `event_type: "skill_deployed"` → INSERT `deploy_events`.
- `GET /reports/deploy-timeline` endpoint em `reports.py`.

**Fase B — Quality Comparison**
- `GET /reports/quality-comparison`: dual-slice com `evaluation_score`, `resolution_rate`, `escalation_rate`, `aht_ms`, delta + `statistical_significance` (warn N < 30).
- `AnaliseQualidadePage.tsx`: tab "Comparação" com `SliceForm` + `MetricComparisonRow` (deltas coloridos ▲▼→).

**Fase C — Timeseries com Deploy Markers**
- `GET /reports/quality-timeseries`: retorna pontos diários/semanais + `deploy_markers[]`.
- `AnaliseQualidadePage.tsx`: tab "Tendência", Recharts `LineChart` + `ReferenceLine` por deploy (linhas laranja tracejadas, hover com versão).

**Fase D — Painel de Grupos de Comparação**
- `GET /reports/quality-metrics`: slice única — usado em parallel fetch pelo frontend.
- `AnaliseComparacaoPage.tsx`: rota `/analise/comparison`, `ComparisonGroupBuilder` (até 4 slices), `GroupedBarChart` (barras agrupadas por KPI), `MetricTable` com N < 30 badges.

---

## Skill-flow DAG Validation + Worker Non-blocking Receive (2026-05-14)

Completa os dois itens pendentes do step `receive` (pontos 3 e 4 do TODO).

**`packages/skill-flow-engine/src/engine.ts`**
- `_getSuccessors(step)` (novo): extrai todos os IDs de próximo step de qualquer step type — cobre todos os campos de transição (`on_success`, `on_failure`, `on_message`, `on_timeout`, `on_disconnect`, `on_max_iterations`, `branches[].next`, `strategy.on_success/on_failure`). Filtra sentinelas do engine (`__complete__`, `__suspended__`, etc.).
- `validateFlow(flow)` (novo, exportado): DFS three-colour (white/gray/black) sobre o grafo de steps. Detecta back-edges (ciclos). Para cada ciclo encontrado, verifica se algum node do ciclo é um `receive` step com `max_iterations` definido. Lança erro descritivo se ciclo não guarded for encontrado. Chamado em `run()` antes do lock, uma vez por execução (O(V+E) — negligível vs BLPOP).

**`packages/skill-flow-engine/src/index.ts`**
- `validateFlow` adicionado ao export público — permite que skill-registry e testes externos validem flows sem instanciar o engine.

**`packages/skill-flow-worker/src/worker.ts`**
- `_inflight: Set<Promise<void>>` adicionado a `SkillFlowWorker` — rastreia todas as execuções em andamento para graceful drain no shutdown.
- `eachMessage`: substituído `await this.handleMessage()` por fire-and-forget com `.catch()` + `.finally(() => _inflight.delete(p))`. Retorna imediatamente — Kafka commita o offset e processa a próxima mensagem sem esperar o BLPOP do `receive` (que pode durar até 4h).
- `gracefulShutdown` (SIGTERM/SIGINT): agora drena `_inflight` via `Promise.allSettled([..._inflight])` antes de desconectar o Redis. Sessions com `receive` ativo recebem o sentinela `session:closed` do orchestrator-bridge, desbloqueando o BLPOP rapidamente.

---

## Pool Context Enrichment — Routing Engine → ContextStore (2026-05-14)

Routing Engine agora escreve contexto do pool alocado no ContextStore após cada roteamento bem-sucedido, tornando `@ctx.session.pool.*` disponível em skill-flows sem consulta ao agent-registry.

**`packages/routing-engine/src/plughub_routing/models.py`**
- `PoolConfig`: novo campo `mentionable_pools: dict[str, str] | None = None` — alias→pool_id map proveniente do agent-registry. Compatível com `extra="ignore"` existente (campo explícito agora).

**`packages/routing-engine/src/plughub_routing/kafka_listener.py`**
- `_handle_pool_event()`: passa `mentionable_pools=pool_data.get("mentionable_pools") or None` ao construir `PoolConfig`. Log atualizado para incluir aliases de pools mencionáveis.

**`packages/routing-engine/src/plughub_routing/main.py`**
- `_write_pool_context()` (novo): lê `{tenant_id}:pool_config:{pool_id}` do Redis (cache próprio do routing engine, sem I/O extra), escreve 2–3 entradas no hash ContextStore `{tenant_id}:ctx:{session_id}`:
  - `session.pool.id` → pool_id (string, confidence 1.0)
  - `session.pool.channels` → channel_types (list, confidence 1.0)
  - `session.pool.mentionable_pools` → dict alias→pool_id (omitido se vazio)
  - visibility: `agents_only`; TTL 24h com NX (não sobrescreve TTL existente)
- `_process_message()`: chama `_write_pool_context()` via `asyncio.create_task()` após `result.allocated == True` (fire-and-forget, nunca falha o roteamento).

---

## Audit Profile — LGPD Compliance Role (2026-05-14)

Perfil de auditoria LGPD implementado como módulo ABAC `audit`, sem criar role fixa. Docs: [`docs/arcos/audit-lgpd.md`](docs/arcos/audit-lgpd.md).

**`infra/modules.yaml`** — Módulo `audit` com 5 campos: `sessions`, `mcp_calls`, `user_access`, `data_requests`, `config_snapshot`. Seedado em `auth.module_registry`.

**`packages/analytics-api/src/plughub_analytics_api/clickhouse.py`**
- DDL `_DDL_MCP_AUDIT_LOG`: `ReplacingMergeTree`, colunas `event_id/tenant_id/session_id/instance_id/server_name/tool_name/allowed/injection_detected/duration_ms/source/masked_input_fields/timestamp/date`. Idempotente para replay de consumer.
- DDL `_DDL_AUDIT_ACCESS_LOG`: `MergeTree` (não-deduplicado por design — todo acesso DPO é registro permanente). Colunas: `access_id/tenant_id/accessed_by/resource/resource_id/accessed_at/date`.
- Ambos adicionados a `_ALL_DDL`.
- Métodos: `insert_mcp_audit_log()`, `insert_audit_access_log()`, `query_mcp_audit_calls()` (filtros: tenant_id, session_id, from_dt, to_dt, masked_only, limit).

**`packages/analytics-api/src/plughub_analytics_api/models.py`**
- `parse_mcp_audit_event()` agora retorna `list[dict] | None` (dual-write pattern). Primeiro item: `session_timeline` row (existente). Segundo item: novo `mcp_audit_log` row com `masked_input_fields` completo. Compatível com `_process_message` que já normaliza para lista.

**`packages/analytics-api/src/plughub_analytics_api/consumer.py`**
- `_write_row()`: dois novos branches `mcp_audit_log` e `audit_access_log`.

**`packages/analytics-api/src/plughub_analytics_api/audit_router.py`** (novo)
- Router FastAPI prefix `/v1/audit`.
- `_require_audit_access(field, credentials)`: decodifica JWT, verifica `module_config.audit.{field} >= read_only`, impõe tenant isolation. Raises 401/403/503.
- `GET /v1/audit/sessions/{session_id}/messages`: requer `audit.sessions`. Lê `analytics.messages` (conteúdo mascarado). Side-effect: insere linha imutável em `audit_access_log` (fire-and-forget).
- `GET /v1/audit/mcp-calls`: requer `audit.mcp_calls`. Tenant isolation JWT vs query param. Parâmetros: `session_id`, `from_dt`, `to_dt`, `masked_only=true`, `limit=200`.

**`packages/analytics-api/src/plughub_analytics_api/main.py`**
- Import e registro de `audit_router`.

**`packages/platform-ui/src/modules/audit/AuditPage.tsx`** (novo)
- 5 tabs: Sessions (ativo), MCP Calls (ativo), User Access / Data Requests / Config Snapshot (stubs "Em desenvolvimento").
- Auth via `getAccessToken()` (JWT in-memory). Base URL `VITE_ANALYTICS_URL`.
- Warning banner vermelho em todos os tabs: "Todo acesso a esta área é registrado em log de auditoria".
- SessionsTab: busca por session_id, renderiza timeline com role color chips e tokens mascarados.
- McpCallsTab: filtros (session_id, masked_only checkbox, from/to datetime), tabela com `masked_input_fields` como badges amarelos, badge de injection_detected.

**`packages/platform-ui/src/shell/Sidebar.tsx`**
- Item standalone "Auditoria LGPD" (ícone 🔍) entre Analytics e Configuração. Roles: admin, supervisor. ABAC gate: `audit.sessions`.

**`packages/platform-ui/src/app/routes.tsx`**
- Import `AuditPage` + rota `{ path: 'audit', element: <AuditPage /> }`.

**`packages/platform-ui/src/i18n/locales/en/shell.json`** — `nav.audit = "Audit"`
**`packages/platform-ui/src/i18n/locales/pt-BR/shell.json`** — `nav.audit = "Auditoria LGPD"`

---

## Bugfix — G2: on_human_end hooks não disparavam para especialistas plughub-native (2026-05-13)

**`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`**

**Causa raiz**: quando o agente humano clicava "Desligar" enquanto um especialista `plughub-native` (ex: `skill_auth_form_v1`) ainda estava em execução, o bridge corretamente armazenava `pending_on_human_end` no Redis e adiava o disparo dos hooks `on_human_end`. No entanto, o bloco responsável por processar esse key deferido — SREM de `active_ai_specialists` + GETDEL `pending_on_human_end` — existia **apenas** no handler Kafka de `conference_agent_completed` (linha ~2500), que é publicado pela ferramenta MCP `agent_done` do `runtime.ts`. Para agentes `plughub-native`, o skill flow executa **inline** no bridge via `activate_native_agent` (await bloqueante na linha 1867), sem nunca chamar a ferramenta MCP — portanto o evento Kafka nunca era publicado, e o `pending_on_human_end` ficava preso no Redis até expirar (300s). Resultado: wrapup e NPS nunca chegavam ao Console nem ao webchat quando havia especialista nativo ativo no momento do "Desligar".

**Fix** (`main.py`):
1. Inicializa `_is_hook_agent = False` antes do bloco `if conference_id and native_instance_id:` (linha 1734) para garantir que a variável esteja sempre definida no escopo.
2. Adicionado bloco **G2 inline** ao fim da seção `if conference_id:` no caminho de conclusão nativo (após Fase B/C, antes de `elif framework == "human":`). Quando `activate_native_agent` retorna e o especialista não é hook agent (`not _is_hook_agent`):
   - SREM `native_instance_id` de `session:{id}:active_ai_specialists`
   - SCARD para verificar se restam outros especialistas
   - Se 0: GETDEL `pending_on_human_end` → se existir, chama `_write_pre_hook_context` + `fire_pool_hooks(on_human_end)` + `_hook_timeout_guard`
   - Fallbacks: sem hooks no pool → `_trigger_contact_close`; sem http/pool/tenant → `_trigger_contact_close`

**Log de diagnóstico adicionado**: `"All native specialists done — dispatching deferred on_human_end: session=... pool=..."` + `"on_human_end hooks dispatched (native deferred): session=... pool=... count=..."`.

**Verificado em produção**: NPS aparece no webchat com pergunta de 0-10 (botões de lista); wrapup aparece no Console com pergunta de classificação e campo de resumo. Ambos os agentes listados em "AGENTES AI NA SESSÃO" no painel direito do Console.

---

## Bugfix — NPS buttons no webchat + masked_fields em menu.render (2026-05-13)

**`packages/mcp-server-plughub/src/tools/bpm.ts`**

Dois bugs na entrega de mensagens pós-`agent_done` via array visibility:

**Bug 1 — NPS buttons não apareciam no webchat**
Para `isArrayVis && hasMenu`, o stream recebia `type: "message"` em vez de `type: "interaction_request"`. O `StreamSubscriber` do channel-gateway converte `interaction_request` → WS `interaction.request` (com botões clicáveis); `message` → WS `msg.text` (texto plano, sem botões). Resultado: o cliente via webchat recebia a pergunta NPS como texto em vez de ver os botões de resposta.
- Fix: quando `isArrayVis && hasMenu`, escreve `type: "interaction_request"` no stream com payload completo (`menu_id`, `interaction`, `prompt`, `options`, `fields`, `masked_fields`).
- Mensagens de texto simples (`isArrayVis && !hasMenu`) continuam usando `type: "message"` sem alteração.

**Bug 2 — `masked_fields` ausente no `menu.render`**
O evento `menu.render` publicado em `agent:events:{sessionId}` (para o Console) não incluía `masked_fields`. O handler `menu.render` do `AgentAssistContext` já suportava o campo; apenas não o recebia.
- Fix: `menu.render` agora inclui `masked_fields: parsed.menu!.masked_fields ?? undefined`.

---

## Bugfix — NPS customer reply visível ao agente após sessionClosed (2026-05-13)

**`packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx`**

No handler `message.text`, após `sessionClosed === true` mensagens com `author === "customer"` eram adicionadas ao array de mensagens do contato. Isso fazia a resposta do cliente ao NPS (ex: "1") aparecer na view do agente humano, que já havia encerrado seu atendimento.

Fix: ao processar `message.text`, se `c.sessionClosed && msg.author === "customer"` o evento é silenciosamente ignorado. Mensagens de hook agents (wrapup, NPS) ainda chegam normalmente pois têm `author === "agent_ai"`.

---

## Bugfix — Pool Lifecycle Hooks (wrapup + NPS) não chegavam ao Console (2026-05-13)

Dois bugs introduzidos durante Arc 11 Fases C+D impediam que wrapup e NPS funcionassem.

**Bug 1 — `packages/mcp-server-plughub/src/server.ts`**
O WS server desfazia a subscription de `agent:events:{sessionId}` ao receber qualquer `session.closed`, inclusive o `reason=client_disconnect` publicado pelo bridge ANTES de disparar os hooks `on_human_end`. Com isso, todas as mensagens de wrapup/NPS eram perdidas quando o cliente desconectava.
- Fix: só cancela subscription quando `reason === "agent_done"` (publicado por `_trigger_contact_close()` após todos os hooks terminarem).

**Bug 2 — `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py` linha 878**
O guarda G2 (`active_ai_specialists`) excluía hooks do set via chave `hook_conf` apenas para `on_human_end` e `post_human`. Agentes de hook `on_human_start` (como o Echo evaluator) não recebiam a chave e entravam no set. Como rodam em loop `receive→notify→receive` indefinido, nunca saíam — quando o agente humano clicava Desligar, G2 detectava count=1 e adiava os hooks para sempre.
- Fix: incluir `on_human_start` no conjunto de tipos que gravam a chave `hook_conf`.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx`**
- `session.closed reason=client_disconnect`: mantém o contato ativo (`sessionClosed=true`, `pendingCloseModal=false`) em vez de removê-lo — hooks ainda precisam interagir com o agente humano.
- `session.closed reason=agent_done`: remove o contato (todos os hooks completaram).

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`**
- `handleSend`: quando `sessionClosed=true` força `visibility=agents_only` para que respostas do agente após o encerramento vão para o BLPOP do hook (não para o cliente).

---

## Arc 11 — Console Orchestration: Fases C + D — Delegar Tarefa + OrchestrationTab (2026-05-13)

Spec completa: [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

### Fase C (F3) — Delegar Tarefa: seleção de mensagens + drawer

**`packages/platform-ui/src/modules/agent-assist/types.ts`** (Task #100)
- `ActiveTab` expanded: `"estado" | "capacidades" | "contexto" | "orquestracao"`.
- `AiState`, `AiParticipantInfo`, `PipelineTransition` interfaces added (synced from WSL path).
- `SupervisorState.ai_participants?: AiParticipantInfo[]` + `pipeline_transitions?: PipelineTransition[]` added.

**`packages/platform-ui/src/modules/agent-assist/components/MessageBubble.tsx`** (Task #100)
- New props: `isSelected?: boolean`, `onToggleSelection?: () => void`.
- When `onToggleSelection` provided: wraps bubble in `group` flex row with hover-only checkbox button.
- Selected state: `ring-1 ring-orange-400 rounded-2xl` on bubble wrapper; checkbox shows orange fill + `✓`.

**`packages/platform-ui/src/modules/agent-assist/components/ChatArea.tsx`** (Task #100)
- New props: `selectedMessageIds?: Set<string>`, `onToggleSelection?: (messageId: string) => void`.
- Orange selection toolbar shown above scroll area when `selectedMessageIds.size > 0`.
- Each `MessageBubble` receives `isSelected` + `onToggleSelection` forwarded.

**`packages/platform-ui/src/modules/agent-assist/components/ActionBar.tsx`** (Task #100)
- `DelegarButton` sub-component: orange accent, count badge overlay (max "9+") when `selectedCount > 0`.
- New `ActionBarProps`: `selectedCount?: number`, `onDelegar?: () => void`.
- Button shown when `onDelegar` is provided and `mentionableAgents.length > 0`.

**`packages/platform-ui/src/modules/agent-assist/components/DelegarTarefaDrawer.tsx`** (Task #100, new file)
- Slide-in right drawer (fixed, `w-80`); 3-section form: agent picker + instruction textarea + visibility radio.
- Pre-fills instruction from `prefilledContext` prop (concatenation of selected message texts).
- Escape closes; ⌘↵ submits. Orange accent throughout.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #100)
- State: `selectedMessageIds: Set<string>`, `showDelegarDrawer`, `delegatedAgents: Set<string>`.
- `handleToggleMessageSelection(messageId)` — toggles set membership.
- `prefilledContext` — joins selected message texts with `\n---\n` separator.
- `handleDelegate(agentTypeId, instruction, visibility)` — calls `handleSend(@{id} {instr})`, adds toast, clears selection, closes drawer.
- `useEffect` resets selection on contact change.
- Fixed TDZ bug: `selected` declaration moved before hook calls.

### Fase D (F4) — OrchestrationTab: supervisor view + interventions

**`packages/mcp-server-plughub/src/server.ts`** (Task #101)
- `GET /api/supervisor_state/:sessionId`: extended with `ai_participants` (array from `session:{id}:ai_agents` SET + per-instance metadata + pipeline-state derivation) and `pipeline_transitions` (from `{tenant}:pipeline:{sessionId}.transitions[]`). Same logic as MCP tool `supervisor.ts`.
- `POST /api/inject-context/:sessionId`: writes ContextStore entry `{key: ContextEntry}` to `{tenantId}:ctx:{sessionId}` Redis hash. Body: `{ key, value, confidence?, source? }`. Reads `tenantId` from `session:{id}:meta`.
- `POST /api/force-complete/:sessionId`: updates `{tenantId}:pipeline:{sessionId}` status → `"completed"` with `force_complete_reason`, `force_complete_outcome`, `force_complete_at`. Body: `{ reason?, outcome? }`.

**`packages/platform-ui/src/modules/agent-assist/hooks/useSupervisorState.ts`** (Task #102)
- Return type changed from `SupervisorState | null` to `{ state: SupervisorState | null; refresh: () => void }`.
- `refresh` exposes the internal `fetchState` callback for on-demand re-fetch (used by OrchestrationTab after supervisor interventions).

**`packages/platform-ui/src/modules/agent-assist/components/AiParticipantCard.tsx`** (Task #102, synced to Windows path)
- Copied from WSL path to Windows development path (the two are physically distinct directories).

**`packages/platform-ui/src/modules/agent-assist/components/tabs/OrchestrationTab.tsx`** (Task #102, new file)
- Three sections: (1) AI agents list (`AiParticipantCard` per participant); (2) pipeline transition timeline — reverse-chronological, step-type icons, formatted timestamps; (3) Supervisor interventions: `InjectContextForm` (key + value + confidence, POST to `/api/inject-context`) + `ForceCompleteConfirm` (2-step confirm, POST to `/api/force-complete`).
- Both intervention forms call `onRefresh()` on success to trigger immediate state re-poll.

**`packages/platform-ui/src/modules/agent-assist/components/RightPanel.tsx`** (Task #102)
- New props: `mcpBase?: string`, `onRefreshState?: () => void`.
- `OrchestrationTab` imported and rendered when `activeTab === "orquestracao"`.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #102)
- `useSupervisorState` destructured as `{ state: supervisorState, refresh: refreshSupervisorState }`.
- Tab bar: "Orq." tab added; gated on `session.role === "supervisor" || "admin"`.
- `RightPanel` receives `onRefreshState={refreshSupervisorState}`.

---

## Arc 11 — Console Orchestration: Fase A — Cartões de Participantes AI (2026-05-13)

Spec completa: [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

### Backend — `supervisor_state` extended with `ai_participants[]`

**`packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`** (Task #94)
- Native agent activation path: after `segment_id` write, sets `session:{session_id}:ai_participant:{instance_id}` (TTL 4h) with `{ role, agent_type_id, pool_id, segment_id, joined_at }`.
- YAML fallback path: same write after `sadd ai_agents`, `role: "primary"`.

**`packages/mcp-server-plughub/src/tools/supervisor.ts`** (Task #94)
- Section 8 added: reads `session:{id}:ai_agents` SET, pipeline_state (`{tenant_id}:pipeline:{session_id}`), `menu:waiting:{session_id}` hash, `receive:waiting:{session_id}` hash.
- Per instance: reads `session:{id}:ai_participant:{instance_id}` for metadata; derives `ai_state { current_step, step_type, step_status, waiting_for, since_ms }`.
- Step derivation rules: menu:waiting hit → `step_status=waiting, step_type=menu`; receive:waiting hit → `waiting, receive`; pipeline.status=`suspended` → `waiting, suspend`; `completed` → `done`; `failed` → `error`; otherwise → `running`, type inferred from step_id naming.
- `ai_participants: AiParticipant[]` added to supervisor_state JSON response.

### Frontend — `AiParticipantCard` + polling + drawer

**`packages/platform-ui/src/modules/agent-assist/types.ts`** (Task #95)
- `AiState` interface: `current_step`, `step_type`, `step_status`, `waiting_for`, `since_ms`.
- `AiParticipantInfo` interface: `instance_id`, `agent_type_id`, `pool_id`, `role`, `segment_id`, `joined_at`, `ai_state`.
- `SupervisorState.ai_participants?: AiParticipantInfo[]` added.

**`packages/platform-ui/src/modules/agent-assist/hooks/useSupervisorState.ts`** (Task #95)
- Added 3 s periodic `setInterval` poll alongside the existing event-driven refresh. Keeps AI participant step state live even when no WS events arrive.

**`packages/platform-ui/src/modules/agent-assist/components/AiParticipantCard.tsx`** (Task #95, new file)
- Card shows: `🤖` icon, formatted agent_type_id, role badge (primary/specialist/supervisor), status chip (running/waiting/done/error with CSS animation for running), step ID formatted as `type: label`, elapsed time, waiting_for pill when applicable.
- Click opens drawer: agent metadata + step detail + last 5 messages from this agent in the session + "Encerrar segmento" button (disabled when status=done).
- "Encerrar segmento" calls `onTerminateSegment(instance_id)` which sends `@{instance_id} terminate_self` via WS and closes the drawer.

**`packages/platform-ui/src/modules/agent-assist/components/tabs/EstadoTab.tsx`** (Task #95)
- New props: `sessionMessages?: ChatMessage[]`, `onTerminateSegment?: (instanceId: string) => void`.
- "Agentes AI na Sessão" section added at top of tab (only shown when `ai_participants.length > 0`), renders one `AiParticipantCard` per participant.

**`packages/platform-ui/src/modules/agent-assist/components/RightPanel.tsx`** (Task #95)
- New props: `sessionMessages?: ChatMessage[]`, `onTerminateSegment?` threaded down to `EstadoTab`.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #95)
- `handleTerminateSegment(instanceId)` → calls `handleSend(\`@${instanceId} terminate_self\`)`.
- `RightPanel` call updated with `sessionMessages={selected?.messages ?? []}` and `onTerminateSegment={handleTerminateSegment}`.

---

## Arc 11 — Console Orchestration: Fase B — Adicionar Especialista (2026-05-13)

Spec completa: [`docs/arcos/arc11-console-orchestration.md`](docs/arcos/arc11-console-orchestration.md)

### Backend — `GET /v1/pools/:poolId/mentionable-agents` (Task #97)

**`packages/agent-registry/src/routes/pools.ts`** (Task #97)
- New route `GET /v1/pools/:pool_id/mentionable-agents` inserted before the generic `GET /v1/pools/:pool_id` (critical ordering — prevents Express matching sub-path as `:pool_id`).
- Reads `pool.mentionable_pools` (JSON array of pool_id strings); returns `{agents:[]}` when empty.
- `prisma.agentType.findMany` WHERE `tenant_id` + `status: active` + `pools.some.pool.pool_id IN mentionablePoolIds`; includes pools JOIN to resolve `pool_id` per agent.
- Response shape: `{ agents: [{ agent_type_id, pool_id, description, capabilities }] }`.

### Frontend — `AdicionarEspecialistaButton` + `useMentionableAgents` (Task #98)

**`packages/platform-ui/src/modules/agent-assist/types.ts`** (Task #98)
- `MentionableAgent` interface: `{ agent_type_id, pool_id: string|null, description: string|null, capabilities: Record<string, unknown> }`.
- `PoolInfo.mentionable_pools?: string[]` added.

**`packages/platform-ui/src/modules/agent-assist/hooks/useMentionableAgents.ts`** (Task #98, new file)
- Fetches `GET /v1/pools/${poolId}/mentionable-agents` on `poolId` change.
- Returns `MentionableAgent[]`; defaults to `[]` on error or when `poolId` is null.
- Cleanup cancellation pattern (`cancelled` flag) prevents stale state on rapid poolId changes.

**`packages/platform-ui/src/modules/agent-assist/components/ActionBar.tsx`** (Task #98)
- `AdicionarEspecialistaButton` sub-component with 2-step inline flow:
  - Step 1: scrollable dropdown list of agents — name, description, `agent_type_id`, `pool_id`.
  - Step 2: back chevron + context `<textarea>` (⌘↵ submits, Esc closes) + "Convidar" button.
  - Purple accent (`text-purple-700 bg-purple-50 border-purple-200`); hidden when `mentionableAgents` is empty.
- New `ActionBarProps`: `mentionableAgents?: MentionableAgent[]`, `onAddSpecialist?: (agentTypeId: string, context: string) => void`.
- Button rendered before `<IniciarProcessoButton>` in the action row.

**`packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`** (Task #98)
- `useMentionableAgents(currentPoolId)` — `currentPoolId = selected?.poolId ?? null`.
- `handleAddSpecialist(agentTypeId, context)` → `handleSend(`@${agentTypeId} ${context}`)` + info toast.
- `ActionBar` call updated: `mentionableAgents={mentionableAgents}` + `onAddSpecialist={handleAddSpecialist}`.

---

## Arc 12 — Agent Business Events: Fases C + D (2026-05-13)

Spec completa: [`docs/arcos/arc12-agent-business-events.md`](docs/arcos/arc12-agent-business-events.md)

### Fase C — 3 analytics endpoints

**`packages/analytics-api/src/plughub_analytics_api/reports_query.py`** (Task #92)
- `query_agent_events_series()` / `_fetch_agent_events_series()` — time-series by period × category; granularity `hour|day|week`; `startsWith(category, ...)` prefix filter
- `query_agent_events_summary()` / `_fetch_agent_events_summary()` — aggregation by `group_by` (`category|skill_id|pool_id|agent_type_id`); pagination; validated against `VALID_GROUP_BY` set
- `query_agent_events_categories()` / `_fetch_agent_events_categories()` — catalogue of active category prefixes (max 500); pool/skill filter

**`packages/analytics-api/src/plughub_analytics_api/reports.py`** (Task #92)
- `GET /reports/agent-events/series` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`, `skill_id`, `granularity`, `format`
- `GET /reports/agent-events/summary` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`, `group_by`, `page`, `page_size`, `format`
- `GET /reports/agent-events/categories` — params: `tenant_id`, `from`, `to`, `pool_id`, `skill_id` (JSON only)

### Fase D — Dashboard cards (2 cards)

**`packages/analytics-api/src/plughub_analytics_api/display_formatters.py`** (Task #93)
- `_fetch_agent_event_timeseries_sync()` — daily aggregation (count + avg value) for a category prefix
- `_fetch_agent_event_summary_sync()` — grouped aggregation (top 20 groups by event count)
- `fmt_agent_event_timeseries()` — async; returns `LineChartData` with series `['Total', 'Média']`
- `fmt_agent_event_summary()` — async; returns `BarChartData`; `group_by` validated against allowed set

**`packages/analytics-api/src/plughub_analytics_api/display.py`** (Task #93)
- `GET /reports/display/agent-event-timeseries` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`
- `GET /reports/display/agent-event-summary` — params: `tenant_id`, `from`, `to`, `category`, `pool_id`, `group_by`
- Route count updated from 14 → 16 in module docstring

**`packages/platform-ui/src/dashboard/catalog.ts`** (Task #93)
- `agent-event-timeseries` — `LineChartData`; configurable params: `category`, `pool_id`
- `agent-event-summary` — `BarChartData`; configurable params: `category`, `pool_id`, `group_by`; `group_by` placeholder lists all valid values for discoverability

---

## Arc 12 — Agent Business Events: Fases A + B (2026-05-13)

Spec completa: [`docs/arcos/arc12-agent-business-events.md`](docs/arcos/arc12-agent-business-events.md)

### Fase A — Schema + ClickHouse DDL + analytics-api consumer

**`packages/schemas/src/agent-events.ts`** (Task #88)
- `AGENT_EVENT_CATEGORY_REGEX` — `^[a-z0-9_]+(\.[a-z0-9_]+){1,4}$` (2–5 dot-separated segments)
- `AGENT_EVENT_PII_TAG_KEYS` — set of 20 blocked PII keywords (`cpf`, `cnpj`, `phone`, `email`, `token`, `api_key`, …)
- `AgentBusinessEventSchema` — full event schema with `event_id`, `tenant_id`, `session_id`, `journey_id` (nullable), `agent_type_id`, `skill_id`, `pool_id`, `category`, `category_l1..l4` (pre-decomposed), `value`, `tags`, `emitted_at`
- `AgentEventInputSchema` — agent-facing input with max 10 tags, 64-char limits
- `decomposeCategoryLevels()` — splits category into `{l1, l2, l3, l4}` with empty-string defaults
- Exported from `packages/schemas/src/index.ts`

**`packages/analytics-api/src/plughub_analytics_api/clickhouse.py`** (Task #89)
- `_DDL_AGENT_BUSINESS_EVENTS` — `MergeTree()` (immutable events, no dedup), partitioned by month, ORDER BY `(tenant_id, category_l1, category_l2, category_l3, emitted_at)`, TTL 2 years
- `_AGENT_BUSINESS_EVENT_COLS` + `insert_agent_business_event()` method on `AnalyticsStore`
- `_agent_business_event_row()` row builder with fallback category decomposition and tag coercion to `dict[str, str]`

**`packages/analytics-api/src/plughub_analytics_api/models.py`** (Task #89)
- `parse_agent_business_event()` — validates required fields, coerces `value` to float, decomposes category levels, sanitises tags

**`packages/analytics-api/src/plughub_analytics_api/consumer.py`** (Task #89)
- Topic `agent.events` added to `_TOPICS`
- Parser `parse_agent_business_event` added to `_PARSERS`
- `agent_business_events` table dispatch added to `_write_row()`

### Fase B — MCP Tool `agent_event`

**`packages/mcp-server-plughub/src/tools/agent-events.ts`** (Task #90)
- `AgentEventToolInputSchema` — extends `AgentEventInputSchema` with `session_token` + `session_id`
- Full validation chain:
  - JWT decode (`verifySessionToken`) → resolves `tenant_id`, `agent_type_id`, `instance_id`
  - PII tag key check (case-insensitive against `AGENT_EVENT_PII_TAG_KEYS`)
  - Namespace isolation: `category_l1 === pool_id` from `session:{id}:meta` (enforced when pool_id resolvable)
  - Rate limit: `{tenant_id}:agent_event_count:{session_id}` Redis INCR, max 50 (env `AGENT_EVENT_RATE_LIMIT`)
- Context enrichment from Redis `session:{id}:meta`: `pool_id`, `skill_id`, `journey_id` (best-effort, non-fatal)
- Publishes to Kafka topic `agent.events` with full `AgentBusinessEvent` payload (category_l1..l4 pre-decomposed)
- Returns `{ event_id, category, value, emitted_at, session_event_count }`

**`packages/mcp-server-plughub/src/server.ts`** (Task #90)
- Import `registerAgentEventTools` + `AgentEventDeps`
- `agentEventDeps = { redis, kafka }` wired
- `registerAgentEventTools(server, agentEventDeps)` registered

---

## AI Gateway — AccountSelector: preferred_config_ids[] por InferenceRequest (2026-05-13)

### `packages/ai-gateway/src/plughub_ai_gateway/account_selector.py` (Task #76)
- `LLMAccount`: novo campo `config_id: str = ""` — GatewayConfig ID do agent-registry; identifica a qual configuração o API key pertence
- `AccountSelector.pick()`: novo parâmetro `preferred_config_ids: list[str] | None`. Quando não-vazio: primeira tentativa restrita a accounts cujo `config_id` está na lista; se nenhum disponível (throttled/RPM), cai graciosamente para o pool completo (degradação graciosa — nunca falha por causa do filtro)
- Método privado `_pick_least_loaded()` extraído para evitar duplicação entre preferred pass e full-pool pass

### `packages/ai-gateway/src/plughub_ai_gateway/models.py` (Task #76)
- `InferenceRequest`: novo campo `preferred_config_ids: list[str] = []` — lista de GatewayConfig IDs a preferir para esta chamada; populado a partir de `EvaluationCampaign.gateway_config_ids`

### `packages/ai-gateway/src/plughub_ai_gateway/inference.py` (Task #76)
- `InferenceEngine._call_with_fallback()`: aceita `preferred_config_ids` e passa para `account_selector.pick()` — tanto na chamada primária quanto na retry após throttle
- `InferenceEngine.infer()`: extrai `req.preferred_config_ids` e passa para `_call_with_fallback()`

### `packages/ai-gateway/src/plughub_ai_gateway/config.py` (Task #76)
- `Settings`: dois novos campos `anthropic_config_ids: str = ""` e `openai_config_ids: str = ""` (env `PLUGHUB_ANTHROPIC_CONFIG_IDS`, `PLUGHUB_OPENAI_CONFIG_IDS`)
- `get_anthropic_config_ids()` e `get_openai_config_ids()`: parseia CSV, retorna lista paralela às API keys; sempre padded com `""` para manter alinhamento index-a-index

### `packages/ai-gateway/src/plughub_ai_gateway/main.py` (Task #76)
- Passa `config_id=anthropic_config_ids[idx]` e `config_id=openai_config_ids[idx]` ao construir cada `LLMAccount`; backward compat total (sem config_id = `""` = sem preferência)

### `packages/ai-gateway/.../tests/test_account_selector.py` (Task #76)
- 3 novos testes: `preferred_config_ids` seleciona account correto, fallback quando preferido throttled, lista vazia = comportamento normal
- 3 novos testes: `get_anthropic_config_ids()` parsing, padding, vazio

**Uso típico — isolamento de avaliação:**
```
PLUGHUB_ANTHROPIC_API_KEYS=sk-realtime,sk-evaluation
PLUGHUB_ANTHROPIC_CONFIG_IDS=gcfg_realtime,gcfg_evaluation
```
Então `EvaluationCampaign.gateway_config_ids=["gcfg_evaluation"]` → `InferenceRequest.preferred_config_ids=["gcfg_evaluation"]` → avaliação usa `sk-evaluation` sem competir com agentes realtime.

---

## Arc 6 — EvaluationCampaign: evaluation_pool_id + evaluation_calendar_id + gateway_config_ids (2026-05-13)

### `packages/schemas/src/evaluation.ts` (Task #74)
- `EvaluationCampaignSchema` extended with 3 new optional fields:
  - `evaluation_pool_id?: z.string()` — pool under evaluation; used as hard filter in `check_sample`
  - `evaluation_calendar_id?: z.string()` — calendar for business-hours SLA deadline calculation in `compute_expires_at`; takes precedence over legacy `schedule.calendar_id`
  - `gateway_config_ids?: z.array(z.string()).default([])` — GatewayConfig IDs allowed for the evaluation's AI agents (reserved for AI Gateway routing)

### `packages/evaluation-api/src/plughub_evaluation_api/db.py` (Task #75)
- Idempotent DDL migration via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for all 3 columns in `evaluation.campaigns`
- `create_campaign()`: accepts and inserts `evaluation_pool_id`, `evaluation_calendar_id`, `gateway_config_ids`
- `update_campaign()`: allowed-set extended with the 3 new fields
- `list_campaigns()`: optional `evaluation_pool_id` query filter added

### `packages/evaluation-api/src/plughub_evaluation_api/router.py` (Task #75)
- `CampaignCreate` and `CampaignUpdate` Pydantic models: added `evaluation_pool_id: str | None`, `evaluation_calendar_id: str | None`, `gateway_config_ids: list[str] = []`
- `GET /v1/evaluation/campaigns`: `evaluation_pool_id` query param forwarded to db filter
- `POST /v1/evaluation/campaigns/check-sample`: hard filter — if campaign has `evaluation_pool_id` and `session_meta.pool_id` doesn't match, returns `should_sample: false` immediately (before statistical sampling)

### `packages/evaluation-api/src/plughub_evaluation_api/sampling.py` (Task #75)
- `compute_expires_at()`: uses `campaign.evaluation_calendar_id` with fallback to legacy `schedule.calendar_id`

### `packages/platform-ui/src/types/index.ts` (Task #77)
- `EvaluationCampaign` interface: added `evaluation_pool_id?: string`, `evaluation_calendar_id?: string`, `gateway_config_ids?: string[]`

### `packages/platform-ui/src/modules/evaluation/CampaignsPage.tsx` (Task #77)
- `CreateModal`: added `usePoolOptions` hook (fetches `/v1/operational/pools`) and `useCalendarOptions` hook (fetches `/v1/calendars?tenant_id=`)
- Two new selectors in modal form: **Pool avaliado** (hard filter for sampling) + **Calendário de SLA** (business-hours deadline)
- New values passed to `createCampaign()` as `evaluation_pool_id` and `evaluation_calendar_id`
- Detail panel: two new info cards showing pool and calendar for selected campaign

### `packages/platform-ui/src/i18n/locales/{en,pt-BR}/evaluation.json` (Task #77)
- Added keys under `campaigns.modal`: `evaluationPoolLabel`, `selectPool`, `evaluationCalendarLabel`, `selectCalendar`
- Added keys under `campaigns.detail`: `evaluationPool`, `evaluationCalendar`, `noPool`, `noCalendar`

---

## Receive Step — Full Implementation + Bug Fixes (2026-05-12)

### `packages/skill-flow-engine/src/steps/receive.ts`
- **Sentinel clearing fix**: ao retornar com sucesso do BLPOP, limpa todas as chaves `*:__notified__` de `ctx.state.results` antes de retornar o payload. Sem essa limpeza, em flows cíclicos (receive → notify → receive) o sentinel `ecoar_mensagem:__notified__` = `"completed"` permanecia após a primeira iteração e bloqueava silenciosamente todas as execuções subsequentes do `notify`.
- **Activity flag (CrashDetector)**: seta `receive:active:{tenant}:{session}:{instance}` com TTL 30s e renova via `setInterval(15s)` durante o BLPOP — evita re-enfileiramento falso por expiração de heartbeat.
- **Lock renewal**: chama `ctx.renewLock(timeoutSec + 60)` antes do BLPOP; aborta graciosamente se o lock foi assumido por outra instância (crash recovery).
- **Inline notify (pre-block)**: envia `step.notify.message` com `visibility: agents_only` (padrão) antes de registrar no HASH e bloquear — sinaliza prontidão ao agente convidante.
- **Ciclos declarativos**: contador `_receive_iterations_{stepId}` em `pipeline_state.results`; ao atingir `max_iterations` zera o contador e retorna `on_max_iterations`.
- **BLPOP duplo**: monitora `receive:result:{sid}:{iid}` (evento) e `session:closed:{sid}` (sessão fechada) simultaneamente; retorna `on_disconnect` quando a sessão fecha durante espera.

### `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py`
- **Fix double re-arm para mensagens de customer**: em `process_contact_event`, o branch `if event_type == "message_sent"` adicionou um guard `if _ms_author_role == "customer": return` — mensagens de customer já são roteadas para `receive:waiting` por `process_inbound`; sem o guard, o evento analytics publicado ao Kafka (`conversations.events`) era consumido e roteava novamente, causando dois `BLPOP` wakes por mensagem de customer e dois bubbles `[Echo] aguardando...`.
- **`_route_to_receive_waiting`**: função helper que roteia eventos stream para instâncias bloqueadas em receive; filtra por `event_types`, `author_role`, `visibility`; echo suppression via `instance_id_of_author`; LPUSH em `receive:result:{sid}:{iid}`.
- **Bridge contract**: em cada `message_sent` de agente (publicado por `notification_send` ou `message_send` via `conversations.events` Kafka), o bridge roteia para os waiting receivers que passam no filtro; customer messages chegam diretamente de `process_inbound`.

### `packages/platform-ui/Dockerfile`
- Adicionado `resolver 127.0.0.11 valid=10s ipv6=off;` no bloco `server {}` do nginx — previne caching de IPs de containers após rebuilds (Docker DNS resolver interno).

### `packages/skill-flow-engine/skills/agente_evaluador_echo_v1.yaml`
- Skill de teste para o mecanismo receive → notify → receive (cyclic DAG).
- Ecoa cada mensagem de `primary` ou `customer` como nota `agents_only`.
- Valida: registro em `receive:waiting`, LPUSH do bridge, echo suppression, `inviter_participant_id` no ContextStore.
- Ativação via pool hook `on_human_start` de `retencao_humano`.
- `max_iterations: 100`, `timeout_s: 600`.

### `packages/schemas/src/skill.ts` (Task #79)
- `ReceiveStepFilterSchema`: `author_role?`, `visibility?`, `event_types?`
- `ReceiveStepNotifySchema`: `message`, `visibility?`
- `ReceiveStepSchema`: `type: "receive"`, `filter?`, `notify?`, `timeout_s?`, `max_iterations?`, `on_max_iterations?`, `output_as?`, `on_message`, `on_timeout?`, `on_disconnect?`, `on_failure`
- `StepSchema` union atualizado para incluir `ReceiveStepSchema`

### `packages/skill-flow-engine/src/redis-keys.ts` (Task #80)
- `receiveResult(sessionId, instanceId)` → `receive:result:{sid}:{iid}`
- `receiveWaiting(sessionId)` → `receive:waiting:{sid}`
- `sessionClosed(sessionId)` → `session:closed:{sid}`
- `activeInstance(tenantId, sessionId, instanceId)` → `receive:active:{tid}:{sid}:{iid}`

---

## Arc 10 Phase E — Dashboard Journey Cards (2026-05-11)

### `packages/analytics-api/src/plughub_analytics_api/display_formatters.py`
- `fmt_journey_active_count()`: MetricCardData — contagem de jornadas ativas com tendência vs período anterior (query `argMax(status)` no `journey_events` FINAL)
- `fmt_journey_resolution_rate()`: BarChartData — taxa de resolução (%) por `skill_id`; denominador = apenas jornadas terminais (`completed/failed/cancelled`)
- `fmt_journey_funnel()`: DonutData — distribuição de jornadas por status mais recente
- `fmt_journey_median_duration()`: BarChartData — duração mediana (p50) em minutos por `skill_id`; calcula `max(event_time) - min(event_time)` por jornada; filtra jornadas terminais
- Todos os formatters aceitam `skill_id: str | None` como filtro opcional
- Helpers privados `_fetch_journey_*_sync()` com queries ClickHouse `argMax` sobre `journey_events FINAL`

### `packages/analytics-api/src/plughub_analytics_api/display.py`
- 4 novos endpoints adicionados: `GET /reports/display/journey-active-count`, `journey-resolution-rate`, `journey-funnel`, `journey-median-duration`
- Docstring do módulo atualizada (14 rotas total)
- Import dos 4 novos formatters

### `packages/platform-ui/src/dashboard/catalog.ts`
- 4 novas entradas em `ENDPOINT_CATALOG`:
  - `journey-active-count` → `metric_card` (3×2) — param fixo `skill_id`
  - `journey-resolution-rate` → `bar_chart` (6×4) — param fixo `skill_id`
  - `journey-funnel` → `donut` (4×3) — param fixo `skill_id`
  - `journey-median-duration` → `bar_chart` (6×4) — param fixo `skill_id`

---

## Arc 10 Phase D.5 — Journey Spec Refinements (2026-05-11)

### `packages/schemas/src/journey.ts`
- `JourneyEventSchema`: campos opcionais `current_step`, `session_outcome`, `session_started_at`, `session_ended_at` adicionados ao tipo `journey_session_linked`

### `packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py`
- `emit_journey_session_linked()`: aceita `current_step`, `session_outcome`, `session_started_at`, `session_ended_at` (todos opcionais); inclui no payload quando presentes

### `packages/workflow-api/src/plughub_workflow_api/router.py`
- `respond_collect`: passa `current_step = instance.get("current_step")` para `emit_journey_session_linked` — captura o passo do workflow no momento em que a sessão de collect é vinculada

### `packages/workflow-api/src/plughub_workflow_api/journey_router.py`
- `JourneyLinkSessionRequest`: campos opcionais `current_step`, `session_outcome`, `session_started_at`, `session_ended_at`
- `link_session` endpoint: repassa todos os campos para `emit_journey_session_linked`

### `packages/analytics-api/src/plughub_analytics_api/clickhouse.py`
- `_DDL_JOURNEY_EVENTS`: 4 colunas novas — `current_step Nullable(String)`, `session_outcome Nullable(String)`, `session_started_at Nullable(DateTime64(3))`, `session_ended_at Nullable(DateTime64(3))`
- `_JOURNEY_EVENT_COLS`: lista de colunas atualizada
- `_journey_event_row()`: popula as 4 novas colunas do dict de entrada

### `packages/analytics-api/src/plughub_analytics_api/models.py`
- `parse_journey_event()`: extrai `current_step`, `session_outcome`, `session_started_at`, `session_ended_at` do payload Kafka

**Regra de interseção confirmada:** `db_create_journey` e MCP `journey_start` nunca tocam `sessions.journey_id` — verificado. Sessions só recebem `journey_id` via `collect.events` processado pelo Core (collect sessions exclusivamente). Múltiplas journeys podem compartilhar o mesmo `origin_session_id` sem conflito.

---

## Arc 10 Phase D — Console Journey Integration (2026-05-11)

### `packages/platform-ui/src/modules/agent-assist/types.ts`
- `PoolInfo`: campo `mentionable_journeys?: string[]` adicionado

### `packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx`
- `fetchPools`: extrai `mentionable_journeys` do response da agent-registry e povoa `PoolInfo`

### `packages/platform-ui/src/modules/agent-assist/components/ActionBar.tsx`
- `IniciarProcessoButton`: dropdown de skill_ids de `mentionable_journeys` com label humanizado (remove prefixo `skill_` e sufixo `_vN`)
- `ActionBarProps`: props `mentionableJourneys?` e `onIniciarProcesso?` adicionadas
- Botão "🗺️ Processo ▾" renderizado na barra de ações quando `onIniciarProcesso` e `mentionableJourneys.length > 0`

### `packages/platform-ui/src/modules/agent-assist/components/tabs/HistoricoTab.tsx`
- `HistoricoTabProps`: campo `tenantId?: string | null` adicionado
- `useCustomerJourneys(tenantId, customerId)`: hook local que consulta `GET /analytics/reports/journeys` filtrando por `customer_id`; retorna apenas jornadas `active` e `suspended`
- `OpenJourneys`: seção "Processos em aberto" com badges coloridos por status, renderizada antes de "Contatos anteriores"

### `packages/platform-ui/src/modules/agent-assist/AgentAssistPage.tsx`
- `mentionableJourneys`: estado derivado de `availablePools` filtrado pelo `poolId` do contato selecionado
- `handleIniciarProcesso(skillId)`: `POST /v1/journeys` com `{ tenant_id, skill_id, session_id }` + toast feedback
- `ActionBar`: props `mentionableJourneys` e `onIniciarProcesso` conectadas
- `HistoricoTab`: prop `tenantId` conectada

### `packages/platform-ui/src/modules/agent-flow/ProcessosPage.tsx`
- `MergeButton`: componente dropdown "⛓ Unir jornadas" adicionado ao painel de detalhes de jornadas no tab Jornadas (visível apenas para jornadas `active`/`suspended`; chama `POST /v1/journeys/merge`; remove itens sem candidatos possíveis)

---

## Arc 10 Phase C — Journey Monitor (ProcessosPage) (2026-05-11)

### `packages/analytics-api/src/plughub_analytics_api/reports.py`
- Import `query_journeys_report` adicionado
- Novo endpoint `GET /reports/journeys`: filtra por `skill_id`, `status`, `customer_id`, `from_dt/to_dt`, paginação; retorna `data` (journey summaries), `kpis` (por skill_id), `meta`; status filter via HAVING sobre estado agregado

### `packages/platform-ui/src/modules/workflows/api/hooks.ts`
- Tipos `JourneyStatus`, `Journey`, `JourneyKpi` adicionados
- `useJourneys(tenantId, skillId?, status?)`: polling 15s para `GET /analytics/reports/journeys`; retorna `{ journeys, kpis, loading, refresh }`
- `useJourney(journeyId)`: fetch sob demanda de `GET /v1/journeys/{id}` (workflow-api)

### `packages/platform-ui/src/modules/agent-flow/ProcessosPage.tsx`
- Reestruturado com dois tabs: **Jornadas** (tab padrão) e **Instâncias**
- `JourneysTab`: KPI strip por skill_id (total, taxa resolução, duração p50) + lista de jornadas com filtro de status + painel de detalhes (timeline, session_count, sessão de origem, workflow_instance_id, customer_id)
- `InstancesTab`: visão existente de instâncias de workflow preservada sem alterações funcionais
- Ambos os tabs mantêm refresh manual acessível

---

## Arc 10 Phase B — Journey Automatic Session Linking (2026-05-11)

### `packages/workflow-api/src/plughub_workflow_api/db.py`
- Migration `ALTER TABLE workflow.collect_instances ADD COLUMN IF NOT EXISTS journey_id UUID` — propaga journey_id do instance para o collect
- `_row_to_instance()`: expõe `journey_id` no dict serializado
- `_row_to_collect()`: expõe `journey_id` no dict serializado
- `db_create_collect()`: aceita `journey_id: str | None = None` e persiste na tabela
- `db_create_journey_for_instance()` (NEW): cria Journey vinculada a instance já existente; idempotente (retorna journey existente se instance já tem `journey_id`); transacional com `SELECT FOR UPDATE`; deriva `skill_id = flow_id`, back-links `instances.journey_id`

### `packages/workflow-api/src/plughub_workflow_api/config.py`
- Adicionado `journey_topic: str = "journey.events"` a `Settings`

### `packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py`
- `emit_collect_requested()`: aceita `journey_id: str | None = None`; inclui no payload quando presente

### `packages/workflow-api/src/plughub_workflow_api/router.py`
- `persist_collect`: lê `journey_id` do instance e repassa para `db_create_collect()` e `emit_collect_requested()`
- `respond_collect`: após `emit_collect_responded`, se `collect.journey_id` e `body.session_id`, emite `journey_session_linked` via `emit_journey_session_linked()` (falha não bloqueia resposta)
- Imports: `db_create_journey_for_instance`, `emit_journey_session_linked` adicionados

### `packages/workflow-api/src/plughub_workflow_api/journey_router.py`
- Novo endpoint `POST /v1/journeys/from-instance/{instance_id}`: chamado pelo skill-flow-worker quando `creates_journey:true`; idempotente; emite `journey_started` apenas para journeys recém-criadas
- Import `db_create_journey_for_instance` adicionado

### `packages/skill-flow-worker/src/workflow-client.ts`
- `WorkflowInstance`: campo `journey_id?: string` adicionado
- `WorkflowClient.createJourneyForInstance(instanceId, tenantId)` (NEW): `POST /v1/journeys/from-instance/{id}` com headers `x-tenant-id` + `x-internal:1`

### `packages/skill-flow-worker/src/engine-runner.ts`
- `EngineRunner.runInstance()`: antes de `engine.run()`, verifica `flowDefinition.creates_journey === true && !instance.journey_id`; se verdadeiro, chama `workflowClient.createJourneyForInstance()`; falha é não-fatal (log + continua sem journey)

---

## Arc 10 Phase A — Journey Backend Foundation (2026-05-11)

### `packages/schemas/src/journey.ts` (NEW)
- `JourneyStatusSchema`, `JourneySchema` — entidade Journey com todos os campos
- `JourneyEventTypeSchema`, `JourneyEventSchema` — 9 tipos de eventos Kafka
- `JourneyStartInputSchema/OutputSchema`, `JourneyLinkSessionInputSchema`, `JourneyMergeInputSchema` — contratos MCP tool
- Exportações adicionadas a `src/index.ts`

### `packages/workflow-api` — Journey CRUD + Router
- **`db.py`**: DDL `workflow.journeys` (self-referential FK `merged_into_journey_id`); migration `workflow.instances.journey_id`; `_row_to_journey()`; CRUD: `db_create_journey`, `db_get_journey`, `db_list_journeys`, `db_set_journey_workflow_instance`, `db_update_journey_status`, `db_merge_journeys` (transactional, protege contra re-merge)
- **`kafka_emitter.py`**: `emit_journey_started`, `emit_journey_session_linked`, `emit_journey_status_changed`, `emit_journey_merged`
- **`journey_router.py`** (NEW): `APIRouter(prefix="/v1/journeys")` — 5 endpoints: POST (create+trigger), GET /{id}, GET (list), POST /{id}/link-session, POST /{id}/merge, PATCH /{id}/status; helper `_trigger_workflow()` chama `/v1/trigger`
- **`main.py`**: `journey_router` registrado

### `packages/mcp-server-plughub/src/tools/journey.ts` (NEW)
- `journey_start` — cria Journey + dispara workflow; retorna `journey_id` + `workflow_instance_id`
- `journey_link_session` — vincula sessão adicional à journey
- `journey_merge` — absorve secondary no primary (irreversível); Phase D
- Registrado em `server.ts` com `JourneyDeps` (`WORKFLOW_API_URL`)

### `packages/analytics-api` — Kafka consumer + ClickHouse
- **`models.py`**: `parse_journey_event()` — mapeia 8 event types para `journey_events` table; status derivado de `_JOURNEY_STATUS_MAP`
- **`clickhouse.py`**: `_DDL_JOURNEY_EVENTS` (ReplacingMergeTree ORDER BY tenant_id, event_id); `_journey_event_row()`; `AnalyticsStore.insert_journey_event()`; DDL adicionado a `_ALL_DDL`
- **`consumer.py`**: tópico `journey.events` adicionado a `_TOPICS`, `_PARSERS`; `journey_events` adicionado a `_write_row`

---

## Arc 10 — Journey Merge/Split Spec (2026-05-11)

**`docs/modules/arc10-journey.md`**
- Status `merged` adicionado ao enum; campo `merged_into_journey_id` na entidade
- Kafka event types: `journey_merged` + `journey_split` (futuro — Phase F)
- Phase D: `journey_merge(journey_id_primary, journey_id_secondary)` — operação irreversível, secondary vira read-only
- Phase F: `journey_split` com 3 open decisions documentadas
- Invariantes: merged journey é read-only; merge é irreversível

**`TODO.md`**: Phase D e Phase F atualizados

---

## Skill Flow Editor — Folder Tree Sidebar (2026-05-11)

**`packages/platform-ui/src/modules/skill-flows/SkillFlowsPage.tsx`**
- Campo `folder?: string` no YAML lido como metadado de view-only (path 2-níveis, ex: `"clientes/retencao"`)
- `buildFolderTree(skills)` separa skills por `classification.type` em raízes Agentes/Workflows; constrói árvore de pastas
- Sidebar expandível: raízes + pastas com `paddingLeft` por profundidade; busca colapsa para lista plana
- Estado: `expandedRoots: Set<string>`, `expandedFolders: Set<string>` (ambos abertos por padrão)

---

## AgentFlowDeployPage — Skill Selector Grouping (2026-05-11)

**`packages/platform-ui/src/modules/agent-flow/AgentFlowDeployPage.tsx`**
- `groupSkillsForSelect(skills)` → `<optgroup>` HTML nativo com labels "Agentes", "Agentes / pasta", "Workflows / pasta"
- Skills separadas por `classification.type === 'orchestrator'` antes do agrupamento por `folder`

---

## AgentFlowDeployPage — Role-Based Access Control (2026-05-11)

**`packages/platform-ui/src/modules/agent-flow/AgentFlowDeployPage.tsx`**
- `hasEditRole(roles)` — `developer | admin` → pode configurar slot Próxima (Edit button)
- `hasOperateRole(roles)` — `operator | supervisor | admin` → pode promover e reverter
- Editar slot Próxima: `onEdit` prop do `SlotCard` só passada quando `canEdit`
- Botões Promover/Rollback: `disabled={!canOperate}` + `title` explicativo no hover
- Banner de permissão: exibido quando o usuário tem apenas um dos dois papéis, explicando o que pode e o que não pode fazer (dev vê azul, operator/supervisor vê verde; admin não vê banner)
- Lógica usa `session.roles[]` (array multi-role) — usuário com ambos os papéis tem acesso completo

---

## Arc 9 — Monitor + Console Scope Filtering (2026-05-11)

Transparently restricts what a supervisor sees in the live Monitor and Console based on JWT claims from Arc 9. Admin (empty arrays) is unrestricted.

**`packages/platform-ui/src/types/index.ts`**
- `Session.supervisedAgentTypes: string[]` adicionado — `[]` = irrestrito (admin); não-vazio = escopo Arc 9

**`packages/platform-ui/src/auth/AuthContext.tsx`**
- `CurrentUser.supervisedAgentTypes: string[]` adicionado
- `buildSession()` params: `supervised_agent_types?: string[]` opcional (backwards-compat); fallback `[]` para JWTs legados
- `currentUser` memo inclui `supervisedAgentTypes`

**`packages/platform-ui/src/modules/service/MonitorPage.tsx`**
- Heatmap: `pools` filtrado por `session.accessiblePools` antes de passar para `HeatmapGrid` — supervisores com escopo só vêem seus pools

**`packages/platform-ui/src/modules/config-recursos/HumanAgentsPage.tsx`**
- `LiveTab`: instâncias filtradas por `session.supervisedAgentTypes` após load — supervisores vêem só agentes do seu escopo

**`packages/platform-ui/src/modules/config-recursos/InstancesPage.tsx`**
- Instâncias AI filtradas por `session.supervisedAgentTypes` após load

*Console (AgentAssistPage)*: já filtrado via `accessiblePools` em `AgentAssistContext.fetchPools` — sem mudança necessária.

---

## Masking Bloco 2b — AgentAssistPage Token Rendering (2026-05-11)

**`packages/platform-ui/src/modules/agent-assist/components/ChatArea.tsx`**
- `useMaskingDisplayRules()` chamado uma vez no componente; `maskingRules` passado para cada `MessageBubble`

**`packages/platform-ui/src/modules/agent-assist/components/MessageBubble.tsx`**
- Prop `maskingRules?: MaskingRulesMap` adicionada
- `{message.text}` substituído por `{renderWithTokens(message.text, maskingRules)}` — tokens aparecem como chips no console do operador

---

## Masking Bloco 2 — Channel-Aware Display Architecture (2026-05-11)

**`packages/platform-ui/src/components/MaskedToken.tsx`** *(novo)*
- `TOKEN_RE`, `parseToken()` — regex e parser para formato `[category:tk_xxx:display]`
- `MaskedToken` component — chip estilizado com ícone de categoria, label e valor parcial; suporta `display_screen: display_partial | full_mask | hidden`
- `renderWithTokens(text, rules?)` — split de texto com substituição de tokens por `<MaskedToken>` components; retorna `React.ReactNode[]`
- `useMaskingDisplayRules()` — hook que lê namespace `masking` do Config API e retorna `MaskingRulesMap` per-category; defaults aplicados quando ausente
- `DEFAULT_DISPLAY_RULE` — `display_partial / silence / echo_to_customer=false / echo_to_operator=true`

**`packages/platform-ui/src/modules/service/components/SessionTranscript.tsx`**
- `EntryRow` migrado: usa `renderWithTokens(text, maskingRules)` ao invés de `extractText` raw — tokens aparecem como chips ao invés de strings brutas
- Removida `maskSensitiveContent()` (regex keyword crude) — substituída por token-aware rendering
- `useMaskingDisplayRules()` chamado no componente raiz; `maskingRules` passado para todos os `EntryRow`

**`packages/platform-ui/src/modules/masking/MaskingPage.tsx`**
- Section 5 "Display Rules by Category": grid de cards por categoria com 4 controles cada — `display_screen` (select), `display_voice` (select), `echo_to_customer` (toggle), `echo_to_operator` (toggle)
- Salva em namespace `masking`, chave `rule.{category}` no Config API
- `MiniToggle` component, `selectStyle`, `getMaskingRule()` helper, `saveMaskingRule()`

---

## Masking Bloco 1 — Security Fixes (2026-05-11)

Correções de segurança identificadas no mapeamento de vetores de vazamento. Nenhuma mudança de API ou schema — apenas remoção de exposição de dados sensíveis em logs.

**`packages/mcp-server-plughub/src/server.ts`**
- `menu_submit` handler: removido `resultText` dos três `console.log` (linhas 844, 854, 928). Valor coletado no menu (pode ser senha, CPF, etc.) não aparece mais em logs de servidor. Contexto de debug preservado (sessionId, resultKey, menu_id, pushed).

**`packages/sdk/src/mcp-interceptor.ts`**
- Fallback `AUDIT_WRITE_FAILED`: `input_snapshot` e `output_snapshot` agora são redactados como `"[REDACTED]"` antes de ir para `console.error`. Metadados de auditoria (tenant, session, tool, duration, allowed) são preservados para rastreabilidade LGPD.
- `_collectTokenPaths(value, path)`: novo método síncrono — varre os args originais (antes de resolução) e retorna caminhos de campo que contêm tokens mascarados `[category:tk_xxx:display]`.
- `_sanitizeSnapshotForAudit(snapshot, maskedPaths)`: substitui valores de campos mascarados por `"[MASKED]"` no snapshot de auditoria.
- `_audit()`: aceita `masked_input_fields?: string[]`; quando presente, o `input_snapshot` armazenado no `AuditRecord` tem os campos mascarados substituídos — os valores originais não chegam ao Kafka/ClickHouse. `masked_input_fields` lista quais campos eram sensíveis sem expor seus valores.
- Call site pré-resolution: `maskedInputFields` coletado dos args originais antes de `_resolveArgsTokens()`.

---

## Arc 9 — Agent Groups & Supervisor Scope (2026-05-11)

Implementação completa da entidade `AgentGroup` como camada de gestão de pessoas ortogonal a Pool.

**auth-api** (`packages/auth-api/src/plughub_auth_api/`)
- `db.py`: 5 novas tabelas DDL em schema `auth` (`agent_groups`, `agent_group_members`, `agent_group_users`, `agent_group_supervisors`, `agent_group_shifts`); funções CRUD completas para cada entidade; `resolve_supervisor_scope(pool, user_id, role)` — resolução de shifts com conversão de DOW (spec 0=Sun ↔ Python weekday 0=Mon via `(dow+1)%7`); sentinela `["__no_active_shift__"]` para grupos ativos sem membros configurados
- `jwt_utils.py`: `create_access_token()` estendido com `supervised_groups`, `supervised_agent_types`, `supervised_user_ids`; todos emitidos como `[]` (não `null`) para compatibilidade JSON
- `router.py`: `_make_token_response` convertida para `async`; chama `resolve_supervisor_scope` no login e no refresh; claims denormalizados no JWT
- `groups_router.py` (novo): `APIRouter(prefix="/v1/groups")`; CRUD de grupos + sub-recursos `/members`, `/users`, `/supervisors`, `/shifts`; todos os endpoints autenticados por `X-Admin-Token`
- `main.py`: `groups_router` registrado

**analytics-api** (`packages/analytics-api/src/plughub_analytics_api/`)
- `pool_auth.py`: `PoolPrincipal` estendido com `supervised_agent_types: list[str] | None`; decodificação do claim JWT no `optional_pool_principal()`
- `reports_query.py`: `_apply_agent_scope()` para filtro direto (segments, performance, availability); `_agent_scope_session_join()` para sessões via LEFT JOIN em `segments FINAL`; os 5 pares de funções `query_*/fetch_*` atualizados com parâmetro `supervised_agent_types`
- `reports.py`: os 5 endpoints de relatório passam `supervised_agent_types = pool_principal.supervised_agent_types`

**platform-ui** (`packages/platform-ui/src/`)
- `modules/groups/GroupsPage.tsx` (novo): lista de grupos com drawer lateral; 4 tabs — Info (edição de nome/descrição), Members (agent_type_ids + is_human), Supervisors (users), Shifts (dias da semana + janela horária + timezone + toggle ativo)
- `i18n/locales/en/groups.json` + `pt-BR/groups.json` (novos): namespace `groups` completo
- `i18n/locales/*/shell.json`: chave `nav.groups` adicionada
- `i18n/index.ts`: namespace `groups` registrado
- `shell/Sidebar.tsx`: entrada "Groups" adicionada ao grupo Configuração (ABAC `config.users`)
- `app/routes.tsx`: rota `config/groups` mapeada para `GroupsPage`

**CLAUDE.md**: seção Arc 9 adicionada; item removido do `## Pending`

---

## Backend-dependent Pages — AgentsPage Relatório + EventsPage + AnaliseProcessosPage (2026-05-09)

Implementação completa das últimas três páginas que dependiam de endpoints backend não existentes:

**AgentsPage sub-tab Relatório** (`packages/platform-ui/src/modules/contacts/AgentsPage.tsx`)
- Nova tab "Relatório" ao lado de "Monitor" com query a `GET /reports/agent-performance/daily` (endpoint já existia)
- Filtros: range de data, pool (dropdown dinâmico), agente (dropdown populado pelos dados)
- KPI strip: total sessões, resolução média ponderada, escalonamento médio ponderado, tempo médio
- Tabela sortável com mini-bars para resolução/escalonamento/transferência/humano; export CSV

**EventsPage backend** (`packages/analytics-api/`)
- `query_events` em `reports_query.py`: UNION ALL de 7 branches (session_opened, session_closed, message_sent, agent_done/routed, agent_pause, agent_ready, workflow events) com `CAST(NULL AS Nullable(String))` para type safety no ClickHouse
- Endpoint `GET /reports/events` com filtros: `session_id`, `pool_id`, `channel`, `event_type`; pool_scope RBAC via `accessible_pools`; paginação padrão
- Otimização: quando `event_type` é especificado, apenas os branches relevantes são incluídos no UNION
- Frontend `EventsPage.tsx` já existia e estava aguardando este endpoint

**AnaliseProcessosPage** (full stack)
- `query_workflow_summary` em `reports_query.py`: agrega `workflow_events` por `flow_id` ou `campaign_id` usando `countDistinctIf`; retorna `completion_rate`, `failure_rate`, `avg_duration_ms` (calculados em Python pós-query para evitar divisão por zero no SQL)
- Endpoint `GET /reports/workflow-summary?group_by=flow_id|campaign_id`
- `AnaliseProcessosPage.tsx`: substituído placeholder por página completa com filter bar (data + group_by), 5 KPI cards, tabela sortável com `OutcomeBar` (distribuição de outcomes) + `RateBar` (conclusão/falha), export CSV

---

## Dashboard #35 — Part 4: Analytics-API Display Endpoints (2026-05-09)

10 endpoints `GET /reports/display/*` no `analytics-api`, com formatters centralizados em `display_formatters.py`. Cada endpoint retorna o shape exato do DisplayTool correspondente — os cards new-format passam a mostrar dados reais de ClickHouse/Redis.

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `packages/analytics-api/src/plughub_analytics_api/display_formatters.py` | Funções de query + formatação para cada endpoint; `_fmt_dt()` aceita ISO8601 e `YYYY-MM-DD`; `_prev_period()` calcula período anterior para KPI trend; `_buckets_to_chart()` converte timeseries buckets para `BarChartData`/`LineChartData` shape |
| `packages/analytics-api/src/plughub_analytics_api/display.py` | FastAPI router `prefix="/reports/display"` com 10 rotas; parâmetros `from`/`to` (alias para datas do FilterBar); `pool_id` opcional; auth via `optional_pool_principal` |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `packages/analytics-api/src/plughub_analytics_api/main.py` | `from .display import router as display_router` + `app.include_router(display_router)` |

**Endpoints implementados:**

| Endpoint | Tool compatível | Shape retornado |
|---|---|---|
| `GET /reports/display/session-volume` | bar_chart, line_chart | `BarChartData` (`x_labels` + `series`) |
| `GET /reports/display/handle-time` | line_chart, bar_chart | `BarChartData` (`x_labels` + `series`) |
| `GET /reports/display/evaluation-score` | line_chart, bar_chart | `BarChartData` (`x_labels` + `series`) |
| `GET /reports/display/sessions-by-pool` | bar_chart | `BarChartData` (pool_id como categorias) |
| `GET /reports/display/outcome-distribution` | donut | `DonutData` (`labels` + `values`) |
| `GET /reports/display/pool-status` | table | `TableData` (Redis snapshots, live) |
| `GET /reports/display/agent-performance` | table | `TableData` (ClickHouse segments) |
| `GET /reports/display/kpi-sessions` | metric_card | `MetricCardData` com trend vs período anterior |
| `GET /reports/display/kpi-resolution` | metric_card | `MetricCardData` (ratio 0-1, format: percent) |
| `GET /reports/display/kpi-score` | metric_card | `MetricCardData` com trend vs período anterior |

**Reutilização:** timeseries endpoints delegam para `query_volume_timeseries`, `query_handle_time_timeseries`, `query_score_timeseries` existentes; agent-performance delega para `query_agent_performance_report`; pool-status reutiliza `get_pool_snapshots` do Redis.

---

## Dashboard #35 — Part 3: Runtime Filters (FilterBar + GlobalFilters) (2026-05-09)

Sistema de filtros globais do dashboard: admin configura quais filtros o template expõe; usuário controla valores em runtime via `FilterBar`; cada card `NewDashboardCard` lê os valores via `buildQueryUrl()` na hora do fetch.

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `src/dashboard/FilterBar.tsx` | `FilterBar` component: renderiza controles de `date`, `select`, `multi_select`; botão ↺ Limpar quando há filtros ativos; `FILTER_PRESETS` com Período e Pool |
| `src/dashboard/FilterConfigPanel.tsx` | Painel admin (edit mode, sidebar): adicionar/remover presets de filtros; editor de opções para filtros select |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `src/modules/dashboards/DashboardsPage.tsx` | `globalFilters` state (carregado do template); `runtimeFilters` state (seed dos defaults); `setRuntimeFilter` / `resetRuntimeFilters`; `updateGlobalFilters` (sets dirty); `handleSave` persiste `global_filters`; `<FilterBar>` entre TopBar e grid; `<FilterConfigPanel>` no sidebar em edit mode; `runtimeFilters` passado para `<CardRenderer>` |

**Fluxo completo:** admin adiciona preset "Período" em edit mode → salva template com `global_filters: [{date_from}, {date_to}]` → usuário vê FilterBar com pickers → ao alterar datas, todos os cards new-format re-buscam via `buildQueryUrl()` com os novos valores. Cards legados (TimeseriesChart) ignoram `runtimeFilters` (mantêm seus próprios controles internos).

---

## Dashboard #35 — Part 2: AddCardModal 3-step + NewDashboardCard (2026-05-09)

Modal de criação de cards substituído por fluxo de 3 passos. Novos cards criados diretamente como `NewDashboardCard` (tool_id + query + tool_config).

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `src/dashboard/catalog.ts` | `ENDPOINT_CATALOG` — 10 endpoints `/reports/display/*` com `compatible_tools`, `configurable_params`, defaults de dimensão |
| `src/dashboard/AddCardModal.tsx` | Modal 3-step: (1) escolher métrica, (2) escolher visualização, (3) configurar título + params fixos; `onAdd(NewDashboardCard)` |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `src/modules/dashboards/DashboardsPage.tsx` | `CARD_PRESETS` / `DISPLAY_OPTIONS` / antigo `AddCardModal` removidos; `addCard(card: NewDashboardCard)` direto; imports limpos |

**Comportamento do modal:** `tenant_id` sempre fixo com o valor do tenant atual; `from`/`to` sempre runtime (filter_key `date_from`/`date_to`); `pool_id` fixo se preenchido pelo usuário, runtime caso contrário. Navegação Passo 1 → 2 → 3 com Voltar; indicador de progresso com dots.

---

## Dashboard #35 — Part 1: Display Tool Registry (2026-05-09)

Implementada a infraestrutura de Display Tools no `platform-ui` (sem dependência de backend). Cards antigos continuam funcionando sem alteração.

**Arquivos criados:**

| Arquivo | Descrição |
|---|---|
| `src/dashboard/tools/types.ts` | Contratos TypeScript: `DisplayTool`, `NewDashboardCard`, `CardQuery`, `QueryParam`, `GlobalFilter`, `buildQueryUrl()` + 5 data shapes |
| `src/dashboard/tools/MetricCardTool.tsx` | KPI com número grande, label e trend arrow (↑↓) |
| `src/dashboard/tools/BarChartTool.tsx` | Barras verticais via recharts, stacked opcional |
| `src/dashboard/tools/LineChartTool.tsx` | Linhas com pontos, eixo X com labels |
| `src/dashboard/tools/DonutTool.tsx` | Donut chart com legenda lateral e tooltip de % |
| `src/dashboard/tools/TableTool.tsx` | Tabela MxN com header fixo, scroll interno, ordenação client-side |
| `src/dashboard/tools/registry.ts` | Mapa `toolId → DisplayTool` + `normalizeCard()` com migration map dos 6 tipos antigos |
| `src/dashboard/CardRenderer.tsx` | Dispatcher: detecta `tool_id` (new path) vs `type` (legacy path); faz fetch + polling para new-format cards |

**Arquivos modificados:**

| Arquivo | Alteração |
|---|---|
| `src/types/index.ts` | `DashboardTemplate.cards` → `(DashboardCard | NewDashboardCard)[]`; `global_filters?`; re-exports de `NewDashboardCard`, `CardQuery`, `QueryParam`, `GlobalFilter` |
| `src/api/dashboard-hooks.ts` | `savePersonalLayout` e `loadPersonalLayout` aceitam `AnyDashboardCard[]` |
| `src/modules/dashboards/DashboardsPage.tsx` | `CardContent` removido; substituído por `<CardRenderer>`; `cards` state: `(DashboardCard | NewDashboardCard)[]`; card header title: suporte a ambos os formatos |

**Invariants mantidos:** cards antigos continuam sendo renderizados via `LegacyCardContent` → `TimeseriesChart`. `normalizeCard()` existe mas não é chamado ainda (Part 2). Nenhum endpoint `/reports/display/*` é requerido em Part 1.

---

## CLAUDE.md Otimização Fase 2 — docs/modules/ completo (2026-05-09)

Criados 5 novos arquivos em `docs/modules/` para seções que não tinham referência completa fora do CLAUDE.md:

| Arquivo criado | Conteúdo |
|---|---|
| `docs/modules/arc5-segments.md` | ContactSegment data model, ClickHouse schema, endpoints |
| `docs/modules/ai-gateway.md` | Multi-account rotation, AccountSelector, model profiles, sentiment emission |
| `docs/modules/arc8-agent-availability.md` | Pause tracking, Kafka events, ClickHouse schema, analytics endpoint |
| `docs/modules/usage-metering.md` | Dimensions, Redis counters, assertQuota pattern, cycle reset |
| `docs/modules/pricing.md` | Billing model, reserve pools, endpoints, quota side effects |
| `docs/modules/session-replayer.md` | Pipeline, Hydrator pattern, ReplayContext, timing replay, Comparison Mode |

CLAUDE.md atualizado: `→ See [docs/modules/xxx.md]` adicionado em cada seção que agora tem um arquivo dedicado. Seção "Pending — CLAUDE.md Otimização Fase 2" atualizada para Fase 3.

TODO.md: Fase 2 marcada como concluída; item Fase 3 mantido.

---

## Language Cleanup Phase 2 — ABAC field names (2026-05-09)

Renomeados os 3 identificadores ABAC em português que violavam a Language Rule (English in code):

| Antigo | Novo | Módulo |
|---|---|---|
| `relatorio` | `report` | `evaluation` |
| `recursos` | `resources` | `config` |
| `mascaramento` | `masking` | `config` |

**Arquivos alterados:**

- `infra/modules.yaml` — campos renomeados nas seções `evaluation` e `config`.
- `packages/auth-api/src/plughub_auth_api/db.py` — 3 migrations idempotentes adicionadas a `ensure_schema()`: renomeiam as chaves no JSONB `auth.users.module_config` (condição `? 'old_key'` garante no-op se já migrado).
- `packages/platform-ui/src/shell/Sidebar.tsx` — já usava nomes ingleses (`report`, `resources`, `masking`) desde a Language Cleanup Phase 1.
- `packages/platform-ui/src/i18n/locales/en/contacts.json` e `pt-BR/contacts.json` — chave `"relatorio"` → `"report"` (chave órfã, sem usages em código).
- `packages/platform-ui/src/i18n/locales/en/home.json` e `pt-BR/home.json` — chaves `"recursos"` → `"resources"` e `"recursosDesc"` → `"resourcesDesc"`.
- `packages/platform-ui/src/modules/home/HomePage.tsx` — `t('quickLinks.recursos')` → `t('quickLinks.resources')`, `t('quickLinks.recursosDesc')` → `t('quickLinks.resourcesDesc')`, `to="/config/recursos"` → `to="/config/resources"`.

**Comportamento no restart:** `_register_platform_modules()` já faz upsert do módulo inteiro com `ON CONFLICT DO UPDATE SET schema = EXCLUDED.schema`, então `auth.module_registry` é atualizado automaticamente. As migrations de `module_config` garantem que permissões existentes de usuários não são perdidas.

TODO.md: seção "Language Cleanup — Fase 2" removida.

---

## Task #173 — writeStreamEntry centralizado no mcp-server-plughub (2026-05-09)

Eliminados 4 pontos de `redis.xadd()` direto em `server.ts` que usavam formatos inconsistentes (campos `author` como JSON object sem campos flat `author_id`/`author_role`, ausência de `segment_id`). Todos migrados para `writeStreamEntry()` que garante validação Zod em runtime e layout canônico no stream.

Pontos migrados em `server.ts`:
- `writeParticipantEvent` — `participant_joined`/`participant_left` com `visibility: "all"`
- Mention dispatcher — `message` com `visibility: "agents_only"`
- Hook agent response path — `message` com `visibility: streamVis` (dinâmico)
- Normal message path — `message` com `visibility: "all"`

Resultado: `server.ts` não contém nenhum `xadd` direto. Todo entry no stream canônico passa por `writeStreamEntry()` com `author_id`/`author_role` flat fields obrigatórios — elimina fallbacks em `_parse_entry` do analytics-api.

---

## Quota namespace removido do Config API seed (2026-05-09)

As três entradas do namespace `quota` (`max_concurrent_sessions`, `llm_tokens_daily`, `messages_daily`) foram removidas do seed.py por serem código morto:

- Nenhuma é lida em runtime por nenhum componente via Config API
- `max_concurrent_sessions`: `checkConcurrentSessions()` lê diretamente `{tenant}:quota:max_concurrent_sessions` no Redis, que é escrito pelo pricing-api (não pelo Config API)
- `llm_tokens_daily` / `messages_daily`: conceitualmente incorretos no modelo multi-conta — limites por conta API não cabem como valor único global por tenant
- Quando o pricing for integrado, escreverá `{tenant}:quota:*` diretamente no Redis por plano ativado

Nota adicionada ao cabeçalho do seed.py documentando o fluxo correto.
TODO.md: seção "Quotas — refatoração" removida.

---

## Pool Registry routing_expression — verificado como já implementado (2026-05-09)

Code review confirmou que `routing_expression` está completamente suportado:
- `PoolRegistrationSchema` (schemas) — campo `routing_expression: RoutingExpressionSchema.optional()`
- Prisma schema — `routing_expression Json?`
- `validators/pool.ts` — `CreatePoolSchema`/`UpdatePoolSchema` herdam via extend/partial
- `routes/pools.ts` — create e update ambos persistem o campo corretamente

TODO.md: seção "Pool Registry — routing_expression field" removida.

---

## Task #41 — Remover category do AI Gateway + schemas (2026-05-09)

O AI Gateway é producer puro de dados de sentimento — não deve classificar scores em categorias. As faixas (satisfied/neutral/frustrated/angry) são configuráveis por tenant via Config API; qualquer classificação feita com valores hardcoded seria incorreta após mudança de configuração. A responsabilidade de classificar pertence ao consumer (analytics-api), que tem acesso às faixas corretas.

**Alterações:**

`packages/schemas/src/platform-events.ts`:
- `SentimentUpdatedEventSchema` — removido campo `category`. Comentário documenta explicitamente que a classificação é responsabilidade do consumer.

`packages/ai-gateway/src/plughub_ai_gateway/`:
- `sentiment_config.py` — deletado (classificação dinâmica não pertence ao AI Gateway).
- `sentiment_emitter.py` — removidos `_classify()`, import `sentiment_config`, campo `category` do payload Kafka, contagens por categoria (`satisfied/neutral/frustrated/angry`) do hash `sentiment_live`. Hash agora contém apenas `avg_score`, `score_total`, `count`, `last_session_id`, `updated_at`.
- `config.py` — removido `config_api_url` (não mais necessário).
- `main.py` — removidos import `sentiment_config`, `reload()` no startup, `refresh_loop()` background task e cancel no teardown.

TODO.md: seção "Sentimento — _classify() dinâmico" já havia sido removida na sessão anterior.

---

## Scheduled Deploy gap — verificado como já implementado (2026-05-09)

Code review revelou que o TODO "Skill Flow — Scheduled Deploy gap" já estava resolvido. Items confirmados como presentes:

- `_resolve_flow_definition()` em `workflow-api/router.py` (linhas 125–153): busca o skill pelo `flow_id` no agent-registry quando `metadata` não contém `flow_definition`, e injeta automaticamente antes de salvar a instância.
- Chamado em `trigger_workflow` via `resolved_metadata = await _resolve_flow_definition(...)`.
- `agent_registry_url` configurado em `workflow-api/config.py`.
- `skill_scheduled_deploy_v1.yaml` presente em `skill-flow-engine/skills/`.
- `scheduleSkillDeploy()` removido da UI no Task #33 (seção de agendamento foi retirada do `AgentFlowDeployPage`).

TODO.md: seção "Skill Flow — Scheduled Deploy gap" removida.

---

## Session TTLs dinâmicos via Config API (2026-05-09)

Eliminados ~25 literais `14400` hardcoded de 3 componentes. Todos os TTLs de sessão Redis agora lidos do namespace `session` do Config API no startup, com fallback silencioso aos valores default (14400s / 3600s).

**Arquivos modificados:**

`packages/orchestrator-bridge/`:
- `session_config.py` (novo) — `SessionConfigCache`, mesmo padrão do `RoutingConfigCache`. Singleton `session_config`. Defaults espelham `seed.py`.
- `main.py` — importa `session_config`; adiciona `CONFIG_API_URL` env var; função `_stl()` retorna TTL dinamicamente; substitui todos os literais `14400` por `_stl()`; carrega Config API no startup; `_handle_config_changed` agora aceita `http` e invalida + recarrega ao receber `namespace=session`.

`packages/conversation-writer/`:
- `config.py` — adiciona `config_api_url: str = "http://localhost:3500"`.
- `main.py` — função `_fetch_session_ttl()` busca `transcript_ttl_s` via `urllib.request` no startup; `RedisBuffer` criado com TTL dinâmico.

`packages/session-replayer/`:
- `stream_hydrator.py` — `StreamHydrator.__init__` recebe `ttl: int = HYDRATION_TTL_SECONDS`; usa `self._ttl` internamente.
- `replayer.py` — `Replayer.__init__` recebe `context_ttl: int = REPLAY_CONTEXT_TTL`; usa `self._context_ttl` no `redis.set(ex=...)`.
- `consumer.py` — `SessionReplayerConsumer` adiciona `CONFIG_API_URL` env var; `start()` busca `replayer_hydration_ttl_s` e `replay_context_ttl_s` do Config API antes de iniciar consumers; passa TTLs ao construir `StreamHydrator` e `Replayer`.

TODO.md: item "orchestrator-bridge TTLs dinâmicos" removido.

---

## Arc 8 — Disponibilidade e Pausas de Agentes — backend completo (verificado 2026-05-09)

Verificação de code review revelou que todo o backend Arc 8 já estava implementado. TODO.md estava desatualizado. Itens confirmados como presentes:

- `AgentPauseEventSchema` em `platform-events.ts` — campos `reason_id`, `reason_label`, `note` presentes.
- Config API seed `agent_activity.pause_reasons` — já presente em `seed.py`.
- `PUT /api/agent-pause` e `PUT /api/agent-resume` em `mcp-server-plughub/src/server.ts` (linhas 944–1050) — publicam `agent_pause`/`agent_ready` em `agent.lifecycle` com `reason_id`/`reason_label`.
- `parse_agent_lifecycle` em `analytics-api/models.py` — processa `agent_pause` (action=open) e `agent_ready` (action=close_check).
- Tabela `agent_pause_intervals` (ClickHouse `ReplacingMergeTree`) em `clickhouse.py`.
- `upsert_agent_pause_interval` e Redis state machine no consumer (`consumer.py`).
- `GET /reports/agent-availability` em `reports.py` + `query_agent_availability` em `reports_query.py`.
- Testes unitários em `tests/test_reports.py`.

TODO.md: seção Arc 8 removida (todos os itens concluídos).

---

## Config API Seed — novos namespaces e chaves de sessão (2026-05-09)

**Arquivo:** `packages/config-api/src/plughub_config_api/seed.py`

**O que mudou:**

Namespace `session` — 6 chaves TTL adicionadas para centralizar todos os TTLs de Redis em um único namespace, eliminando literais hardcoded nos componentes:
- `orchestrator_session_ttl_s` (14400) — bridge session state
- `transcript_ttl_s` (14400) — conversation-writer
- `replayer_hydration_ttl_s` (3600) — Hydrator (session_replayer)
- `replay_context_ttl_s` (3600) — ReplayContext hash
- `pool_config_ttl_s` (3600) — PoolConfigCache no orchestrator-bridge
- `sentiment_live_ttl_s` (300) — sentiment_live hash (duplicado de `sentiment.live_ttl_s` para o bridge ler tudo de um namespace)

Namespace `audit_policy` — substitui `masking` como namespace primário para políticas de mascaramento/auditoria (LGPD). Chaves: `authorized_roles`, `default_retention_days`, `capture_input_default`, `capture_output_default`. Entradas `masking.*` mantidas como aliases deprecados com prefixo `[DEPRECATED]` na descrição.

Namespace `analytics_consumer` — substitui `consumer` como namespace primário para configuração do consumidor Kafka da analytics-api. Entradas `consumer.*` mantidas como aliases deprecados.

Docstring do arquivo atualizado para listar namespaces ativos vs aliases deprecados.

---

## Bugfix — Routing Engine: sessões órfãs na fila ao desconectar cliente (2026-05-08)

**Problema:** Quando o cliente WebSocket desconectava (refresh, fechar aba, queda de rede), a sessão permanecia no ZSET `{tenant}:pool:{pool_id}:queue` indefinidamente — exibida na tela operacional de pools como sessão ativa, gerando falso-positivo de SLA excedido.

**Root cause:** O Channel Gateway já publicava `ContactClosedEvent` em `conversations.events` corretamente. O Routing Engine não consumia esse tópico e nunca gravava a chave `session:{id}:closed` no Redis — que é o marcador que o `_drain_queue_for_agent` usa para pular sessões encerradas.

**Solução (apenas routing-engine):**

- ✅ `config.py` — adicionado `kafka_topic_events: str = "conversations.events"`
- ✅ `kafka_listener.py` — novo `SessionClosedEventHandler`:
  - Recebe `event_type="contact_closed"`, grava `session:{id}:closed = reason` (TTL 7d)
  - Lê `{tenant}:queue_contact:{session_id}` para descobrir `pool_id`, remove do ZSET e deleta o JSON
- ✅ `kafka_listener.py` — `run_listeners()` e `_dispatch()` atualizados para aceitar e rotear `kafka_topic_events`
- ✅ `main.py` — `run_listeners` chamado com `kafka_topic_events = settings.kafka_topic_events`
- ✅ `main.py` — `_periodic_queue_drain` agora verifica `session:{id}:closed` antes de re-rotear (defesa em profundidade)

**Componentes alterados:** `routing-engine` apenas. Channel Gateway, Core e orchestrator-bridge sem alteração.

---

## Task #38 — Calendar Arc: modelo dois níveis + feriados recorrentes + detecção de conflitos (2026-05-08)

**Problema:** O calendário era um template compartilhado por referência, o que significava que exceções de um pool (manutenção, etc.) vazavam para outros pools que usassem o mesmo calendário. Feriados não podiam ser marcados como recorrentes anuais. Datas sobrepostas entre conjuntos de feriados eram silenciosamente resolvidas pelo último registro.

**Solução:** Modelo dois níveis onde (1) o calendário define a política compartilhada e (2) a associação `pool ↔ calendário` carrega exceções específicas do pool com prioridade 1 no motor.

**calendar-api — db.py:**
- ✅ `_DDL_ASSOCIATIONS` — coluna `exceptions JSONB NOT NULL DEFAULT '[]'` na tabela `calendar_associations`
- ✅ `_DDL_ASSOC_ADD_EXCEPTIONS` — migration idempotente `ADD COLUMN IF NOT EXISTS`
- ✅ `ensure_schema` — executa a migration idempotente
- ✅ `_row_to_assoc` — inclui `exceptions` no retorno
- ✅ `db_create_association` — persiste `exceptions`
- ✅ `db_update_association` — PATCH de `exceptions` de uma associação existente
- ✅ `db_upsert_pool_association` — upsert `ON CONFLICT DO UPDATE SET exceptions = EXCLUDED.exceptions`
- ✅ `db_delete_associations_for_entity` — remove todas as associações de uma entidade
- ✅ `db_get_associations_for_engine` — merge de exceções: `assoc.exceptions` (prioridade) + `cal.exceptions` (deduplica por date)

**calendar-api — router.py:**
- ✅ `AssociationCreate` — aceita `exceptions: list[dict] = []`
- ✅ `AssociationUpdate` — Pydantic model para PATCH
- ✅ `AssociationUpsert` — Pydantic model para PUT /upsert
- ✅ `PATCH /v1/associations/{id}` — atualiza exceções
- ✅ `PUT /v1/associations/upsert` — idempotent upsert (limpa associações antigas, cria nova com exceções)
- ✅ `DELETE /v1/associations/entity` — remove todas associações de uma entidade

**agent-registry:**
- ✅ `prisma/schema.prisma` — `calendar_id String?` no model Pool
- ✅ `prisma/migrations/20260508220000_add_pool_calendar_id/migration.sql`
- ✅ `src/routes/pools.ts` — persiste `calendar_id` no create + update
- ✅ `packages/schemas/src/agent-registry.ts` — `calendar_id: z.string().uuid().optional()` em `PoolRegistrationSchema`

**platform-ui — CalendarsPage.tsx:**
- ✅ `HolidaysEditor` — checkbox "Repete todo ano": salva `MM-DD` em vez de `YYYY-MM-DD`; badge ↺ na lista de feriados recorrentes
- ✅ `conflictDates` — `useMemo` detecta datas sobrepostas entre conjuntos selecionados
- ✅ Warning ⚠️ inline no seletor de conjuntos quando há datas duplicadas

**platform-ui — PoolsPage.tsx:**
- ✅ `PoolExceptionsEditor` — componente inline para gerenciar exceções do pool (fecha o dia todo ou horário especial)
- ✅ `calExceptions` state — carregado assincronamente da associação existente ao abrir o drawer
- ✅ `loadPoolAssociation` — busca associações do pool em `GET /v1/associations`
- ✅ `handleSubmit` — após salvar o pool no agent-registry, faz PUT `/v1/associations/upsert` (com calendar_id + exceptions) ou DELETE `/v1/associations/entity` (se sem calendário)
- ✅ Seção "Exceções deste Pool" aparece no drawer somente quando `calendar_id` está definido

**i18n:**
- ✅ `en/calendars.json` — `holidaySet.recurringLabel`, `calendar.holidayConflict`, `calendar.holidayConflictHint`
- ✅ `pt-BR/calendars.json` — traduções correspondentes

---

## Task #37 — Config/Channels: hierarquia Conta → Endpoints (2026-05-08)

**Problema:** GatewayConfig (credenciais) e ChannelEndpoint (endereços → pools) eram entidades paralelas sem relação explícita, listadas em sub-tabs separadas.

**Solução:** FK nullable `gateway_config_id` no ChannelEndpoint + UI hierárquica onde cada conta (GatewayConfig) é um card pai com seus endpoints filhos abaixo.

**Backend — agent-registry:**
- ✅ `prisma/schema.prisma` — `gateway_config_id String?` em ChannelEndpoint + relação bidirecional com GatewayConfig
- ✅ `prisma/migrations/20260508200000_channel_endpoint_gateway_config_fk/migration.sql` — ADD COLUMN + índice
- ✅ `src/types/channel-endpoint.ts` — `gateway_config_id: string | null` no type shim + `updateMany` adicionado
- ✅ `src/routes/channel-endpoints.ts` — aceita `gateway_config_id` em POST + PUT; filtro em GET; webhook adicionado a VALID_CHANNELS

**Frontend — platform-ui:**
- ✅ `src/types/index.ts` — `gateway_config_id: string | null` em ChannelEndpoint; `gateway_config_id?` em Create/UpdateChannelEndpointInput
- ✅ `src/api/registry.ts` — `listChannelEndpoints` aceita filtro opcional `gatewayConfigId`
- ✅ `modules/config-channels/ChannelAccountCard.tsx` — novo componente card pai: mostra credenciais + tabela de endpoints filhos; formulários inline para criar/editar/deletar endpoints dentro do card
- ✅ `modules/config-channels/index.tsx` — reestruturado: ChannelPanel usa ChannelAccountCard como hierarquia principal; webhook mantém ChannelEndpointList standalone; webchat mantém sub-tab "Runtime Settings"

---

## Task #36 — Config/Channels: consolidação GatewayConfig (2026-05-08)

**Problema resolvido:** GatewayConfig (credenciais de API) estava em Resources/Channels enquanto ChannelEndpoints (roteamento) estava em Config/Channels — duas telas separadas para configurar o mesmo canal.

**Solução:** Config/Channels virou o ponto único de configuração de canal. Resources ficou apenas com Pools + Skills.

**Frontend — platform-ui:**
- ✅ `modules/config-channels/channel-meta.ts` — `CHANNEL_META` extraído para arquivo compartilhado (8 canais: whatsapp, webchat, voice, email, sms, instagram, telegram, webrtc, webhook)
- ✅ `modules/config-channels/GatewayConfigPanel.tsx` — CRUD de GatewayConfig escopado por canal; sub-tabs expandíveis inline; suporte a create/edit/delete com masking de credenciais
- ✅ `modules/config-channels/index.tsx` — adicionada sub-tab "Credentials" para todos os canais que possuem campos de API; sub-tabs: Endpoints | Credentials | Settings (Settings apenas webchat/webhook)
- ✅ `modules/config-recursos/index.tsx` — aba "Channels" removida; Resources agora tem apenas Pools + Skills
- ✅ `modules/config-recursos/ChannelsPage.tsx` — marcado `@deprecated`; arquivo mantido para referência

**Estrutura final de Config/Channels por canal:**
- Endpoints: mapeamento identifier → pool (ChannelEndpointList, existente)
- Credentials: API keys/tokens/secrets (GatewayConfigPanel, novo)
- Settings: configuração de comportamento runtime via Config API (webchat: auth_timeout, attachments; webhook: path)

---

## Task #34 — Service/Pools: monitoração operacional de pools (2026-05-08)

**Backend — agent-registry:**
- ✅ `src/infra/redis.ts` — Redis client (ioredis) com opKeys: poolSnapshot, poolQueue, poolInstances
- ✅ `src/routes/operational.ts` — `GET /v1/operational/pools` + `GET /v1/operational/pools/:id/queue`
  - Fallback automático: snapshot Redis (Routing Engine) → live counts (:instances SCARD + :queue ZCARD)
  - Pool config (DB) + estado operacional (Redis) combinados por pool_id
  - Queue drill-down: posição, session_id, aguardando, SLA excedido, espera estimada restante
- ✅ `src/app.ts` — operationalRouter montado em `/v1/operational`
- ✅ `package.json` — ioredis adicionado
- ✅ `config.ts` — redis_url adicionado
- ✅ `docker-compose.demo.yml` — REDIS_URL + depends_on redis adicionados ao agent-registry

**Frontend — platform-ui:**
- ✅ `modules/contacts/PoolsPage.tsx` — nova página `/contacts/pools`:
  - Summary pills: agentes disponíveis, contatos em fila, pools com fila, total
  - Tabela de pools: status, disponíveis, fila, espera est., SLA, canais, snapshot age
  - Drill-down inline por pool: lista de sessões em fila com posição + wait + SLA excedido
  - Auto-refresh 15s, filtros por pool (select dinâmico) e status
  - `PoolStatusCard` exportado para reutilização em dashboard (Task #35)
- ✅ `modules/contacts/AgentsPage.tsx` — redesign: grid de cards → tabela compacta; tab "Lista" removida
- ✅ `shell/Sidebar.tsx` — item "Pools" adicionado em Service
- ✅ `app/routes.tsx` — rota `/contacts/pools` adicionada
- ✅ i18n pt-BR + en — chave `nav.service.pools` adicionada

---

## Task #33 — Deploy Pool-Centric: redesign completo (2026-05-08)

**Conceito corrigido:** deploy é uma operação de *pool*, não de skill. O usuário escolhe um pool e atribui um skill-flow a cada slot.

**Backend — agent-registry:**
- ✅ `prisma/migrations/20260508120000_add_pool_skill_slots/migration.sql` — tabela `pool_skill_slots` (pool_id + tenant_id + slot como chave; FK para pools com CASCADE; índice pool+tenant)
- ✅ `prisma/schema.prisma` — modelo `PoolSkillSlot` + relação `skill_slots` em `Pool`
- ✅ `src/routes/pool-slots.ts` — 4 endpoints montados em `/v1/pools/:pool_id`:
  - `GET /slots` — retorna todos os 3 slots do pool
  - `PUT /slots/next` — único slot editável; 403 para previous/current; auto-fetches yaml_snapshot
  - `POST /promote` — next→current, current→previous, next cleared (transaction)
  - `POST /rollback` — previous→current, previous cleared (transaction)
- ✅ `src/app.ts` — `poolSlotsRouter` montado em `/v1/pools/:pool_id` com `mergeParams: true`

**Frontend — platform-ui:**
- ✅ `AgentFlowDeployPage.tsx` — reescrita pool-centric:
  - Lista de pools no painel esquerdo com filtro
  - 3 cards por pool: Anterior (cinza) / Corrente (verde) / Próxima (azul)
  - Somente "Próxima" tem botão Editar; Corrente/Anterior mostram "🔒 somente leitura"
  - `NextSlotEditor`: skill dropdown + `ConfigForm` derivado de `skill.interface_schema`
  - "Copiar do Corrente": intersection merge — campos que existem em ambos são copiados; novos campos (só no novo schema) recebem badge "novo"; campos removidos são ignorados
  - Rollback usa `config_json` salvo sem revalidação (operação de emergência)
  - `ConfirmModal` para Promover e Rollback
  - Headers `x-tenant-id` em todos os fetches

---

## Full-Stack — 3-Slot Deploy Lifecycle (2026-05-08)

### Task #31 — Flow/Deploy: modelo de 3 slots (anterior / corrente / próxima)

**Backend — agent-registry:**
- ✅ `prisma/migrations/20260508000000_add_skill_version_slots/migration.sql` — tabela `skill_version_slots` + enum `SkillSlot` (previous/current/next)
- ✅ `prisma/schema.prisma` — modelo `SkillVersionSlot` + enum `SkillSlot` + relação `version_slots` em `Skill`
- ✅ `src/routes/skill-slots.ts` — 4 endpoints: `GET /slots`, `PUT /slots/:slot`, `POST /promote`, `POST /rollback`
- ✅ `src/app.ts` — `skillSlotsRouter` montado em `/v1/skills/:skill_id` com `mergeParams: true`

**Backend — workflow-api:**
- ✅ `config.py` — adicionado `agent_registry_url` (default `http://localhost:3300`)
- ✅ `router.py` — `_resolve_flow_definition()`: injeta `flow_definition` de agent-registry no metadata quando omitido em `POST /v1/workflow/trigger` para skill_*_v{n}
- ✅ `docker-compose.demo.yml`, `docker-compose.full.yml`, `docker-compose.arc4.yml` — `PLUGHUB_WORKFLOW_AGENT_REGISTRY_URL` adicionado

**Frontend — platform-ui:**
- ✅ `AgentFlowDeployPage.tsx` — reescrita completa; substituiu histórico-as-rollback por modelo de slots:
  - Painel de 3 colunas: Anterior / Corrente / Próxima
  - Desenvolvedor: botão "Editar" por slot (yaml_snapshot + config_json + pool_ids)
  - Operador: "Promover" (Próxima→Corrente, Corrente→Anterior) e "Rollback" (Anterior→Corrente) com confirmação
  - Após promote/rollback: chama `POST /deploy` para registrar em `skill_deployments`
  - Histórico de deploys colapsável
  - Deploys agendados colapsável (agendamento via workflow-api + cancelamento)
  - Monitor de handoff gradual colapsável (polling 15s)

---

## platform-ui — Contacts & Nav Restructure (2026-05-08)

### Task #30 — Grupos Atendimento + Análise + Flow expandido

**Novas páginas criadas:**
- ✅ `SessionsPage.tsx` (`/contacts/sessions`) — lista unificada de sessões (inbound + outbound), com filtros revisados: tipo (inbound/outbound), status (active/closed/abandoned), session_id, canal, pool, agent
- ✅ `AgentsPage.tsx` (`/contacts/agents`) — sub-abas Monitor (instâncias ao vivo, polling 15s) e Lista (placeholder Arc 8)
- ✅ `EventsPage.tsx` (`/contacts/events`) — stream plano de eventos com filtros: período, session_id (busca exata), canal, pool, tipo de evento
- ✅ `FlowMonitorPage.tsx` (`/flow/monitor`) — pool cards em tempo real (extrai scope "sessões" do MonitorTab)
- ✅ `ProcessosPage.tsx` (`/flow/processos`) — workflow instances com filtro de status, painel de detalhe, link para sessão de origem em `/contacts/sessions`
- ✅ `AnaliseContatosPage.tsx` (`/analise/contatos`) — seletor de período (Dia/Semana/Mês/Ano) + custom range; wraps AnaliseTab
- ✅ `AnaliseAgentesPage.tsx` (`/analise/agentes`) — filtros próprios; wraps AgentsTab (heatmap de disponibilidade + pausas)
- ✅ `AnaliseProcessosPage.tsx` (`/analise/processos`) — placeholder (backend analytics pendente)
- ✅ `AnaliseQualidadePage.tsx` (`/analise/qualidade`) — placeholder (backend evaluation summary pendente)

**Sidebar.tsx atualizado:**
- ✅ Grupo `Atendimento` (📞): Sessions · Agents · Events · Agent Assist
- ✅ Grupo `Fluxo` (🔄): Editor · Deploy · Monitor · Processos (2 novos sub-itens)
- ✅ Grupo `Análise` (📊) novo: Contatos · Agentes · Processos · Qualidade
- ✅ ABAC bypass para admin/supervisor/developer aplicado também nos itens de grupo

**routes.tsx atualizado:**
- ✅ Novas rotas: `/contacts/sessions`, `/contacts/agents`, `/contacts/events`, `/flow/monitor`, `/flow/processos`, `/analise/*`
- ✅ Redirects legados: `/contacts` → `/contacts/sessions`, `/monitor` → `/flow/monitor`, `agent-flow/monitor` → `/flow/monitor`, `config/agent-reports` → `/analise/agentes`, `reports` → `/analise/contatos`
- ✅ ContactsPage (`/contacts`) substituída — redirecionada para `/contacts/sessions`

**i18n:**
- ✅ `shell.json` (pt-BR + en): chaves `nav.service.*`, `nav.flow.monitor`, `nav.flow.processos`, `nav.analise.*` adicionadas

**Documentação:**
- ✅ `docs/modules/task-30-contacts-restructure.md` — especificação completa do redesign com decisões de design, filtros, colunas, ABAC e componentes

> Histórico de itens implementados removidos do `## Pending` do CLAUDE.md para reduzir noise no contexto.
> Itens pendentes: ver `TODO.md`.

---

## platform-ui — UI fixes + Skills/Pools/Config refactor (2026-05-07)

### Contacts (#23)
- ✅ Tab bar separada do título (border-b não mais aparecia sob "Contatos")
- ✅ Normalização de URL stale (`?tab=lista` → `?tab=report` via useEffect)
- ✅ Tab "Report" renomeada para "List" (`tabs.list`); i18n já tinha a chave
- ✅ Admin/supervisor/developer bypassam ABAC — veem todas as abas (List · Monitor · Analysis · Agents)

### Calendars (#24)
- ✅ Botão "New" adicionado nas seções Calendar e Holiday Sets
- ✅ Aba "Associations" removida (TabBar + AssociationsTab + código relacionado removidos)
- ✅ Tab state tipado com union `CalTab = 'calendars' | 'holiday-sets'`

### Flow/Editor (#25)
- ✅ Botão "New" removido (skills vêm do registry YAML, não são criadas pela UI)
- ✅ `NewSkillForm`, `AgentTypeConfig`, `FRAMEWORKS`, `ROLES`, `handleNewConfirm` removidos
- ✅ `isNew`, `pendingAgentType` removidos do estado

### Config/Platform — Masking + Namespaces (#26, #27)
- ✅ `MaskingPage.tsx`: namespace `masking` → `audit_policy`; chaves `capture_input`, `capture_output`, `token_retention_days`
- ✅ `NamespaceEditor`: namespaces `masking` e `audit_policy` removidos (têm UI própria)
- ✅ Namespaces `routing` + `session` unificados em grupo "Roteamento & Timeouts" com section headers
- ✅ Namespace `expurgo` adicionado (`voice_recording_days`, `attachment_days`)
- ✅ `NamespacePanel` sub-componente extraído; suporte a grupos (`namespaceIds[]`)
- ✅ `useMultiNamespace` hook adicionado em `config-hooks.ts`

### Resources/Skills (#28)
- ✅ `SkillsPage.tsx` reescrito: CRUD de competency skills no namespace `competency_skills` da Config API
- ✅ Cada entry: `key` (snake_case) + `value.domain` (0-9)
- ✅ Visual domain bar (10 pips) + slider inline para add/edit
- ✅ Admin token field para operações de escrita

### Resources/Pools (#29)
- ✅ `PoolsPage.tsx` reescrito: form de Modal para Drawer (slide-in direita, Escape para fechar)
- ✅ `routing_weights` substitui `routing_expression` na UI: Fixos (per-skill 0-9) + Dinâmicos (5 fatores 0-9)
- ✅ Competency skills carregadas de `/config/competency_skills` para o seletor de Fixos
- ✅ `routing_skills[]` derivado automaticamente dos Fixos com peso > 0 no save
- ✅ `types/index.ts`: `RoutingWeights`, `RoutingWeightsDinamicos`, `ROUTING_WEIGHTS_DEFAULTS` adicionados

### Bugfix
- ✅ `SkillFlowsPage.tsx` linha 408: referência residual a `isNew` removida

---

## platform-ui — Language cleanup + ChannelEndpoint (2026-05-07)

### Language rule — English identifiers in code

- ✅ **CLAUDE.md**: added "Language Rule — English in code, Portuguese only in display" to Naming Conventions section with examples.
- ✅ **routes.tsx**: `/config/canais` → `/config/channels`, `/config/recursos` → `/config/resources`, `/evaluation/avaliacoes` → `/evaluation/evaluations`. Legacy routes kept as `<Navigate>` redirects. Tab query params: `?tab=relatorio`→`?tab=report`, `?tab=analise`→`?tab=analysis`.
- ✅ **Sidebar.tsx**: all navKeys renamed to English (`service`, `flow`, `quality`, `config`); all hrefs updated to English paths; all i18n key calls updated (`t('nav.service')`, `t('nav.channels')`, `t('nav.resources')`, etc.); ABAC field references updated: `mascaramento`→`masking`, `relatorio`→`report`, `recursos`→`resources`.
- ✅ **shell.json (pt-BR + en)**: all i18n key names renamed to English while preserving translated values.
- ✅ **i18n/index.ts**: namespace `atendimento` → `service`; import variables renamed accordingly.
- ✅ **ContactsPage.tsx**: `ContactTab` type `'relatorio'|'analise'` → `'report'|'analysis'`; all tab references updated.
- ✅ **agent-assist types.ts**: `ultima_analise` → `last_analysis`.
- ✅ **useCopilotState.ts + CapacidadesTab.tsx**: updated to use `last_analysis`.
- ✅ **atendimento/MonitorPage.tsx**: `useTranslation('atendimento')` → `useTranslation('service')`.
- ✅ **config-channels/**: new module at English path; `index.tsx` + `WebChatConfigPage.tsx` created (logic same as config-canais version).

### Channel Endpoints — ChannelEndpoint entity

- ✅ **`@plughub/schemas`**: `ChannelEndpointChannelSchema`, `ChannelEndpointSchema`, `CreateChannelEndpointSchema`, `UpdateChannelEndpointSchema`, `ChannelEndpointQuerySchema` added to `packages/schemas/src/channel-endpoint.ts` and exported from `index.ts`.
- ✅ **`agent-registry` Prisma schema**: `ChannelEndpoint` model added with `@@unique([tenant_id, channel, identifier])`.
- ✅ **`agent-registry` routes**: `src/routes/channel-endpoints.ts` — full CRUD (GET list with `?channel=` filter, GET/:id, POST, PUT/:id with channel+identifier immutable, DELETE/:id). 409 on duplicate identifier. `publishRegistryChanged` on every write. Registered at `/v1/channel-endpoints` in `app.ts`.
- ✅ **`agent-registry` type shim**: `src/types/channel-endpoint.ts` — `ChannelEndpointRow` + `ChannelEndpointDelegate` for pre-generate Prisma workaround.
- ✅ **`platform-ui` types**: `ChannelEndpoint`, `ChannelEndpointChannel`, `CreateChannelEndpointInput`, `UpdateChannelEndpointInput` added to `types/index.ts`.
- ✅ **`platform-ui` api/registry.ts**: `listChannelEndpoints`, `createChannelEndpoint`, `updateChannelEndpoint`, `deleteChannelEndpoint` added.
- ✅ **`platform-ui` ChannelEndpointList.tsx**: new component — inline create/edit form, pool dropdown, delete with confirm; `IDENTIFIER_HINT` and `IDENTIFIER_PLACEHOLDER` maps per channel type.
- ✅ **`platform-ui` config-channels/index.tsx**: restructured with two sub-tabs per channel — "Endpoints" (`ChannelEndpointList`) + "General Settings" (`WebChatConfigPage` for webchat, coming soon for others).

**Manual steps required (WSL terminal):**
- `cd packages/agent-registry && npx prisma migrate dev --name add_channel_endpoint`
- `git mv packages/platform-ui/src/modules/config-canais packages/platform-ui/src/modules/config-channels` (then delete if config-channels already works)
- `git mv packages/platform-ui/src/modules/atendimento packages/platform-ui/src/modules/service` + update all imports referencing `@/modules/atendimento/`

---

## platform-ui — Config/Recursos + Config/Plataforma + Config/Canais (2026-05-06)

### Configuração / Recursos

- ✅ **Pool form — routing_expression weights**: 5 pesos dinâmicos de prioridade (`weight_sla=1.0`, `weight_wait=0.8`, `weight_tier=0.6`, `weight_churn=0.9`, `weight_business=0.4`) adicionados ao form de pool. Grid 2×3, inputs numéricos com step=0.1, botão "Restaurar padrões". Payload só envia `routing_expression` quando difere dos defaults. Coluna "Prioridade" na tabela mostra SLA+Churn com tooltip completo.
- ✅ **Pool form — calendar_id + routing_skills**: dropdown de template de calendário (calendar-api) e checkboxes de competency skills (Config API namespace `routing`) integrados ao form.
- ✅ **Types**: `RoutingExpression`, `ROUTING_EXPRESSION_DEFAULTS` adicionados a `types/index.ts`; `Pool`, `CreatePoolInput`, `UpdatePoolInput` atualizados.
- ✅ **Remover tab AgentTypes**: tab AgentTypes removida de `config-recursos/index.tsx` (Pools · Skills · Channels apenas). AgentType é criado pelo editor de SkillFlow ao criar novo skill.
- ✅ **SkillFlowsPage — inline AgentType**: `NewSkillForm` substitui `NewSkillPrompt`; ao criar skill, formulário completo de AgentType (framework, execution_model, role, max_concurrent, pools) é preenchido inline; `agent_type_id = skill_id`; POST /v1/agent-types disparado após skill salvo.

### Configuração / Plataforma

- ✅ **NamespaceEditor reestruturado**: 8 namespaces → 5 ativos. Removidos: `sentiment` (UI dedicada), `dashboard` (migrado ao módulo Dashboards), `webchat` (migrado a Configuração/Canais). Renomeados labels: `consumer` → "Consumer Analytics", `masking` mantido como legado com label "Mascaramento (legado)" + novo `audit_policy`. Entrada `routing` com descrição atualizada (sem score_weights — agora por pool).
- ✅ **SentimentBandsEditor**: novo componente dedicado para edição de faixas de sentimento com níveis numéricos (1=pior, N=melhor). Validação de contiguidade e cobertura [-1.0, 1.0]. Barra visual de cor vermelho→verde. Suporte a 2–6 faixas configuráveis. Salva `sentiment.bands` no Config API sem texto (i18n resolve labels). Nova tab "💬 Sentimento" no ConfigPlataformaPage.
- ✅ **RoutingSkillsManager**: CRUD de competency skills (key + domain range) no namespace `routing`. Tab "🎯 Roteamento" no ConfigPlataformaPage.
- ✅ **ConfigPlataformaPage tabs**: ⚙️ Configuração | 💬 Sentimento | 📅 Calendários | 🎯 Roteamento.

### Configuração / Canais (novo módulo)

- ✅ **config-canais/index.tsx**: seção `/config/canais` com tabs por canal. WebChat ativo; WhatsApp/Voice/Email/SMS marcados "em breve".
- ✅ **WebChatConfigPage.tsx**: configuração do canal WebChat em 3 grupos — autenticação WS (`auth_timeout_s`), política de attachments (`attachment_expiry_days`, `upload_limits_mb` por MIME type), info sobre JWT secret. Lê/escreve namespace `webchat` no Config API. Admin token inline.
- ✅ **Rota + Sidebar**: `/config/canais` registrado em `routes.tsx`; item "📡 Canais" na sidebar entre Plataforma e Calendários. Chave i18n `nav.canais` em pt-BR e en.

---

## platform-ui — Sidebar / Navigation refactor (2026-05-06)

- ✅ **Fix eco no histórico de segmento (Atendimento/Contatos)**: `mcp-server-plughub/src/server.ts` — substituído `redis.xadd()` direto por `writeStreamEntry()` no handler `menu_submit` (respeita invariante arquitetural). O `xadd` direto não populava os campos flat `author_id`/`author_role` que `_parse_entry()` do analytics-api exige, causando eco/duplicação na exibição das conversas do segmento no histórico do contato. Adicionado `import { writeStreamEntry }` do `lib/write-stream-entry`. Verificado e funcionando na docker-demo.
- ✅ **Scripts linux**: permissões de execução corrigidas em `check-infra.sh`, `seed-demo.sh`, `set-env.sh`, `setup.sh`.

---

## Context-Aware / ContextStore

- ✅ **Fase 2 — Co-pilot**: `copilot_emitter.py` (AI Gateway) — `analyze_for_copilot()` fire-and-forget; endpoint `POST /v1/copilot/analyze`; `GET /copilot_state/:sessionId` no mcp-server-plughub; `useCopilotState` hook; `CapacidadesTab.tsx` com `CopilotSection`; 28/28 testes.
- ✅ **Fase 3 — Step `resolve` nativo**: Executor completo de 5 fases (gap check → CRM → LLM question → BLPOP → LLM extract); schemas em `@plughub/schemas`; 15 unit tests.

---

## Arc 5 — ContactSegment (v2)

- ✅ **Enrichment post-hoc de `segment_id`**: `SegmentEnricher` em `analytics-api/segment_enricher.py` — lookup chain LRU → Redis → ClickHouse FINAL; `_ENRICHED_TOPICS = {"sentiment.updated", "mcp.audit"}`; 27/27 testes em `test_segment_enricher.py`. Total analytics-api: 226/226.
- ✅ **Materialized views ClickHouse**: `mv_agent_performance_daily` (AggregatingMergeTree, POPULATE) + `v_agent_performance` — `GET /reports/agent-performance/daily`; `mv_segment_summary` + `v_segment_summary` — `GET /reports/sessions/complexity`. Pool scoping em ambos.

---

## Skill Deploy Lifecycle (Phase 2)

- ✅ **Deploy agendado**: `skill_scheduled_deploy_v1.yaml` com `reason: timer` suspend; MCP tool `skill_deploy` em `mcp-server-plughub`; `scheduled_at` em `PersistSuspendRequest`; `GET /v1/skills/:id/deployments/scheduled`; UI `AgentFlowDeployPage.tsx` com agendamento e cancelamento.
- ✅ **Graceful handoff monitor**: `GET /v1/skills/:id/handoff-status` — detecta deploy recente, consulta analytics-api por sessões ativas antes de `deployed_at`. UI com polling a cada 10s.
- ✅ **Automated rollback button**: botão "↩ Rollback" no histórico; two-step (PUT yaml_snapshot + POST re-deploy); `RollbackConfirmModal`; badge "rollback" em entradas originadas por rollback.

---

## Config API / Routing Engine

- ✅ **Consumo de `config.changed` namespace `routing`**: `RoutingConfigCache` em `routing_config.py` — fetch no startup, invalida/recarrega via `ConfigChangedHandler`. `performance_score_weight` dinâmico com fallback para env var. 15/15 testes em `test_routing_config.py`.

---

## Arc 8 — Frontend

- ✅ **platform-ui — AgentReportsPage**: `/config/agent-reports` (supervisor, admin) — duas sub-abas: "Disponibilidade" (pivot agent × data, células âmbar por intensidade) e "Pausas" (tabela flat de reason_breakdown + exportação CSV). `useAvailability` hook.
- ✅ **platform-ui — PauseReasonModal**: modal intercepta "Pausar"; busca motivos do Config API com fallback para DEFAULT_REASONS; `requires_note: true` exibe textarea obrigatória; chama best-effort `PUT /api/agent-pause`.

---

## Arc 2 — Fechamento

- ✅ E2E scenario 12: webchat auth flow + media upload end-to-end
- ✅ Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)
- ✅ WebChat reconexão fase 2: stream TTL expirado + jwt_secret por tenant
- ✅ AttachmentStore fase 2: S3/MinIO
- ✅ Magic bytes validation no upload
- ✅ Pricing Module v1: planos, tarifas, ciclo de billing

---

## Arc 3 — Analytics, Dashboard Operacional e Relatórios

Todos os 12 itens implementados:

1. ✅ AI Gateway — `sentiment_emitter.py`: `emit_sentiment_updated` (Kafka) + `update_sentiment_live` (Redis). 41 assertions.
2. ✅ analytics-api — consumer + ClickHouse (6 tabelas ReplacingMergeTree, 8 tópicos, commit manual). 30 assertions.
3. ✅ analytics-api — dashboard endpoints: `GET /dashboard/operational` (SSE), `/metrics`, `/sentiment`. 18 assertions.
4. ✅ analytics-api — reports + BI export: sessions/agents/quality/usage com paginação e CSV. 26 assertions.
5. ✅ analytics-api — camada admin consolidada: `/admin/consolidated`, RBAC JWT, cross-tenant. 21 assertions.
6. ✅ operator-console fase 1 — heatmap + métricas realtime: SSE, PoolTile, MetricsPanel. 157 kB JS gzip 50 kB.
7. ✅ operator-console fase 2 — drill-down: sessions ativas → transcrição ao vivo (SSE + ClickHouse fallback). 61 assertions.
8. ✅ operator-console fase 3 — intervenção ativa: supervisor join/message/leave via REST. 173 kB JS gzip 54 kB.
9. ✅ Metabase setup: `docker-compose.infra.yml`, driver ClickHouse, Row Policies por tenant, 5 questions + dashboard.
10. ✅ Config Management Module: `packages/config-api/`, PostgreSQL, Redis cache 60s, 9 namespaces seedados. 27 assertions.
11. ✅ Timeseries endpoints: `/reports/timeseries/volume`, `/handle_time`, `/score`. Bucketing dinâmico. Pool scoping.
12. ✅ Dashboard drag-and-drop + templates: `DashboardsPage.tsx`, `TimeseriesChart`, react-grid-layout, Config API namespace `dashboards`.

---

## Arc 5 — ContactSegment v1

- ✅ Schemas em `@plughub/schemas/src/contact-segment.ts`: `ContactSegmentSchema`, `ConversationParticipantEventSchema`
- ✅ orchestrator-bridge: `_publish_participant_event` com `segment_id`, `sequence_index`, `parent_segment_id`, `outcome`
- ✅ analytics-api: tabelas `segments` + `session_timeline` (ClickHouse); endpoints `/reports/segments`, `/reports/agents/performance`, `/reports/agent-performance/daily`, `/reports/sessions/complexity`
- ✅ E2E scenario 23: Parts A/B/C — 11 assertions (`--segments` flag)

---

## AI Gateway — Multi-account Rotation

- ✅ `AccountSelector` em `account_selector.py`: Redis-backed, stateless, scoring por (rpm_used/rpm_limit × 0.7) + (tpm_used/tpm_limit × 0.3)
- ✅ Configuração multi-chave: `PLUGHUB_ANTHROPIC_KEYS` (vírgula) + `PLUGHUB_OPENAI_KEYS` como fallback
- ✅ `_call_with_fallback`: rotação automática em 429/529 → `mark_throttled` → próxima conta → cross-provider fallback
- ✅ Isolamento de workloads: profiles `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini), `evaluation` (Haiku isolado)
- ✅ `OpenAIProvider` com graceful degradation se SDK ausente; tool format conversion; role mapping
- ✅ Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `utilization_rpm_weight`, `evaluation_model`, `evaluation_max_tokens`
- ✅ 29 assertions em `test_account_selector.py`
- ✅ E2E scenario 26: throttle → fallback → recovery (`--fallback` flag)

---

## Arc 6 v2 — Permissões 2D + Workflow Motor

- ✅ `evaluation_permissions` substituído por ABAC (`module_config.evaluation.revisar` / `contestar`)
- ✅ `_check_abac_permission` em `router.py`; graceful degradation para tokens legacy
- ✅ Campos de workflow em `evaluation_results`: `workflow_instance_id`, `resume_token`, `action_required`, `current_round`, `deadline_at`, `lock_reason`
- ✅ Anti-replay de `round` em review/contestation endpoints
- ✅ Consumer `workflow.events` no evaluation-api
- ✅ `skill_revisao_simples_v1.yaml` e `skill_revisao_treplica_v1.yaml`
- ✅ MCP tool `evaluation_lock` (idempotente)
- ✅ E2E scenarios 27/28 (11 + 11 assertions)

---

## Calendar API — Fase 4: Múltiplos Intervalos de Tempo por Dia

- ✅ **Engine**: já suportava múltiplos slots desde a implementação inicial (estrutura `{day, open, slots: [{open, close}]}`); nenhuma mudança necessária no backend
- ✅ **5 novos testes de engine** (`test_engine.py` — total: 46/46):
  - `test_between_slots_returns_start_of_next_slot` — 12:30 durante pausa → retorna 13:00 mesmo dia
  - `test_within_first_slot_returns_current_time` — 10:00 dentro do primeiro slot → já aberto
  - `test_after_all_slots_returns_next_day_open` — 17:30 após último slot → 08:00 dia seguinte
  - `test_is_open_false_during_gap` — gap entre slots retorna `False`; início de slot retorna `True`
  - `test_crosses_gap_between_slots` — `add_business_duration` 11:00 + 3h cruza pausa (12–13) → 15:00
- ✅ **CalendarsPage.tsx** — corrigido bug de formato de dados (UI enviava `{day_of_week, start_time, end_time}` mas o engine lê `{day, open, slots: [{open, close}]}`); tipos `TimeInterval` + `WeeklyDaySchedule` agora corretos
- ✅ **WeeklyEditor** — reescrito para suportar N intervalos por dia: cada slot é uma linha; botão `× ` remove um intervalo; botão `+ intervalo` adiciona (máx 4); fallback para `08:00–18:00` ao remover último slot
- ✅ **`scheduleLabel`** — exibe `Segunda(2)` quando o dia tem múltiplos slots
