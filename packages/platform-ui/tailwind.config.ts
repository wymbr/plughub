import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}'
  ],
  theme: {
    extend: {
      colors: {
        // ── Brand & core ─────────────────────────────────────────────────────
        primary:         '#1B4F8A',  // brand blue — buttons, links, focus rings
        'primary-dark':  '#163F70',  // hover/active state for primary
        'primary-light': '#DBEAFE',  // tinted backgrounds, selected rows
        secondary:       '#2D9CDB',  // secondary actions
        'secondary-dark':'#2484BE',  // hover/active state for secondary
        accent:          '#00B4D8',  // highlights, active indicators
        dark:            '#1A1A2E',  // body text, headings

        // ── Neutral / gray ────────────────────────────────────────────────────
        // Note: the token named 'gray' collides with Tailwind's built-in gray
        // scale. Use 'muted' for new code; 'gray' kept for backward compat.
        gray:            '#6B7280',  // muted text — legacy alias, prefer 'muted'
        muted:           '#6B7280',  // muted/secondary text
        'muted-light':   '#9CA3AF',  // placeholder text, disabled labels
        lightGray:       '#E5E7EB',  // borders — legacy alias, prefer 'border'
        'light-gray':    '#E5E7EB',  // borders (kebab-case alias)
        border:          '#E5E7EB',  // border colour
        'border-strong': '#D1D5DB',  // stronger borders, dividers

        // ── Surfaces ──────────────────────────────────────────────────────────
        surface:         '#FFFFFF',  // card/panel backgrounds
        'surface-muted': '#F9FAFB',  // shell/app background (replaces bg-gray-50)
        'surface-alt':   '#F3F4F6',  // alternate row bg, hover bg on ghost items
        tableAlt:        '#EFF6FF',  // table alternate row (legacy alias)

        // ── Semantic: success ─────────────────────────────────────────────────
        green:           '#059669',  // success text / icon
        'green-light':   '#D1FAE5',  // success background tint
        'green-text':    '#065F46',  // success text on light background

        // ── Semantic: warning ─────────────────────────────────────────────────
        warning:         '#D97706',  // warning text / icon
        'warning-light': '#FEF3C7',  // warning background tint
        'warning-text':  '#92400E',  // warning text on light background

        // ── Semantic: error / danger ──────────────────────────────────────────
        red:             '#DC2626',  // error text / icon / danger button
        'red-light':     '#FEE2E2',  // error background tint
        'red-text':      '#991B1B',  // error text on light background

        // ── Semantic: info ────────────────────────────────────────────────────
        info:            '#2D9CDB',  // info (same as secondary)
        'info-light':    '#DBEAFE',  // info background tint
        'info-text':     '#1E40AF',  // info text on light background

        // ── Domain: evaluation / contestation ─────────────────────────────────
        // "contested" = orange — pending review, open contestation
        contested:           '#EA580C',
        'contested-light':   '#FED7AA',
        'contested-text':    '#9A3412',

        // "revised" = teal — review decision where score was adjusted
        revised:             '#0D9488',
        'revised-light':     '#CCFBF1',
        'revised-text':      '#134E4A',

        // ── Domain: agent type ────────────────────────────────────────────────
        // "ai" = indigo — AI agent indicators, AI participant cards
        ai:                  '#6366F1',
        'ai-light':          '#E0E7FF',
        'ai-text':           '#3730A3',
      },

      fontFamily: {
        sans: ['Inter', 'sans-serif']
      },

      // ── Extended type scale (below Tailwind's text-xs = 12px) ─────────────
      // These sizes are needed for compact badges, pills, metadata labels in
      // dense data-heavy interfaces. Never use arbitrary text-[Npx] — use these.
      //   text-micro   = 9px  → reserved for superscript badges and tiny counts
      //   text-2xs     = 10px → compact labels, badge text, table cell metadata
      //   text-xs      = 12px → default Tailwind (kept as-is)
      fontSize: {
        'micro': ['0.5625rem', { lineHeight: '0.875rem' }],   // 9px / 14px
        '2xs':   ['0.625rem',  { lineHeight: '1rem'    }],    // 10px / 16px
      },

      // ── Z-index scale ──────────────────────────────────────────────────────
      zIndex: {
        dropdown: '10',
        sticky:   '20',
        overlay:  '30',
        modal:    '40',
        toast:    '50',
        tooltip:  '60',
      },

      // ── Box shadows ───────────────────────────────────────────────────────
      boxShadow: {
        card:   '0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)',
        panel:  '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
        modal:  '0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)',
        toast:  '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
      },
    }
  },
  plugins: []
}

export default config
