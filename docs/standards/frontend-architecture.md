# Padrão: Arquitetura Frontend — Platform UI

> Guia prático para construir módulos, componentes e features no shell unificado do PlugHub
> Última atualização: 2026-04-28

---

## Princípio: Platform UI como Shell Único de Operadores

O `platform-ui` é o **ponto de entrada único** para todos os operadores, supervisores e administradores da plataforma. Não devem ser criados apps React standalone novos. Toda interface administrativa vai aqui.

**Consequência:** Quando alguém pedir uma nova feature para gerenciar X, pergunte:
- "Essa feature é para operadores?" → Vai no platform-ui
- "Essa feature é para agentes humanos?" → Não faz parte do platform-ui (uso diferente, app separada se necessário)
- "Essa feature é para dados de um serviço backend?" → Backend primeiro, depois UI no platform-ui

---

## Decisão: Novo Módulo vs Expandir Módulo Existente

Antes de criar uma pasta `modules/novo-algo/`, pergunte:

| Pergunta | Sim → Novo módulo | Não → Expandir existente |
|---|---|---|
| É uma seção de navegação à parte na Sidebar? | ✅ Novo | ❌ Usar existente |
| Tem CRUD completo e independente? | ✅ Novo | ❌ Usar existente |
| É um painel de visualização de dados com múltiplos visualizadores? | ✅ Novo | ❌ Usar existente |
| É uma tab dentro de um módulo existente? | ❌ Novo | ✅ Expandir |
| É um formulário ou action de um recurso existente? | ❌ Novo | ✅ Expandir |
| É uma visualização complementar a um módulo (ex: detalhe de item)? | ❌ Novo | ✅ Expandir |

**Exemplo:**
- Novo Skill Editor → Novo módulo `/skill-flows` (tem nav própria, editor + debug, visual complexo)
- Nova coluna em tabela de Pools → Expandir `config-recursos` (é um detalhe de Pool, não seção independente)
- Novo painel de Aprovações → Novo módulo `/approvals` (fluxo independente, nav própria)

---

## Passo a Passo: Criar um Módulo Completo

Assumindo que vamos criar um módulo de **Aprovações** (`/approvals`).

### Etapa 1: Estrutura de Pasta

```bash
mkdir -p src/modules/approvals/components
mkdir -p src/modules/approvals/api
mkdir -p src/modules/approvals/types
```

Crie um arquivo `index.tsx` que exporta a página principal:

```typescript
// src/modules/approvals/index.tsx
export { default } from './ApprovalsPage'
```

### Etapa 2: Componente Principal

```typescript
// src/modules/approvals/ApprovalsPage.tsx
import React, { useEffect } from 'react'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import { PageHeader, Card, Button, Table, Spinner, Badge } from '@/components/ui'
import { useApprovals } from './api/approvals'
import ApprovalDetailModal from './components/ApprovalDetailModal'

interface Approval {
  id: string
  title: string
  requestor: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

const ApprovalsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('modules')
  const { approvals, isLoading, error, refetch } = useApprovals(session?.tenant_id || '')
  
  const [selectedId, setSelectedId] = React.useState<string | null>(null)

  const columns = [
    { key: 'title', label: 'Solicitação', width: 'flex-1' },
    { key: 'requestor', label: 'Solicitante', width: 'w-32' },
    {
      key: 'status',
      label: 'Status',
      width: 'w-24',
      render: (v: string) => (
        <Badge variant={v === 'pending' ? 'warning' : v === 'approved' ? 'success' : 'error'}>
          {v}
        </Badge>
      )
    },
    { key: 'created_at', label: 'Data', width: 'w-32' },
  ]

  return (
    <div className="h-full overflow-hidden flex flex-col">
      <PageHeader
        title={t('approvals.title')}
        subtitle={t('approvals.subtitle')}
        actions={
          <Button onClick={() => refetch()}>
            {t('common.refresh')}
          </Button>
        }
      />

      <Card className="flex-1 mt-6 overflow-hidden flex flex-col">
        {isLoading && <Spinner />}
        {error && <div className="text-red-600 p-4">{error}</div>}
        
        {!isLoading && approvals.length === 0 && (
          <div className="flex items-center justify-center h-64 text-gray-500">
            {t('approvals.no_items')}
          </div>
        )}

        {!isLoading && approvals.length > 0 && (
          <Table
            columns={columns}
            data={approvals}
            onRowClick={(row: Approval) => setSelectedId(row.id)}
          />
        )}
      </Card>

      {selectedId && (
        <ApprovalDetailModal
          approval_id={selectedId}
          onClose={() => setSelectedId(null)}
          onApprove={() => {
            refetch()
            setSelectedId(null)
          }}
        />
      )}
    </div>
  )
}

export default ApprovalsPage
```

