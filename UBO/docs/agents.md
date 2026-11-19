# Council Agents

The three evaluating agents each examine a Gold-stage URO through a distinct analytical lens and return an `AgentEvaluation`. All three run concurrently via `asyncio.gather()` inside `CouncilOrchestrator.evaluate()`.

**Common interface** (`agents/base.py`):

```python
class BaseAgent(ABC):
    name: str   # display name used in logs and AdjudicationResult

    @abstractmethod
    async def evaluate(self, uro: URO) -> AgentEvaluation:
        ...
```

Each agent is **stateless per call** — all context comes from the URO argument. The one exception is `TheGraphArchitect`, which also receives a shared `correlation_window` list at construction time.

---

## The Quant — `agents/quant.py`

**Analytical lens:** Numbers don't lie — but they can be manipulated. Detects statistical anomalies, payment threshold breaches, Beneish earnings manipulation signals, and SoD conflicts in quantitative data.

### Source Routing

| Source System | What it analyses |
|---|---|
| SAP | AMOUNT field, currency, Beneish M-Score, SoD role pairs |
| FRED | Risk indicator deltas and macro signal thresholds |
| SEC_EDGAR | Beneish M-Score from EDGAR financial fields |
| All others | Falls back to generic quant scan of `risk_indicators` |

### Signals Detected

#### 1. Z-Score Anomaly

Compares transaction amount against sector-specific baselines:

| Sector | Mean | Std Dev |
|---|---|---|
| SAP (default) | $75,000 | $45,000 |
| Generic | $50,000 | $30,000 |

```
z = (amount - mean) / std
```

- `|z| > 3σ` → **"extreme statistical outlier"** → ESCALATE signal
- `|z| > 2σ` → **"elevated statistical outlier"** → MONITOR signal

#### 2. Round-Number Bias

`amount % 10,000 == 0` → **"potential manual entry bias or fabricated figure"**

Round amounts without business justification are a classic indicator of manipulated entries.

#### 3. Beneish M-Score

Eight-variable model for earnings manipulation detection. Sourced from:
- SAP `risk_indicators`: `dsri`, `gmi`, `aqi`, `sgi`, `depi`, `sgai`, `tata`, `lvgi`
- EDGAR financial fields: same names

| M-Score | Verdict |
|---|---|
| > −1.78 | **LIKELY MANIPULATOR** → ESCALATE |
| > −2.22 | **Gray zone** → MONITOR |
| ≤ −2.22 | Clear |

#### 4. Payment Threshold Breach

| Threshold | Limit |
|---|---|
| Single transaction | $500,000 |
| Daily aggregate | $2,000,000 |

Triggers ESCALATE with exact amount in evidence.

#### 5. SoD Conflict Detection

Checks `conflicting_roles` against known incompatible pairs:

- `FI-AP-POSTING` + `FI-AP-APPROVAL`
- `FI-GL-POSTING` + `FI-GL-APPROVAL`
- `FI-VENDOR-MASTER` + `FI-AP-POSTING`
- `FI-PAYROLL-RUN` + `FI-PAYROLL-APPROVAL`
- `IT-ACCESS-PROVISIONING` + `IT-ACCESS-APPROVAL`

Each matched pair is reported individually in `evidence`.

### Verdict Logic

| Condition | Verdict | Confidence |
|---|---|---|
| Any critical keyword in signals (MANIPULATOR, SoD conflict, threshold breach) | ESCALATE | 0.65 + 0.07 × signal_count, max 0.95 |
| Moderate signals only (outlier, round-number bias) | MONITOR | 0.50 + 0.08 × signal_count, max 0.85 |
| No signals on a quant-relevant event | CLEAR | 0.55 |
| Non-quant event type | INSUFFICIENT_DATA | 0.30 |

**Risk delta:** +0.15 (ESCALATE) · +0.04 (MONITOR) · −0.05 (CLEAR) · 0.0 (INSUFFICIENT_DATA)

---

## The Linguist — `agents/linguist.py`

**Analytical lens:** When what people say doesn't match what they do, that gap is itself a risk signal. Detects narrative drift, obfuscation language, boilerplate text, and commit message suppression patterns.

### Source Routing

| Source System | What it reads |
|---|---|
| SAP | `BKTXT` (posting text) and `SGTXT` (line item text) |
| GitHub | `commits[].message` fields (up to 5) |
| SailPoint | `justification` / `requestJustification` field |
| All others | `risk_indicators.narrative` or `risk_indicators.description` |

### Keyword Lexicons

All patterns are compiled regexes using `\b` word boundaries and `re.IGNORECASE`. Groups are non-capturing (`(?:...)`) so `findall()` returns plain strings.

#### Obfuscation Patterns

Matches vague or evasive language in business narratives:

```
misc | miscellaneous | various | other | general | routine | regular |
per request | as discussed | see email | n/a | tbd | todo | test |
temp | temporary | quick fix | urgent | emergency | bypass |
exception | override | manual | special
```

#### Boilerplate Patterns

Indicates automated or copy-paste submissions without human authorship:

```
auto-generated | system-generated | automated | script | bot | pipeline
```

#### Urgency Escalators

Language that often precedes bypass attempts or override requests:

```
immediately | asap | critical | must-have | no time | deadline |
ceo | cfo | board | audit
```

#### Commit Suppression (GitHub only)

Keywords in commit messages that suppress CI checks or audit gates:

```
skip-ci | no-verify | force | wip | fixup | squash | revert | hotfix | emergency
```

