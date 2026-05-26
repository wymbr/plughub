# Módulo: platform-ui (`packages/platform-ui/`)

> Última atualização: 2026-05-25 · Estado: Arc 16

> Shell unificado para operadores, supervisores e administradores da plataforma PlugHub
> Runtime: React 18 + TypeScript · Vite · React Router v6 · Tailwind CSS
> Porta de desenvolvimento: 5174

---

## O que é

O `platform-ui` é o **shell único de PlugHub** — a aplicação onde operadores, supervisores, administradores, desenvolvedores e perfis de negócio operam toda a plataforma. Consolidou e substituiu os apps legados (`operator-console`, `agent-assist-ui`) em uma única SPA com roteamento compartilhado, autenticação unificada, design system consistente e controle de acesso ABAC.

> **Invariante:** nunca criar um novo `packages/my-ui/` standalone — toda nova superfície de UI é um módulo dentro do `platform-ui`.

**Escopo (estado atual — Arc 16):**
- Console de orquestração humana / Agent Assist (operador dirige agentes AI como coparticipantes)
- Monitoramento de sessões, agentes, pools, eventos e processos
- Editor visual de Skill Flows + deploy lifecycle
- Plataforma de avaliação de qualidade (Forms, Campaigns, Knowledge, Evaluations, Calibração, Curadoria)
- Analytics em tempo real (Sessions/Agents/Events/Processes/Quality)
- Gestão de recursos (pools, tipos de agente, skills, instâncias), canais, calendários, masking e billing
- Gestão de usuários e Agent Groups
- Auditoria LGPD (módulo de compliance/DPO)

**Não é responsável por:**
- Execução de skills (Skill Flow Engine)
- Lógica de roteamento (Routing Engine)
- Inferência LLM (AI Gateway)

---

## Stack Técnica

| Camada | Tecnologia | Versão |
|---|---|---|
| **Framework** | React | 18+ |
| **Linguagem** | TypeScript | 5.3+ |
| **Build tool** | Vite | 5.0+ |
| **Roteamento** | React Router | v6 |
| **Estilos** | Tailwind CSS | 3.3+ |
| **i18n** | react-i18next | 13.0+ |
| **HTTP** | Fetch API (nativa) + Vite proxies |
| **State** | React hooks + Context API |
| **Dev server** | Vite dev server | Porta 5174 |

---

## Estrutura de Diretórios

```
platform-ui/
  src/
    main.tsx                    ← Ponto de entrada
    index.css                   ← Tailwind imports
    
    app/
      App.tsx                   ← Componente raiz + Router
      routes.tsx                ← Configuração de rotas (RouteObject[])
    
    auth/
      AuthContext.tsx           ← Contexto de autenticação
      useAuth.ts                ← Hook useAuth()
      LoginPage.tsx             ← Página de login
      ProtectedRoute.tsx        ← Wrapper de rota protegida
    
    shell/
      Shell.tsx                 ← Layout base (Sidebar + TopBar + Outlet)
      Sidebar.tsx               ← Navegação lateral (w-56)
      TopBar.tsx                ← Barra superior (perfil, logout)
    
    components/
      ui/
        Button.tsx              ← Componente Button (variações)
        Card.tsx                ← Card layout
        Badge.tsx               ← Badges (status)
        Table.tsx               ← Tabela genérica
        Modal.tsx               ← Modal/Dialog
        Input.tsx               ← Input text
        Select.tsx              ← Select dropdown
        Spinner.tsx             ← Loading spinner
        PageHeader.tsx          ← Cabeçalho de página
        EmptyState.tsx          ← Estado vazio
    
    modules/
      home/
        HomePage.tsx            ← Dashboard inicial
      
      atendimento/              ← /monitor (Monitor de sesões)
        MonitorPage.tsx
        api/
          monitor.ts            ← Hooks e API calls
        components/
          PoolHeatmap.tsx
          SessionList.tsx
          TranscriptView.tsx
      
      config-recursos/          ← /config/recursos (Pools, Agents, Skills)
        index.tsx
        components/
          PoolsTab.tsx
          AgentTypesTab.tsx
          SkillsTab.tsx
          InstancesTab.tsx
        api/
          resources.ts
      
      config-plataforma/        ← /config/platform (Plataforma, Canais, Billing)
        ConfigPlataformaPage.tsx
        components/
          ConfigNamespaceTabs.tsx
          ChannelPanel.tsx
          PricingPanel.tsx
        api/
          config.ts
      
      workflows/                ← /workflows (Workflows, Campanhas)
        WorkflowsPage.tsx
        components/
          WorkflowList.tsx
          CampaignPanel.tsx
        api/
          workflows.ts
      
      _placeholder/             ← Páginas em roadmap
        PlaceholderPage.tsx
    
    i18n/
      config.ts                 ← Configuração i18next
      locales/
        pt-BR/
          common.json           ← Strings comuns
          modules.json          ← Strings por módulo
          nav.json              ← Labels da navegação
        en/
          ...
    
    types/
      index.ts                  ← Types globais (Session, User, roles)
    
    api/
      client.ts                 ← Fetch wrapper com autenticação
      constants.ts              ← URLs base, timeouts

  vite.config.ts                ← Proxies para backend
  tailwind.config.ts            ← Design tokens
  tsconfig.json
  package.json
```

