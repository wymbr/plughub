# Módulo: platform-ui (`packages/platform-ui/`)

> Shell unificado para operadores, supervisores e administradores da plataforma PlugHub
> Runtime: React 18 + TypeScript · Vite · React Router v6 · Tailwind CSS
> Porta de desenvolvimento: 5174

---

## O que é

O `platform-ui` é o **shell administrativo centralizado** de PlugHub — a interface unificada onde operadores, supervisores e administradores gerenciam pools, agentes, configurações, workflows, campanhas e assistência em tempo real. Substitui e consolida os apps legados (`operator-console`, `agent-assist-ui`) em uma única aplicação com roteamento compartilhado, autenticação unificada e design system consistente.

**Escopo:**
- Monitoramento de sessões e sentimento (pools, filas, agentes)
- Gestão de recursos (pools, tipos de agente, skills, instâncias)
- Configuração de canais e credenciais
- Orquestração de workflows e campanhas
- Supervisor assistido por IA (co-pilot)
- Relatórios e analytics em tempo real

**Não é responsável por:**
- Interação com o cliente (Agent Assist UI — app separada para agentes)
- Execução de skills (Skill Flow Engine)
- Lógica de roteamento (Routing Engine)

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
| `/` (home), `/config/*` (config), `/dashboards`, `/reports` | Padrão | `px-6 py-6` com scroll |
| `/monitor`, `/workflows`, `/agent-assist`, `/skill-flows` | Full-bleed | `overflow-hidden`, sem padding |

**Adicionar rota full-bleed:**

```typescript
// src/shell/Shell.tsx
const FULL_BLEED_ROUTES = ['/monitor', '/agent-assist', '/config/platform', '/workflows', '/sua-nova-rota']
```

### Sidebar — Navegação

`Sidebar.tsx` renderiza:
1. Logo + nome do tenant
2. Menu de módulos (links via `useNavigate()`)
3. Setor de user (perfil + logout)

**Adicionar item de menu:**

```typescript
// src/shell/Sidebar.tsx
const menuItems = [
  { label: 'Monitor', path: '/monitor', icon: 'Activity' },
  { label: 'Workflows', path: '/workflows', icon: 'Zap' },
  { label: 'Seu Novo Módulo', path: '/novo-modulo', icon: 'Star' }, // ← Novo
]
```

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

### RBAC (Role-Based Access Control)

Verificar permissões de um usuário:

```typescript
const { session } = useAuth()

const isAdmin = session?.roles.includes('admin')
const isSupervisor = session?.roles.includes('supervisor')

return isAdmin ? <AdminPanel /> : <OperatorView />
```

---

## Módulos Implementados

| Rota | Módulo | Status | Descrição |
|---|---|---|---|
| `/` | Home | ✅ | Dashboard inicial com KPIs |
| `/monitor` | Atendimento (Monitor) | ✅ | Heatmap de pools, sessões ativas, transcrição ao vivo |
| `/config/recursos` | Config Recursos | ✅ | CRUD de Pools, Agent Types, Skills, Instâncias |
| `/config/platform` | Config Plataforma | ✅ | Plataforma (namespaces), Canais (credenciais), Billing |
| `/workflows` | Workflows | ✅ | Orquestração, histórico, campanhas de coleta |

---

## Módulos Placeholder (Roadmap)

| Rota | Módulo | Fase | Status |
|---|---|---|---|
| `/agent-assist` | Agent Assist | Arc 2 | 📋 Integração do agente humano com co-pilot IA |
| `/dashboards` | Dashboards | Arc 3 | 📋 Analytics em tempo real (SSE) |
| `/reports` | Reports | Arc 3 | 📋 Relatórios e BI |
| `/skill-flows` | Skill Flows | Arc 2 | 📋 Editor visual de skills + depuração |
| `/config/access` | Access Control | Arc 2 | 📋 RBAC e permissões por role |
| `/developer` | Developer Tools | Arc 3 | 📋 Logs, trace de APIs, playground MCP |
| `/business` | Business Analytics | Arc 3 | 📋 Métricas de negócio |

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
