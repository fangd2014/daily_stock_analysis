# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-07-25
- Primary product surfaces: stock analysis dashboard, quantitative strategy workspace
- Evidence reviewed: `apps/dsa-web/src`, `apps/dsa-web/src/index.css`, `docs/DESIGN.md`, `docs/PRD.md`, `README.md`

## Brand

- Personality: calm, precise, auditable financial research terminal
- Trust signals: observable data time, explicit risk rules, complete execution logs, and paper-trading disclaimers
- Avoid: guaranteed-return language, decorative charts without decision value, hidden strategy parameters, and irreversible actions

## Product goals

- Goals: make strategy logic, execution state, latest selections, and operating guidance understandable in one workspace
- Non-goals: broker connectivity, real-money order placement, parameter editing, or arbitrary server-side command execution
- Success signals: a user can select a strategy, explain its rules, start it, follow its logs, and interpret its output without CLI access

## Personas and jobs

- Primary personas: the repository owner running local paper-trading research
- User jobs: compare recent strategies, execute a trusted strategy, audit the calculation process, and prepare the next simulated action
- Key contexts of use: desktop review after market close, occasional tablet/mobile status checks

## Information architecture

- Primary navigation: stock analysis, quantitative selection
- Core routes/screens: `/` and `/quant`
- Content hierarchy: strategy selector, selected strategy summary, execution console, selection results, operating guidance

## Design principles

- Evidence before action: show observable date and data source beside every result
- Progressive detail: surface the decision first while keeping formulas, gates, and parameters directly inspectable
- Safe execution: expose only registered strategy actions and make paper-trading scope visible
- Tradeoffs: favor dense, scannable tables on desktop and stacked cards on narrow screens

## Visual language

- Color: reuse the existing dark base with cyan accents, green success, amber caution, and red risk states
- Typography: reuse the system sans stack; use tabular numerals and monospace only for logs and identifiers
- Spacing/layout rhythm: 4/8/12/16/24 pixel rhythm with compact terminal-style panels
- Shape/radius/elevation: reuse 8-16 pixel radii and low-contrast borders; reserve glow for active states
- Motion: short status transitions only; no continuous animation except active progress indicators
- Imagery/iconography: inline SVG icons with text labels; no decorative imagery

## Components

- Existing components to reuse: navigation dock, terminal cards, buttons, badges, loading and empty states
- New/changed components: strategy list, strategy rule panel, run console, normalized result table, operation guide
- Variants and states: idle, queued, running, completed, failed, missing-data, and stale-result
- Token/component ownership: global tokens remain in `apps/dsa-web/src/index.css`; quant-only layout lives in `QuantPage.css`

## Accessibility

- Target standard: WCAG 2.1 AA where practical
- Keyboard/focus behavior: all tabs, strategy choices, and run actions are keyboard reachable with visible focus
- Contrast/readability: status is always expressed by text and icon in addition to color
- Screen-reader semantics: semantic headings, tables, live status regions, and descriptive button labels
- Reduced motion and sensory considerations: honor `prefers-reduced-motion` for new animations

## Responsive behavior

- Supported breakpoints/devices: 320px and wider, optimized for 1280px desktop
- Layout adaptations: three-column workspace becomes one column below 960px; tables scroll horizontally
- Touch/hover differences: selected states persist independently of hover; controls keep at least 40px touch height

## Interaction states

- Loading: preserve layout with compact skeleton/status text
- Empty: explain whether no report exists or no stocks passed the gates
- Error: keep previous results visible and show the actionable failure message near the run console
- Success: refresh the selected strategy result when a run completes
- Disabled: prevent duplicate runs of the same strategy and explain the current status
- Offline/slow network: polling failures do not erase existing logs or results

## Content voice

- Tone: concise, factual, and explicit about uncertainty
- Terminology: use “模拟执行”, “观察名单”, “买入条件”, and “失效条件”; never imply a guaranteed trade
- Microcopy rules: dates include the signal cutoff; percentages state whether lower or higher is favorable

## Implementation constraints

- Framework/styling system: React 19, TypeScript, Vite, Tailwind utility classes plus repository CSS tokens, FastAPI backend
- Design-token constraints: extend current CSS variables instead of introducing a parallel theme
- Performance constraints: poll only while a run is active; cap retained log lines and run history
- Compatibility constraints: Python 3.10+, current API CORS/static hosting, no broker dependency
- Test/screenshot expectations: TypeScript build and lint, Python syntax/API tests, responsive layout inspection when a browser is available

## Open questions

- [ ] Decide whether authenticated deployments need role-based permission for strategy execution / repository owner / before public hosting
- [ ] Decide whether completed run logs should move from report files into a database / repository owner / when multi-user history is needed