---

## Design System

### Tokens de Cor

| Token | Valor | Uso |
|---|---|---|
| **primary** | `#1B4F8A` | Botões primários, Sidebar background |
| **secondary** | `#2D9CDB` | Links, badges secundários |
| **accent** | `#00B4D8` | Destaque, hover states |
| **green** | `#059669` | Status "ok", sucesso, áreas positivas |
| **warning** | `#D97706` | Alertas, campos obrigatórios |
| **red** | `#DC2626` | Erros, status críticos |
| **gray-50** | `#F9FAFB` | Background principal |
| **gray-200** | `#E5E7EB` | Borders, separadores |
| **gray-800** | `#1F2937` | Texto principal |

### Tailwind config (`tailwind.config.ts`)

```typescript
export default {
  theme: {
    extend: {
      colors: {
        primary:   '#1B4F8A',
        secondary: '#2D9CDB',
        accent:    '#00B4D8',
      },
    },
  },
}
```

**Nunca usar hex colors inline!** Sempre preferir tokens Tailwind: `bg-primary`, `text-gray-800`, `border-gray-200`, etc.

### Tipografia

- **Font:** Inter (via `@import` em `index.css`)
- **Tamanhos:** `text-sm`, `text-base`, `text-lg`, `text-xl`, `text-2xl`
- **Pesos:** `font-normal`, `font-semibold`, `font-bold`
- **Line height:** `leading-normal`, `leading-relaxed`

---

## Componentes UI Disponíveis

Todos importáveis via `@/components/ui/*`:

| Componente | Caminho | Props principais | Quando usar |
|---|---|---|---|
| **Button** | `Button.tsx` | `variant`, `size`, `disabled` | Qualquer ação interativa |
| **Card** | `Card.tsx` | `className`, `children` | Agrupar conteúdo com border/shadow |
| **Badge** | `Badge.tsx` | `variant` (status, default) | Status pills, tags |
| **Table** | `Table.tsx` | `columns`, `data`, `onRowClick` | Dados tabulares com sort/filter |
| **Modal** | `Modal.tsx` | `open`, `onClose`, `title` | Diálogos bloqueantes |
| **Input** | `Input.tsx` | `type`, `placeholder`, `error` | Entrada de texto |
| **Select** | `Select.tsx` | `options`, `value`, `onChange` | Dropdowns |
| **Spinner** | `Spinner.tsx` | `size` (sm, md, lg) | Estados de carregamento |
| **PageHeader** | `PageHeader.tsx` | `title`, `subtitle`, `actions` | Cabeçalho de página |
| **EmptyState** | `EmptyState.tsx` | `icon`, `title`, `action` | Listas vazias |

**Exemplo de uso:**

```typescript
import { Button, Card, Badge } from '@/components/ui'

export const MyComponent = () => (
  <Card>
    <Badge variant="status">Ativo</Badge>
    <Button onClick={() => alert('Clicado!')}>
      Enviar
    </Button>
  </Card>
)
```

---

## Shell / Layout

### Estrutura base

```
┌─────────────────────────────────────┐
│         TopBar (h-16)               │  Perfil, logout, notificações
├─────────────┬───────────────────────┤
│   Sidebar   │   Main content        │  Full-bleed ou com padding
│  (w-56)     │   (Outlet)            │
│             │                       │
│  bg-primary │ bg-gray-50            │
│             │                       │
└─────────────┴───────────────────────┘
```

### Shell.tsx — Padrão vs Full-bleed

O componente `Shell` detecta a rota e alterna entre dois layouts:

| Rota | Layout | Padding |
|---|---|---|
| `/` (home), `/config/*`, `/evaluation/*`, `/audit` | Padrão | `px-6 py-6` com scroll |
| `/monitor`, `/agent-assist`, `/agent-flow/editor`, `/workflow/*` | Full-bleed | `overflow-hidden`, sem padding |

**Adicionar rota full-bleed:**

