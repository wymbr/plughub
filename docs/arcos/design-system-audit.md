# Design System Audit — PlugHub platform-ui

**Date:** 2026-05-18  
**Scope:** `packages/platform-ui/src`  
**Files analysed:** 100+ `.tsx` files, `tailwind.config.ts`, `src/index.css`

---

## Summary

| Dimension | Score | Detail |
|---|---|---|
| Token coverage | 3/10 | Color-only; no spacing, radius, shadow, z-index, or motion tokens |
| Naming consistency | 5/10 | Tokens defined but bypassed in 95 of 100 feature files |
| Component completeness | 4/10 | 10 base components, ~10 critical patterns missing entirely |
| Documentation | 0/10 | No component docs, no usage guidelines, no do/don't examples |
| **Overall** | **30/100** | Foundation is there; application is inconsistent |

**Total hardcoded value instances across the codebase: ~3,280**
- Raw colour scale (`text-blue-*`, `bg-green-*`, `bg-yellow-*`, etc.): **1,399 instances** across **95 files**
- Raw gray scale (`bg-gray-50`, `text-gray-400`, `text-gray-600`, etc.): **1,534 instances** across **86 files**
- Off-palette colours with no token backing (`teal`, `indigo`, `purple`, `cyan`, `pink`): **130 instances** across **31 files**
- Arbitrary spacing/sizing values (`w-[300px]`, `h-[48px]`, `[#hex]`): **347 instances** across **53 files**

---

## 1. Design Tokens

### 1.1 What Exists

`tailwind.config.ts` defines 10 colour tokens and the Inter font family:

```ts
colors: {
  primary:   '#1B4F8A',   // brand blue
  secondary: '#2D9CDB',   // lighter blue
  accent:    '#00B4D8',   // cyan accent
  dark:      '#1A1A2E',   // near-black text
  gray:      '#6B7280',   // muted text
  lightGray: '#E5E7EB',   // borders
  tableAlt:  '#EFF6FF',   // table row alt
  green:     '#059669',   // success
  warning:   '#D97706',   // warning amber
  red:       '#DC2626',   // error/danger
}
```

`src/index.css` applies Tailwind base layers and a single `bg-white` on `body`. Nothing more.

### 1.2 What is Missing

| Token category | Status | Impact |
|---|---|---|
| Colour scale variants (e.g. `green-100`, `green-800`) | ❌ Missing | Every semantic colour badge, pill, and alert re-invents shading in raw Tailwind |
| Surface/background tokens (`surface`, `surface-muted`, `surface-alt`) | ❌ Missing | Shell uses `bg-gray-50`, feature pages use `bg-white` — no single source of truth |
| Border-radius scale | ❌ Missing | `rounded`, `rounded-lg`, `rounded-full` used arbitrarily with no consistent system |
| Shadow scale | ❌ Missing | `shadow`, `shadow-sm`, `shadow-xl` each used on different components with no rationale |
| Spacing scale overrides | ❌ Missing | 347 arbitrary `[px]`/`[rem]` values; no design-time constraint |
| Z-index scale | ❌ Missing | `z-50` hardcoded in Modal; other overlays use different values |
| Motion/transition | ❌ Missing | `transition-colors` and `animate-spin` used ad hoc; no consistent duration |
| Typography scale | ❌ Missing | Font weights and size steps (`text-xs`, `text-sm`, `text-base`, `text-lg`, `text-xl`, `text-2xl`) used freely with no binding constraint |

### 1.3 Token Naming Issues

`lightGray` is camelCase while Tailwind convention is kebab-case (`light-gray`). When developers write `border-lightGray` it works but looks inconsistent with standard classes like `border-gray-200`. This has caused many developers to skip the token and reach for `border-gray-200` directly.

