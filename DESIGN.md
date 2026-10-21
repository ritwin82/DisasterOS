---
name: DisasterOS
description: A compact, low-glare resilience console for monitoring, controlled failure, and bounded recovery.
colors:
  canvas: "#0b1120"
  surface: "#091315"
  surface-raised: "#0d1a1d"
  surface-hover: "#112226"
  control-surface: "#0e2022"
  line: "#1b3437"
  line-strong: "#285055"
  text: "#e6efed"
  muted: "#8da3a0"
  quiet: "#78908c"
  resilience-green: "#58d39b"
  resilience-green-hover: "#70dfaa"
  comparative-blue: "#69aeda"
  caution-amber: "#e6ad4b"
  incident-red: "#e56d72"
  ink-on-green: "#04100c"
  success-surface: "#0b1b17"
  warning-surface: "#1c180e"
  danger-surface: "#1c1012"
  telemetry-track: "#16282b"
typography:
  display:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "1.65rem"
    fontWeight: 720
    lineHeight: 1
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 650
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 680
  body:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "0.86rem"
    lineHeight: 1.48
  label:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "0.66rem"
    fontWeight: 700
    letterSpacing: "0.085em"
rounded:
  control: "5px"
  alert: "6px"
  panel: "7px"
  pill: "999px"
spacing:
  compact: "0.45rem"
  control-block: "0.72rem"
  panel: "0.85rem"
  section: "1rem"
  page-gutter: "1.6rem"
components:
  button-primary:
    backgroundColor: "{colors.resilience-green}"
    textColor: "{colors.ink-on-green}"
    rounded: "{rounded.control}"
    height: "38px"
  button-primary-hover:
    backgroundColor: "{colors.resilience-green-hover}"
    textColor: "{colors.ink-on-green}"
    rounded: "{rounded.control}"
  button-secondary:
    backgroundColor: "{colors.control-surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "38px"
  status-surface:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "0.72rem 0.85rem"
  telemetry-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "0.85rem 0.9rem"
  recovery-stage:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "0.85rem"
  navigation-tabs:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    height: "42px"
  source-tag:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.resilience-green}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "0.32rem 0.58rem"
---

# Design System: DisasterOS

## Overview

**Creative North Star: "The Resilience Control Room"**

DisasterOS feels like a purpose-built control room viewed during a long operating shift: low-glare, compact, calm under normal load, and unmistakable when conditions worsen. Near-black surfaces, precise separators, dense telemetry, and tabular numerals keep attention on evidence rather than decoration.

The interface is flat and operational. Green marks healthy state and primary action, blue appears only for comparative data, and amber or red communicates escalating risk. Generic AI gloss, decorative gradients, excessive rounding, and pictorial emoji are outside the visual language.

**Key Characteristics:**

- Deep navy-black canvas with restrained teal-toned surface layering.
- Dense, scan-first telemetry with compact labels and tabular figures.
- Semantic color that stays quiet until state or comparison requires it.
- Fine borders and short transitions instead of shadows or ornamental effects.
- Responsive reflow at 800px with motion reduced at the operating-system preference.

## Colors

The palette is a low-chroma night environment punctuated by a restrained resilience green and unambiguous operational states.

### Primary

- **Resilience Green** (`{colors.resilience-green}`): Healthy risk state, progress, active tabs, chart emphasis, focus outlines, links, source tags, and the primary action.
- **Resilience Green Hover** (`{colors.resilience-green-hover}`): The brighter confirmation state for the primary button only.

### Secondary

- **Comparative Blue** (`{colors.comparative-blue}`): A comparison-only chart series for memory data; it is not a second action color.

### Tertiary

- **Caution Amber** (`{colors.caution-amber}`): Medium risk, warning telemetry, disk chart data, and recovery-disabled benchmark bars.
- **Incident Red** (`{colors.incident-red}`): High or critical risk, danger telemetry, and failed recovery states.

### Neutral

- **Control-Room Canvas** (`{colors.canvas}`): Page background and scrollbar track.
- **Instrument Surface** (`{colors.surface}`): Default panel, metric, chart, expander, and bordered-container fill.
- **Raised Instrument Surface** (`{colors.surface-raised}`): Secondary control fill.
- **Hover Surface** (`{colors.surface-hover}`): Hover feedback on secondary buttons.
- **Control Surface** (`{colors.control-surface}`): Default secondary-button fill.
- **Structural Line** (`{colors.line}`): Standard dividers and panel borders.
- **Strong Structural Line** (`{colors.line-strong}`): Header rule, control borders, and scrollbar thumb.
- **Primary Text** (`{colors.text}`): Headings, readings, and operational copy.
- **Muted Text** (`{colors.muted}`): Captions, supporting detail, and chart labels.
- **Quiet Label** (`{colors.quiet}`): Small uppercase telemetry and workflow labels.
- **Ink on Green** (`{colors.ink-on-green}`): High-contrast text on the primary green action.
- **Success, Warning, and Danger Surfaces** (`{colors.success-surface}`, `{colors.warning-surface}`, `{colors.danger-surface}`): Tonal workflow-stage backgrounds for verified, cautionary, and failed states.
- **Telemetry Track** (`{colors.telemetry-track}`): Inactive capacity behind utilization bars.

### Named Rules

**The Semantic Signal Rule.** Green communicates health and primary action; blue is reserved for comparison; amber and red are reserved for escalating operational state.

**The Low-Glare Rule.** Large areas remain near-black or dark teal; bright colors appear on readings, state, focus, and compact controls rather than as decorative fields.

