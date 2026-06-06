import "dotenv/config";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import {
  SearchFredSeriesInput,
  GetFredSeriesInput,
  GetFredObservationsInput,
  searchFredSeries,
  getFredSeriesInfo,
  getFredObservations,
} from "./tools/fred.js";

import {
  LookupCompanyInput,
  GetFinancialsInput,
  GetPeersByIndustryInput,
  GetCompanyRisksInput,
  lookupCompany,
  getCompanyFinancials,
  getPeersByIndustry,
  getCompanyRisks,
} from "./tools/edgar.js";

const server = new McpServer({
  name: "dendrai-financial",
  version: "1.0.0",
});

// ── FRED tools ──────────────────────────────────────────────────────────────

server.tool(
  "search_fred_series",
  "Search FRED for macroeconomic time-series by keyword (e.g. 'unemployment', 'CPI', 'interest rate'). Returns series IDs and metadata.",
  SearchFredSeriesInput.shape,
  async (input) => {
    const results = await searchFredSeries(input as any);
    return { content: [{ type: "text", text: JSON.stringify(results, null, 2) }] };
  }
);

server.tool(
  "get_fred_series_info",
  "Get metadata for a specific FRED series by its ID (e.g. 'UNRATE', 'GDP', 'FEDFUNDS').",
  GetFredSeriesInput.shape,
  async (input) => {
    const result = await getFredSeriesInfo(input as any);
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

server.tool(
  "get_fred_observations",
  "Fetch time-series observations (data points) for a FRED series. Supports date range filtering, frequency aggregation, and unit transformations.",
  GetFredObservationsInput.shape,
  async (input) => {
    const result = await getFredObservations(input as any);
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

// ── EDGAR tools ─────────────────────────────────────────────────────────────

server.tool(
  "lookup_company",
  "Look up a company in the SEC EDGAR database by ticker symbol or company name. Returns the CIK number needed for other tools.",
  LookupCompanyInput.shape,
  async (input) => {
    const result = await lookupCompany(input as any);
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

server.tool(
  "get_company_financials",
  "Fetch financial data from 10-K (annual) or 10-Q (quarterly) SEC filings for a company. Returns filing URLs, dates, and key XBRL financial metrics (revenue, net income, assets, liabilities, EPS, etc.).",
  GetFinancialsInput.shape,
  async (input) => {
    const result = await getCompanyFinancials(input as any);
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

server.tool(
  "get_peers_by_industry",
  "Find peer companies in the same industry using the 4-digit SIC code from SEC EDGAR. Returns a list of companies that have filed 10-Ks in that industry.",
  GetPeersByIndustryInput.shape,
  async (input) => {
    const result = await getPeersByIndustry(input as any);
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

server.tool(
  "get_company_risks",
  "Extract the 'Item 1A — Risk Factors' section from a company's recent 10-K or 10-Q filings from SEC EDGAR. Returns the full risk narrative text.",
  GetCompanyRisksInput.shape,
  async (input) => {
    const result = await getCompanyRisks(input as any);
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

// ── Start ───────────────────────────────────────────────────────────────────

const transport = new StdioServerTransport();
await server.connect(transport);