### Etapa 3: API Hooks

```typescript
// src/modules/approvals/api/approvals.ts
import { useEffect, useState } from 'react'
import { Approval } from '../types/approval'

interface UseApprovalsReturn {
  approvals: Approval[]
  isLoading: boolean
  error: string | null
  refetch: () => Promise<void>
}

export const useApprovals = (tenantId: string): UseApprovalsReturn => {
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchApprovals = async () => {
    if (!tenantId) return

    setIsLoading(true)
    setError(null)

    try {
      const response = await fetch(`/api/approvals?tenant_id=${tenantId}`, {
        headers: {
          'Accept': 'application/json',
        }
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()
      setApprovals(data.items || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao buscar aprovações')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchApprovals()
    // Polling a cada 30s
    const interval = setInterval(fetchApprovals, 30000)
    return () => clearInterval(interval)
  }, [tenantId])

  return {
    approvals,
    isLoading,
    error,
    refetch: fetchApprovals
  }
}

// Mutation hook para aprovar/rejeitar
export const useApproveDecision = () => {
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (approvalId: string, decision: 'approve' | 'reject', reason?: string) => {
    setIsSubmitting(true)
    setError(null)

    try {
      const response = await fetch(`/api/approvals/${approvalId}/decision`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ decision, reason })
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      return await response.json()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erro ao processar decisão'
      setError(msg)
      throw err
    } finally {
      setIsSubmitting(false)
    }
  }

  return { submit, isSubmitting, error }
}
```

### Etapa 4: Types

```typescript
// src/modules/approvals/types/approval.ts
export interface Approval {
  id: string
  title: string
  description: string
  requestor: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  updated_at: string
  data: Record<string, unknown>  // Payload específico da aprovação
}
```

### Etapa 5: Componentes Específicos

```typescript
// src/modules/approvals/components/ApprovalDetailModal.tsx
import React from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Button, Spinner } from '@/components/ui'
import { useApproveDecision } from '../api/approvals'

interface ApprovalDetailModalProps {
  approval_id: string
  onClose: () => void
  onApprove: () => void
}

const ApprovalDetailModal: React.FC<ApprovalDetailModalProps> = ({
  approval_id,
  onClose,
  onApprove
}) => {
  const { t } = useTranslation('modules')
  const { submit, isSubmitting, error } = useApproveDecision()
  const [reason, setReason] = React.useState('')

  const handleApprove = async () => {
    try {
      await submit(approval_id, 'approve', reason)
      onApprove()
      onClose()
    } catch {
      // Erro já armazenado em state
    }
  }

  const handleReject = async () => {
    try {
      await submit(approval_id, 'reject', reason)
      onApprove()
      onClose()
    } catch {
      // Erro já armazenado em state
    }
  }

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={t('approvals.decision_title')}
    >
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded">
          {error}
        </div>
      )}

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-semibold mb-2">
            {t('approvals.reason')}
          </label>
          <textarea
            className="w-full border border-gray-300 rounded p-2"
            rows={4}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t('approvals.reason_placeholder')}
          />
        </div>

        <div className="flex justify-end gap-2">
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={isSubmitting}
          >
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            onClick={handleReject}
            disabled={isSubmitting}
          >
            {isSubmitting ? <Spinner size="sm" /> : t('approvals.reject')}
          </Button>
          <Button
            onClick={handleApprove}
            disabled={isSubmitting}
          >
            {isSubmitting ? <Spinner size="sm" /> : t('approvals.approve')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

export default ApprovalDetailModal
```

### Etapa 6: Adicionar Rota

```typescript
// src/app/routes.tsx
import ApprovalsPage from '@/modules/approvals'

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <Shell />,
    children: [
      // ... rotas existentes
      {
        path: 'approvals',
        element: <ApprovalsPage />
      },
    ]
  }
]
```

### Etapa 7: Adicionar à Navegação

```typescript
// src/shell/Sidebar.tsx
const menuItems = [
  { label: 'Home', path: '/', icon: 'Home' },
  { label: 'Monitor', path: '/monitor', icon: 'Activity' },
  { label: 'Workflows', path: '/workflows', icon: 'Zap' },
  { label: 'Aprovações', path: '/approvals', icon: 'CheckCircle' },  // ← NOVO
  // ...
]
```

### Etapa 8: Adicionar Strings i18n

