# PlugHub Design System — `components/ui`

Shared primitive components for the PlugHub operator UI. All components use semantic design tokens from `tailwind.config.ts` — **never use raw hex values or Tailwind color-scale classes** (e.g. `text-blue-600`, `bg-green-100`) in new code.

---

## Design Tokens

### Colors

| Token | Hex | Use |
|---|---|---|
| `primary` | `#1B4F8A` | Buttons, links, focus rings, active states |
| `primary-dark` | `#163F70` | Hover/active on primary |
| `primary-light` | `#DBEAFE` | Tinted backgrounds, selected rows |
| `secondary` | `#2D9CDB` | Secondary actions |
| `dark` | `#1A1A2E` | Body text, headings |
| `muted` | `#6B7280` | Secondary text, labels |
| `muted-light` | `#9CA3AF` | Placeholders, disabled labels |
| `border` | `#E5E7EB` | Default borders |
| `border-strong` | `#D1D5DB` | Stronger borders, dividers |
| `surface` | `#FFFFFF` | Card/panel backgrounds |
| `surface-muted` | `#F9FAFB` | Shell/app background |
| `surface-alt` | `#F3F4F6` | Alternate rows, hover on ghost items |

**Semantic palettes** — each has three tiers: base (icon/text on white), `-light` (background tint), `-text` (text on light background):

| Semantic | Base | Light | Text |
|---|---|---|---|
| Success | `green` | `green-light` | `green-text` |
| Warning | `warning` | `warning-light` | `warning-text` |
| Error / danger | `red` | `red-light` | `red-text` |
| Info | `info` | `info-light` | `info-text` |
| Contested (orange) | `contested` | `contested-light` | `contested-text` |
| Revised (teal) | `revised` | `revised-light` | `revised-text` |
| AI agents (indigo) | `ai` | `ai-light` | `ai-text` |

### Typography

| Token | Size | Use |
|---|---|---|
| `text-micro` | 9px | Superscript counts, tiny badges |
| `text-2xs` | 10px | Compact badge text, table metadata |
| `text-xs` | 12px | Default small text (Tailwind built-in) |
| `text-sm` | 14px | Labels, secondary content |
| `text-base` | 16px | Body copy, button default |

Font: **Inter** (`font-sans`).

### Z-index scale

| Token | Value | Use |
|---|---|---|
| `z-dropdown` | 10 | Dropdowns, tooltips inline |
| `z-sticky` | 20 | Sticky headers |
| `z-overlay` | 30 | Drawers/side panels |
| `z-modal` | 40 | Modals/dialogs |
| `z-toast` | 50 | Toast notifications |
| `z-tooltip` | 60 | Floating tooltips |

### Shadows

| Token | Use |
|---|---|
| `shadow-card` | Cards, list items |
| `shadow-panel` | Side panels |
| `shadow-modal` | Modals, drawers |

---

## Components

### Button

Extends `React.ButtonHTMLAttributes`. Forwards ref.

```tsx
import Button from '@/components/ui/Button'

<Button variant="primary" size="md" onClick={handleSave}>
  Salvar
</Button>

<Button variant="danger" loading={isDeleting}>
  Excluir
</Button>

<Button variant="ghost" leftIcon={<Plus className="w-4 h-4" />}>
  Adicionar
</Button>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `variant` | `'primary' \| 'secondary' \| 'ghost' \| 'danger'` | `'primary'` | Visual style |
| `size` | `'sm' \| 'md' \| 'lg'` | `'md'` | Padding and font size |
| `loading` | `boolean` | `false` | Shows spinner; blocks interaction |
| `leftIcon` | `ReactNode` | — | Icon before the label |
| `rightIcon` | `ReactNode` | — | Icon after the label |
| `disabled` | `boolean` | — | Inherited from HTML |

**Do / Don't**

✅ Use `variant="danger"` for destructive actions, `variant="ghost"` for low-emphasis actions.  
✅ Pass `loading` when the action has async side effects — it prevents double-clicks automatically.  
❌ Don't add `pointer-events-none` manually — use the `disabled` prop or `loading`.  
❌ Don't use raw `<button>` elements in new pages — use this component for consistent focus styles.

---

### Badge

Read-only semantic label. Not interactive.

```tsx
import Badge from '@/components/ui/Badge'