## Typography

**Display Font:** Segoe UI Variable (with Segoe UI, system-ui, and sans-serif fallbacks)  
**Body Font:** Segoe UI Variable (with Segoe UI, system-ui, and sans-serif fallbacks)  
**Chart Font:** IBM Plex Sans (with Segoe UI and sans-serif fallbacks)

**Character:** The system stack is neutral, compact, and native to the operating environment. Weight and tracking create hierarchy without introducing a decorative display face; chart labels may use IBM Plex Sans for technical clarity.

### Hierarchy

- **Display** (720, 1.65rem, 1 line-height, -0.025em): Product identity in the console header.
- **Headline** (650, 1.15rem, -0.015em): Primary section headings.
- **Title** (680, 1rem): Recovery-stage values and compact operational titles.
- **Body** (0.86rem, 1.48 line-height): Status context and explanatory copy.
- **Metric** (680, 1.45rem, 1.1 line-height): High-priority telemetry readings with tabular numerals.
- **Label** (700, 0.66rem, 0.085em, uppercase): Status, telemetry, and recovery-step labels.

### Named Rules

**The Readings First Rule.** Use tabular numerals for telemetry and metrics so changing values remain aligned and easy to compare.

**The Compact Label Rule.** Operational labels are small, uppercase, tracked, and subordinate to the value they identify.

## Layout

The main canvas is wide but bounded at 1500px, with a 1.6rem desktop gutter and compact vertical rhythm. The header pairs brand context with right-aligned environment metadata. Status uses a three-column grid, telemetry uses four equal columns, and recovery uses three contiguous stages; related panels share borders rather than floating independently.

At the observed 800px breakpoint, the page gutter reduces to 0.75rem. Header and status regions stack, telemetry becomes a two-by-two grid, and recovery stages become one vertical sequence. Horizontal tabs remain a single scrollable row instead of wrapping. Density is preserved rather than replaced with oversized mobile controls.

## Elevation & Depth

The system uses no shadows. Depth comes from small tonal steps between canvas and surfaces, one-pixel structural borders, and state-specific background shifts. Hover states change fill and border color without lifting the component.

### Named Rules

**The Flat Console Rule.** Surfaces remain flat at rest and in interaction; use tonal layering and borders, never drop shadows, to establish hierarchy.

## Shapes

The form language is compact and only gently softened. Buttons use 5px corners, alerts use 6px corners, and primary panels use 7px corners. Pills are reserved for short metadata and source tags. Adjacent telemetry and recovery cells are clipped by one shared 7px container so the sequence reads as one instrument, not a row of unrelated cards.

## Components

### Buttons

- **Shape:** Compact rectangular controls with gently softened corners (5px) and a minimum height of 38px.
- **Primary:** Resilience Green fill with Ink on Green text and a matching border; used for decisive, bounded actions.
- **Secondary:** Dark raised surface with Primary Text and a Strong Structural Line border.
- **Hover / Focus:** Secondary buttons shift to Hover Surface with a green border and text; primary buttons brighten to Resilience Green Hover. Keyboard focus uses a 2px green outline with a 2px offset.
- **Disabled:** Retains component styling at 45% opacity.

### Status Tags

- **Style:** Small uppercase tracked text inside a fully rounded pill, with Resilience Green text and a restrained green-toned border.
- **Use:** Short data provenance and environmental metadata only; tags do not replace buttons.

### Cards / Containers

- **Corner Style:** Gently curved outer corners (7px).
- **Background:** Instrument Surface by default; recovery stages may use the three semantic tonal surfaces.
- **Shadow Strategy:** None; depth follows the Flat Console Rule.
- **Border:** One-pixel Structural Line, with Strong Structural Line reserved for stronger boundaries.
- **Internal Padding:** Compact panel padding in the 0.72rem to 0.9rem range.

### Telemetry

- **Structure:** Uppercase quiet label, large tabular reading, then a 3px utilization track.
- **State:** Green is normal, amber begins at 75% utilization, and red begins at 90% utilization.
- **Grouping:** Four cells share one border and outer radius on desktop; two-by-two grouping is used below 800px.

### Recovery Flow

- **Structure:** Three contiguous stages labeled Step 1 through Step 3, each carrying a title, value, and concise detail.
- **State:** Neutral stages use Instrument Surface; verified, warning, and failed outcomes use their corresponding tonal surfaces.
- **Responsive Behavior:** The stages stack vertically below 800px while retaining their internal hierarchy.

### Navigation

- **Style:** Compact horizontally scrollable tabs above a one-pixel divider.
- **Active State:** Green text, a dark active fill, and a 2px green underline.
- **Default State:** Muted text with no decorative iconography.

## Do's and Don'ts

### Do:

- **Do** keep the canvas deep navy-black and use fine teal structural lines to organize dense information.
- **Do** reserve green for healthy state, focus, progress, active navigation, and primary action.
- **Do** use blue only when a comparative data series needs separation from the green primary series.
- **Do** present changing measurements with tabular numerals and compact uppercase labels.
- **Do** preserve the 800px reflow and the reduced-motion override when extending the interface.

### Don't:

- **Don't** add gradients, glows, glass effects, drop shadows, or decorative visual noise.
- **Don't** use oversized type, inflated whitespace, or large rounded cards that dilute console density.
- **Don't** use emojis or generic decorative icons as status communication.
- **Don't** use amber or red decoratively; they are operational warnings.
- **Don't** turn every datum into an isolated floating card when a shared instrument surface communicates the relationship more clearly.
