import { z } from "zod";
import { fetchJson, buildUrl } from "../utils/http.js";

const FRED_BASE = "https://api.stlouisfed.org/fred";

function apiKey(): string {
  const key = process.env.FRED_API_KEY;
  if (!key) throw new Error("FRED_API_KEY environment variable is not set");
  return key;
}

// ── Tool schemas ────────────────────────────────────────────────────────────

export const SearchFredSeriesInput = z.object({
  query: z.string().describe("Natural-language search text, e.g. 'unemployment rate' or 'CPI'"),
  limit: z.number().int().min(1).max(50).default(10).describe("Number of results to return (1-50)"),
});

export const GetFredSeriesInput = z.object({
  series_id: z.string().describe("FRED series ID, e.g. 'UNRATE', 'GDP', 'CPIAUCSL'"),
});

export const GetFredObservationsInput = z.object({
  series_id: z.string().describe("FRED series ID"),
  observation_start: z.string().optional().describe("Start date YYYY-MM-DD (optional)"),
  observation_end: z.string().optional().describe("End date YYYY-MM-DD (optional)"),
  frequency: z.enum(["d","w","bw","m","q","sa","a"]).optional()
    .describe("Aggregation frequency: d=daily, w=weekly, m=monthly, q=quarterly, a=annual"),
  units: z.enum(["lin","chg","ch1","pch","pc1","pca","cch","cca","log"]).optional()
    .describe("Units transformation, e.g. 'pc1' = percent change from year ago"),
  limit: z.number().int().min(1).max(1000).default(100).describe("Max observations to return"),
});

// ── FRED API calls ──────────────────────────────────────────────────────────

export async function searchFredSeries(input: z.infer<typeof SearchFredSeriesInput>) {
  const url = buildUrl(`${FRED_BASE}/series/search`, {
    search_text: input.query,
    limit: input.limit,
    api_key: apiKey(),
    file_type: "json",
  });
  const data = await fetchJson<{ seriess: FredSeries[] }>(url);
  return data.seriess.map(s => ({
    id: s.id,
    title: s.title,
    frequency: s.frequency_short,
    units: s.units,
    seasonal_adjustment: s.seasonal_adjustment_short,
    last_updated: s.last_updated,
    observation_start: s.observation_start,
    observation_end: s.observation_end,
    notes: s.notes?.slice(0, 300),
  }));
}

export async function getFredSeriesInfo(input: z.infer<typeof GetFredSeriesInput>) {
  const url = buildUrl(`${FRED_BASE}/series`, {
    series_id: input.series_id,
    api_key: apiKey(),
    file_type: "json",
  });
  const data = await fetchJson<{ seriess: FredSeries[] }>(url);
  return data.seriess[0] ?? null;
}

export async function getFredObservations(input: z.infer<typeof GetFredObservationsInput>) {
  const url = buildUrl(`${FRED_BASE}/series/observations`, {
    series_id: input.series_id,
    observation_start: input.observation_start,
    observation_end: input.observation_end,
    frequency: input.frequency,
    units: input.units,
    limit: input.limit,
    sort_order: "desc",
    api_key: apiKey(),
    file_type: "json",
  });
  const data = await fetchJson<{ observations: FredObservation[] }>(url);
  // Filter out missing-value placeholders
  return data.observations
    .filter(o => o.value !== ".")
    .map(o => ({ date: o.date, value: parseFloat(o.value) }));
}

// ── Types ────────────────────────────────────────────────────────────────────

interface FredSeries {
  id: string;
  title: string;
  frequency_short: string;
  units: string;
  seasonal_adjustment_short: string;
  last_updated: string;
  observation_start: string;
  observation_end: string;
  notes?: string;
}

interface FredObservation {
  date: string;
  value: string;
}
