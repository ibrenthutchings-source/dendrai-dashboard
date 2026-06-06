import { z } from "zod";
import { fetchJson, buildUrl } from "../utils/http.js";
const EDGAR_BASE = "https://data.sec.gov";
const EFTS_BASE = "https://efts.sec.gov";
// ── Tool schemas ────────────────────────────────────────────────────────────
export const LookupCompanyInput = z.object({
    ticker: z.string().optional().describe("Stock ticker symbol, e.g. 'AAPL'"),
    company_name: z.string().optional().describe("Full or partial company name — provide either this or ticker"),
});
export const GetFinancialsInput = z.object({
    cik: z.string().describe("10-digit CIK (zero-padded), e.g. '0000320193'"),
    form_type: z.enum(["10-K", "10-Q"]).default("10-K").describe("Filing type"),
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
function padCik(cik) {
    return String(cik).replace(/^0+/, "").padStart(10, "0");
}
/** Resolve ticker → CIK using SEC company_tickers.json */
export async function lookupCompany(input) {
    const tickers = await fetchJson("https://www.sec.gov/files/company_tickers.json");
    const entries = Object.values(tickers);
    if (input.ticker) {
        const t = input.ticker.toUpperCase();
        const match = entries.find(e => e.ticker === t);
        if (!match)
            throw new Error(`Ticker ${t} not found in SEC company list`);
        return { cik: padCik(match.cik_str), name: match.title, ticker: match.ticker };
    }
    const q = input.company_name.toLowerCase();
    const matches = entries
        .filter(e => e.title.toLowerCase().includes(q))
        .slice(0, 10)
        .map(e => ({ cik: padCik(e.cik_str), name: e.title, ticker: e.ticker }));
    return matches;
}
/** Fetch recent 10-K or 10-Q filings and extract key financial facts */
export async function getCompanyFinancials(input) {
    const cik = padCik(input.cik);
    // 1. Get filing list
    const submissions = await fetchJson(`${EDGAR_BASE}/submissions/CIK${cik}.json`);
    const filings = submissions.filings.recent;
    const results = [];
    for (let i = 0; i < filings.form.length && results.length < input.limit; i++) {
        if (filings.form[i] !== input.form_type)
            continue;
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
    // 2. Fetch XBRL company facts for financial metrics
    let financialFacts = null;
    try {
        const facts = await fetchJson(`${EDGAR_BASE}/api/xbrl/companyfacts/CIK${cik}.json`);
        financialFacts = extractKeyFacts(facts);
    }
    catch {
        // Not all companies have XBRL facts
    }
    return {
        company: { cik, name: submissions.name, sic: submissions.sic, sic_description: submissions.sicDescription },
        filings: results,
        financial_facts: financialFacts,
    };
}
/** Get peer companies sharing the same SIC code */
export async function getPeersByIndustry(input) {
    const url = buildUrl(`${EFTS_BASE}/hits.json`, {
        q: `"${input.sic_code}"`,
        dateRange: "custom",
        startdt: "2020-01-01",
        forms: "10-K",
    });
    // Use full-text search to find companies that filed 10-Ks and match SIC
    // Fall back to the company search endpoint filtered by SIC
    const searchUrl = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=${input.sic_code}&dateb=&owner=include&count=${input.limit}&search_text=&action=getcompany&output=atom`;
    // Use the submissions search which supports SIC filtering
    const apiUrl = buildUrl(`${EDGAR_BASE}/submissions/`, {});
    // EDGAR doesn't have a direct SIC filter API, so we use the company search JSON
    const searchApiUrl = `https://efts.sec.gov/LATEST/search-index?q=%22%22&dateRange=custom&startdt=2023-01-01&enddt=2024-12-31&forms=10-K`;
    // Best available approach: query EDGAR full-text search by SIC code in company facts
    const companiesUrl = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=${input.sic_code}&dateb=&owner=include&count=${input.limit}&search_text=&output=atom`;
    // Use the EDGAR company search API (JSON format)
    const jsonSearchUrl = `https://efts.sec.gov/LATEST/search-index?q=%22%22&forms=10-K&dateRange=custom&startdt=2024-01-01`;
    // The most reliable SIC-based lookup is via the submissions dataset
    // We'll use the company_tickers_exchange.json and filter by SIC from submissions
    const peers = await fetchCompanyBySic(input.sic_code, input.limit);
    return peers;
}
async function fetchCompanyBySic(sic, limit) {
    // Use EDGAR full-text search to find recent 10-K filers by SIC
    const url = `https://efts.sec.gov/LATEST/search-index?q=%22%22&forms=10-K&dateRange=custom&startdt=2024-01-01&enddt=2025-12-31`;
    // Preferred: use the SEC company search endpoint that accepts SIC
    const searchUrl = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=${sic}&dateb=&owner=include&count=${limit}&search_text=&output=atom`;
    // Use the JSON API via EDGAR submissions index
    // The cleanest approach without a dedicated SIC endpoint is to query
    // https://efts.sec.gov/LATEST/search-index with category filter
    const apiUrl = `https://efts.sec.gov/LATEST/search-index?q=%22%22&dateRange=custom&startdt=2024-01-01&forms=10-K&hits.hits._source.period_of_report=*`;
    // Actually use the submissions/company-concept approach:
    // SEC provides a company_tickers.json; to filter by SIC we must fetch each submission.
    // A practical shortcut: use the EDGAR company search with SIC via the atom/RSS feed
    // and parse it — or use the newer JSON browse endpoint.
    const browseUrl = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=${sic}&dateb=&owner=include&count=${limit}&search_text=&output=atom`;
    let atomXml;
    try {
        const res = await fetch(browseUrl, {
            headers: { "User-Agent": "dendrai-mcp/1.0 (contact@dendrai.io)" },
        });
        atomXml = await res.text();
    }
    catch {
        return [];
    }
    // Parse Atom XML entries to extract company info
    const matches = [...atomXml.matchAll(/<company-info>[\s\S]*?<conformed-name>(.*?)<\/conformed-name>[\s\S]*?<cik>(.*?)<\/cik>[\s\S]*?<assigned-sic>(.*?)<\/assigned-sic>[\s\S]*?<\/company-info>/g)];
    return matches.slice(0, limit).map(m => ({
        name: m[1].trim(),
        cik: padCik(m[2].trim()),
        sic: m[3].trim(),
    }));
}
/** Extract risk factors section from recent filings */
export async function getCompanyRisks(input) {
    const cik = padCik(input.cik);
    const submissions = await fetchJson(`${EDGAR_BASE}/submissions/CIK${cik}.json`);
    const filings = submissions.filings.recent;
    const riskResults = [];
    let found = 0;
    for (let i = 0; i < filings.form.length && found < input.limit; i++) {
        if (!["10-K", "10-Q"].includes(filings.form[i]))
            continue;
        const accession = filings.accessionNumber[i].replace(/-/g, "");
        const primaryDoc = filings.primaryDocument[i];
        const filingDate = filings.filingDate[i];
        // Fetch the filing index to find the right document
        try {
            const indexUrl = `${EDGAR_BASE}/Archives/edgar/data/${parseInt(cik)}/${accession}/`;
            const indexJson = await fetchJson(`${EDGAR_BASE}/Archives/edgar/data/${parseInt(cik)}/${accession}/${filings.accessionNumber[i]}-index.json`);
            // Find the main 10-K/10-Q document (htm/html)
            const mainDoc = indexJson.directory.item.find(f => f.name === primaryDoc || f.name.endsWith(".htm") && f.type === filings.form[i]);
            if (!mainDoc)
                continue;
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
        }
        catch {
            continue;
        }
    }
    return {
        company: { cik, name: submissions.name },
        risks: riskResults,
    };
}
// ── Text extraction helpers ─────────────────────────────────────────────────
function extractRiskFactors(html) {
    // Strip tags for plain text
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
    // Find "Item 1A" risk factors section
    const riskStart = text.search(/item\s+1a[\s.:]+risk\s+factors/i);
    if (riskStart === -1)
        return null;
    // Find end at Item 1B or Item 2
    const afterStart = text.slice(riskStart + 50);
    const riskEnd = afterStart.search(/item\s+1b[\s.:]+|item\s+2[\s.:]+/i);
    const section = riskEnd !== -1
        ? afterStart.slice(0, riskEnd)
        : afterStart.slice(0, 8000);
    // Return first 6000 chars trimmed
    return section.trim().slice(0, 6000);
}
function extractKeyFacts(facts) {
    const us = facts.facts["us-gaap"] ?? {};
    const pick = (concept) => {
        const c = us[concept];
        if (!c)
            return null;
        // Prefer annual (10-K) values
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
