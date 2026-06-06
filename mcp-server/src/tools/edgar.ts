import { z } from "zod";
import { fetchJson, buildUrl } from "../utils/http.js";

const EDGAR_BASE = "https://data.sec.gov";
const EFTS_BASE  = "https://efts.sec.gov";

// ── Tool schemas ────────────────────────────────────────────────────────────

export const LookupCompanyInput = z.object({
  ticker: z.string().optional().describe("Stock ticker symbol, e.g. 'AAPL'"),
  company_name: z.string().optional().describe("Full or partial company name — provide either this or ticker"),
});

export const GetFinancialsInput = z.object({
  cik: z.string().describe("10-digit CIK (zero-padded), e.g. '0000320193'"),
  form_type: z.enum(["10-K","10-Q"]).default("10-K").describe("Filing type"),
  limit: z.number().int().min(1).max(10).default(4)
    .describe("Number of recent filings to return"),
});

export const GetPeersByIndustryInput = z.object({
  sic_code: z.string().describe("4-digit SIC code, e.g. '7372' for software"),
  limit: z.number().int().min(1).max(50).default(20)
    .describe("Number of peer companies to return"),
});

export const GetCompanyRisksInput = z.object({
  cik: z.string().describe("10-digit CIK (zero-padded)"),
  limit: z.number().int().min(1).max(5).default(2)
    .describe("Number of recent filings to scan for risk factors"),
});

// ── EDGAR helpers ───────────────────────────────────────────────────────────

function padCik(cik: string | number): string {
  return String(cik).replace(/^0+/, "").padStart(10, "0");
}

export async function lookupCompany(input: z.infer<typeof LookupCompanyInput>) {
  const tickers = await fetchJson<Record<string, TickerEntry>>(
    "https://www.sec.gov/files/company_tickers.json"
  );

  const entries = Object.values(tickers);

  if (input.ticker) {
    const t = input.ticker.toUpperCase();
    const match = entries.find(e => e.ticker === t);
    if (!match) throw new Error(`Ticker ${t} not found in SEC company list`);
    return { cik: padCik(match.cik_str), name: match.title, ticker: match.ticker };
  }

  const q = input.company_name!.toLowerCase();
  const matches = entries
    .filter(e => e.title.toLowerCase().includes(q))
    .slice(0, 10)
    .map(e => ({ cik: padCik(e.cik_str), name: e.title, ticker: e.ticker }));

  return matches;
}