The token named `gray` (#6B7280) collides with Tailwind's built-in `gray` scale. A developer writing `text-gray-500` is NOT using the PlugHub `gray` token — they are using Tailwind's default. This is a silent split that accounts for most of the 1,534 raw gray instances.

---

## 2. Component Inventory

### 2.1 Existing Components (`src/components/ui/`)

| Component | Variants | States | Sizes | forwardRef | Issues |
|---|---|---|---|---|---|
| `Button` | primary, secondary, ghost, danger | default, hover, disabled | sm, md, lg | ✅ | Hover states use raw `bg-blue-900` / `bg-blue-500` (not token-derived). No `loading` state. No `icon-only` mode. No `link` variant. |
| `Input` | — | default, focus, error | — | ✅ | No `textarea` variant. No character counter. No password toggle. No search icon slot. Error state only on border (no background tint). |
| `Select` | — | default, focus, error | — | ✅ | Mirrors Input — parity is good. No multi-select. No async/searchable. |
| `Modal` | — | open/closed | — | — | Fixed `max-w-md` only. No size variants. No keyboard trap (`Escape` key, focus lock). No scroll lock on `body`. No `loading` overlay. Close button is an SVG in JSX rather than a reusable `IconButton`. |
| `Table` | — | loading (skeleton), empty | — | — | No sorting. No pagination. No row selection. `keyField` defaults to `'id'` which silently fails for non-id keys. `onRowClick` always shows `cursor-pointer` even without handler. |
| `Badge` | active, suspended, failed, default | — | — | — | Coverage is agent-status-only. All other domain states (evaluation workflow states, journey status, calibration signal, contestation state) are re-implemented inline with raw Tailwind throughout feature pages. |
| `Card` | — | — | — | — | Minimal. No `header`/`footer` slots. No `variant` (outlined, elevated, flat). |
| `Spinner` | — | — | sm, md, lg | — | Good. No label/accessible text. |
| `PageHeader` | — | — | — | — | Good. Breadcrumbs use `<a>` not `<Link>` — will cause full-page reloads. |
| `EmptyState` | — | — | — | — | Good pattern. Default SVG icon is a document which doesn't fit all contexts (e.g. "no sessions" in monitor). |

### 2.2 Missing Components

The following patterns are each implemented from scratch in multiple feature files with no shared component:

| Pattern | Re-implemented in | Notes |
|---|---|---|
| **Textarea** | FormsPage, CampaignsPage, AvaliacoesPage, GroupsPage, and more | Native `<textarea>` with `className` repeated each time |
| **Drawer / Slide-over** | DelegarTarefaDrawer, GroupsPage drawer, CurationReview drawer, CalibrationDashboard | Each uses `fixed inset-y-0 right-0` pattern with subtle differences in backdrop and width |
| **Tabs** | AgentAssistPage (5 tabs), ContactsPage, AuditPage, EvaluationForms | Each has its own `activeTab` state, click handler, and underline/border styling — inconsistent across pages |
| **Toast / Notification** | ToastContainer exists only in `agent-assist/components` — not in `components/ui/` | Feature code outside agent-assist has no consistent feedback primitive |
| **Tooltip** | Multiple inline title attributes or custom hover `div` elements | No accessible tooltip component |
| **Checkbox** | AccessPage, ModulePermissionForm, FormsPage | Native `<input type="checkbox">` with bespoke ring styling |
| **Switch / Toggle** | Config pages | No shared component |
| **RadioGroup** | CampaignsPage, contestation policy UI | Bespoke `div` click handlers |
| **Form / FormField** | None — each form manages label+input+error independently | No abstraction; label association relies on developers consistently providing `htmlFor` |
| **Pagination** | None — each table that needs it implements independently | No shared component |
| **Skeleton / LoadingState** | Table has internal skeleton rows; other pages use `Spinner` or nothing | No composable loading-state system |
| **Alert / InlineMessage** | `div` with `bg-red-50 border border-red-100` pattern in 15+ files | No shared feedback component for inline messages |
| **Popover / Dropdown** | Multiple bespoke implementations for menus and overlays | Risk of accessibility and z-index conflicts |

---

## 3. Colour Token Violations — Detailed

### 3.1 The core problem: semantic colours without scale variants

The design intent is clear — `green` = success, `warning` = amber, `red` = danger. But feature components need the full semantic range: a light background tint at 10% opacity, a medium border at 30%, and a dark text at full saturation. Without token variants for these, developers pull from Tailwind's default scale.

**Example from `AvaliacoesPage.tsx`:**
```tsx
// Score colour — 3 different ad-hoc implementations in the same file
score >= 0.8 ? 'bg-green-100 text-green-800' :
score >= 0.6 ? 'bg-yellow-100 text-yellow-800' :
               'bg-red-100 text-red-800'

// Contestation status
submitted:  'bg-blue-100 text-blue-700',
approved:   'bg-green-100 text-green-800',
rejected:   'bg-red-100 text-red-700',
contested:  'bg-orange-100 text-orange-700',   // ← orange not in token system at all
```

```tsx
// Decision colours
text-green-700 | text-teal-700 | text-red-700   // ← teal not in token system
```

**Example from `Button.tsx` (the component itself violates its own token system):**
```tsx
primary:   'bg-primary hover:bg-blue-900 text-white ...',    // hover = raw Tailwind
secondary: 'bg-secondary hover:bg-blue-500 text-white ...',  // hover = raw Tailwind
ghost:     'bg-transparent hover:bg-gray-200 ...',           // hover = raw Tailwind (not lightGray)
```

### 3.2 Off-palette colours — tokens that don't exist anywhere

These colours appear in feature code with no token backing whatsoever:

| Colour | Instances | Example usage |
|---|---|---|
| `orange` | ~25 files | contestation state, warning banners, curation signals |
| `teal` | ~15 files | evaluation review decisions, segment types |
| `indigo` | ~10 files | ABAC module badges, some chart series |
| `purple` | ~8 files | AI agent indicators, calibration |
| `cyan` | ~5 files | Some status indicators |
| `pink` | ~3 files | Sentiment negative |

Each of these is used semantically — orange for "contested", teal for "revised", indigo for AI vs human. But they are not in `tailwind.config.ts`, meaning: (a) they cannot be easily changed, (b) they may change inadvertently on Tailwind upgrades, and (c) they cannot be documented.

### 3.3 Shell background token gap

`Shell.tsx` line 16: `bg-gray-50` — this is the application's main background surface and it is not a token. Any future brand refresh must find this by text search rather than config change.

---

## 4. Accessibility Issues Detected

These are structural issues found in the code, not a full WCAG audit (that's the next step):

| Issue | Location | WCAG criterion |
|---|---|---|
| Modal has no focus trap | `Modal.tsx` | 2.1.2 No Keyboard Trap |
| Modal does not lock `body` scroll | `Modal.tsx` | Visual integrity |
| `EmptyState` default icon has no `aria-label` on the SVG | `EmptyState.tsx` | 1.1.1 Non-text Content |
| `TopBar` language toggle button has no `aria-label` | `TopBar.tsx` | 1.3.1 Info and Relationships |
| Breadcrumb `<a>` in `PageHeader` should be `<Link>` | `PageHeader.tsx` | Not WCAG but causes page reloads |
| Close button in `Modal` has no accessible label | `Modal.tsx` | 1.1.1 Non-text Content |
| `Table` row click (`cursor-pointer` always) when no handler | `Table.tsx` | 1.3.1 Info and Relationships |
| Colour-only status encoding throughout `Badge`, `AvaliacoesPage` | Multiple | 1.4.1 Use of Colour |
| Score thresholds (≥0.8, ≥0.6) encoded only in colour | `AvaliacoesPage.tsx` | 1.4.1 Use of Colour |

A full accessibility audit will surface more — particularly in the agent-assist Console which is keyboard-heavy.

---

## 5. Priority Actions

### Action 1 — Extend colour token system (Immediate — ~2h)

Add semantic colour scales to `tailwind.config.ts`. Each semantic colour needs 3 variants: `{name}` (full), `{name}-light` (10-20% opacity background), `{name}-text` (accessible on white). 

Also add surface tokens, and register `orange` and `teal` as proper tokens since they carry clear semantic meaning in the platform.

```ts
// tailwind.config.ts extension
colors: {
  // … existing tokens …
  
  // Semantic scale variants
  'green-light':   '#D1FAE5',   // bg tint (Tailwind green-100)
  'green-text':    '#065F46',   // text on light bg (Tailwind green-800)
  'warning-light': '#FEF3C7',   // bg tint
  'warning-text':  '#92400E',   // text on light bg
  'red-light':     '#FEE2E2',   // bg tint
  'red-text':      '#991B1B',   // text on light bg
  
  // New semantic tokens (currently off-palette)
  'contested':      '#EA580C',  // orange — contestation/pending review
  'contested-light':'#FED7AA',
  'contested-text': '#9A3412',
  'revised':        '#0D9488',  // teal — review decision revised
  'revised-light':  '#CCFBF1',
  'revised-text':   '#134E4A',
  'ai':             '#6366F1',  // indigo — AI agent indicators
  'ai-light':       '#E0E7FF',
  'ai-text':        '#3730A3',
  
  // Surfaces
  'surface':        '#FFFFFF',
  'surface-muted':  '#F9FAFB',  // replaces bg-gray-50 in Shell
  'surface-border': '#E5E7EB',  // replaces lightGray (also rename lightGray → surface-border)
}
```

Fix `lightGray` → `light-gray` naming for Tailwind convention compliance (currently works but is non-standard).

### Action 2 — Fix Button hover states (Immediate — 30m)

Replace raw Tailwind hover classes in `Button.tsx` with token-consistent values:

```tsx
primary:   'bg-primary hover:bg-[#163F70] text-white ...',   // or add primary-dark token
secondary: 'bg-secondary hover:bg-[#2484BE] text-white ...',  // or add secondary-dark token
ghost:     'bg-transparent hover:bg-light-gray text-dark ...', // use token, not bg-gray-200
```

Best fix: add `primary-dark` and `secondary-dark` as tokens so hover states are also theme-controlled.

### Action 3 — Create shared Badge semantic variants (High — 1h)

Extend `Badge.tsx` to cover all domain states used across the platform. The current 4 variants cover ~10% of badge use cases:

```tsx
// Proposed variants
type BadgeVariant =
  | 'active' | 'suspended' | 'failed' | 'default'  // existing
  | 'pending' | 'processing' | 'completed'           // workflow states
  | 'contested' | 'approved' | 'rejected' | 'revised' // evaluation states
  | 'ai' | 'human'                                   // agent type
  | 'info' | 'success' | 'warning' | 'error'        // semantic generics
```

### Action 4 — Build missing Drawer and Tabs components (High — 3h each)

Both are implemented 4+ times with subtle inconsistencies in z-index, animation, and backdrop behaviour. Create shared components:

- `Drawer` — right slide-over, configurable width (sm/md/lg), backdrop click to close, Escape key, focus trap, `title` prop
- `Tabs` — horizontal tab bar, `activeTab` controlled externally, ARIA `tablist`/`tab`/`tabpanel`, keyboard navigation (Arrow keys)

### Action 5 — Eliminate raw gray scale (Medium — ~8h, can be spread across feature work)

The `gray` token collision is the root cause of 1,534 raw-gray instances. Resolution: rename `gray` → `text-muted` in the token config, add proper surface tokens, then do a codebase-wide replace. This is a breaking rename but the total surface area is entirely internal.

```ts
// Rename plan
gray:      '#6B7280'  →  muted: '#6B7280'
lightGray: '#E5E7EB'  →  border: '#E5E7EB'
```

Then token-map:
- `text-gray-400` → `text-muted/60` (or a new `muted-light` token)
- `text-gray-500` → `text-muted`
- `text-gray-600` → `text-muted` or `text-dark/80`
- `text-gray-700` → `text-dark/70`
- `text-gray-900` → `text-dark`
- `bg-gray-50`    → `bg-surface-muted`
- `bg-gray-100`   → `bg-surface-alt` (new token)
- `bg-gray-200`   → `bg-light-gray` or `bg-border`

### Action 6 — Add Textarea, Checkbox, and Alert to `components/ui/` (Medium — 2h)

Three components with the highest re-implementation count:

- `Textarea` — extends `Input` API, adds `rows`, `maxLength`, character counter
- `Checkbox` — accessible `input[type=checkbox]` with label, error, indeterminate state
- `Alert` — inline feedback with `variant` (info/success/warning/error), icon slot, dismissible

### Action 7 — Write component documentation (Ongoing)

Even a minimal `README.md` in `src/components/ui/` listing props, variants, and usage examples for each component would prevent much of the re-invention happening in feature code. Start with Button, Badge, Modal, and Table — the four most misused.

---

## 6. Component Completeness Detail

| Component | Variants | States | Sizes | Keyboard | Docs | Score |
|---|---|---|---|---|---|---|
| Button | ✅ 4 | ⚠️ missing loading | ✅ 3 | ✅ | ❌ | 7/10 |
| Input | ⚠️ 1 (no textarea, search) | ✅ | ❌ | ✅ | ❌ | 5/10 |
| Select | ⚠️ 1 | ✅ | ❌ | ✅ | ❌ | 5/10 |
| Modal | ❌ 1 (no sizes) | ❌ no loading | ❌ | ❌ no trap | ❌ | 3/10 |
| Table | ⚠️ 1 | ✅ loading+empty | ❌ | ❌ no nav | ❌ | 5/10 |
| Badge | ❌ 4 (covers ~10% of usage) | ❌ | ❌ | N/A | ❌ | 2/10 |
| Card | ❌ 1 | ❌ | ❌ | N/A | ❌ | 3/10 |
| Spinner | ✅ 1 | ✅ | ✅ 3 | N/A | ❌ | 7/10 |
| PageHeader | ✅ 1 | ✅ | ❌ | N/A | ❌ | 6/10 |
| EmptyState | ✅ 1 | ✅ | ❌ | N/A | ❌ | 6/10 |
| **Drawer** | ❌ missing | — | — | — | — | 0/10 |
| **Tabs** | ❌ missing | — | — | — | — | 0/10 |
| **Toast** | ❌ missing (exists only in agent-assist) | — | — | — | — | 0/10 |
| **Textarea** | ❌ missing | — | — | — | — | 0/10 |
| **Checkbox** | ❌ missing | — | — | — | — | 0/10 |
| **Alert** | ❌ missing | — | — | — | — | 0/10 |

---

## 7. Recommended Execution Sequence

Given the planned sequence (Design System → Accessibility + Microcopy → UX Audit → Brand), the order of design system work that will best unblock subsequent phases:

**Sprint 1 — Token hygiene (blocks everything else)**
1. Extend colour tokens (Action 1) — blocks Accessibility and Brand phases
2. Fix Button hover states (Action 2) — quick win, blocks Brand
3. Rename `gray`/`lightGray` tokens (part of Action 5) — 30 min change, big payoff

**Sprint 2 — Missing components (blocks UX Audit)**
4. Shared `Badge` variants (Action 3)
5. `Drawer` component (Action 4)
6. `Tabs` component (Action 4)
7. `Alert`/`InlineMessage` component (Action 6)

**Sprint 3 — Form primitives + grey elimination**
8. `Textarea`, `Checkbox`, `Switch` (Action 6)
9. Token-guided gray elimination (Action 5) — can be done file-by-file during normal feature work

**Sprint 4 — Documentation**
10. `src/components/ui/README.md` with usage, variants, and do/don't examples (Action 7)

---

## 8. What's Working Well

The foundation is solid. The 10 base components are well-typed with TypeScript interfaces, properly use `forwardRef`, accept `className` for extensibility, and follow a consistent API pattern (`label`, `error`, `className`, spread props). The token concept is right — there's a clear design intent (primary blue, success green, warning amber, danger red) that just needs to be completed and enforced.

The biggest structural strength is that all feature code imports from a single `@/components/ui/` path, so adding a new component there and refactoring feature code to use it is straightforward — no architectural change needed.
