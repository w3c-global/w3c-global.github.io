AGENTU — BRAND PACK
===================
Version 1.0 · 2026 · Agentu Inc
"Financial infrastructure for AI"


CONTENTS
--------
  index.html                         Open this first — visual overview of the whole pack
  logos/
    agentu-wordmark-light.svg        Wordmark for light / warm-paper grounds (navy word, forest period)
    agentu-wordmark-dark.svg         Wordmark on navy ground (paper word, light-green period)
    agentu-wordmark-green.svg        Wordmark on forest-green ground (paper word, navy period)
    agentu-appicon.svg               App icon / avatar — navy tile, 512×512, "A."
    agentu-favicon.svg               Favicon — navy tile, 32×32, "A."
  color/
    agentu-palette.svg               One-sheet swatch reference
    agentu-colors.css                CSS custom properties (--agentu-navy, …)
    agentu-colors.json               Same tokens as JSON (hex + rgb + role)
  type/
    agentu-type.css                  Font @import + the working type scale as classes
  (Full guidelines: open the "Agentu Brand Guidelines" deck)


THE WORDMARK
------------
  Always "Agentu" + a coloured period. The period is the only accent.
  Initial cap, lower-case body. Never all-caps, never all-lower.
  Typeface: Source Serif 4 · Semibold (600), tracking +0.4%.
  Minimum size: 120px wide (screen) · 24mm wide (print).
  Clear space: the cap-height of the "A" on every side.
  Where the name won't fit, the "A." app-icon mark is the only permitted abbreviation.


COLOUR
------
  Agentu Navy    #0B1B2D   rgb(11,27,45)     Primary ground, headers, icon
  Agentu Green   #1B603C   rgb(27,96,60)     Accent — period, buttons, verified
  Warm Paper     #F6F4EF   rgb(246,244,239)  Light field (never pure #FFF)
  Green Light    #4FA177   rgb(79,161,119)   Accent on dark grounds
  Ink            #16202B   rgb(22,32,43)     Body text on paper
  Slate          #54616E   rgb(84,97,110)    Secondary text
  Mist           #8493A3   rgb(132,147,163)  Labels on dark
  Line           #DCD7CB   rgb(220,215,203)  Hairlines on paper
  Signal Amber   #C9A24A   rgb(201,162,74)   Pending / attention ONLY
  Balance: ~60% paper / 30% navy / 10% green. Green is an accent, never a field.


TYPOGRAPHY (all free / open-source — Google Fonts)
--------------------------------------------------
  Source Serif 4   Display & headlines · the wordmark · weights 400–600
  Libre Franklin   Body & interface · weights 400–600
  IBM Plex Mono    Data, labels, timestamps, figures · weights 400–500


VOICE
-----
  Proof, not promises.   ·  Precise, not loud.  ·  Infrastructure, not magic.
  Calm, exact, credible. We are infrastructure for institutions, not a consumer product.


NOTE ON THE SVG FILES
---------------------
  The SVGs embed an @import for Source Serif 4, so they render correctly opened
  in any modern browser. Some design tools ignore SVG @import — install Source
  Serif 4 first, or request outlined (vector-converted) versions for print
  production lockups.