```typescript
// src/shell/Shell.tsx
const FULL_BLEED_ROUTES = ['/monitor', '/agent-assist', '/config/platform', '/agent-flow/editor', '/sua-nova-rota']
```

### Sidebar — Navegação

`Sidebar.tsx` renderiza logo + nome do tenant, os grupos de navegação e o setor de usuário. A navegação é organizada em **grupos** (`navKey`), e cada item pode ter gates ABAC.

| Grupo | Ícone | Itens (resumo) | Gate ABAC |
|---|---|---|---|
| Home | 🏠 | Dashboard inicial | — |
| Console | 🖥️ | Console / Agent Assist | `contacts.operacao` |
| Monitor | 📡 | Sessions / Agents / Pools / Events / Processes | — |
| Fluxo | 🔄 | Editor / Deploy | `skill_flows.operacao` |
| Avaliação | ✓ | Forms / Campaigns / Knowledge / Evaluations / Calibração / Curadoria | `evaluation.*` |
| Analytics | 📊 | Sessions / Agents / Events / Processes / Quality | `visualizar` / `report` |
| Configuração | ⚙️ | Dashboards / Recursos / Plataforma / Canais / Calendários / Masking / Billing / Groups / Acesso | — |

Itens standalone fora dos grupos: **Auditoria LGPD** (🔍, gate ABAC `audit.sessions`).

**Redirects legados:** `/workflows` → `/workflow/monitor`; `/skill-flows` → `/agent-flow/editor`; `/reports` → `/contacts?tab=analise`.

Os identificadores técnicos (rotas, `navKey`, tab IDs) são sempre em inglês — apenas os labels exibidos passam por `t()`.

---

## Autenticação

### AuthContext

Armazena `Session` (usuário + token + roles) em localStorage.

```typescript
interface Session {
  user_id: string
  email: string
  tenant_id: string
  roles: Array<'operator' | 'supervisor' | 'admin' | 'developer' | 'business'>
  token: string  // JWT
}
```

### useAuth Hook

```typescript
const { session, isAuthenticated, login, logout } = useAuth()

if (!isAuthenticated) {
  return <Navigate to="/login" />
}

console.log(session.user_id)  // Acesso seguro
```

### ProtectedRoute

Wrapper que redireciona para `/login` se não autenticado:

```typescript
<ProtectedRoute>
  <Shell /> {/* Renderiza só se autenticado */}
</ProtectedRoute>
```

### RBAC — papéis

Cinco papéis: `operator` (Console + Contacts), `supervisor` (+ Avaliação + Reports), `admin` (+ Configuração + Skills), `developer` (+ DevTools), `business` (transversal, sem itens operacionais). Verificação simples via `session.roles`:

```typescript
const { session } = useAuth()
const isAdmin = session?.roles.includes('admin')
```

### ABAC — controle de acesso por módulo (`module_config`)

Além dos papéis, a `platform-ui` aplica **ABAC** (attribute-based access control). O JWT carrega `module_config`: para cada módulo (`evaluation`, `contacts`, `billing`, `config`, `skill_flows`, `workflows`, `agent_assist`, `campaigns`, `audit`) e cada campo, um nível de acesso (`none | read_only | write_only | read_write`) + lista de `scope[]`.

```typescript
// PermissionChecker — avaliado localmente a partir do module_config do JWT
const perms = usePermissions()           // ou makePermissions(session.moduleConfig)
perms.can('config', 'channels', 'read_write')      // boolean
perms.can('evaluation', 'revisar', 'read_only', 'pool_sac')  // com scope
```

**Gates de navegação:** itens do menu são filtrados por dois campos ABAC transversais:
- `operacao` — libera Monitor / Editor / Calendar / Deploy / Agent Assist
- `visualizar` — libera abas de Reports / Análise

`accessible_pools` no JWT faz filtro de linha em analytics; `supervisedAgentTypes` (Arc 9) restringe as abas Agents/Instances do Monitor de forma transparente. Contas legacy sem `module_config` degradam graciosamente (acesso liberado). Defesa em profundidade: o backend repete as verificações nos endpoints sensíveis.

---

## Módulos Implementados

A `platform-ui` é um produto completo. Os módulos abaixo estão entregues e em produção.

