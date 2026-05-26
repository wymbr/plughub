# Accessibility Audit — PlugHub platform-ui

**Standard:** WCAG 2.1 AA  
**Date:** 2026-05-18  
**Method:** Static code analysis across ~100 `.tsx` files  
**Scope:** All of `packages/platform-ui/src`

---

## Summary

| Severity | Count |
|---|---|
| 🔴 Critical (blocks task completion) | 9 |
| 🟠 Major (significant barrier) | 14 |
| 🟡 Minor (improvement) | 10 |
| **Total** | **33** |

**Key headline:** ARIA attributes exist in only 10 files. Of those, 8 are the new `components/ui/` files built in Sprint 2. The original 100+ feature files have virtually zero ARIA — no live regions, no landmark labels, no interactive element names.

---

## 1. Perceivable

### 1.1 Color Contrast

| # | Element | Foreground | Background | Ratio | Required | Pass? | Severity |
|---|---|---|---|---|---|---|---|
| C1 | Author labels in `MessageBubble` (`text-[10px]`) | `#94A3B8` (slate-400) | `#F8FAFC` (slate-100 bubble) | ~2.0:1 | 4.5:1 | ❌ | 🔴 Critical |
| C2 | `text-indigo-400` (agent_human label) | `#818CF8` | `#FFFFFF` | ~3.1:1 | 4.5:1 | ❌ | 🔴 Critical |
| C3 | `text-violet-500` (agent_ai label) | `#8B5CF6` | `#FFFFFF` | ~3.0:1 | 4.5:1 | ❌ | 🔴 Critical |
| C4 | `text-gray-400` / `text-gray-500` (placeholder, captions) | `#9CA3AF` | `#FFFFFF` | ~2.5:1 | 4.5:1 | ❌ | 🟠 Major |
| C5 | `text-gray-500` in LoginPage subtitle | `#6B7280` | `#FFFFFF` | ~4.9:1 | 4.5:1 | ✅ | — |
| C6 | `primary` on white (#1B4F8A) | `#1B4F8A` | `#FFFFFF` | ~8.2:1 | 4.5:1 | ✅ | — |
| C7 | `warning-text` on `warning-light` | `#92400E` | `#FEF3C7` | ~6.3:1 | 4.5:1 | ✅ | — |
| C8 | `ai-text` on `ai-light` | `#3730A3` | `#E0E7FF` | ~8.2:1 | 4.5:1 | ✅ | — |
| C9 | `contested-text` on `contested-light` | `#9A3412` | `#FED7AA` | ~5.4:1 | 4.5:1 | ✅ | — |
| C10 | Sentiment strip colours (colour-only encoding) | — | — | — | — | ❌ | 🟠 Major |

**C1–C3 root cause:** `MessageBubble` uses 10px author labels with colours in the 2–3:1 range. At 10px, these are below the "large text" threshold (18pt/24px) so the 4.5:1 standard applies. Both font size and colour fail simultaneously.

**C4:** `text-gray-400` and `text-gray-500` appear in ~86 files as captions, placeholder text, helper text, and table empty states. Tailwind gray-400 (#9CA3AF) has only 2.53:1 contrast on white.

**C10:** Sentiment is conveyed exclusively via colour: `bg-red-500`/`bg-green-500`/`bg-yellow-500` bullet + `text-red-700`/`text-green-700`/`text-yellow-700` score. Users with colour-blindness cannot distinguish states. Unicode arrows (↑↓→) are correct additions for trend but lack `aria-label`.

### 1.2 Text Alternatives

| # | Element | Issue | Criterion | Severity |
|---|---|---|---|---|
| T1 | Emoji navigation icons in `Sidebar.tsx` (🏠 🖥️ 📡 🔄 ✓ 📊 ⚙️ 🔍) | Not hidden from screen readers; read aloud as "house", "desktop computer", etc. | 1.1.1 | 🟠 Major |
| T2 | Trend arrows in `ChatArea` (`↑ ↓ →`) | No `aria-label` — announced as arrow character | 1.1.1 | 🟡 Minor |
| T3 | Sentiment pulse dot in `ChatArea` | `animate-pulse` `div` with no text alternative | 1.1.1 | 🟡 Minor |
| T4 | Selection checkbox button in `MessageBubble` (Arc 11) | `<button>` styled as checkbox with no `aria-label`, `aria-checked`, or `aria-pressed` | 4.1.2 | 🔴 Critical |

---

## 2. Operable

### 2.1 Keyboard Accessibility

| # | Element | Issue | Criterion | Severity |
|---|---|---|---|---|
| K1 | `AgentInput` textarea | No `<label>` and no `aria-label` — screen readers announce nothing meaningful | 3.3.2 | 🔴 Critical |
| K2 | `AgentInput` "/" button | Has `title` attribute only — `title` is not reliably announced; needs `aria-label` | 4.1.2 | 🟠 Major |
| K3 | `AgentInput.handleBlur` | Automatically re-focuses textarea when focus leaves to `document.body`. Keyboard users who Tab off the textarea to reach other controls may have focus snapped back — a soft focus trap | 2.1.2 | 🔴 Critical |
| K4 | Message selection checkbox in `MessageBubble` | `opacity-0 group-hover:opacity-100` — invisible to keyboard users unless message is already selected | 2.1.1 | 🔴 Critical |
| K5 | Sidebar expand/collapse buttons | No `aria-expanded` to convey state; collapsed sidebar has no programmatic announcement | 4.1.2 | 🟠 Major |
| K6 | Feature-level modals (PauseReasonModal, CloseModal, etc.) | No Escape key, no focus trap, no `aria-modal` — only the new shared Modal has these | 2.1.2 | 🟠 Major |
| K7 | Sidebar nav item with `href="#"` for group toggles | Using `href="#"` on `<Link>` causes scroll-to-top and is not semantically a link | 4.1.2 | 🟡 Minor |
| K8 | `CannedPhrasesPalette` (opened by `/` key) | No evidence of Escape key or focus management when palette opens | 2.1.2 | 🟠 Major |

### 2.2 Focus Visibility

| # | Element | Issue | Criterion | Severity |
|---|---|---|---|---|
| F1 | Feature `<button>` elements throughout | `outline-none` without `focus-visible:ring-*` replacement in most feature files — keyboard focus is invisible | 2.4.7 | 🔴 Critical |
| F2 | `AgentInput` textarea | `focus:outline-none focus:ring-2 focus:ring-indigo-500` — ring exists, but `focus:` (not `focus-visible:`) means it shows on mouse click too. Minor but stylistically wrong | 2.4.7 | 🟡 Minor |
| F3 | Table rows with `onRowClick` | Rows are `<tr>` elements (not `<button>`) with click handler; not keyboard focusable at all | 2.1.1 | 🟠 Major |

### 2.3 Touch Targets

| # | Element | Actual Size | Required | Pass? | Severity |
|---|---|---|---|---|---|
| TT1 | "/" button in `AgentInput` | `w-8 h-8` = 32×32px | 44×44px | ❌ | 🟠 Major |
| TT2 | Message selection checkbox button | `w-4 h-4` = 16×16px | 44×44px | ❌ | 🔴 Critical |
| TT3 | Button `size="sm"` (`px-3 py-1 text-sm`) | ~28px height | 44px | ❌ | 🟡 Minor |
| TT4 | Modal close button (`p-1 w-5 h-5`) | ~28×28px total tap area | 44×44px | ❌ | 🟡 Minor |

---

## 3. Understandable

### 3.1 Forms and Labels

| # | Element | Issue | Criterion | Severity |
|---|---|---|---|---|
| L1 | `AgentInput` textarea | No label — placeholder is the only hint. Placeholder disappears on input and is not a label | 3.3.2 | 🔴 Critical |
| L2 | Native `<textarea>` in feature pages (FormsPage, CampaignsPage, AvaliacoesPage, GroupsPage) | Direct HTML `<textarea>` with className but no `<label htmlFor>` — now there is `Textarea` in the design system but not yet adopted | 3.3.2 | 🟠 Major |
| L3 | Native `<input>` checkboxes in `AccessPage`, `ModulePermissionForm` | No consistent `<label>` association — now `Checkbox` exists but not yet adopted | 3.3.2 | 🟠 Major |
| L4 | `Input.tsx` and `Select.tsx` | Label prop is optional. When omitted, there's no fallback `aria-label` — input has no accessible name | 3.3.2 | 🟡 Minor |

### 3.2 Error Identification

| # | Element | Issue | Criterion | Severity |
|---|---|---|---|---|
| E1 | Score colour coding in `AvaliacoesPage` | Score thresholds (≥0.8/≥0.6/<0.6) conveyed only by colour (green/yellow/red background) — no text label | 1.4.1 + 3.3.1 | 🟠 Major |
| E2 | Inline error messages in feature forms | ~15 files use raw `div bg-red-50 border border-red-100` — no `role="alert"`, no association with the triggering input | 3.3.1 | 🟠 Major |

---

## 4. Robust

### 4.1 ARIA and Semantic HTML

| # | Element | Issue | Criterion | Severity |
|---|---|---|---|---|
| A1 | `ChatArea` message list | No `role="log"` or `aria-live` — new messages are not announced to screen readers at all | 4.1.3 | 🔴 Critical |
| A2 | AI typing indicator in `ChatArea` | No `aria-live` region — screen reader users have no indication the agent is typing | 4.1.3 | 🟠 Major |
| A3 | Sidebar navigation | `<nav>` present but no `aria-label` to distinguish it from other `<nav>` elements | 1.3.1 | 🟡 Minor |
| A4 | `TopBar` | `<div>` with no `role="banner"` or `<header>` element | 1.3.1 | 🟡 Minor |
| A5 | Page-level `<h1>` consistency | Some routes render `<h1>` via `PageHeader`, others have none — inconsistent heading hierarchy | 1.3.1 | 🟡 Minor |
| A6 | `Table.tsx` clickable rows | `<tr onClick>` — `<tr>` is not an interactive element; keyboard users cannot activate rows | 4.1.2 | 🟠 Major |
| A7 | `document.documentElement.lang` | Not audited (in `index.html`) — must be `lang="pt-BR"` for default language | 3.1.1 | 🟠 Major |

---

## 5. Color Contrast — New Token System Check

The tokens added in Sprint 1 pass WCAG 4.5:1 in their intended use (dark text on light background):

| Token pair | Ratio | Pass |
|---|---|---|
| `primary` (#1B4F8A) on white | 8.24:1 | ✅ |
| `green-text` (#065F46) on `green-light` (#D1FAE5) | 8.2:1 | ✅ |
| `warning-text` (#92400E) on `warning-light` (#FEF3C7) | 6.3:1 | ✅ |
| `red-text` (#991B1B) on `red-light` (#FEE2E2) | 6.7:1 | ✅ |
| `contested-text` (#9A3412) on `contested-light` (#FED7AA) | 5.4:1 | ✅ |
| `revised-text` (#134E4A) on `revised-light` (#CCFBF1) | 8.8:1 | ✅ |
| `ai-text` (#3730A3) on `ai-light` (#E0E7FF) | 8.2:1 | ✅ |
| `muted` (#6B7280) on white | 4.9:1 | ✅ |
| `muted-light` (#9CA3AF) on white | 2.5:1 | ❌ — only for placeholder/decorative |

**`muted-light` (#9CA3AF) must never be used for informational text.** Only acceptable for decorative placeholders. If used as text, it fails at 2.5:1.

---

## 6. Priority Fixes

### Immediate (Critical — block task completion for keyboard/SR users)

**Fix 1 — `AgentInput`: add `aria-label` to textarea and "/" button; fix focus trap (K1, K2, K3, L1)**

The textarea is the primary interaction point of the entire Console. A screen reader user cannot compose or send a message. The blur handler that re-focuses the textarea is a focus trap.

**Fix 2 — `ChatArea`: add `role="log"` and `aria-live="polite"` to message list (A1)**

Screen reader users receive zero notification of incoming messages — the entire async conversation is invisible to them.

**Fix 3 — Message selection checkbox: fix semantics and touch target (T4, K4, TT2)**

The 16×16px button is not keyboard-focusable via hover. Needs `aria-label`, `aria-pressed`, and a minimum 44px tap area.

**Fix 4 — Feature-level focus rings: global `focus-visible:ring` (F1)**

Hundreds of `<button>` elements have `outline-none` with no `focus-visible:ring-*` replacement. This is the highest-volume fix (affects ~90 files) and should be addressed via a Tailwind base layer rule.

**Fix 5 — `MessageBubble` author label contrast (C1–C3)**

`text-[10px]` author labels at 2–3:1 contrast are a double failure (size + colour). Use `text-xs` minimum and `text-muted` (#6B7280, 4.9:1) or darker.

### Short-term (Major)

**Fix 6 — Sidebar emojis: `aria-hidden="true"` on icons (T1)**

Add `aria-hidden="true"` to emoji icon spans; ensure nav item labels have adequate text.

**Fix 7 — `Table.tsx` keyboard access (A6, F3)**

`<tr onClick>` rows should have `tabIndex={0}`, `role="row"` is fine, but the click handler needs a keyboard `onKeyDown` (Enter/Space). Or refactor to `<button>` cells.

**Fix 8 — Feature modals: adopt shared `Modal` (K6)**

`CloseModal`, `PauseReasonModal`, and other local modals need to be refactored to use the shared `Modal` component which has focus trap, Escape, and ARIA.

**Fix 9 — `index.html`: add `lang="pt-BR"` (A7)**

Must confirm lang attribute exists on `<html>`.

**Fix 10 — Sentiment: text alternative for colour encoding (C10)**

Add text label (e.g., "Sentimento: Positivo") alongside colour dot. Wrap trend arrow in `<span aria-label="Tendência: melhorando">↑</span>`.

### Minor

**Fix 11 — Sidebar nav `href="#"` group toggles (K7)**

Replace with `<button>` for group toggles instead of `<Link to="#">`.

**Fix 12 — `TopBar`: add `<header>` element (A4)**

Wrap TopBar content in `<header role="banner">`.

**Fix 13 — Sidebar: `aria-label` on `<nav>` and `aria-expanded` on toggles (A3, K5)**

`<nav aria-label="Navegação principal">` and `aria-expanded={isExpanded}` on each group toggle button.

**Fix 14 — `Input`/`Select`: require `aria-label` fallback (L4)**

When `label` prop is omitted, `Input` and `Select` should accept and forward `aria-label` / `aria-labelledby` props.

**Fix 15 — `prefers-reduced-motion` (implied by C3/T3)**

Wrap `animate-pulse` and `animate-spin` in `@media (prefers-reduced-motion: reduce)` via Tailwind's `motion-safe:` / `motion-reduce:` variants.

---

## 7. Recommended Implementation Order

Given the current state of the codebase, the highest-ROI fixes in order:

1. **Global focus ring** — add to `index.css` Tailwind base layer (fixes ~90 files in 5 lines)
2. **`lang` attribute** — check/add to `index.html` (1 line)
3. **`AgentInput`** — label + aria-label + fix blur trap (most critical single component)
4. **`ChatArea`** — `role="log"` + `aria-live` for messages
5. **Sidebar** — `aria-hidden` on emojis, `aria-label` on `<nav>`, `aria-expanded` on groups, `<button>` for toggles
6. **`MessageBubble`** — author label contrast + touch target for checkbox
7. **`TopBar`** — wrap in `<header>`
8. **Feature modals** — adopt shared `Modal` (CloseModal, PauseReasonModal)
9. **Score colour encoding** — add text labels to AvaliacoesPage
10. **Sentiment strip** — text + `aria-label` on trend arrows