```json
// src/i18n/locales/pt-BR/modules.json
{
  "approvals": {
    "title": "Aprovações",
    "subtitle": "Aguardando sua decisão",
    "no_items": "Nenhuma aprovação pendente",
    "decision_title": "Tomar decisão",
    "reason": "Motivo (opcional)",
    "reason_placeholder": "Explique sua decisão...",
    "approve": "Aprovar",
    "reject": "Rejeitar"
  }
}
```

```json
// src/i18n/locales/en/modules.json
{
  "approvals": {
    "title": "Approvals",
    "subtitle": "Awaiting your decision",
    "no_items": "No pending approvals",
    "decision_title": "Make a decision",
    "reason": "Reason (optional)",
    "reason_placeholder": "Explain your decision...",
    "approve": "Approve",
    "reject": "Reject"
  }
}
```

### Etapa 9: Teste e Deploy

```bash
# Iniciar dev server
npm run dev

# Abrir http://localhost:5174/approvals
# Testar navegação, carregamento, interação

# Build para produção
npm run build

# Verificar warnings de TypeScript
npx tsc --noEmit
```

---

## Componentes UI: Quando Usar Cada Um

### Button

Qualquer ação interativa (click):

```typescript
<Button onClick={handleClick}>
  Enviar
</Button>

// Variantes disponíveis:
<Button variant="primary">Primário (padrão)</Button>
<Button variant="secondary">Secundário</Button>
<Button variant="danger">Perigoso</Button>
<Button disabled>Desabilitado</Button>
<Button size="sm">Pequeno</Button>
<Button size="lg">Grande</Button>
```

### Card

Agrupar conteúdo relacionado com border/shadow:

```typescript
<Card>
  <h2>Título</h2>
  <p>Conteúdo</p>
</Card>

// Com padding/margin customizado:
<Card className="p-8 mt-4">...</Card>
```

### Badge

Status/tags pequenas:

```typescript
<Badge>Default</Badge>
<Badge variant="status">Ativo</Badge>
<Badge variant="success">Sucesso</Badge>
<Badge variant="warning">Aviso</Badge>
<Badge variant="error">Erro</Badge>
```

### Table

Dados tabulares com suporte a sort/filter:

```typescript
<Table
  columns={[
    { key: 'name', label: 'Nome', width: 'flex-1' },
    { key: 'status', label: 'Status', width: 'w-24' },
  ]}
  data={items}
  onRowClick={(row) => setSelected(row.id)}
/>
```

### Modal

Diálogos bloqueantes:

```typescript
<Modal
  open={isOpen}
  onClose={handleClose}
  title="Confirmar ação"
>
  <p>Tem certeza?</p>
  <div className="flex gap-2 mt-6 justify-end">
    <Button variant="secondary" onClick={handleClose}>
      Cancelar
    </Button>
    <Button onClick={handleConfirm}>
      Confirmar
    </Button>
  </div>
</Modal>
```

### Input

Entrada de texto:

```typescript
<Input
  type="text"
  placeholder="Seu nome"
  value={name}
  onChange={(e) => setName(e.target.value)}
  error={nameError}
/>
```

### Select

Dropdown:

```typescript
<Select
  options={[
    { value: 'opt1', label: 'Opção 1' },
    { value: 'opt2', label: 'Opção 2' },
  ]}
  value={selected}
  onChange={setSelected}
/>
```

### Spinner

Loading indicator:

```typescript
<Spinner />
<Spinner size="sm" />
<Spinner size="lg" />
```

### PageHeader

Cabeçalho de página com título e actions:

```typescript
<PageHeader
  title="Meu Módulo"
  subtitle="Descrição"
  actions={
    <Button onClick={handleRefresh}>
      Atualizar
    </Button>
  }
/>
```

### EmptyState

Estado vazio em listas:

```typescript
<EmptyState
  icon="InboxIcon"
  title="Nenhum item"
  action={
    <Button onClick={handleCreate}>
      Criar novo
    </Button>
  }
/>
```

---

## Design Tokens: Paleta de Cores

### Uso semântico

| Token | Cor | Uso |
|---|---|---|
| **primary** | `#1B4F8A` | Botões primários, Sidebar, CTA principal |
| **secondary** | `#2D9CDB` | Links secundários, badges, texto destaque |
| **accent** | `#00B4D8` | Hover, focus, destaque leve |
| **green** | `#059669` | Status ok, sucesso, campos positivos |
| **warning** | `#D97706` | Alertas, campos obrigatórios, aviso |
| **red** | `#DC2626` | Erro, status crítico, ações perigosas |
| **gray-50** | `#F9FAFB` | Background principal |
| **gray-100** | `#F3F4F6` | Background secundário |
| **gray-200** | `#E5E7EB` | Borders, dividers |
| **gray-800** | `#1F2937` | Texto principal |

