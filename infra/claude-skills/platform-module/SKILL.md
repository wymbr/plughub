---
name: platform-module
description: "Adicionar novo módulo/painel à interface do operador (platform-ui). Use quando o usuário pedir para criar telas, painéis ou funcionalidades na web app. Inclui registro de rotas, sidebar, componentes UI e i18n."
license: Proprietary. LICENSE.txt has complete terms
---

# Skill: Novo Módulo no platform-ui

## Quando usar

Use este skill sempre que o usuário pedir para:
- Adicionar um novo painel, tela ou seção à interface do operador
- Criar uma página de configuração, relatório, dashboard ou listagem
- Integrar uma nova funcionalidade visual que exija roteamento e navegação

Exemplos de trigger:
- "Crie um painel de configuração de webhooks"
- "Adicione uma tela de gestão de usuários"
- "Implemente um dashboard de métricas"

## Pré-requisito: conhecer o projeto

Antes de começar qualquer código:

1. Leia `packages/platform-ui/src/` — estrutura de componentes e rotas
2. Verifique `src/components/ui/` — componentes disponíveis (Button, Card, Table, etc.)
3. Consulte `src/app/routes.tsx` — padrão de registro de rotas
4. Examine `src/shell/Sidebar.tsx` — padrão de NavItem

## Passo a passo

### 1. Criar pasta e componente principal

```bash
mkdir -p packages/platform-ui/src/modules/{nome_modulo}
```

Criar arquivo `packages/platform-ui/src/modules/{nome_modulo}/index.tsx`:

```typescript
import React from 'react'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { useTranslation } from 'react-i18next'

export const {NomeModulo}Page: React.FC = () => {
  const { t } = useTranslation('{nome_modulo}')

  return (
    <div className="p-6">
      <PageHeader 
        title={t('title')}
        description={t('description')}
      />
      
      <div className="mt-8 space-y-4">
        <Card>
          <h3 className="text-lg font-semibold text-gray-900">
            {t('section_title')}
          </h3>
          <p className="text-gray-600">
            {t('section_description')}
          </p>
        </Card>
      </div>
    </div>
  )
}

export default {NomeModulo}Page
```

### 2. Registrar a rota em routes.tsx

Arquivo: `packages/platform-ui/src/app/routes.tsx`

Adicionar import no topo:
```typescript
import {NomeModulo}Page from '@/modules/{nome_modulo}'
```

Adicionar ao array `routes` dentro da seção `/shell` (depois do check de autenticação):

```typescript
{
  path: '{url_path}',
  element: <{NomeModulo}Page />,
  loader: () => ({ title: '{page_title}' })
}
```

Exemplo completo:
```typescript
{
  path: 'billing',
  element: <BillingPage />,
  loader: () => ({ title: 'Billing Configuration' })
}
```

### 3. Adicionar ao Sidebar

Arquivo: `packages/platform-ui/src/shell/Sidebar.tsx`

Adicionar novo `NavItem` ao array `navItems` com o seguinte padrão:

```typescript
{
  label: 'Billing',                    // texto exibido
  href: '/shell/billing',               // rota registrada
  icon: DollarSign,                     // ícone (importado de @heroicons/react)
  roles: ['operator', 'admin']          // filtro por roles
}
```

**Roles disponíveis:**
- `admin` — acesso total
- `operator` — operador de plataforma
- `agent` — agente humano

### 4. Adicionar traduções (opcional mas recomendado)

Criar arquivo: `packages/platform-ui/src/i18n/locales/{nome_modulo}.json`

Estrutura mínima:
```json
{
  "title": "Meu Módulo",
  "description": "Descrição do módulo",
  "section_title": "Seção Principal",
  "section_description": "Descrição da seção"
}
```

Registrar em `src/i18n/i18n.ts`:
```typescript
import {nome_modulo}PT from './locales/{nome_modulo}.json'

resources: {
  pt: {
    '{nome_modulo}': {nome_modulo}PT,
    // ... outros namespaces
  }
}
```

## Componentes disponíveis em src/components/ui/

