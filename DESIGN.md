---
name: Family Hub
description: The household's own books, comics, films and TV, kept like a printed almanac of the family's year.
colors:
  bookplate-violet: "#a98bff"
  bookplate-violet-light: "#6236d0"
  night-ground: "#0a0a0a"
  night-panel: "#0f0f0f"
  night-panel-raised: "#191919"
  night-rule: "#2a2a2a"
  night-rule-strong: "#6b6b6b"
  newsprint-ink: "#f2f1ec"
  pencil-grey: "#a09f9a"
  day-ground: "#ecebe7"
  day-panel: "#f4f3f0"
  day-ink: "#111111"
  paper-ground: "#f5f1e8"
  paper-ink: "#0d0d0d"
  blueprint-ground: "#f4f2ec"
  blueprint-ink: "#1b22b2"
  danger-night: "#ff7a6b"
  caution-night: "#f2b84b"
  success-night: "#6fd49a"
  danger-day: "#b0261b"
  caution-day: "#8a5300"
  success-day: "#17683b"
typography:
  display:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(30px, 4.6vw, 50px)"
    fontWeight: 400
    lineHeight: 1.05
    letterSpacing: "-0.015em"
    fontVariation: "'wdth' 125"
  headline:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(22px, 2.6vw, 34px)"
    fontWeight: 400
    lineHeight: 1.3
    fontVariation: "'wdth' 112"
  title:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.2
  figure:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "46px"
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "-0.01em"
    fontFeature: "'tnum'"
    fontVariation: "'wdth' 125"
  body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Archivo, Arial Narrow, Helvetica Neue, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: "0.05em"
    fontVariation: "'wdth' 75"
  cover:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.2
rounded:
  none: "0px"
  sheet: "12px"
  arch: "28px"
  pill: "999px"
  dot: "50%"
spacing:
  "4": "4px"
  "8": "8px"
  "10": "10px"
  "12": "12px"
  "16": "16px"
  "22": "22px"
  "28": "28px"
  "40": "40px"
  "72": "72px"
components:
  button-primary:
    backgroundColor: "{colors.bookplate-violet}"
    textColor: "{colors.night-ground}"
    rounded: "{rounded.pill}"
    padding: "0 18px"
    height: "38px"
  button-primary-hover:
    backgroundColor: "transparent"
    textColor: "{colors.bookplate-violet}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.newsprint-ink}"
    rounded: "{rounded.pill}"
    padding: "0 18px"
    height: "38px"
  button-secondary-hover:
    backgroundColor: "{colors.night-panel-raised}"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.danger-night}"
    rounded: "{rounded.pill}"
  button-danger-hover:
    backgroundColor: "{colors.danger-night}"
    textColor: "{colors.night-ground}"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.newsprint-ink}"
    rounded: "{rounded.none}"
    padding: "0 12px"
    height: "36px"
  chip-pressed:
    backgroundColor: "{colors.newsprint-ink}"
    textColor: "{colors.night-ground}"
  input:
    backgroundColor: "{colors.night-ground}"
    textColor: "{colors.newsprint-ink}"
    rounded: "{rounded.none}"
    padding: "0 12px"
    height: "42px"
  nav-item-current:
    backgroundColor: "{colors.newsprint-ink}"
    textColor: "{colors.night-ground}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
  section-label-bar:
    backgroundColor: "{colors.newsprint-ink}"
    textColor: "{colors.night-ground}"
    typography: "{typography.title}"
    padding: "4px 12px 3px"
  panel:
    backgroundColor: "{colors.night-panel}"
    padding: "22px"
    rounded: "{rounded.none}"
  profile-door:
    backgroundColor: "{colors.night-panel}"
    textColor: "{colors.newsprint-ink}"
    rounded: "28px 28px 0 0"
    padding: "16px 12px 20px"
  dialog:
    backgroundColor: "{colors.night-panel}"
    textColor: "{colors.newsprint-ink}"
    rounded: "{rounded.none}"
    padding: "24px"
    width: "min(480px, calc(100vw - 32px))"
---

# Design System: Family Hub

## Overview

**Creative North Star: "The Household Almanac"**

Family Hub is the household's yearly record of what it reads and watches, printed rather than rendered. Every page is one ink on one ground: a faint 24px grid with a heavier rule every 120px, a fine print grain, open sections marked only by four corner brackets, and solid label bars for headings. Against that plain sheet, the family's own things carry all the colour: covers, posters, jackets, spines and the one accent you act on.