### Usando tokens em Tailwind

```typescript
// CERTO: Usar classes Tailwind
<button className="bg-primary text-white hover:bg-primary/90">
  Enviar
</button>

<div className="border-gray-200 p-4 bg-gray-50">
  Conteúdo
</div>

// ERRADO: Hex colors inline (NUNCA)
<button style={{ backgroundColor: '#1B4F8A' }}>  ❌
  Enviar
</button>

<div style={{ borderColor: '#E5E7EB' }}>  ❌
  Conteúdo
</div>
```

### Extender tokens no Tailwind config

Se precisar novo token:

```typescript
// tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: {
        primary: '#1B4F8A',
        secondary: '#2D9CDB',
        // ... adicione aqui, não inline no JSX
      }
    }
  }
}
```

---

## Autenticação: Proteger Conteúdo por Role

### Uso básico de useAuth

```typescript
const { session, isAuthenticated } = useAuth()

if (!isAuthenticated) {
  return <Navigate to="/login" />
}

console.log(session.roles)  // ['operator', 'supervisor']
```

### Condicional por role

```typescript
const canDelete = session?.roles.includes('admin')
const canApprove = session?.roles.includes('supervisor') || session?.roles.includes('admin')

return (
  <div>
    {canApprove && (
      <Button onClick={handleApprove}>Aprovar</Button>
    )}
    
    {!canDelete ? (
      <Button disabled>Deletar</Button>
    ) : (
      <Button variant="danger" onClick={handleDelete}>Deletar</Button>
    )}
  </div>
)
```

### Componente ProtectedByRole (criar se não existir)

```typescript
// src/components/ProtectedByRole.tsx
interface ProtectedByRoleProps {
  requiredRoles: string[]
  fallback?: React.ReactNode
  children: React.ReactNode
}

export const ProtectedByRole: React.FC<ProtectedByRoleProps> = ({
  requiredRoles,
  fallback = null,
  children
}) => {
  const { session } = useAuth()
  const hasRole = session?.roles.some(r => requiredRoles.includes(r))

  return hasRole ? <>{children}</> : <>{fallback}</>
}

// Uso:
<ProtectedByRole
  requiredRoles={['admin', 'supervisor']}
  fallback={<p>Sem permissão</p>}
>
  <Button onClick={handleDelete}>Deletar</Button>
</ProtectedByRole>
```

---

## i18n: Adicionar Traduções

### Estrutura recomendada

```json
// src/i18n/locales/pt-BR/modules.json
{
  "nome_modulo": {
    "page": {
      "title": "Título da página",
      "subtitle": "Subtítulo"
    },
    "components": {
      "card_title": "Título do card"
    },
    "messages": {
      "success": "Operação realizada!",
      "error": "Erro ao processar",
      "no_items": "Nenhum item encontrado"
    },
    "buttons": {
      "save": "Salvar",
      "delete": "Deletar",
      "cancel": "Cancelar"
    }
  }
}
```

### Uso no componente

```typescript
import { useTranslation } from 'react-i18next'

export const MyComponent = () => {
  const { t } = useTranslation('modules')

  return (
    <div>
      <h1>{t('nome_modulo.page.title')}</h1>
      {isSuccess && (
        <div className="text-green-600">
          {t('nome_modulo.messages.success')}
        </div>
      )}
      <Button>{t('nome_modulo.buttons.save')}</Button>
    </div>
  )
}
```

---

## Layouts: Padrão vs Full-Bleed

### Padrão (com padding)

Usado em `/`, `/config/*`, `/dashboards`:

```typescript
// Shell renderiza automaticamente:
<main className="flex-1 overflow-auto">
  <div className="px-6 py-6">
    <Outlet />
  </div>
</main>
```

**Quando usar:** Formulários, listas simples, páginas de config, dashboards estatísticos

### Full-bleed (sem padding)

Usado em `/monitor`, `/workflows`, `/agent-assist`:

```typescript
// Shell renderiza automaticamente:
<main className="flex-1 overflow-hidden">
  <Outlet />
</main>
```

**Quando usar:** Visualizadores em tempo real, gráficos, heatmaps, transcripts ao vivo

### Para sua nova rota

Se quer full-bleed:

```typescript
// src/shell/Shell.tsx
const FULL_BLEED_ROUTES = [
  '/monitor',
  '/agent-assist',
  '/config/platform',
  '/workflows',
  '/approvals'  // ← Adicione aqui se quiser full-bleed
]
```

---

## Anti-padrões: Nunca Fazer

### ❌ 1. Criar novo package React standalone

