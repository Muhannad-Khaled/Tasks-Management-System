# Synthetic SOW corpus

No real SOWs were available, so this corpus was authored to drive development and
evaluation. Each document exists as **TXT, DOCX, and PDF** (same content, three parsers)
and has a hand-written gold standard in `gold/`.

Regenerate the rendered files after editing `content.py`:

```bash
.venv/Scripts/python data/sample_sows/generate.py
```

| Document | Profile | What it exercises |
|---|---|---|
| `sow_a_cairomart` | clean & complete | Happy path. Every number is explicit, so any `ASSUMED` output counts against precision. |
| `sow_b_quickbite` | gappy | The assumption engine. Offer count, earn rate, location count, training duration, team size and SLAs are all deliberately absent. |
| `sow_c_glowbeauty` | messy & adversarial | Parsing validation and grounding. Duplicate paragraph, a 2.1 → 4 section-numbering gap, a conditional "12 or maybe 15 offers" trap, TBD values, constraints buried in a pasted email, and a legal section with nothing extractable. |

The gold files record expected facts with their `source_status` (`explicit` /
`inferred` / `assumed`), expected requirements per team, expected dependencies, and —
for B and C — the gaps that must surface as assumptions rather than invented facts.

Authoring caveat: the same author wrote both the documents and the gold standards, so
SOW A risks being easier than a real SOW. SOW C is deliberately adversarial to offset
this, and all three were written before any extraction prompt existed.
