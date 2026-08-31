#!/usr/bin/env python3
"""
SIC code -> Dendrai industry bucket.

Single source of truth for "what industry is this filer in" — extracted
from api_server.py (where GET /industry/from-sic serves it to the frontend,
mirrored client-side by risk-engine.js's sic2industry) so material_accounts_tool.py
can classify a filer's industry too without api_server.py importing a tool
module or a tool module importing the FastAPI app.
"""

from __future__ import annotations

_SIC_RANGES = [
    (lambda n: n == 3674 or (3672 <= n <= 3679) or n in (3559, 3577), "Semiconductors"),
    (lambda n: n in (3711, 3714, 3716, 3519),                          "Automotive OEM"),
    (lambda n: 7370 <= n <= 7379,                                       "Software & Cloud"),
    (lambda n: 6020 <= n <= 6199,                                       "Financial Services"),
    (lambda n: (2830 <= n <= 2836) or (8010 <= n <= 8099),             "Healthcare & Pharma"),
    (lambda n: 5200 <= n <= 5999,                                       "Retail & Consumer"),
    (lambda n: (1300 <= n <= 1382) or n == 2911,                       "Energy & Resources"),
    (lambda n: 4911 <= n <= 4939,                                       "Utilities"),
    (lambda n: 2000 <= n <= 3999,                                       "Industrial & Manufacturing"),
]


def classify_sic(sic) -> str:
    try:
        n = int(sic)
    except (ValueError, TypeError):
        return "Generic"
    for pred, industry in _SIC_RANGES:
        if pred(n):
            return industry
    return "Generic"


# material_accounts_tool.py's three template buckets, mapped from the finer
# industries above. "Software & Cloud" covers both traditional software and
# SaaS — the deferred-revenue/capitalized-software template applies to
# either. Every other classify_sic() bucket (including "Semiconductors" and
# "Automotive OEM", which are manufacturers in substance) falls through to
# the generic top-N-by-materiality account detector.
MATERIAL_ACCOUNT_TEMPLATES = {
    "Industrial & Manufacturing": "manufacturing",
    "Semiconductors": "manufacturing",
    "Automotive OEM": "manufacturing",
    "Financial Services": "financial_services",
    "Software & Cloud": "saas",
}


def template_bucket(sic) -> str:
    """One of "manufacturing" | "financial_services" | "saas" | "generic"."""
    return MATERIAL_ACCOUNT_TEMPLATES.get(classify_sic(sic), "generic")
