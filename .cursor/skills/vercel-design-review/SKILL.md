---
name: vercel-design-review
description: Reviews web UI code against Vercel Web Interface Guidelines for accessibility, keyboard support, forms, animation, performance, and copy. Use when auditing interfaces, reviewing walkthrough/ROI UI changes, or when the user asks for a design review, accessibility check, or Vercel-style UI audit.
---

# Vercel Design Review

Audit UI code against distilled [Vercel Web Interface Guidelines](https://vercel.com/design/guidelines). Source: [vercel-labs/web-interface-guidelines](https://github.com/vercel-labs/web-interface-guidelines).

## When to run

- Before merging walkthrough or ROI tab UI changes
- After adding forms, tables, toggles, modals, or print layouts
- When accessibility or mobile behavior is questioned

## Review workflow

1. Read the target files (HTML/CSS/JS or component code).
2. Check each category below. Flag violations as **MUST** (blocking), **SHOULD** (improve), **NEVER** (critical anti-pattern).
3. Output a concise report: file → issue → rule → suggested fix.
4. Prioritize seller-facing flows: walkthrough note entry, Looks Fine, Include toggle, ROI scenario tabs, print.

## Interactions

### Keyboard (MUST)

- Full keyboard support per [WAI-ARIA APG](https://www.w3.org/WAI/ARIA/apg/patterns/)
- Visible focus rings (`:focus-visible`; group with `:focus-within`)
- Manage focus (trap, move, return) in modals and drawers
- NEVER `outline: none` without a visible replacement

### Targets & input (MUST)

- Hit target ≥24px (mobile ≥44px); expand hit area if visual is smaller
- Mobile `<input>` font-size ≥16px to prevent iOS zoom
- NEVER disable browser zoom
- `touch-action: manipulation` on interactive controls

### Forms (MUST)

- Hydration-safe inputs; controlled inputs need `onChange`
- NEVER block paste
- Loading buttons: spinner + keep original label
- Enter submits focused field; errors inline next to fields
- `autocomplete`, meaningful `name`, correct `type` and `inputmode`
- Warn on unsaved changes before navigation
- Checkbox/radio: label + control share one hit target

### State & navigation (MUST)

- URL reflects tabs/filters/expanded panels where practical
- Links use `<a>` for navigation; NEVER `<div onClick>` for navigation
- No dead ends — always offer recovery

### Feedback (MUST/SHOULD)

- Confirm destructive actions or offer Undo
- `aria-live="polite"` for toasts and async validation
- Ellipsis character `…` for loading ("Saving…") and follow-up actions ("Rename…")
- SHOULD: optimistic UI with rollback on failure

## Animation (MUST/NEVER)

- Honor `prefers-reduced-motion`
- Animate `transform` and `opacity` only — NEVER layout props
- NEVER `transition: all`
- Interruptible, input-driven motion (no gratuitous autoplay)
- SVG: apply transforms to wrapper with `transform-box: fill-box`

## Layout (MUST/SHOULD)

- Deliberate alignment; optical tweaks ±1px when needed
- Test mobile, laptop, ultra-wide
- Respect `env(safe-area-inset-*)`
- Avoid unwanted scrollbars; fix overflow
- SHOULD: flex/grid over JS measurement

## Content & accessibility (MUST)

- Skeletons mirror final content (avoid CLS)
- Design empty, sparse, dense, and error states
- `font-variant-numeric: tabular-nums` for cost columns
- Redundant status cues — not color-only
- Icon-only buttons need `aria-label`
- Semantic HTML before ARIA (`button`, `a`, `label`, `table`)
- Hierarchical headings; skip link for main content
- `scroll-margin-top` on heading anchors
- Curly quotes `" "`; use `…` not `...`
- Locale-aware numbers via `Intl.NumberFormat`

## Performance (MUST)

- Virtualize large lists (>50 rows) — walkthrough has ~130 components
- Mutations target <500ms; show loading state
- Prevent CLS (explicit image dimensions)
- Batch layout reads/writes; minimize re-renders on keystroke saves

## Design (SHOULD/MUST)

- Layered shadows (ambient + direct)
- Nested radii: child ≤ parent
- Prefer APCA contrast over WCAG 2
- Increase contrast on `:hover`/`:active`/`:focus`
- Accessible charts and color-blind-safe palettes

## Vercel copywriting (SHOULD)

- Active voice, action-oriented labels
- Consistent terminology across tabs
- Positive framing in errors ("Clear your note first" not "Invalid state")
- Concise; avoid jargon sellers won't use

## Report format

```markdown
# Vercel Design Review — [scope]

## Summary
[1–2 sentences: pass / needs work]

## Critical (MUST)
- [file:line] Issue — rule — fix

## Improvements (SHOULD)
- [file:line] Issue — rule — fix

## Passed
- [notable good patterns]

## Seller-flow checklist
- [ ] Walkthrough note field: placeholder-only prompts, no accidental persist
- [ ] Looks Fine: disabled when note exists; keyboard reachable
- [ ] Include toggle: independent state, clear label
- [ ] ROI tabs: keyboard + focus visible
- [ ] Print: content stable, no layout shift
```

## Additional reference

For the full rule set, see [reference.md](reference.md).