```typescript
// ERRADO: novo diretório packages/novo-app/
// Resultado: apps paralelos, autenticação duplicada, design system quebrado

// CERTO: novo módulo em src/modules/novo-app/
```

### ❌ 2. Hex colors inline no JSX

```typescript
// ERRADO
<button style={{ backgroundColor: '#1B4F8A' }}>
  Enviar
</button>

// CERTO
<button className="bg-primary">
  Enviar
</button>
```

### ❌ 3. CSS customizado sem tokens

```typescript
// ERRADO
const MyButton = styled.button`
  background-color: #1B4F8A;
  padding: 8px 16px;
  border-radius: 4px;
`

// CERTO: Usar componentes UI existentes ou estender Tailwind via config
<Button className="px-4 py-2 rounded">
  Enviar
</Button>
```

### ❌ 4. State global sem contexto

```typescript
// ERRADO
let globalUser = null

// CERTO: Usar AuthContext ou criar Context específico
const { session } = useAuth()
// ou
const { data } = useMyContext()
```

### ❌ 5. Fetch sem abstração

```typescript
// ERRADO
const MyComponent = () => {
  const [data, setData] = useState(null)
  useEffect(() => {
    fetch('/api/my-endpoint')
      .then(r => r.json())
      .then(setData)
  }, [])
}

// CERTO: Hook customizado com lógica centralizada
const { data } = useMyModule()
```

### ❌ 6. i18n hardcoded

```typescript
// ERRADO
<h1>Meu Módulo</h1>

// CERTO
const { t } = useTranslation('modules')
<h1>{t('meu_modulo.title')}</h1>
```

### ❌ 7. Componentes UI sem reutilização

```typescript
// ERRADO: Criar botão customizado no módulo
const MyButton = (props) => (
  <button className="bg-blue-500 text-white p-2 rounded">
    {props.children}
  </button>
)

// CERTO: Usar <Button /> do @/components/ui
import { Button } from '@/components/ui'
<Button>{children}</Button>
```

### ❌ 8. API calls sem tratamento de erro

```typescript
// ERRADO
const data = await fetch('/api/x').then(r => r.json())
setData(data)

// CERTO: Try-catch, error state, mensagem ao usuário
try {
  const res = await fetch('/api/x')
  if (!res.ok) throw new Error(...)
  setData(await res.json())
} catch (err) {
  setError(err.message)
  // Mostrar erro em UI
}
```

### ❌ 9. TypeScript `any`

```typescript
// ERRADO
const data: any = response.json()

// CERTO: Type explícito
interface MyData {
  id: string
  name: string
}
const data: MyData = await response.json()
```

### ❌ 10. Modules fora de src/modules/

```typescript
// ERRADO
src/
  pages/
    MyPage.tsx
  components/
    MyFeatureComponent.tsx

// CERTO
src/
  modules/
    meu-modulo/
      components/
        MyFeatureComponent.tsx
      MeuModuloPage.tsx
```

---

## Checklist: Nova Feature

- [ ] Rota adicionada em `routes.tsx`
- [ ] Nav item adicionado em `Sidebar.tsx` (se novo módulo)
- [ ] Componente principal criado em `modules/*/`
- [ ] API hooks criados em `api/`
- [ ] Types definidos em `types/` ou `api/`
- [ ] Strings i18n adicionadas (PT-BR e EN)
- [ ] Todos componentes UI usam `@/components/ui`
- [ ] Design tokens usados (sem hex inline)
- [ ] TypeScript sem `any`
- [ ] Error handling em todas as API calls
- [ ] Loading states visíveis
- [ ] Testado em dev: `npm run dev`
- [ ] Build sem warnings: `npm run build`
- [ ] TypeScript check: `npx tsc --noEmit`

---

## Referência Rápida: Imports Comuns

```typescript
// Autenticação
import { useAuth } from '@/auth/useAuth'
import { ProtectedRoute } from '@/auth/ProtectedRoute'

// UI Components
import { Button, Card, Badge, Modal, Input, Select, Spinner, PageHeader, EmptyState, Table } from '@/components/ui'

// i18n
import { useTranslation } from 'react-i18next'

// React Router
import { useNavigate, useParams, useLocation, Navigate } from 'react-router-dom'

// Types
import type { Session } from '@/types'
```

---

## Suporte

Para dúvidas sobre padrões, arquitetura ou implementação:
- Consulte `docs/modulos/platform-ui.md` para referência da estrutura
- Veja exemplos em módulos existentes: `src/modules/atendimento/`, `src/modules/workflows/`
- Abra issue no repositório com a tag `frontend`
