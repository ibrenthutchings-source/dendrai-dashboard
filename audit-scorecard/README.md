# Internal audit scorecard

A standalone page (`index.html`, no build step, no external libraries) that reads
`internal_audit_scorecard_sample.csv` and draws the scorecard. The figures in the
sample CSV are invented.

## Opening it

To read the CSV live, serve this folder:

```
py -m http.server 8765 --directory audit-scorecard
```

Then go to http://localhost:8765. Point it at another file with `?csv=real_data.csv`.

You can also double-click `index.html`. Browsers block a page opened from disk
from reading files next to it, so the page falls back to a built-in copy of the
sample and says so. Use **Load CSV** (or drop a CSV on the page) to load real data.

## CSV format

One row per metric. Columns are matched by name, case-insensitive, in any order:

| Column | Meaning |
|---|---|
| `id` | Stable key. The headline cards are chosen by id (see `CONFIG.headline`). |
| `area` | Planning, Delivery, Quality, Governance, People, Efficiency (other areas are added at the end). |
| `metric` | Display name. |
| `unit` | `pct` (fractions, 0.88 = 88%), `days`, `count`, `score`, `hrs`. |
| `better` | `up`, `down` or `neutral`. Neutral metrics have no target and are never coloured. |
| `target` | Placeholder targets live here, so changing one is a data edit only. |
| `q3_25`, `q4_25`, … | One column per quarter, named `q<1-4>_<yy>`. Any number of quarters (minimum two; YoY needs five). The page sorts them and treats the last as latest. |

`qoq`, `yoy` and `status` may be present but are ignored. The page computes them.

## Calculations

- QoQ = latest − prior quarter; YoY = latest − same quarter a year earlier. Percentages are shown in pp.
- Status: on target if latest ≥ target (`up`) or ≤ target (`down`).
- Progress scale: `(value − first quarter) ÷ (target − first quarter)`, capped to −100%…150%. The overall and area lines are unweighted means. Metrics with no target, or whose first quarter already equals the target, are excluded.

Layout settings (headline cards, area order, cap) are in the `CONFIG` block at the top of the script in `index.html`.