<Badge variant="active">Ativo</Badge>
<Badge variant="ai" dot>Claude Sonnet</Badge>
<Badge variant="contested">Em revisão</Badge>
```

**Variants**

| Group | Values |
|---|---|
| Agent lifecycle | `active`, `suspended`, `failed`, `default` |
| Workflow | `pending`, `processing`, `completed`, `cancelled` |
| Semantic | `success`, `warning`, `error`, `info` |
| Evaluation | `approved`, `contested`, `rejected`, `revised` |
| Agent type | `ai`, `human` |

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `variant` | `BadgeVariant` | `'default'` | Color and semantic meaning |
| `dot` | `boolean` | `false` | Prepends a colored dot indicator |
| `className` | `string` | `''` | Extra Tailwind classes |

**Do / Don't**

✅ Use the domain-specific variants (`ai`, `human`, `contested`, `revised`) for evaluation and agent-type UIs.  
✅ Use `dot` to add visual weight without changing size — useful in compact table cells.  
❌ Don't use `Badge` for clickable filters — use `<button>` styled as a chip instead.  
❌ Don't override badge colors with `className` using raw hex — extend `variantClasses` in the component if a new variant is needed.

---

### Alert

Inline contextual message. Dismissible.

```tsx
import Alert from '@/components/ui/Alert'

<Alert variant="warning" title="Atenção">
  Esta ação não pode ser desfeita.
</Alert>

<Alert variant="error" onDismiss={() => setError(null)}>
  {error}
</Alert>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `variant` | `'info' \| 'success' \| 'warning' \| 'error'` | `'info'` | Semantic color |
| `title` | `string` | — | Optional bold heading |
| `onDismiss` | `() => void` | — | Shows × button when provided |

**Do / Don't**

✅ Use `title` for critical alerts where the type (error/warning) alone isn't self-explanatory.  
✅ Prefer inline `Alert` over toast for errors from form submission — it persists until the user dismisses.  
❌ Don't use `Alert` for success confirmation of a completed action — prefer a brief toast for that.

---

### Input

Labeled text input with error state. Forwards ref.

```tsx
import Input from '@/components/ui/Input'

<Input
  label="Nome do agente"
  placeholder="agente_retencao_v1"
  value={name}
  onChange={e => setName(e.target.value)}
  error={errors.name}
/>
```

**Props**

| Prop | Type | Description |
|---|---|---|
| `label` | `string` | Visible label above the input |
| `error` | `string` | Error message below; turns border red |

Accepts all `HTMLInputElement` attributes.

**Do / Don't**

✅ Always provide `label` for accessibility — don't rely on `placeholder` alone.  
❌ Note: `Input` currently uses the legacy `border-lightGray` token internally. Prefer `Textarea` or native `<input>` with semantic tokens for new fields until this is fixed.

---

### Textarea

Multi-line input with character counter and hint. Forwards ref.

```tsx
import Textarea from '@/components/ui/Textarea'

<Textarea
  label="Instrução de sistema"
  hint="Descreva o comportamento esperado do agente."
  maxLength={2000}
  showCount
  rows={6}
  value={prompt}
  onChange={e => setPrompt(e.target.value)}
  error={errors.prompt}
/>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `label` | `string` | — | Visible label |
| `hint` | `string` | — | Helper text below (hidden when error shown) |
| `error` | `string` | — | Error message; turns border red |
| `maxLength` | `number` | — | Enables counter; passed to native maxlength |
| `showCount` | `boolean` | `false` | Show counter even without maxLength |
| `rows` | `number` | `3` | Initial height |

**Do / Don't**

✅ Use `hint` for format guidance ("Máximo 200 caracteres") — it disappears automatically when an `error` appears.  
✅ Set `maxLength` on sensitive fields (prompts, notes) to prevent accidental large submissions.  
❌ Don't set `resize="none"` via className unless the height is truly fixed — users on small screens need to resize.

---

### Select

Labeled native `<select>`. Forwards ref.

```tsx
import Select from '@/components/ui/Select'

<Select
  label="Canal"
  options={[
    { value: 'webchat', label: 'WebChat' },
    { value: 'whatsapp', label: 'WhatsApp' },
  ]}
  value={channel}
  onChange={e => setChannel(e.target.value)}
  error={errors.channel}
/>
```

**Props**

| Prop | Type | Description |
|---|---|---|
| `label` | `string` | Visible label |
| `options` | `{ value: string; label: string }[]` | Option list |
| `error` | `string` | Error message; turns border red |

Accepts all `HTMLSelectElement` attributes.

**Do / Don't**

✅ Use for short, static lists (< 10 options).  
❌ Note: like `Input`, uses legacy `border-lightGray` token — pass `className` with `border-border` if consistency matters.  
❌ Don't use `Select` for async-loaded, searchable, or multi-select lists — implement a custom combobox.

---

### Checkbox

Accessible checkbox with optional indeterminate state. Forwards ref.

```tsx
import Checkbox from '@/components/ui/Checkbox'