export async function getCompanyFinancials(input: z.infer<typeof GetFinancialsInput>) {
  const cik = padCik(input.cik);

  const submissions = await fetchJson<SubmissionsResponse>(
    `${EDGAR_BASE}/submissions/CIK${cik}.json`
  );

  const filings = submissions.filings.recent;
  const results: FilingSummary[] = [];

  for (let i = 0; i < filings.form.length && results.length < input.limit; i++) {
    if (filings.form[i] !== input.form_type) continue;

    const accession = filings.accessionNumber[i].replace(/-/g, "");
    const primaryDoc = filings.primaryDocument[i];
    const filingDate = filings.filingDate[i];
    const reportDate = filings.reportDate[i];

    results.push({
      form: filings.form[i],
      filing_date: filingDate,
      report_date: reportDate,
      accession_number: filings.accessionNumber[i],
      filing_url: `https://www.sec.gov/Archives/edgar/data/${parseInt(cik)}/${accession}/${primaryDoc}`,
      index_url: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${cik}&type=${input.form_type}&dateb=&owner=include&count=10`,
    });
  }

  let financialFacts: FinancialFacts | null = null;
  try {
    const facts = await fetchJson<CompanyFacts>(
      `${EDGAR_BASE}/api/xbrl/companyfacts/CIK${cik}.json`
    );
    financialFacts = extractKeyFacts(facts);
  } catch {
    // Not all companies have XBRL facts
  }

  return {
    company: { cik, name: submissions.name, sic: submissions.sic, sic_description: submissions.sicDescription },
    filings: results,
    financial_facts: financialFacts,
  };
}

export async function getPeersByIndustry(input: z.infer<typeof GetPeersByIndustryInput>) {
  const peers = await fetchCompanyBySic(input.sic_code, input.limit);
  return peers;
}

async function fetchCompanyBySic(sic: string, limit: number) {
  const browseUrl = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=${sic}&dateb=&owner=include&count=${limit}&search_text=&output=atom`;

  let atomXml: string;
  try {
    const res = await fetch(browseUrl, {
      headers: { "User-Agent": "dendrai-mcp/1.0 (contact@dendrai.io)" },
    });
    atomXml = await res.text();
  } catch {
    return [];
  }

  const matches = [...atomXml.matchAll(
    /<company-info>[\s\S]*?<conformed-name>(.*?)<\/conformed-name>[\s\S]*?<cik>(.*?)<\/cik>[\s\S]*?<assigned-sic>(.*?)<\/assigned-sic>[\s\S]*?<\/company-info>/g
  )];

  return matches.slice(0, limit).map(m => ({
    name: m[1].trim(),
    cik: padCik(m[2].trim()),
    sic: m[3].trim(),
  }));
}

export async function getCompanyRisks(input: z.infer<typeof GetCompanyRisksInput>) {
  const cik = padCik(input.cik);

  const submissions = await fetchJson<SubmissionsResponse>(
    `${EDGAR_BASE}/submissions/CIK${cik}.json`
  );

  const filings = submissions.filings.recent;
  const riskResults: RiskResult[] = [];

  let found = 0;
  for (let i = 0; i < filings.form.length && found < input.limit; i++) {
    if (!["10-K", "10-Q"].includes(filings.form[i])) continue;

    const accession = filings.accessionNumber[i].replace(/-/g, "");
    const primaryDoc = filings.primaryDocument[i];
    const filingDate = filings.filingDate[i];

    try {
      const indexJson = await fetchJson<FilingIndex>(
        `${EDGAR_BASE}/Archives/edgar/data/${parseInt(cik)}/${accession}/${filings.accessionNumber[i]}-index.json`
      );

      const mainDoc = indexJson.directory.item.find(
        f => f.name === primaryDoc || f.name.endsWith(".htm") && f.type === filings.form[i]
      );

      if (!mainDoc) continue;

      const docUrl = `${EDGAR_BASE}/Archives/edgar/data/${parseInt(cik)}/${accession}/${mainDoc.name}`;
      const html = await fetch(docUrl, {
        headers: { "User-Agent": "dendrai-mcp/1.0 (contact@dendrai.io)" },
      }).then(r => r.text());

      const risks = extractRiskFactors(html);
      if (risks) {
        riskResults.push({
          form: filings.form[i],
          filing_date: filingDate,
          accession_number: filings.accessionNumber[i],
          risk_factors: risks,
        });
        found++;
      }
    } catch {
      continue;
    }
  }

  return {
    company: { cik, name: submissions.name },
    risks: riskResults,
  };
}

// ── Text extraction helpers ─────────────────────────────────────────────────

function extractRiskFactors(html: string): string | null {
  const text = html
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s{2,}/g, " ")
    .trim();

  const riskStart = text.search(/item\s+1a[\s.:]+risk\s+factors/i);
  if (riskStart === -1) return null;

  const afterStart = text.slice(riskStart + 50);
  const riskEnd = afterStart.search(/item\s+1b[\s.:]+|item\s+2[\s.:]+/i);

  const section = riskEnd !== -1
    ? afterStart.slice(0, riskEnd)
    : afterStart.slice(0, 8000);

  return section.trim().slice(0, 6000);
}

function extractKeyFacts(facts: CompanyFacts): FinancialFacts {
  const us = facts.facts["us-gaap"] ?? {};

  const pick = (concept: string) => {
    const c = us[concept];
    if (!c) return null;
    const units = Object.values(c.units)[0] ?? [];
    const annual = units.filter(u => u.form === "10-K" && u.fp === "FY").sort((a, b) => b.end.localeCompare(a.end));
    const quarterly = units.filter(u => u.form === "10-Q").sort((a, b) => b.end.localeCompare(a.end));
    const latest = annual[0] ?? quarterly[0];
    return latest ? { value: latest.val, period_end: latest.end, form: latest.form } : null;
  };

  return {
    revenue: pick("Revenues") ?? pick("RevenueFromContractWithCustomerExcludingAssessedTax"),
    net_income: pick("NetIncomeLoss"),
    total_assets: pick("Assets"),
    total_liabilities: pick("Liabilities"),
    stockholders_equity: pick("StockholdersEquity"),
    operating_income: pick("OperatingIncomeLoss"),
    eps_basic: pick("EarningsPerShareBasic"),
    cash_and_equivalents: pick("CashAndCashEquivalentsAtCarryingValue"),
    long_term_debt: pick("LongTermDebt"),
    research_and_development: pick("ResearchAndDevelopmentExpense"),
  };
}

// ── Types ────────────────────────────────────────────────────────────────────

interface TickerEntry {
  cik_str: number;
  ticker: string;
  title: string;
}

interface SubmissionsResponse {
  name: string;
  sic: string;
  sicDescription: string;
  filings: {
    recent: {
      form: string[];
      filingDate: string[];
      reportDate: string[];
      accessionNumber: string[];
      primaryDocument: string[];
    };
  };
}

interface FilingSummary {
  form: string;
  filing_date: string;
  report_date: string;
  accession_number: string;
  filing_url: string;
  index_url: string;
}

interface RiskResult {
  form: string;
  filing_date: string;
  accession_number: string;
  risk_factors: string;
}

interface FinancialFacts {
  revenue: FactValue | null;
  net_income: FactValue | null;
  total_assets: FactValue | null;
  total_liabilities: FactValue | null;
  stockholders_equity: FactValue | null;
  operating_income: FactValue | null;
  eps_basic: FactValue | null;
  cash_and_equivalents: FactValue | null;
  long_term_debt: FactValue | null;
  research_and_development: FactValue | null;
}

interface FactValue {
  value: number;
  period_end: string;
  form: string;
}

interface CompanyFacts {
  facts: {
    "us-gaap"?: Record<string, ConceptData>;
    dei?: Record<string, ConceptData>;
  };
}

interface ConceptData {
  units: Record<string, UnitEntry[]>;
}

interface UnitEntry {
  val: number;
  end: string;
  form: string;
  fp?: string;
}

interface FilingIndex {
  directory: {
    item: Array<{ name: string; type: string }>;
  };
}