| Componente | Import | Quando usar |
|---|---|---|
| **Button** | `import { Button } from '@/components/ui/Button'` | Ações, triggers, submit, close |
| **Card** | `import { Card } from '@/components/ui/Card'` | Containers de conteúdo, panels, grupos |
| **Table** | `import { Table } from '@/components/ui/Table'` | Listas estruturadas, grids de dados |
| **Badge** | `import { Badge } from '@/components/ui/Badge'` | Status, labels, tags, categorias |
| **Modal** | `import { Modal } from '@/components/ui/Modal'` | Confirmações, diálogos, forms modais |
| **Input** | `import { Input } from '@/components/ui/Input'` | Campos de texto, busca, filtros |
| **Select** | `import { Select } from '@/components/ui/Select'` | Dropdowns, seletores, combos |
| **PageHeader** | `import { PageHeader } from '@/components/ui/PageHeader'` | Cabeçalho da página com título e descrição |
| **EmptyState** | `import { EmptyState } from '@/components/ui/EmptyState'` | Estado vazio, sem dados, nada encontrado |
| **Spinner** | `import { Spinner } from '@/components/ui/Spinner'` | Indicador de carregamento |

## Design tokens Tailwind

| Token | Valor Hex | Uso |
|---|---|---|
| `text-primary` / `bg-primary` | #1B4F8A | Cor primária, sidebar, botões principais |
| `text-secondary` / `bg-secondary` | #2D9CDB | Ações secundárias, links, destaques |
| `text-green-600` | #059669 | Sucesso, ativo, positivo |
| `text-amber-600` | #D97706 | Alerta, aviso, pendente |
| `text-red-600` | #DC2626 | Erro, crítico, negativo |
| `text-gray-900` | #111827 | Texto principal (bodies) |
| `text-gray-600` | #4B5563 | Texto secundário, subtítulos |

## Exemplo completo: módulo /billing

### Estrutura de pastas
```
packages/platform-ui/src/modules/billing/
├── index.tsx
└── (components adicionais, if needed)
```

### Código do componente
```typescript
// packages/platform-ui/src/modules/billing/index.tsx
import React, { useEffect, useState } from 'react'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { Table } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'

interface InvoiceRow {
  id: string
  period: string
  amount: number
  status: 'paid' | 'pending' | 'overdue'
}

export const BillingPage: React.FC = () => {
  const { t } = useTranslation('billing')
  const { session } = useAuth()
  const [invoices, setInvoices] = useState<InvoiceRow[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchInvoices = async () => {
      try {
        const response = await fetch(`/api/billing/invoices?tenant_id=${session?.tenant_id}`)
        const data = await response.json()
        setInvoices(data)
      } catch (error) {
        console.error('Failed to fetch invoices:', error)
      } finally {
        setLoading(false)
      }
    }

    if (session?.tenant_id) {
      fetchInvoices()
    }
  }, [session?.tenant_id])

  const statusBadgeColor = (status: string) => {
    switch (status) {
      case 'paid':
        return 'bg-green-100 text-green-800'
      case 'pending':
        return 'bg-amber-100 text-amber-800'
      case 'overdue':
        return 'bg-red-100 text-red-800'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }

  return (
    <div className="p-6">
      <PageHeader 
        title={t('title')}
        description={t('description')}
        actions={[
          <Button key="export" variant="outline">{t('export')}</Button>,
          <Button key="download">{t('download_invoice')}</Button>
        ]}
      />

      <div className="mt-8 space-y-6">
        <Card>
          <div className="grid grid-cols-3 gap-4 mb-6">
            <div>
              <p className="text-sm text-gray-600">{t('total_due')}</p>
              <p className="text-2xl font-bold text-primary">R$ 15.000,00</p>
            </div>
            <div>
              <p className="text-sm text-gray-600">{t('next_billing')}</p>
              <p className="text-lg font-semibold">Maio 1º, 2026</p>
            </div>
            <div>
              <p className="text-sm text-gray-600">{t('status')}</p>
              <Badge className="mt-1">Ativo</Badge>
            </div>
          </div>
        </Card>

        <Card>
          <h3 className="text-lg font-semibold text-gray-900 mb-4">
            {t('invoices')}
          </h3>
          {loading ? (
            <p className="text-gray-600">{t('loading')}</p>
          ) : invoices.length === 0 ? (
            <p className="text-gray-500 text-center py-8">{t('no_invoices')}</p>
          ) : (
            <Table
              columns={[
                { header: t('period'), accessor: 'period' },
                { header: t('amount'), accessor: 'amount' },
                { 
                  header: t('status'), 
                  accessor: 'status',
                  cell: (status) => (
                    <Badge className={statusBadgeColor(status)}>
                      {t(`status_${status}`)}
                    </Badge>
                  )
                }
              ]}
              data={invoices}
            />
          )}
        </Card>
      </div>
    </div>
  )
}

export default BillingPage
```

### Registro em routes.tsx
```typescript
import BillingPage from '@/modules/billing'

// Dentro do array routes, na seção /shell:
{
  path: 'billing',
  element: <BillingPage />,
  loader: () => ({ title: 'Billing & Invoicing' })
}
```