The mood is calm, printed and warm. The calm comes from restraint (one typeface, flat surfaces, square controls). The warmth comes from household objects standing in for generic UI: a shelf of the year's spines, a ribbon bookmark as long as your progress, the book page washed in its cover's colour, the year written out as sentences, an arched door for each person. When a new screen needs something visual, reach for an object the family would recognise from a real bookshelf before reaching for a chart or a tile.

The household picks one look (Dark, Light, Paper or Blueprint, plus an accent hue), and the system must hold in all four. An admin may let each person show in their own colour; it is off by default.

**Key Characteristics:**
- One ink, one ground, one accent; pictures in full colour.
- Archivo at three widths: wide and light for titles, wide and heavy for figures, condensed caps for small labels.
- Flat surfaces. Depth comes from tone, rules and corner brackets, never drop shadows.
- Capsule buttons for actions; everything else is square.
- Household objects (shelves, spines, jackets, ribbons, doors) in place of generic charts and tiles.

## Colors

A near-monochrome printed sheet, with a single violet stamp for action and full-colour pictures on top.

### Primary
- **Bookplate Violet** (`bookplate-violet`; `bookplate-violet-light` in the Light scheme): the one colour you act on. It's used for primary buttons, links, the active hue, the ribbon bookmark, the spine progress mark and the recap's book titles. When an admin picks a custom accent, it becomes `oklch(0.76 0.15 h)` in Dark and `oklch(0.44 0.16 h)` in Light. The lightness and chroma are fixed so every hue clears 4.5:1. In Paper the accent is the ink itself (black). In Blueprint the picked hue becomes the whole print ink.

### Neutral
- **Night Ground / Night Panel / Night Panel Raised**: the Dark scheme's page, section fill and pressed or hovered fill. They are 2–6% apart: just enough tone to separate layers without any lift.
- **Newsprint Ink**: the Dark scheme's text, rules, corner brackets and solid label bars. It's a warm off-white, not pure white.
- **Pencil Grey**: secondary text, hints and placeholders.
- **Night Rule / Night Rule Strong**: grid lines and dividers, and control outlines.
- **Day, Paper and Blueprint grounds and inks**: the same roles in the light schemes. Paper is a warm cream with black ink. Blueprint is cream with blue ink, and there its covers are tinted towards the ink when a custom hue is set.

### Status
- **Danger, Caution, Success** (night and day variants): red for a problem, amber for a heads-up, green for a done thing. Each clears 4.5:1 on its scheme's page and panels. They are used only for status, never for decoration.

### Named Rules
**The Pictures-Keep-Their-Colour Rule.** Covers, posters, photos and jackets always show in full colour, in every scheme. Everything else stays in the one ink. (The original Minimalist bundle greyscaled pictures; `app.css` deliberately overrides that.)

**The Accent-Is-For-Acting Rule.** The accent marks what you can press or what is yours right now (the current progress, the picked book). It is never used for warnings; a problem is Danger red.

## Typography

**Display, Body and Label font:** Archivo, a variable font with weight 100–900 and width 62–125%, self-hosted, with Helvetica Neue and Arial as fallbacks.
**Cover font:** Georgia (with Times New Roman), used only on drawn jackets and 3D book fronts.
**Mono:** ui-monospace, Consolas; only for file paths and technical values on admin screens.

**Character:** one family stretched three ways reads like a well-set almanac. Wide is for what you look at, condensed is for what you scan, and normal width is for what you read.

### Hierarchy
- **Display** (400, clamp 30–50px, 1.05, width 125%): page titles, in sentence case. A small accent glyph follows each one.
- **Headline** (400, clamp 22–34px, 1.3, width 112%): written-out moments such as the year recap, "This week at home" facts (22px) and the December invitation.
- **Title** (600, 17px): section headings, set reversed in a solid ink label bar.
- **Figure** (800, 46px, width 125%, tabular numbers): the big numbers on Dashboard tiles. Inside sentences, figures are bold and wide at the sentence's own size.
- **Body** (400, 15px, 1.55): paragraphs capped at 70–80ch. Small text is 13px.
- **Label** (600, 12px, 0.05em, width 75%, UPPERCASE): navigation, tabs, table heads, format badges and jacket authors. Labels are never smaller than 11px.

### Named Rules
**The Capitals-Are-Small Rule.** Uppercase is only for small condensed labels (navigation, tabs, table heads, badges). Buttons, section headings, page titles and book titles are in ordinary case, so a long title reads as words.