<Checkbox
  label="Ativar avaliação automática"
  checked={autoEval}
  onChange={e => setAutoEval(e.target.checked)}
/>

<Checkbox
  label="Selecionar todos"
  indeterminate={someChecked && !allChecked}
  checked={allChecked}
  onChange={e => selectAll(e.target.checked)}
/>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `label` | `ReactNode` | — | Visible label; omit for standalone checkbox |
| `error` | `string` | — | Error message below |
| `indeterminate` | `boolean` | `false` | Visual dash state (select-all pattern) |

**Do / Don't**

✅ Use `indeterminate` for the parent checkbox in a select-all table pattern.  
✅ Omit `label` only when a `aria-label` is provided on the element itself.  
❌ Don't use raw `<input type="checkbox">` in new code — this component handles `aria-invalid`, `aria-describedby`, and disabled styles consistently.

---

### Switch

Toggle control rendered as an accessible `role="switch"` button. Forwards ref.

```tsx
import Switch from '@/components/ui/Switch'

<Switch
  checked={isEnabled}
  onChange={setIsEnabled}
  label="Modo de avaliação"
  description="Ativa a avaliação automática ao final de cada sessão."
/>
```

**Props**

| Prop | Type | Description |
|---|---|---|
| `checked` | `boolean` | Controlled value |
| `onChange` | `(checked: boolean) => void` | Called on toggle |
| `label` | `ReactNode` | Visible label next to the toggle |
| `description` | `string` | Smaller helper text below the label |
| `disabled` | `boolean` | Grays out and prevents interaction |

**Do / Don't**

✅ Use `Switch` instead of `Checkbox` for binary settings that take effect immediately (no submit required).  
✅ Always provide `label` — a bare toggle with no label is inaccessible.  
❌ Don't use `Switch` inside forms where the user must press Save — use `Checkbox` instead to make the pending-save state obvious.

---

### Tabs

Accessible tablist with keyboard navigation. Supports `underline` (default) and `pill` visual styles.

```tsx
import Tabs from '@/components/ui/Tabs'

const TABS = [
  { key: 'geral',    label: 'Geral' },
  { key: 'membros',  label: 'Membros', count: memberCount },
  { key: 'turnos',   label: 'Turnos' },
  { key: 'inativo',  label: 'Inativo', disabled: true },
]

<Tabs
  tabs={TABS}
  activeTab={tab}
  onChange={setTab}
  aria-label="Configurações do grupo"
/>

{/* With co-located panels */}
<Tabs
  tabs={TABS}
  activeTab={tab}
  onChange={setTab}
  variant="pill"
  panels={{
    geral:   <GeralPanel />,
    membros: <MembrosPanel />,
  }}
/>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `tabs` | `TabItem[]` | — | Tab definitions |
| `activeTab` | `string` | — | Currently selected tab key |
| `onChange` | `(key: string) => void` | — | Called on tab change |
| `variant` | `'underline' \| 'pill'` | `'underline'` | Visual style |
| `panels` | `Record<string, ReactNode>` | — | Optional: co-locate panels for automatic role/aria wiring |
| `aria-label` | `string` | — | Screen reader label for the tablist |

**TabItem fields**

| Field | Type | Description |
|---|---|---|
| `key` | `string` | Unique identifier |
| `label` | `ReactNode` | Visible label |
| `count` | `number` | Badge count (e.g. unread items) |
| `disabled` | `boolean` | Renders non-interactive |

**Keyboard behavior**: `←` / `→` navigate tabs; `Home` / `End` jump to first/last.

**Do / Don't**

✅ Provide `aria-label` when multiple Tabs components appear on the same page.  
✅ Use `panels` prop when content can live inline — it handles `role="tabpanel"` and `aria-controls` automatically.  
✅ Use `variant="pill"` inside cards and compact panels; `underline` for top-level page navigation.  
❌ Don't put tab content in the `panels` prop if the panel needs to be lazy-loaded — manage it externally and use `hidden` on the container yourself.

---

### Modal

Centered dialog overlay with scroll lock, Escape key, and focus management.

```tsx
import Modal from '@/components/ui/Modal'
import Button from '@/components/ui/Button'

<Modal
  isOpen={showModal}
  onClose={() => setShowModal(false)}
  title="Criar pool"
  size="lg"
  footer={
    <>
      <Button variant="ghost" onClick={() => setShowModal(false)}>Cancelar</Button>
      <Button variant="primary" loading={saving} onClick={handleSave}>Salvar</Button>
    </>
  }