### Adição ao Sidebar
```typescript
import { DollarSign } from '@heroicons/react/outline'

// No array navItems:
{
  label: 'Billing',
  href: '/shell/billing',
  icon: DollarSign,
  roles: ['admin', 'operator']
}
```

### Arquivo de tradução
```json
{
  "title": "Faturamento",
  "description": "Gerencie suas faturas e planos de cobrança",
  "export": "Exportar",
  "download_invoice": "Baixar NF",
  "total_due": "Total a Vencer",
  "next_billing": "Próximo Ciclo",
  "status": "Status",
  "invoices": "Faturas Recentes",
  "period": "Período",
  "amount": "Valor",
  "status_paid": "Pago",
  "status_pending": "Pendente",
  "status_overdue": "Vencido",
  "loading": "Carregando...",
  "no_invoices": "Nenhuma fatura encontrada"
}
```

## Padrões de autenticação e contexto

### Acessar informações da sessão
```typescript
import { useAuth } from '@/auth/useAuth'

const { session } = useAuth()
// session.role: 'admin' | 'operator' | 'agent'
// session.tenant_id: string
// session.user_id: string
```

### Proteger rotas por role
```typescript
const canAccess = session?.role === 'admin' || session?.role === 'operator'
if (!canAccess) {
  return <ErrorPage message="Access denied" />
}
```

## Padrão de fetch com erro tratado

```typescript
const fetchData = async () => {
  setLoading(true)
  try {
    const response = await fetch(`/api/endpoint?tenant_id=${session?.tenant_id}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    setData(data)
  } catch (error) {
    console.error('Fetch failed:', error)
    setError(error instanceof Error ? error.message : 'Unknown error')
  } finally {
    setLoading(false)
  }
}
```

## Checklist antes de declarar pronto

- [ ] Pasta `src/modules/{nome}/` criada com `index.tsx`
- [ ] Componente exporta default e usa APENAS componentes de `@/components/ui/`
- [ ] Rota registrada em `src/app/routes.tsx` com path, element e loader
- [ ] `NavItem` adicionado ao Sidebar com label, href, icon e roles corretos
- [ ] Nenhuma cor hex inline (`#1B4F8A`, etc.) — usar classes Tailwind (`text-primary`)
- [ ] Nenhum CSS customizado; usar Tailwind classes para estilo
- [ ] Arquivo i18n criado (opcional) ou strings hardcoded tem fallback
- [ ] Componente renderiza sem erros (console vazio)
- [ ] NavItem aparece no Sidebar apenas para roles autorizadas

## Anti-padrões (NUNCA fazer)

❌ **Criar novo `packages/meu-modulo/` standalone**
  - ✅ SEMPRE adicionar à `packages/platform-ui/src/modules/`

❌ **Escrever cores hex inline**
  ```typescript
  // ERRADO:
  <div style={{ color: '#1B4F8A' }}>
  // CERTO:
  <div className="text-primary">
  ```

❌ **Criar novo arquivo CSS ou usar styled-components**
  - ✅ Usar classes Tailwind. Se componente não existe, criar em `src/components/ui/`

❌ **Ignorar roles e autorização**
  - ✅ Todo NavItem deve ter `roles` definido
  - ✅ Verificar `useAuth()` para proteger conteúdo sensível

❌ **Colocar lógica de negócio no componente**
  - ✅ Extrair hooks customizados para `src/hooks/` se lógica ficar complexa

❌ **Usar imports relativos além de 2 níveis**
  - ✅ Usar alias: `import { X } from '@/components/...'` ou `@/hooks/...`

❌ **Não registrar em routes.tsx**
  - ✅ Sempre registrar a rota no arquivo central

## Atalhos úteis

**Ícones disponíveis** — importar de `@heroicons/react`:
```typescript
import { 
  CogIcon,           // Configurações
  ChartBarIcon,      // Analytics
  DocumentIcon,      // Documentos
  UserGroupIcon,     // Usuários
  DollarSignIcon,    // Faturamento
  BellIcon,          // Notificações
  ShieldCheckIcon    // Segurança
} from '@heroicons/react/outline'
```

**Componentes compostos (Card + Table combinados):**
```typescript
<Card>
  <div className="flex justify-between items-center mb-4">
    <h3 className="text-lg font-semibold">{title}</h3>
    <Button variant="outline">{action}</Button>
  </div>
  <Table data={data} columns={columns} />
</Card>
```