**The Five-Sizes Rule.** Text sizes come from 12, 13, 15, 18 and 24px, plus the Display, Headline and Figure roles. Don't add new in-between sizes.

## Layout

- **App frame:** a 224px sidebar beside a content column capped at 1160px.
  - At 860px and below, the sidebar becomes one scrolling row above the page.
  - On phones there's a bottom bar.
- **Background grid:** a 24px fine grid with a heavier line every 120px, fixed to the window. It is the sheet everything is printed on.
- **Spacing:** steps of 4, 8, 10, 12, 16, 22, 28, 40 and 72px.
  - Panels pad 22px and stack their contents 16px apart.
  - Rows gap 10–14px.
  - Tiles auto-fit at a 220px minimum.
- **Shelves:** books sit in grids of covers at least 158px wide.
  - The Library has a Covers or Shelf switch, remembered per device.
  - On a phone a shelf inside a panel keeps two covers per row.
- **Breakpoints:** 860px (sidebar), 760px (two-column layouts such as the book page, Home week and dossier stack to one), and 600px (dialog buttons fill the width).
  - On phones every control is at least 44px tall.
  - There is a 16px gutter and no sideways scroll.
- **Order on Home:** the household's own visual leads at full width, and "This week at home" follows below it. Nothing shrinks the visual to a band.

## Elevation & Depth

Family Hub is flat by rule. Nothing has a drop shadow. Layers are told apart by three things:
- tone: Panel over Ground, and Panel Raised for hover and pressed;
- rules: 1px lines, plus a 2px ink rule under the top bar and over each figure tile;
- open corner brackets: 16px arms, 1.5px thick.

Dialogs sit on a 55% black backdrop, framed by a 1.5px ink border, not by a shadow.

### Named Rules
**The No-Lift Rule.** Don't use `box-shadow` for elevation. A picked or selected thing is ringed in the accent or filled with ink; it does not float.

**The Print-Grain Rule.** The grain sits beneath the words (z-index -1). Its opacity is low: 0.05 in Light, 0.08 in Dark, and 0.06 in Paper and Blueprint.

## Shapes

- **Square by default:** chips, tabs, fields, covers, tags, toasts and dialogs all have 0px corners.
- **Exceptions that carry meaning:**
  - Action buttons are full capsules (999px).
  - Profile doors are arches (28px top corners).
  - The door's pane is a circle crossed by a tilted ring.
  - Record sheets (dossiers) take a 12px outer frame.
  - Status dots and avatars are round.
- **Sections** aren't boxes: they are four corner brackets around a lightly tinted fill.
- **The ribbon bookmark** is cut with a swallow-tail notch, 9px deep.

## Components

### Buttons
Plain and sure-footed: capsules, outlined in ink, words in ordinary case.
- **Shape:** full capsule (999px) with a 1.5px border. The default height is 38px; small buttons are 30px. Both are 44px on phones.
- **Primary:** filled with the accent and accent-ink text. On hover it empties to an accent outline with accent text.
- **Secondary:** an ink outline on a transparent fill. On hover it fills with Panel Raised.
- **Line:** an accent outline with accent text, for a second positive action.
- **Danger:** a Danger outline and text. On hover it fills with Danger. The quieter "Not right" variant stays ink and only turns red on hover or focus.
- **Disabled:** keeps its resting look on hover.
- **Focus:** a 2px ink (accent2) outline, offset 3px.

### Chips and tabs
- **Style:** square, with a 1px strong-rule border, transparent fill and 13.5px text. Heights are 36px, 30px (tags) and 28px (extra small).
- **Pressed:** solid ink fill with ground-coloured text, semi-bold. A "mine" tag has an accent border and text.
- **Tabs and switches** (Covers | Shelf, Shelf | Chart, Books | Pages) use the same square ink-fill-when-pressed language, inside a `radiogroup`.

### Panels (sections)
- **Corner style:** four 16px corner brackets in ink, no full border.
- **Background:** Panel at 72% over the grid.
- **Heading:** a solid ink label bar (Title type, reversed) led by a 14px accent glyph. The glyph is one burst everywhere except Home, which keeps a changing set.
- **Padding:** 22px, with a 16px gap.
- **Figure tiles** add a 2px ink rule along the top and a small ink glyph in the corner.

