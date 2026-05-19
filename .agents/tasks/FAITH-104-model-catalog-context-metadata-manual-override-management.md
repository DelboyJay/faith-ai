# FAITH-104 — Model Catalog, Context Metadata, and Manual Override Management

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-067, FAITH-084
**FRS Reference:** Section 3.6, 9.3.4

---

## Objective

Create the authoritative model catalog and management surface that lets FAITH
reason about model context limits honestly while giving the user direct control
over PA model choice and per-agent model overrides.

---

## Scope

- Persist a model catalog for Ollama and OpenRouter models.
- Record model metadata including model identity, context-window information,
  and the provenance of that information (`discovered`, `configured/effective`,
  or `user override`).
- Let the user choose the PA model directly from the UI.
- Let the user define manual per-agent model overrides even when the PA would
  otherwise auto-select a model.
- Allow the user to override a discovered/effective context-window value when
  local runtime reality differs from provider metadata.
- Keep the stored metadata inspectable on disk and reusable by diagnostics.

---

## Notes

- For Ollama, distinguish between the runtime configured/effective context size
  and any catalog/default value.
- For OpenRouter, consume model metadata from the models API rather than hard-coding
  context-window sizes.