### Additional Checks

**SAP — Narrative-Amount Divergence:**
Narrative length < 10 characters on a transaction > $100,000 → signals that the description is too brief to be legitimate.

**GitHub — Message-File Divergence:**
Commit message < 15 characters on a change touching > 20 files → **"potential rubber-stamp bypass"**.

**SailPoint — Privilege Escalation Justification Length:**
Justification < 20 characters on a `PRIVILEGE_ESCALATION` event → **"Justification too brief"**.

### Verdict Logic

| Condition | Verdict | Confidence |
|---|---|---|
| Any signal containing escalation keywords (bypass, suppression, blank, missing, Privilege escalation) | ESCALATE | 0.58 + 0.07 × signal_count, max 0.90 |
| Other signals (obfuscation hits, brevity, boilerplate) | MONITOR | 0.45 + 0.08 × signal_count, max 0.80 |
| No signals | CLEAR | 0.60 |

**Risk delta:** +0.12 (ESCALATE) · +0.04 (MONITOR) · −0.03 (CLEAR)

---

## The Graph Architect — `agents/graph_architect.py`

**Analytical lens:** No event is an island. Every actor, resource, and system is a node in a dependency graph, and risk propagates along edges. Detects blast radius, single-points-of-failure, critical asset targeting, and multi-system cascades.

### Configuration

| Constant | Value | Meaning |
|---|---|---|
| `_SPOF_ROLE_THRESHOLD` | 20 | role_count ≥ 20 → identity is a SPoF |
| `_BLAST_RADIUS_THRESHOLD` | 50 | blast_radius ≥ 50 → HIGH signal |

**Critical assets** (known high-value targets):

```
finance-erp | payroll-db | identity-vault | secrets-manager |
prod-k8s-cluster | treasury-system
```

### Signals Detected

#### 1. Blast Radius Estimation

Calculates a numeric blast radius from the conformed payload:

```
base_radius = (role_count × 8) + (entitlements × 3)
```

Multiplied by an event-type amplifier:

| EventType | Multiplier |
|---|---|
| `CASCADING_FAILURE_SIGNAL` | 20× |
| `SECRET_DETECTED` | 10× |
| `ROLE_EXPLOSION` | 4× |
| `PRIVILEGE_ESCALATION` | 3× |
| `SOD_VIOLATION` | 2× |
| Others | 1× |

`blast_radius ≥ 50` → signal added to evidence.

#### 2. Single-Point-of-Failure Identity

`role_count ≥ 20` → **"Identity has outsized reach — SPoF"**

A user holding 20+ roles can initiate and approve actions across the entire system, making them a concentrated failure point.

#### 3. Critical Asset Targeting

`resource_id` or entitlements contain any critical asset keyword → **"Event targets critical infrastructure"**

#### 4. Multi-System Cascade Detection

Scans the shared `correlation_window` (list of all previously-processed UROs in this session):

- **Actor temporal clustering:** Same `actor_id` appears in ≥ 3 events in the window across ≥ 2 different source systems → **"temporal clustering indicates coordinated activity or compromised account"**
- **Correlation ID cascade:** Same `correlation_id` appears in ≥ 2 events across ≥ 2 different source systems → **"multi-system cascade in progress"**

#### 5. Critical Path Analysis

`PRIVILEGE_ESCALATION` + critical asset targeting simultaneously → **"≤ 2-hop compromise scenario: escalated identity can reach critical asset directly"**

#### 6. Dormant Account Re-activation

`DORMANT_PRIVILEGED_ACCOUNT` event where `last_login_days ≥ 90` → **"ghost account / insider threat indicator"**

Accounts dormant for 90+ days that suddenly show activity are a high-confidence lateral movement signal.

### Verdict Logic

| Condition | Verdict | Confidence |
|---|---|---|
| Multi-system cascade detected | ESCALATE | 0.90 |
| CRITICAL PATH signal | ESCALATE | 0.88 |
| Critical asset targeting | ESCALATE | 0.85 |
| ≥ 2 signals total | ESCALATE | 0.72 |
| 1 moderate signal (blast radius, SPoF, dormant) | MONITOR | 0.60 |
| No signals | CLEAR | 0.65 |

**Risk delta:** +0.18 (ESCALATE) · +0.06 (MONITOR) · −0.04 (CLEAR)

---

## Comparing the Three Agents

| Dimension | The Quant | The Linguist | The Graph Architect |
|---|---|---|---|
| **Primary data** | Numeric fields (amounts, counts, scores) | Text fields (narratives, justifications, commit messages) | Relational fields (actor, roles, resource, correlation IDs) |
| **Strongest signal** | Beneish M-Score, payment thresholds | Bypass/suppression keywords | Multi-system cascade, critical asset targeting |
| **Max ESCALATE confidence** | 0.95 | 0.90 | 0.90 |
| **Stateless?** | Yes | Yes | No — reads `correlation_window` |
| **Source-specific?** | Yes (SAP/FRED/EDGAR specific paths) | Yes (SAP/GitHub/SailPoint specific paths) | Source-agnostic (works on any URO) |
| **MCP-aware?** | Reads `flag_count` from risk_indicators | Reads `narrative` from risk_indicators | Detects cascade via session_id clustering |

The three lenses are deliberately orthogonal: a SAP journal entry with an obfuscated narrative, a round-number amount, and a second correlated SailPoint event would fire all three agents simultaneously, maximising ensemble confidence.
