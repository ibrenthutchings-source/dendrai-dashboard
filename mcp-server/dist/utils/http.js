export async function fetchJson(url, headers = {}) {
    const res = await fetch(url, { headers: { "User-Agent": "dendrai-mcp/1.0 (contact@dendrai.io)", ...headers } });
    if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status} from ${url}: ${body.slice(0, 200)}`);
    }
    return res.json();
}
export function buildUrl(base, params) {
    const url = new URL(base);
    for (const [k, v] of Object.entries(params)) {
        if (v !== undefined)
            url.searchParams.set(k, String(v));
    }
    return url.toString();
}