>
  <PoolForm />
</Modal>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `isOpen` | `boolean` | — | Controls visibility |
| `onClose` | `() => void` | — | Called on Escape or backdrop click |
| `title` | `string` | — | Dialog heading (sets `aria-labelledby`) |
| `size` | `'sm' \| 'md' \| 'lg' \| 'xl'` | `'md'` | Max width: 384 / 448 / 672 / 896px |
| `footer` | `ReactNode` | — | Sticky footer; conventionally holds action buttons |
| `disableBackdropClose` | `boolean` | `false` | Block click-outside-to-close |
| `loading` | `boolean` | `false` | Overlays a spinner on the body |

**Do / Don't**

✅ Put primary action first in the `footer` slot on the right — Cancel left, Confirm right.  
✅ Use `disableBackdropClose` for multi-step wizards where an accidental dismiss would lose progress.  
✅ Use `loading` on the modal (not just the button) when saving blocks the entire form.  
❌ Don't nest modals — use `Drawer` for detail panels that appear alongside a modal.  
❌ Don't use `Modal` for destructive confirmations without explicit warning copy in the body.

---

### Drawer

Right-side panel overlay with focus trap, scroll lock, and keyboard dismiss.

```tsx
import Drawer from '@/components/ui/Drawer'

<Drawer
  isOpen={!!selectedItem}
  onClose={() => setSelectedItem(null)}
  title="Detalhes do agente"
  size="md"
  description="Informações do agente selecionado"
  footer={
    <Button variant="primary" onClick={handleSave}>Salvar</Button>
  }
>
  <AgentDetailPanel agent={selectedItem} />
</Drawer>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `isOpen` | `boolean` | — | Controls visibility |
| `onClose` | `() => void` | — | Called on Escape or backdrop click |
| `title` | `string` | — | Panel heading |
| `size` | `'sm' \| 'md' \| 'lg' \| 'xl'` | `'md'` | Width: 320 / 384 / 480 / 640px |
| `footer` | `ReactNode` | — | Sticky bottom bar |
| `disableBackdropClose` | `boolean` | `false` | Block click-outside-to-close |
| `description` | `string` | — | `sr-only` description for screen readers |

**Do / Don't**

✅ Prefer `Drawer` over `Modal` for detail/edit panels that coexist with the list behind them.  
✅ Use `description` to give screen reader users context about the panel's purpose.  
✅ Use `size="lg"` or `"xl"` for panels with complex forms or multi-column layouts.  
❌ Don't use `Drawer` for simple confirmation dialogs — use `Modal`.

---

### Card

Simple white content block with optional title.

```tsx
import Card from '@/components/ui/Card'

<Card title="Configurações gerais">
  <p className="text-sm text-muted">…</p>
</Card>

<Card className="p-4">
  <KpiStrip />
</Card>
```

**Props**

| Prop | Type | Description |
|---|---|---|
| `title` | `string` | Optional `<h2>` heading |
| `children` | `ReactNode` | Card body |
| `className` | `string` | Extra classes (overrides default `p-6`) |

**Do / Don't**

✅ Use `className="p-4"` to override the default `p-6` for compact cards.  
❌ Don't use `Card` for interactive list items — those need hover/focus states that Card doesn't provide.

---

### PageHeader

Standard page title row with optional breadcrumbs and action button.

```tsx
import PageHeader from '@/components/ui/PageHeader'
import Button from '@/components/ui/Button'

<PageHeader
  title="Grupos de Agentes"
  breadcrumbs={[
    { label: 'Configuração', href: '/config' },
    { label: 'Grupos' },
  ]}
  actionButton={
    <Button variant="primary" onClick={() => setShowCreate(true)}>
      Novo grupo
    </Button>
  }
/>
```

**Props**

| Prop | Type | Description |
|---|---|---|
| `title` | `string` | Page `<h1>` |
| `breadcrumbs` | `{ label: string; href?: string }[]` | Crumbs; last item has no `href` |
| `actionButton` | `ReactNode` | Right-aligned action (usually a Button) |

**Do / Don't**

✅ Use this on every admin/config page to keep heading hierarchy consistent.  
✅ Omit `breadcrumbs` on top-level pages (Home, Monitor) — only use for pages 2+ levels deep.  
❌ Don't put more than one primary action in `actionButton` — if you need multiple, use a `Button` with a dropdown.

---

### Table

Generic data table with column definitions, skeleton loading, and accessible click-to-open rows.

```tsx
import Table from '@/components/ui/Table'

