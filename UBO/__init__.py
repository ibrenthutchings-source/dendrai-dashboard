"""
UBO — Governance Brain
Universal Risk Object pipeline + Council of Agents.

Data flow:
  Raw Source Event
       │
  [Bronze Layer]  →  URO (pipeline_stage="BRONZE")   — immutable capture
       │
  [Silver Layer]  →  URO (pipeline_stage="SILVER")   — conformed + validated
       │
  [Gold Layer]    →  URO (pipeline_stage="GOLD")     — scored + aggregated
       │
  [Council of Agents]
    ├─ The Quant          ─┐
    ├─ The Linguist        ├─ parallel fan-out
    └─ The Graph Architect ─┘
            │
    [The Adjudicator] → URO (pipeline_stage="ADJUDICATED")
"""
