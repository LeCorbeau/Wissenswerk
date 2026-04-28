---
{
  "version": "alpha",
  "name": "Wissenswerk",
  "description": "A restrained documentation and operations interface for corpus-to-wiki workflows.",
  "colors": {
    "primary": "#25312E",
    "on-primary": "#FFFFFF",
    "secondary": "#59615D",
    "accent": "#8E4F3A",
    "surface": "#F7F4EF",
    "surface-raised": "#FFFFFF",
    "border": "#D8D0C6",
    "text": "#1F2523",
    "muted": "#6F7773",
    "success": "#2E6B4F",
    "warning": "#A36A18",
    "danger": "#9A3D35"
  },
  "typography": {
    "h1": {"fontFamily": "Public Sans", "fontSize": "2.25rem", "fontWeight": 700, "lineHeight": "1.15"},
    "h2": {"fontFamily": "Public Sans", "fontSize": "1.5rem", "fontWeight": 700, "lineHeight": "1.25"},
    "body": {"fontFamily": "Public Sans", "fontSize": "1rem", "fontWeight": 400, "lineHeight": "1.6"},
    "label": {"fontFamily": "Public Sans", "fontSize": "0.875rem", "fontWeight": 600, "lineHeight": "1.3"},
    "mono": {"fontFamily": "IBM Plex Mono", "fontSize": "0.875rem", "fontWeight": 400, "lineHeight": "1.5"}
  },
  "rounded": {
    "sm": "4px",
    "md": "8px"
  },
  "spacing": {
    "xs": "4px",
    "sm": "8px",
    "md": "16px",
    "lg": "24px",
    "xl": "32px",
    "2xl": "48px"
  },
  "components": {
    "button-primary": {
      "backgroundColor": "{colors.primary}",
      "textColor": "{colors.on-primary}",
      "rounded": "{rounded.sm}",
      "padding": "8px 14px",
      "typography": "{typography.label}"
    },
    "button-secondary": {
      "backgroundColor": "{colors.surface-raised}",
      "textColor": "{colors.text}",
      "rounded": "{rounded.sm}",
      "padding": "8px 14px",
      "typography": "{typography.label}"
    },
    "panel": {
      "backgroundColor": "{colors.surface-raised}",
      "textColor": "{colors.text}",
      "rounded": "{rounded.md}",
      "padding": "{spacing.lg}"
    }
  }
}
---

## Overview

Wissenswerk should feel like a quiet, precise workbench for turning document corpora into navigable knowledge. The interface prioritizes provenance, status, and repeatable operations over decorative presentation.

## Colors

Use deep green-black as the primary action color, warm paper surfaces for reading, and muted neutral borders for dense operational layouts. Accent clay is reserved for links, focus states, and important but non-destructive actions.

## Typography

Use Public Sans for product UI and article chrome. Use IBM Plex Mono for commands, hashes, document IDs, provider names, and diagnostic output.

## Layout

Prefer dense but breathable layouts: narrow reading columns for prose, full-width operational tables for inventories, and compact panels for status. Use 8px spacing increments and avoid nested cards.

## Elevation & Depth

Use borders before shadows. Shadows are reserved for dialogs and overlays, never for ordinary content sections.

## Shapes

Radii are restrained: 4px for controls and 8px for panels. Avoid pill-shaped controls unless they represent tags.

## Components

Buttons should include icons when an action has a familiar symbol. Tables should keep provenance fields visible without horizontal guessing: source, confidence, status, and updated time are first-class columns.

## Do's and Don'ts

- DO keep provenance, confidence, and write mode visible in automation screens.
- DO use semantic colors only for status and risk.
- DO keep contrast at WCAG AA or better for text and controls.
- DON'T use large decorative hero sections for operational views.
- DON'T use one-note color palettes or ornamental gradients.
- DON'T hide generated changes behind vague success messages.