| Rota | Módulo | Descrição |
|---|---|---|
| `/` | Home | Dashboard inicial com KPIs |
| `/agent-assist` | Console / Agent Assist | Superfície de orquestração humana — operador dirige agentes AI como coparticipantes (Arc 11); painel de 4–5 abas, delegação de tarefa, especialistas |
| `/monitor` | Monitor | Sessions / Agents / Pools / Events / Processes — heatmap, transcrição ao vivo |
| `/agent-flow/editor`, `/workflow/deploy` | Fluxo | Editor visual de Skill Flows + deploy lifecycle (draft/published) |
| `/evaluation/forms` | Avaliação — Forms | CRUD de formulários de avaliação |
| `/evaluation/campaigns` | Avaliação — Campaigns | Campanhas de amostragem + KPIs |
| `/evaluation/knowledge` | Avaliação — Knowledge | Base de conhecimento vetorial (RAG) |
| `/evaluation/avaliacoes` | Avaliação — Evaluations | Tabela unificada de avaliações, drill-down, revisão/contestação por dimensão (Arc 13) |
| `/evaluation/calibration` | Avaliação — Calibração | Calibration Dashboard — score do avaliador AI por skill version (Arc 13) |
| `/evaluation/curadoria` | Avaliação — Curadoria | Fila de curadoria do feedback loop RAG (Arc 13) |
| `/contacts?tab=analise` | Analytics | Sessions / Agents / Events / Processes / Quality (analytics-api `/reports/*`) |
| `/config/recursos` | Config — Recursos | CRUD de Pools, Agent Types, Skills, Instâncias |
| `/config/platform` | Config — Plataforma | Namespaces de configuração, canais, calendários, masking |
| `/config/billing` | Config — Billing | Faturamento por capacidade (BillingPage, role admin) |
| `/config/groups` | Config — Groups | Agent Groups (Arc 9) — organograma + supervisores + turnos |
| `/config/access` | Config — Acesso | Gestão de usuários + permissões ABAC (`module_config`) |
| `/audit` | Auditoria LGPD | Módulo de compliance/DPO — Sessions + MCP Calls (ABAC `audit.*`) |

---

## Padrão de Módulo

Cada módulo segue a mesma estrutura:

```
modules/
  seu-modulo/
    index.tsx                 ← Export nomeado da página principal
    SeuModuloPage.tsx         ← Componente principal (layout + orquestração)
    
    components/
      FeatureOne.tsx          ← Componentes específicos do módulo
      FeatureTwo.tsx
      
    api/
      seu-modulo.ts           ← Hooks de API + tipos
      
    types/
      seu-modulo.ts           ← Types TypeScript (opcional)
```

### Exemplo: Criar novo módulo (`novo-modulo`)

**1. Criar estrutura de pasta:**

```bash
mkdir -p src/modules/novo-modulo/components
mkdir -p src/modules/novo-modulo/api
```

**2. Criar `NovoModuloPage.tsx`:**

```typescript
// src/modules/novo-modulo/NovoModuloPage.tsx
import React from 'react'
import { useAuth } from '@/auth/useAuth'
import { PageHeader, Card } from '@/components/ui'
import { useNovoModulo } from './api/novo-modulo'

const NovoModuloPage: React.FC = () => {
  const { session } = useAuth()
  const { data, isLoading, error } = useNovoModulo()

  if (isLoading) return <div>Carregando...</div>
  if (error) return <div className="text-red-600">{error}</div>

  return (
    <div>
      <PageHeader 
        title="Novo Módulo"
        subtitle={`Tenant: ${session?.tenant_id}`}
      />
      
      <Card className="mt-6">
        <h2 className="text-xl font-semibold mb-4">Conteúdo</h2>
        {data && <pre>{JSON.stringify(data, null, 2)}</pre>}
      </Card>
    </div>
  )
}

export default NovoModuloPage
```

**3. Criar `api/novo-modulo.ts`:**

```typescript
// src/modules/novo-modulo/api/novo-modulo.ts
import { useAuth } from '@/auth/useAuth'
import { useEffect, useState } from 'react'

interface NovoModuloData {
  items: string[]
}

export const useNovoModulo = () => {
  const { session } = useAuth()
  const [data, setData] = useState<NovoModuloData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch('/api/novo-modulo', {
          headers: {
            'Authorization': `Bearer ${session?.token}`,
            'X-Tenant-ID': session?.tenant_id || '',
          }
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const json = await res.json()
        setData(json)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro desconhecido')
      } finally {
        setIsLoading(false)
      }
    }

    fetchData()
  }, [session?.token])

  return { data, isLoading, error }
}
```

**4. Adicionar rota em `routes.tsx`:**

```typescript
import NovoModuloPage from '@/modules/novo-modulo/NovoModuloPage'

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <Shell />,
    children: [
      // ... outras rotas
      {
        path: 'novo-modulo',
        element: <NovoModuloPage />
      },
    ]
  }
]
```

**5. Adicionar nav item em `Sidebar.tsx`:**