const columns = [
  { key: 'name',   label: 'Nome' },
  { key: 'status', label: 'Status',
    render: (_, row) => <Badge variant={row.status}>{row.status}</Badge> },
  { key: 'updated_at', label: 'Atualizado',
    render: v => new Date(v).toLocaleDateString('pt-BR') },
]

<Table
  columns={columns}
  data={agents}
  isLoading={loading}
  keyField="agent_type_id"
  onRowClick={agent => setSelected(agent)}
  rowActionLabel="Abrir detalhes do agente"
  caption="Lista de tipos de agente"
/>
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `columns` | `Column<T>[]` | — | Column definitions |
| `data` | `T[]` | — | Row data |
| `isLoading` | `boolean` | `false` | Shows skeleton rows |
| `keyField` | `string` | `'id'` | Row key field for React key |
| `onRowClick` | `(row: T) => void` | — | Makes rows interactive |
| `rowActionLabel` | `string` | `'Abrir'` | Screen reader label for clickable rows |
| `caption` | `string` | — | `<caption>` rendered as `sr-only` |

**Column definition**

| Field | Type | Description |
|---|---|---|
| `key` | `string` | Maps to `row[key]` |
| `label` | `string` | Column heading |
| `render` | `(value, row) => ReactNode` | Custom cell renderer |

**Do / Don't**

✅ Always set `caption` for tables with non-obvious content — it's hidden visually but read by screen readers.  
✅ Provide `rowActionLabel` when using `onRowClick` — "Abrir" is the default but "Abrir detalhes do agente" is more helpful.  
❌ Don't use this component for tables that need sorting, filtering, or column resizing — build a custom table.  
❌ Don't use `keyField` that isn't actually unique across rows — use a UUID or composite key.

---

### Spinner

Animated loading indicator.

```tsx
import Spinner from '@/components/ui/Spinner'

<Spinner />                        // md, primary colour
<Spinner size="sm" />              // sm — 16×16
<Spinner size="lg" className="text-muted" />  // override colour
```

**Props**

| Prop | Type | Default | Description |
|---|---|---|---|
| `size` | `'sm' \| 'md' \| 'lg'` | `'md'` | 16px / 32px / 48px |
| `className` | `string` | `''` | Override color via `text-*` token |

**Do / Don't**

✅ Use `size="sm"` inline next to text; `size="md"` for full-panel loading states.  
✅ Prefer `Button loading={true}` over placing a `Spinner` inside a button manually.  
❌ Don't render `Spinner` without a visually-hidden `aria-label` on the surrounding container in full-page loading states — add `aria-live="polite"` or `aria-busy` on the parent.

---

### EmptyState

Placeholder for empty list or zero-data views.

```tsx
import EmptyState from '@/components/ui/EmptyState'
import { Inbox } from 'lucide-react'
import Button from '@/components/ui/Button'

<EmptyState
  icon={<Inbox className="w-12 h-12" />}
  title="Nenhum agente encontrado"
  description="Crie um tipo de agente para começar."
  action={
    <Button variant="primary" onClick={() => setShowCreate(true)}>
      Criar agente
    </Button>
  }
/>
```

**Props**

| Prop | Type | Description |
|---|---|---|
| `title` | `string` | Primary message |
| `description` | `string` | Optional secondary explanation |
| `action` | `ReactNode` | Optional CTA (usually a `Button`) |
| `icon` | `ReactNode` | Replaces default document SVG |

**Do / Don't**

✅ Use a lucide-react icon that relates to the content type (Inbox for messages, Users for agents, etc.).  
✅ Make `action` a direct shortcut to create the missing resource when possible.  
❌ Don't use `EmptyState` for error states — use `Alert variant="error"` instead.

---

## Conventions

### Icons
Use **lucide-react v0.383.0** for all UI icons. Add `aria-hidden="true"` to decorative icons, `aria-label` to standalone icon buttons.

```tsx
import { Settings } from 'lucide-react'

<Settings className="w-4 h-4" aria-hidden="true" />
<button aria-label="Configurações"><Settings className="w-5 h-5" /></button>
```

### Focus rings
All interactive components use `focus-visible:ring-2 focus-visible:ring-primary`. Don't add custom focus styles — they'll be inconsistent with keyboard navigation across the app.

### Legacy tokens to avoid
The following tokens exist for backward compatibility but should not be used in new code:

| Legacy | Use instead |
|---|---|
| `gray` | `muted` |
| `lightGray` / `light-gray` | `border` |
| `tableAlt` | `surface-alt` or `primary-light` |
