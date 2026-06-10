# Vercel Web Interface Guidelines — Full Reference

Distilled from [vercel-labs/web-interface-guidelines/AGENTS.md](https://github.com/vercel-labs/web-interface-guidelines/blob/main/AGENTS.md).

Use MUST/SHOULD/NEVER when auditing. Sacrifice grammar for brevity in review output.

## Interactions — Keyboard

- MUST: Full keyboard support per WAI-ARIA APG
- MUST: Visible focus rings (`:focus-visible`; group with `:focus-within`)
- MUST: Manage focus (trap, move, return) per APG patterns
- NEVER: `outline: none` without visible focus replacement

## Interactions — Targets & Input

- MUST: Hit target ≥24px (mobile ≥44px); expand hit area if visual <24px
- MUST: Mobile `<input>` font-size ≥16px
- NEVER: Disable browser zoom (`user-scalable=no`, `maximum-scale=1`)
- MUST: `touch-action: manipulation`
- SHOULD: Set `-webkit-tap-highlight-color` to match design

## Interactions — Forms

- MUST: Hydration-safe inputs
- NEVER: Block paste
- MUST: Loading buttons show spinner and keep original label
- MUST: Enter submits focused input; ⌘/Ctrl+Enter submits in textareas
- MUST: Keep submit enabled until request starts
- MUST: Accept free text; validate after
- MUST: Errors inline next to fields; focus first error on submit
- MUST: `autocomplete` + meaningful `name`; correct `type` and `inputmode`
- SHOULD: Disable spellcheck for emails/codes
- SHOULD: Placeholders end with `…`
- MUST: Warn on unsaved changes before navigation
- MUST: Compatible with password managers & 2FA
- MUST: Trim values (text expansion trailing spaces)
- MUST: No dead zones on checkboxes/radios

## Interactions — State & Navigation

- MUST: URL reflects state (tabs, filters, pagination, expanded panels)
- MUST: Back/Forward restores scroll
- MUST: Links use `<a>` / Next.js `<Link>` for navigation
- NEVER: `<div onClick>` for navigation

## Interactions — Feedback

- SHOULD: Optimistic UI with rollback on failure
- MUST: Confirm destructive actions or Undo window
- MUST: Polite `aria-live` for toasts/validation
- SHOULD: Ellipsis for follow-ups and loading

## Interactions — Touch & Drag

- MUST: Generous targets; avoid finicky interactions
- MUST: Delay first tooltip; instant subsequent
- MUST: `overscroll-behavior: contain` in modals
- MUST: During drag, disable selection; `inert` on dragged elements
- MUST: If it looks clickable, it must be clickable

## Interactions — Autofocus

- SHOULD: Autofocus on desktop with single primary input; rarely on mobile

## Animation

- MUST: Honor `prefers-reduced-motion`
- SHOULD: Prefer CSS > WAAPI > JS libraries
- MUST: Animate compositor-friendly props only
- NEVER: Animate layout props
- NEVER: `transition: all`
- SHOULD: Motion clarifies cause/effect or deliberate delight
- MUST: Interruptible, input-driven
- MUST: Correct `transform-origin`
- MUST: SVG transforms on `<g>` wrapper with `transform-box: fill-box`

## Layout

- SHOULD: Optical alignment (±1px)
- MUST: Deliberate grid/baseline alignment
- SHOULD: Balance icon/text lockups
- MUST: Test mobile, laptop, ultra-wide (50% zoom sim)
- MUST: Respect safe areas
- MUST: Fix overflows
- SHOULD: Flex/grid over JS measurement

## Content & Accessibility

- SHOULD: Inline help first; tooltips last resort
- MUST: Skeletons mirror content
- MUST: `<title>` matches context
- MUST: No dead ends
- MUST: Empty/sparse/dense/error states
- SHOULD: Curly quotes; `text-wrap: balance` on headings
- MUST: `font-variant-numeric: tabular-nums` for numbers
- MUST: Redundant status cues (not color-only)
- MUST: Accessible names even when visuals omit labels
- MUST: Use `…` not `...`
- MUST: `scroll-margin-top` on headings; skip link; hierarchical headings
- MUST: Resilient to long user content
- MUST: Locale-aware dates/numbers (`Intl.*`)
- SHOULD: `translate="no"` on identifiers
- MUST: Accurate `aria-label`; decorative `aria-hidden`
- MUST: Icon-only buttons have `aria-label`
- MUST: Native semantics before ARIA
- MUST: Non-breaking spaces in `10 MB`, `⌘ K`, brand names

## Content Handling

- MUST: Truncate/clamp/break long text
- MUST: Flex children need `min-w-0` for truncation
- MUST: Handle empty states

## Performance

- SHOULD: Test iOS Low Power Mode and Safari
- MUST: Measure without extension skew
- MUST: Minimize re-renders
- MUST: Profile with throttling
- MUST: Batch layout reads/writes
- MUST: Mutations <500ms
- SHOULD: Prefer uncontrolled inputs where possible
- MUST: Virtualize lists >50 items
- MUST: Preload above-fold images; lazy-load rest
- MUST: Prevent CLS
- SHOULD: `dns-prefetch` for CDN domains
- SHOULD: Critical fonts with `font-display: swap`

## Dark Mode & Theming

- MUST: `color-scheme: dark` on `<html>` for dark themes
- SHOULD: `<meta theme-color>` matches background
- MUST: Native `<select>`: explicit `background-color` and `color`

## Hydration

- MUST: Controlled inputs need `onChange`
- SHOULD: Guard date/time against hydration mismatch

## Design

- SHOULD: Layered shadows
- SHOULD: Semi-transparent borders + shadows for crisp edges
- SHOULD: Nested radii concentric
- SHOULD: Hue-consistent borders/shadows/text
- MUST: Accessible charts
- MUST: APCA contrast preferred
- MUST: Higher contrast on interactive states
- SHOULD: Match browser UI to bg
- SHOULD: Avoid gradient banding on dark backgrounds
