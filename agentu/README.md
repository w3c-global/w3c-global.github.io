# Agentu

> AI you can trust with money.

Marketing site for **Agentu** — the financial-infrastructure network that lets autonomous AI run banking, payments and treasury operations, with every action recorded, monitored and verified.

## Overview

A single-page, dependency-free static site. Everything ships in one [`index.html`](index.html): markup, styles, and vanilla JavaScript. No build step, no framework, no bundler.

### Highlights

- **Live ledger** — an animated feed of treasury/payments actions that flip from `checking` to `✓ VERIFIED` in real time.
- **Motion, tastefully** — staggered scroll reveals, animated nav underlines with an active-section indicator, hover lifts on cards and buttons, and a back-to-top control.
- **Accessible by default** — skip link, keyboard focus rings, reduced-motion support (`prefers-reduced-motion`), and an inline-validated contact form with ARIA live regions.
- **Efficient** — layout reads are cached, timers pause in background tabs, and the DOM is only written when state actually changes.

## Structure

```
.
├── index.html              # The entire website (HTML + CSS + JS)
├── brand/                  # Brand assets and guidelines
│   ├── index.html          # Brand guide
│   ├── color/              # Palette (CSS, JSON, SVG swatches)
│   ├── logos/              # Wordmarks, app icon, favicon (SVG)
│   └── type/               # Type styles
└── .claude/
    └── launch.json         # Local static-server config for preview
```

## Run locally

It's a static file — serve the project root with anything:

```bash
# Python
python -m http.server 4321

# Node
npx serve -l 4321
```

Then open <http://localhost:4321>.

## Tech

Plain HTML5, CSS3, and vanilla JavaScript. Fonts: Source Serif 4, Libre Franklin, IBM Plex Mono (Google Fonts).

---

© 2026 Agentu Inc. All rights reserved.