```typescript
{ label: 'Novo Módulo', path: '/novo-modulo', icon: 'Star' }
```

**6. Adicionar strings de i18n:**

```json
// src/i18n/locales/pt-BR/modules.json
{
  "novo_modulo": {
    "title": "Novo Módulo",
    "subtitle": "Gerenciamento de X"
  }
}
```

---

## Internacionalização (i18n)

### Estrutura de locales

```
i18n/
  config.ts                  ← Configuração i18next
  locales/
    pt-BR/                   ← Padrão (PT-BR)
      common.json            ← Strings comuns (botões, labels)
      modules.json           ← Strings de módulos
      nav.json               ← Labels de navegação
    en/
      common.json
      modules.json
      nav.json
```

### Adicionar nova chave

**1. Em `src/i18n/locales/pt-BR/modules.json`:**

```json
{
  "novo_modulo": {
    "title": "Novo Módulo",
    "label_botao": "Clique aqui"
  }
}
```

**2. Em `src/i18n/locales/en/modules.json`:**

```json
{
  "novo_modulo": {
    "title": "New Module",
    "label_botao": "Click here"
  }
}
```

**3. Usar no componente:**

```typescript
import { useTranslation } from 'react-i18next'

const { t } = useTranslation('modules')

return (
  <h1>{t('novo_modulo.title')}</h1>
  <button>{t('novo_modulo.label_botao')}</button>
)
```

### Configuração (i18n/config.ts)

```typescript
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

i18n
  .use(initReactI18next)
  .init({
    lng: 'pt-BR',
    fallbackLng: 'pt-BR',
    resources: {
      'pt-BR': {
        common: ptBrCommon,
        modules: ptBrModules,
        nav: ptBrNav,
      },
      'en': {
        common: enCommon,
        modules: enModules,
        nav: enNav,
      }
    }
  })
```

---

## APIs e Proxies Vite

### Vite config (`vite.config.ts`)

Define proxies para evitar CORS em desenvolvimento:

```typescript
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:3100',      // mcp-server-plughub
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      },
      '/analytics': {
        target: 'http://localhost:3500',      // analytics-api
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/analytics/, '')
      },
      '/config': {
        target: 'http://localhost:3600',      // config-api
        changeOrigin: true
      },
      '/workflow': {
        target: 'http://localhost:3800',      // workflow-api
        changeOrigin: true
      }
    }
  }
})
```

### Endpoints principais

| Caminho | Destino | Serviço |
|---|---|---|
| `/api/*` | `http://localhost:3100` | mcp-server-plughub (BPM, agent, session) |
| `/analytics/*` | `http://localhost:3500` | analytics-api (queries, reports) |
| `/config/*` | `http://localhost:3600` | config-api (plataforma, canais) |
| `/workflow/*` | `http://localhost:3800` | workflow-api (instâncias, webhooks) |

---

## Apps Legados

### operator-console

**Status:** Deprecado em favor de platform-ui

**Localização:** `packages/operator-console/`

**Painéis que serão migrados:** Heatmap, Sessions, Workflows, Campaigns, Pricing, Webhooks, Registry, Skills, Channels, Agents, Config

**Timeline:** Será mantido em read-only até conclusão da migração (veja `standards/operator-console-migration.md`)

### agent-assist-ui

**Status:** Legado (para agentes humanos)

**Localização:** `packages/agent-assist-ui/`

**Diferença:** Não faz parte do platform-ui; é uma aplicação separada para atendentes

**Não será consolidada** — agentes acessam via Agent Assist (integração separada no platform-ui futuro)

---

## Como Rodar

### Desenvolvimento

```bash
# Instalar dependências
npm install

# Iniciar dev server (porta 5174)
npm run dev

# Abrir no navegador
open http://localhost:5174
```

### Build para produção

```bash
npm run build    # Gera dist/
npm run preview  # Preview da build local
```

### Variáveis de ambiente (`.env.local`)

```
VITE_TENANT_ID=tenant_demo
VITE_API_BASE_URL=http://localhost:3100
VITE_ANALYTICS_URL=http://localhost:3500
VITE_CONFIG_API_BASE_URL=http://localhost:3600
VITE_WORKFLOW_API_BASE_URL=http://localhost:3800
```

---

## Checklist de Deploy

- [ ] Build sem erros (`npm run build`)
- [ ] TypeScript check (`npx tsc --noEmit`)
- [ ] Todas as rotas testadas
- [ ] Autenticação funcional
- [ ] i18n testado (PT-BR + EN)
- [ ] Proxies Vite apontando para backends corretos
- [ ] Env vars configuradas em produção
- [ ] Design tokens aplicados (sem hex inline)