### Inputs and fields
- **Style:** square, 42px tall, with a 1px strong-rule border on the Ground colour and Pencil Grey placeholders.
- **Focus:** the global 2px ink outline.
- **Disabled:** dimmed. It must look unusable, as Discover's search does with no search set up.

### Navigation
- **Sidebar and top bar:** condensed uppercase labels with 18px icons. Hover adds a strong-rule border; the current page is solid ink with ground-coloured text.
- The top bar sits on a 2px ink rule.
- On phones the sidebar collapses to one scrolling row, plus a bottom bar.

### Dialog (`ask`)
- One modal `<dialog>` stands in for every confirmation and prompt.
- **Frame:** square, with a 1.5px ink border, 24px padding, at most 480px wide.
- **Content:** a heading in the display face (600, 20px), plain-language consequences, then right-aligned buttons.
- **Destructive asks:** focus Cancel first, and the destructive button uses the Danger style.
- **Motion:** it enters in 0.22s with `cubic-bezier(.16,1,.3,1)`, only when motion is allowed.

### Covers and jackets (signature)
- Covers are 2:3 and square-cornered.
  - Real cover art shows untouched.
  - Without art, the book is drawn as a jacket in its title-derived colour (`--cv`), with a diagonal shade.
- **Jacket details:**
  - an inset 1px frame line;
  - the title in the Cover font at the foot;
  - the author under a short rule, in condensed caps;
  - a faint pattern for the kind of book: two rules for fiction, a band for non-fiction, halftone for graphic novels, a squared grid for game books.
- A format badge sits top-left.
- Finished books on some shelves fade back (90% greyscale, 45% opacity).

### Spines and shelves (signature)
- **Year shelf** (Dashboard) and **Library Shelf view:** books stand as spines in finishing or sort order, with bookends.
  - Thicker spines mean longer books.
  - Striped spines were read outside the family library.
  - A 3px accent mark shows how far through a book being read you are.
  - Spines of read books are quieter: saturation 0.45, brightness 0.8.

### Ribbon bookmark (signature)
- Shown on the book page's cover while you're reading the book.
- An accent ribbon hangs from the top, its length set by your progress (scaled to the top 56% of the cover).
- The percentage reads down it in condensed type.
- It drops in over 0.6s when motion is allowed.

### Book page wash (signature)
- The top of a book page is washed in its cover's colour: the jacket colour, or the cover art's average colour.
- The wash shows at 58% in Dark and 28% in Light and Paper, masked to fade out well before every edge.

### Profile doors (signature)
- **Shape:** arched cards with a 1.5px ink border.
- **Pane:** a round pane showing the person's initial in the wide display face, crossed by a tilted ring.
- **Hover or selected:** the border and ring turn to the accent.
- **Own colour:** when own colours are on, each door's pane and initial take that person's hue, at the scheme's accent lightness.

### Opening a book (motion)
- Clicking a cover or spine grows it into the book page's cover:
  - a View Transition of 0.42s with `cubic-bezier(.16,1,.3,1)`;
  - the rest of the page crossfades in 0.2s.
- With reduced motion, or without View Transitions, the page simply changes.

## Do's and Don'ts

### Do:
- **Do** take every colour from the scheme tokens (`--ink`, `--panel`, `--accent`, `--danger` and the rest) so a new screen works in Dark, Light, Paper and Blueprint, and with a custom hue.
- **Do** keep covers, posters and photos in full colour, and keep everything around them in the one ink.
- **Do** turn the family's data into household objects (a shelf, a spine, a ribbon, a sentence) before reaching for a chart or a big-number tile.
- **Do** frame sections with corner brackets and a solid label bar, and use the one burst glyph outside Home.
- **Do** make every control at least 44px tall on phones, and gate motion behind `prefers-reduced-motion: no-preference`.
- **Do** say what is missing in plain words when a service isn't connected, with a dim not-set-up state rather than broken parts.

### Don't:
- **Don't** add drop shadows or glows for depth. A picked thing is ringed or filled, not lifted.
- **Don't** use the accent for warnings or errors. Problems are Danger red, heads-ups are Caution amber, and done things are Success green.
- **Don't** set buttons, headings or book titles in uppercase. Capitals are only for small condensed labels.
- **Don't** greyscale or tint covers, except Blueprint's ink tint when a custom hue is set.
- **Don't** shrink the household's own Home visual to a band; it leads at full width.
- **Don't** round chips, fields, tabs or covers. Only actions (capsules), doors (arches) and dots are curved.
