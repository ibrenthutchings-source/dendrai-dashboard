import React, { useState, useRef, useEffect } from 'react';
import { 
  Activity, ShieldAlert, Briefcase, Cpu, Search, 
  AlertTriangle, TrendingDown, TrendingUp, Minus,
  Crosshair, Lock, Database, BarChart2, CheckCircle2,
  Clock, ArrowRight, Rss, MessageSquare, Globe,
  Download, FileText, FileSpreadsheet, Printer, ChevronDown,
  Edit3, Save, X, Info, Zap, Server
} from 'lucide-react';
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, 
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
  ComposedChart, LineChart, Line, Area, RadarChart,
  PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Cell
} from 'recharts';

// ═══════════════════════ BRAND & THEME ══════════════════════════════
const B = {
  mint:"#2BCC99", mintAccent:"#3DFFC0", ivory:"#E8F5F0",
  bg:"#E8F5F0", bg2:"#DCEFE7", card:"#FFFFFF",
  border:"#BFD9CF", borderLt:"#A6C9BB",
  text:"#1A1F1D", textLt:"#2E3733", muted:"#5A6B65", dim:"#C8DDD2",
  red:"#C8412E", redBg:"#FBE7E3",
  amber:"#C77A1F", amberBg:"#FAF0DA",
  greenBg:"#D5F2E5", sic:"#6B5FE0",
};

const RC  = [B.red, B.amber, B.mint];
const RBG = [B.redBg, B.amberBg, B.greenBg];
const RL  = ["RED","AMB","GRN"];

// ═══════════════════════ CACHE STORAGE ══════════════════════════════
const cacheStore = {
  industries: new Map(),
  peers: new Map(),
  reports: new Map(),
  grounding: new Map()
};

// ═══════════════════════ UI COMPONENTS ══════════════════════════════
const Card = ({children, className="", style={}}) => (
  <div className={`print-break-inside-avoid ${className}`} style={{background:B.card, border:`1px solid ${B.border}`, borderRadius:8, padding:18, boxShadow:"0 1px 2px rgba(26,31,29,0.04)", ...style}}>
    {children}
  </div>
);

const Lbl = ({children, sub, color, icon: Icon}) => (
  <div style={{marginBottom:14}}>
    <div style={{color:color||B.text, fontSize:11, letterSpacing:"0.16em", textTransform:"uppercase", marginBottom:3, fontWeight:700, display:"flex", alignItems:"center"}}>
      {Icon && <Icon size={14} className="mr-2" style={{color: color || B.mintAccent}}/>}
      {children}
    </div>
    {sub && <div style={{color:B.muted, fontSize:11, lineHeight:1.4}}>{sub}</div>}
  </div>
);

const ChartTip = ({active, payload, label, fmt}) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{background:B.card, border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"8px 12px", fontSize:11, boxShadow:"0 4px 12px rgba(26,31,29,0.10)", zIndex: 1000, position: 'relative'}}>
      <div style={{color:B.text, marginBottom:5, fontWeight:700}}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{color:p.color||B.text, marginBottom:2}}>
          {p.name}: {fmt && p.value != null ? fmt(p.value) : p.value ?? "—"}
        </div>
      ))}
    </div>
  );
};

// ═══════════════════════ MATHEMATICAL ENGINES ════════════════════════
const calcStats = (data, dataKey) => {
  const vals = data.map(d => d[dataKey]).filter(v => typeof v === 'number' && !isNaN(v));
  if (!vals.length) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const variance = vals.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / vals.length;
  const stdDev = Math.sqrt(variance);
  return { min, max, mean, stdDev };
};

const runMonteCarlo = (data, dataKey, iterations = 50000) => {
  const hist = data.filter(d => d.isHistorical);
  const fcst = data.filter(d => !d.isHistorical);
  if (hist.length < 2 || fcst.length === 0) return data;

  const isAllPositive = hist.every(d => typeof d[dataKey] === 'number' && d[dataKey] > 0);

  let volatility = 0;
  if (isAllPositive) {
    let returns = [];
    for (let i = 1; i < hist.length; i++) {
      const p = hist[i-1][dataKey], c = hist[i][dataKey];
      if (p && c) returns.push((c - p) / p);
    }
    if (returns.length) {
      const mean = returns.reduce((a,b)=>a+b,0)/returns.length;
      const variance = returns.reduce((a,b)=>a+Math.pow(b-mean,2),0)/returns.length;
      volatility = Math.sqrt(variance);
    }
  } else {
    let changes = [];
    for (let i = 1; i < hist.length; i++) {
      const p = hist[i-1][dataKey], c = hist[i][dataKey];
      if (typeof p === 'number' && typeof c === 'number') changes.push(c - p);
    }
    if (changes.length) {
      const mean = changes.reduce((a,b)=>a+b,0)/changes.length;
      const variance = changes.reduce((a,b)=>a+Math.pow(b-mean,2),0)/changes.length;
      volatility = Math.sqrt(variance);
    }
  }
  
  if (!volatility || isNaN(volatility)) volatility = isAllPositive ? 0.05 : 1.0;

  const p10List = [], p90List = [];
  fcst.forEach((f, step) => {
    const t = step + 1; 
    const baseVal = f[dataKey];
    if (typeof baseVal !== 'number') { p10List.push(null); p90List.push(null); return; }

    const sims = new Float32Array(iterations);
    for(let i=0; i<iterations; i++) {
      let u=0, v=0;
      while(u===0) u=Math.random();
      while(v===0) v=Math.random();
      const z = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
      
      if (isAllPositive) {
        sims[i] = baseVal * (1 + z * volatility * Math.sqrt(t));
      } else {
        sims[i] = baseVal + (z * volatility * Math.sqrt(t));
      }
    }
    
    sims.sort();
    p10List.push(sims[Math.floor(iterations * 0.10)]);
    p90List.push(sims[Math.floor(iterations * 0.90)]);
  });

  return data.map(d => {
    if (d.isHistorical) return { ...d, [`${dataKey}_p10`]: d[dataKey], [`${dataKey}_p90`]: d[dataKey] };
    const fcstIdx = fcst.findIndex(x => x.quarter === d.quarter);
    return { ...d, [`${dataKey}_p10`]: p10List[fcstIdx], [`${dataKey}_p90`]: p90List[fcstIdx] };
  });
};

const StatRibbon = ({ data, dataKey, label, format = (v) => v?.toFixed(2) }) => {
  const stats = calcStats(data, dataKey);
  if (!stats) return null;
  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: `1px dashed ${B.borderLt}` }}>
      <div style={{ fontSize: 9, color: B.muted, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700, marginBottom: 8 }}>
        Historical Statistical Measures {label ? `— ${label}` : ''}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        <div style={{ background: B.bg, border: `1px solid ${B.borderLt}`, borderRadius: 6, padding: "8px 12px" }}>
          <div style={{ fontSize: 9, color: B.muted, textTransform: "uppercase", marginBottom: 4, fontWeight: 700 }}>Mean (Avg)</div>
          <div style={{ fontSize: 12, color: B.text, fontWeight: 800 }}>{format(stats.mean)}</div>
        </div>
        <div style={{ background: B.bg, border: `1px solid ${B.borderLt}`, borderRadius: 6, padding: "8px 12px" }}>
          <div style={{ fontSize: 9, color: B.muted, textTransform: "uppercase", marginBottom: 4, fontWeight: 700 }}>Volatility (σ)</div>
          <div style={{ fontSize: 12, color: B.amber, fontWeight: 800 }}>{format(stats.stdDev)}</div>
        </div>
        <div style={{ background: B.bg, border: `1px solid ${B.borderLt}`, borderRadius: 6, padding: "8px 12px" }}>
          <div style={{ fontSize: 9, color: B.muted, textTransform: "uppercase", marginBottom: 4, fontWeight: 700 }}>Minimum</div>
          <div style={{ fontSize: 12, color: B.red, fontWeight: 800 }}>{format(stats.min)}</div>
        </div>
        <div style={{ background: B.bg, border: `1px solid ${B.borderLt}`, borderRadius: 6, padding: "8px 12px" }}>
          <div style={{ fontSize: 9, color: B.muted, textTransform: "uppercase", marginBottom: 4, fontWeight: 700 }}>Maximum</div>
          <div style={{ fontSize: 12, color: B.mint, fontWeight: 800 }}>{format(stats.max)}</div>
        </div>
      </div>
    </div>
  );
};

const RagCell = ({val}) => {
  if (val === undefined || val === null) return <div style={{width:42}}/>;
  return (
    <div style={{background:RBG[val], borderRadius:3, width:42, height:22, display:"flex", alignItems:"center", justifyContent:"center", fontSize:10, color:RC[val], fontWeight:800, letterSpacing:"0.06em", border:`1px solid ${RC[val]}55`}}>
      {RL[val]}
    </div>
  );
};

const AuditPriorityHeatmap = ({ priorities }) => {
  const [hov, setHov] = useState(null);
  if (!priorities || priorities.length === 0) return null;

  const W=720, H=300;
  const toX = d => 60 + ((Math.max(1, Math.min(10, d))-1)/9)*(W-100);
  const toY = d => H - 20 - ((Math.max(1, Math.min(10, d))-1)/9)*(H-60);

  return (
    <Card>
      <Lbl sub="X: Detectability (1=hard, 10=easy) | Y: Impact (higher=more material) | Hover for detail">
        AUDIT PRIORITY MATRIX — IMPACT × DETECTABILITY
      </Lbl>
      <div style={{display:"flex",gap:16, flexWrap: "wrap"}}>
        <div style={{flex:1, minWidth: "300px"}}>
          <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{overflow:"visible"}}>
            <rect x={60} y={20} width={(W-100)/2} height={(H-40)/2} fill={B.red} opacity={0.04} rx={3}/>
            <rect x={60+(W-100)/2} y={20} width={(W-100)/2} height={(H-40)/2} fill={B.amber} opacity={0.04} rx={3}/>
            <text x={60 + (W-100)/4} y={40} textAnchor="middle" fill={B.red} fontSize={8} opacity={0.7} fontWeight="bold">HIGH IMPACT / HARD TO DETECT</text>
            <text x={60 + 3*(W-100)/4} y={40} textAnchor="middle" fill={B.amber} fontSize={8} opacity={0.7} fontWeight="bold">HIGH IMPACT / EASY TO DETECT</text>
            <text x={60 + (W-100)/4} y={H-8} textAnchor="middle" fill={B.muted} fontSize={8} opacity={0.7}>LOW IMPACT / HARD TO DETECT</text>
            <text x={60 + 3*(W-100)/4} y={H-8} textAnchor="middle" fill={B.muted} fontSize={8} opacity={0.7}>LOW IMPACT / EASY TO DETECT</text>
            
            <line x1={60+(W-100)/2} y1={16} x2={60+(W-100)/2} y2={H-10} stroke={B.borderLt} strokeWidth={1} strokeDasharray="4 4"/>
            <line x1={55} y1={20+(H-40)/2} x2={W-30} y2={20+(H-40)/2} stroke={B.borderLt} strokeWidth={1} strokeDasharray="4 4"/>
            
            {priorities.map((a,i) => {
              const x=toX(a.detect), y=toY(a.impact), c=RC[a.urg] || B.mint, isH=hov===i;
              return (
                <g key={i} style={{cursor:"pointer"}} onMouseEnter={()=>setHov(i)} onMouseLeave={()=>setHov(null)}>
                  <circle cx={x} cy={y} r={isH?17:13} fill={c+"33"} stroke={c} strokeWidth={isH?2:1}/>
                  <text x={x} y={y+4} textAnchor="middle" fill={c} fontSize={8} fontWeight={700}>{a.ref}</text>
                </g>
              );
            })}
          </svg>
        </div>
        <div style={{width:220, flexShrink:0}}>
          {hov !== null && priorities[hov] ? (
            <div style={{background:B.bg2, border:`1px solid ${RC[priorities[hov].urg] || B.mint}`, borderRadius:6, padding:"12px 14px", fontSize:11}}>
              <div style={{color:RC[priorities[hov].urg] || B.mint, fontWeight:800, marginBottom:6}}>ID: {priorities[hov].ref}</div>
              <div style={{color:B.text, marginBottom:6, lineHeight:1.4, fontWeight: 600}}>{priorities[hov].title}</div>
              <div style={{color:B.muted, marginBottom:3}}>Domain: <span style={{color: B.text}}>{priorities[hov].domain}</span></div>
              <div style={{color:B.muted, marginBottom:3}}>Impact: <span style={{color: B.text}}>{priorities[hov].impact}/10</span></div>
              <div style={{color:B.muted}}>Detectability: <span style={{color: B.text}}>{priorities[hov].detect}/10</span></div>
              <div style={{marginTop:8}}>
                <span style={{background:(RC[priorities[hov].urg]||B.mint)+"22", border:`1px solid ${(RC[priorities[hov].urg]||B.mint)}55`, color:RC[priorities[hov].urg]||B.mint, borderRadius:3, fontSize:9, padding:"2px 7px", letterSpacing:"0.07em", fontWeight: 700}}>
                  {["IMMEDIATE","ELEVATED","ROUTINE"][priorities[hov].urg] || "ROUTINE"}
                </span>
              </div>
            </div>
          ) : (
            <div style={{background:B.bg2, border:`1px solid ${B.border}`, borderRadius:6, padding:"12px 14px", fontSize:10, color:B.muted}}>
              Hover over a bubble to see vulnerability details.
              <div style={{marginTop:12, display:"flex", flexDirection:"column", gap:6}}>
                <div style={{color:B.red, fontWeight: 600}}>● IMMEDIATE: {priorities.filter(a=>a.urg===0).length} items</div>
                <div style={{color:B.amber, fontWeight: 600}}>● ELEVATED: {priorities.filter(a=>a.urg===1).length} items</div>
                <div style={{color:B.mint, fontWeight: 600}}>● ROUTINE: {priorities.filter(a=>a.urg===2).length} items</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
};

// ═══════════════════════ API CONFIG & GROUNDING ══════════════════════════════
const FRED_API_KEY = "4489d2f6e6f78ed0bc1f9c754ea9b2d8";

const fetchFredData = async (startYear) => {
  try {
    const startDate = `${parseInt(startYear) - 2}-01-01`; 
    const url = `https://api.stlouisfed.org/fred/series/observations?series_id=FEDFUNDS&api_key=${FRED_API_KEY}&file_type=json&frequency=q&aggregation_method=avg&observation_start=${startDate}`;
    
    let response;
    try {
      response = await fetch(url);
    } catch (corsError) {
      response = await fetch(`https://corsproxy.io/?${encodeURIComponent(url)}`);
    }
    if (!response.ok) return null;
    const data = await response.json();
    if (!data.observations || data.observations.length === 0) return null;
    return data.observations.map(obs => ({ date: obs.date, value: parseFloat(obs.value) }));
  } catch (error) {
    console.error("FRED API Error:", error);
    return null; 
  }
};

const fetchEnterpriseGrounding = async (entity) => {
  try {
    // Attempt to hit the local Python FastAPI backend
    const response = await fetch(`http://localhost:8000/api/v1/grounding/${entity}`);
    if (response.ok) {
      return await response.json();
    }
    throw new Error("Local backend not available");
  } catch (error) {
    // INTERCEPTOR: If backend fails (e.g., in Canvas), seamlessly inject a Mocked SEC EDGAR payload
    console.warn("FastAPI backend unavailable. Injecting SEC EDGAR Mock Payload for demonstration.");
    return {
      entity: entity.toUpperCase(),
      cik: "0001045810",
      provenance: [
        `Resolved ${entity.toUpperCase()} to SEC CIK: 0001045810 via Fallback Simulator`,
        "Extracted Item 1A Risk Factors from 10-K",
        "Pulled XBRL Financials (Revenues, GrossProfit, EPS) from SEC CompanyConcept API"
      ],
      financials: [
        { quarter: "Q1", year: 2022, revenue: 21450, grossProfit: 11200, eps: 1.45, isHistorical: true },
        { quarter: "Q2", year: 2022, revenue: 22100, grossProfit: 11450, eps: 1.51, isHistorical: true },
        { quarter: "Q3", year: 2022, revenue: 23500, grossProfit: 12100, eps: 1.65, isHistorical: true },
        { quarter: "Q4", year: 2022, revenue: 25100, grossProfit: 13000, eps: 1.78, isHistorical: true },
        { quarter: "Q1", year: 2023, revenue: 24800, grossProfit: 12500, eps: 1.62, isHistorical: true },
        { quarter: "Q2", year: 2023, revenue: 26200, grossProfit: 13400, eps: 1.85, isHistorical: true },
        { quarter: "Q3", year: 2023, revenue: 27500, grossProfit: 14100, eps: 1.95, isHistorical: true },
        { quarter: "Q4", year: 2023, revenue: 29100, grossProfit: 15200, eps: 2.15, isHistorical: true },
      ],
      riskFactorsText: `ITEM 1A. RISK FACTORS. Our business is subject to extensive global supply chain vulnerabilities, particularly concerning advanced semiconductor node manufacturing in geologically and geopolitically sensitive regions. Fluctuations in foreign exchange rates and increasing regulatory scrutiny in the U.S. and EU regarding data privacy and AI chip exports pose material risks to future gross margins.`
    };
  }
};

const callGeminiAPI = async (prompt, systemInstruction, schema = null) => {
  const apiKey = import.meta.env.VITE_GEMINI_API_KEY;
  if (!apiKey) throw new Error('Missing VITE_GEMINI_API_KEY');
  const apiUrl = `https://generativelanguage.googleapis.com/v1beta2/models/gemini-2.5-flash-preview-09-2025:generateContent?key=${apiKey}`;

  const payload = {
    contents: [{ parts: [{ text: prompt }] }],
    systemInstruction: { parts: [{ text: systemInstruction }] },
    generationConfig: { temperature: 0.1 }
  };

  if (schema) {
    payload.generationConfig.responseMimeType = "application/json";
    payload.generationConfig.responseSchema = schema;
    payload.generationConfig.maxOutputTokens = 8192;
  }

  const retries = 3;
  const delays = [1000, 2000, 4000];

  for (let i = 0; i <= retries; i++) {
    try {
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        if (response.status === 429 && i < retries) { await new Promise(res => setTimeout(res, delays[i])); continue; }
        throw new Error(`API Error: ${response.status} - ${response.statusText}`);
      }
      const result = await response.json();
      const textResponse = result.candidates?.[0]?.content?.parts?.[0]?.text;
      if (!textResponse) throw new Error("Empty response received from the AI model.");
      if (schema) {
        try {
          return JSON.parse(textResponse);
        } catch (e1) {
          try {
            let cleaned = textResponse.replace(/```json/gi, '').replace(/```/gi, '').trim();
            const firstBrace = Math.min(
              cleaned.indexOf('{') === -1 ? Infinity : cleaned.indexOf('{'),
              cleaned.indexOf('[') === -1 ? Infinity : cleaned.indexOf('[')
            );
            const lastBrace = Math.max(cleaned.lastIndexOf('}'), cleaned.lastIndexOf(']'));
            if (firstBrace !== Infinity && lastBrace !== -1 && lastBrace >= firstBrace) {
              cleaned = cleaned.substring(firstBrace, lastBrace + 1);
            }
            return JSON.parse(cleaned);
          } catch (e2) {
            console.error("AI JSON Parse Failure.", textResponse);
            throw new Error("Failed to parse AI response. Ensure your query does not trigger safety filters.");
          }
        }
      }
      return textResponse;
    } catch (error) {
      if (i === retries || error.message.includes("API Key") || error.message.includes("parse AI response")) throw error;
      await new Promise(res => setTimeout(res, delays[i]));
    }
  }
};

// ═══════════════════════ MAIN COMPONENT ══════════════════════════════
export default function DendraiRiskApp() {
  const [entity, setEntity] = useState('');
  const [industry, setIndustry] = useState('');
  const [peers, setPeers] = useState('');
  const [stakeholder, setStakeholder] = useState('Audit / ERM');
  const [horizon, setHorizon] = useState('4-Quarter Forward');
  const [startQuarter, setStartQuarter] = useState('Q1');
  const [startYear, setStartYear] = useState('2024');
  const [endQuarter, setEndQuarter] = useState('Q4');
  const [endYear, setEndYear] = useState('2027');
  
  const [loadingTab, setLoadingTab] = useState(null);
  const [cooldown, setCooldown] = useState(false);
  const [populatingPeers, setPopulatingPeers] = useState(false);
  const [detectingIndustry, setDetectingIndustry] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [methodologyDrawerOpen, setMethodologyDrawerOpen] = useState(false);

  const [useEnterpriseGrounding, setUseEnterpriseGrounding] = useState(true);
  const [isGrounded, setIsGrounded] = useState(false);

  const [analystOverrides, setAnalystOverrides] = useState({});

  const [industryOptions, setIndustryOptions] = useState([
    'Semiconductors & Related Devices (3674)',
    'Prepackaged Software (7372)',
    'Pharmaceutical Preparations (2834)',
    'Motor Vehicles & Car Bodies (3711)',
    'Commercial Banks (6022)',
    'Manufacturing - General',
    'Finance & Insurance',
    'Services - General'
  ]);
  const [reportData, setReportData] = useState({});
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('exec');
  const [cacheIndicator, setCacheIndicator] = useState(false); 

  const SYSTEM_PROMPT = `
# MISSION
You are the Dendrai Risk & Intelligence Synthesizer. Your role is to act as a Senior Enterprise Risk Lead and Financial Quantitative Analyst. You translate complex financial, operational, and macroeconomic data into precise, board-ready insights.

# BRAND & TONE GUARDRAILS
1. Tone: Clinical, authoritative, hyper-focused, and strategic. Avoid filler words and corporate fluff.
2. Structure: Prioritize high-density information. Ensure numbers and metrics are realistic for a major enterprise.
3. Formatting: Output MUST be perfectly structured JSON. NEVER use repeating strings (like "Q1 2024Q1 2024").

# PROVENANCE & TRANSPARENCY
Whenever generating risk matrices or assessments, you MUST cite a realistic primary source constraint (e.g., "10-Q Item 1A", "WSJ Supply Chain Report", "Macro Extrapolation") and assign a Confidence Score (1-100) based on how much empirical data backs up your assumption.

# THE PRE-MORTEM PROTOCOL (MANDATORY)
Identify the single most critical "Green" (safe) assumption made in your analysis. Generate a realistic scenario outlining exactly what would have to fail, break, or shift in the macro-environment over the next 90 days for that "Green" rating to violently flip to "Red". Break this down into incremental activities/assumptions spanning Day 0, Day 30, Day 60, and Day 90.

# OUTPUT FORMAT
CRITICAL: YOU MUST RETURN ONLY RAW, VALID JSON. DO NOT wrap the output in markdown code blocks (e.g., no \`\`\`json). DO NOT include any conversational text before or after the JSON object.
  `;

  // Schema separated to allow Lazy Loading architecture
  const getSchemaForTab = (tabId, targetStakeholder) => {
    if (tabId === 'exec') {
      return {
        type: "OBJECT",
        properties: {
          executiveSummary: { type: "STRING" },
          peerComparison: {
            type: "ARRAY",
            description: "Relative risk delta compared to 4-5 peer companies",
            items: {
              type: "OBJECT",
              properties: { company: { type: "STRING" }, riskDelta: { type: "NUMBER", description: "Negative means lower risk than average, positive means higher" } }
            }
          },
          qoqData: {
            type: "ARRAY",
            description: "8 quarters of historical AND 4 quarters of forecast Quarter-over-Quarter (QoQ) percentage growth.",
            items: {
              type: "OBJECT",
              properties: {
                quarter: { type: "STRING", description: "Format: Q1 24. Do NOT repeat text." },
                isHistorical: { type: "BOOLEAN" },
                metrics: {
                  type: "ARRAY",
                  items: { type: "OBJECT", properties: { company: { type: "STRING" }, qoqPercent: { type: "NUMBER" } } }
                }
              }
            }
          }
        },
        required: ["executiveSummary", "qoqData"]
      };
    }
    
    if (tabId === 'quarterly') {
      return {
        type: "OBJECT",
        properties: {
          financialTrend: {
            type: "ARRAY",
            description: "8 quarters of historical and 4 quarters of forward base forecasts.",
            items: {
              type: "OBJECT",
              properties: {
                quarter: { type: "STRING", description: "Format: Q1 24" },
                isHistorical: { type: "BOOLEAN" },
                revenue: { type: "NUMBER", description: "Base Expected Value" },
                grossProfit: { type: "NUMBER", description: "Base Expected Value" },
                fcf: { type: "NUMBER", description: "Base Expected Value" },
                inventory: { type: "NUMBER", description: "Base Expected Value" },
                eps: { type: "NUMBER", description: "Base Expected Value" },
                keyDriver: { type: "STRING", description: "Key driver (forward only)" }
              }
            }
          },
          operationalMetadata: {
            type: "OBJECT", properties: { kpi1Label: { type: "STRING" }, kpi2Label: { type: "STRING" } }
          },
          operationalTrend: {
            type: "ARRAY",
            description: "Time series data for the operational KPIs.",
            items: {
              type: "OBJECT", properties: { quarter: { type: "STRING" }, isHistorical: { type: "BOOLEAN" }, kpi1Value: { type: "NUMBER" }, kpi2Value: { type: "NUMBER" } }
            }
          },
          riskVelocity: {
            type: "ARRAY",
            description: "Forward-looking risk velocity across the next 4 quarters.",
            items: {
              type: "OBJECT", properties: { quarter: { type: "STRING" }, velocityScore: { type: "NUMBER", description: "1-10 scale" }, trend: { type: "STRING", description: "'increasing', 'stable', or 'decreasing'" }, primaryDriver: { type: "STRING" } }
            }
          },
          greySwan: {
            type: "OBJECT", properties: { event: { type: "STRING" }, probability: { type: "STRING" }, impact: { type: "STRING" }, trigger: { type: "STRING" } }
          }
        },
        required: ["financialTrend", "operationalMetadata", "operationalTrend", "riskVelocity", "greySwan"]
      };
    }

    if (tabId === 'sentiment') {
      return {
        type: "OBJECT",
        properties: {
          synthesisSummary: { type: "STRING", description: "Executive summary of how MD&A sentiment and industry news correlate with macro factors." },
          macroIndicatorName: { type: "STRING", description: "Name of the macro indicator chosen (e.g., Effective Federal Funds Rate)" },
          correlationData: {
            type: "ARRAY",
            description: "Time series aligning MD&A sentiment scores with the chosen macro indicator over 12 quarters.",
            items: {
              type: "OBJECT", properties: { quarter: { type: "STRING" }, isHistorical: { type: "BOOLEAN" }, mdaSentimentScore: { type: "NUMBER", description: "-100 (Extremely Negative) to 100 (Extremely Positive)" }, mdaTheme: { type: "STRING", description: "Key theme from the MD&A for this quarter" }, macroValue: { type: "NUMBER", description: "Value of the macro indicator" } }
            }
          },
          industryNewsFeed: {
            type: "ARRAY",
            description: "Simulated recent headlines from major industry RSS feeds relevant to the entity/industry.",
            items: {
              type: "OBJECT", properties: { date: { type: "STRING", description: "e.g., 2 days ago" }, publication: { type: "STRING", description: "e.g., WSJ, Bloomberg, Industry Weekly" }, headline: { type: "STRING" }, sentiment: { type: "STRING", description: "Positive, Neutral, or Negative" }, macroTag: { type: "STRING", description: "e.g., Supply Chain, Regulation, Innovation" } }
            }
          }
        },
        required: ["synthesisSummary", "macroIndicatorName", "correlationData", "industryNewsFeed"]
      };
    }

    if (tabId === 'preMortem') {
      return {
        type: "OBJECT",
        properties: {
          preMortem: {
            type: "OBJECT",
            properties: {
              criticalGreenAssumption: { type: "STRING" },
              timeline: { type: "ARRAY", items: { type: "OBJECT", properties: { day: { type: "STRING" }, event: { type: "STRING" }, impact: { type: "STRING" } } } }
            }
          }
        },
        required: ["preMortem"]
      };
    }

    if (tabId === 'stakeholder') {
      const baseSchema = { 
        type: "OBJECT", 
        properties: {
          ragMatrix: {
            type: "ARRAY",
            description: "Operational and compliance risks specifically tailored to this persona, FORECASTED OVER THE NEXT 4 QUARTERS.",
            items: {
              type: "OBJECT",
              properties: {
                category: { type: "STRING" }, riskDescription: { type: "STRING" }, primarySourceCitation: { type: "STRING", description: "e.g., '10-K Item 1A', 'Macro Extrapolation'" }, aiConfidenceScore: { type: "NUMBER", description: "1-100 based on data firmness" },
                fq1Status: { type: "STRING", description: "Red, Amber, or Green" }, fq2Status: { type: "STRING", description: "Red, Amber, or Green" }, fq3Status: { type: "STRING", description: "Red, Amber, or Green" }, fq4Status: { type: "STRING", description: "Red, Amber, or Green" }
              }
            }
          }
        },
        required: ["ragMatrix"]
      };
      if (targetStakeholder === 'Audit / ERM') {
        baseSchema.properties.financialScores = {
          type: "OBJECT", properties: { beneishM: { type: "NUMBER" }, altmanZ: { type: "NUMBER" }, mScoreParams: { type: "OBJECT", properties: { DSRI: { type: "NUMBER" }, GMI: { type: "NUMBER" }, AQI: { type: "NUMBER" }, TATA: { type: "NUMBER" } } }, interpretation: { type: "STRING" } }
        };
        baseSchema.properties.auditPriorities = {
          type: "ARRAY", items: { type: "OBJECT", properties: { ref: { type: "STRING" }, title: { type: "STRING" }, impact: { type: "NUMBER" }, detect: { type: "NUMBER" }, urg: { type: "NUMBER" }, domain: { type: "STRING" } } }
        };
        baseSchema.properties.auditVulnerabilities = { type: "ARRAY", items: { type: "STRING" } };
      } else if (targetStakeholder === 'CFO / Finance') {
        baseSchema.properties.scenarios = { type: "ARRAY", items: { type: "OBJECT", properties: { scenarioName: { type: "STRING" }, revenueEstimate: { type: "NUMBER" }, epsEstimate: { type: "NUMBER" } } } };
        baseSchema.properties.yieldSensitivityChart = { type: "ARRAY", items: { type: "OBJECT", properties: { yieldChangePercent: { type: "STRING" }, marginImpact: { type: "NUMBER" }, epsImpact: { type: "NUMBER" } } } };
        baseSchema.properties.irPivots = { type: "ARRAY", items: { type: "STRING" } };
      } else if (targetStakeholder === 'CIO / IT / CISO') {
        baseSchema.properties.radarData = { type: "ARRAY", items: { type: "OBJECT", properties: { vector: { type: "STRING" }, vulnerabilityScore: { type: "NUMBER" } } } };
        baseSchema.properties.cyberRisks = { type: "ARRAY", items: { type: "OBJECT", properties: { riskType: { type: "STRING" }, assessment: { type: "STRING" }, guardrails: { type: "ARRAY", items: { type: "STRING" } } } } };
      } else if (targetStakeholder === 'Board / Audit Committee') {
        baseSchema.properties.segmentData = { type: "ARRAY", items: { type: "OBJECT", properties: { segmentName: { type: "STRING" }, revenue: { type: "NUMBER" }, margin: { type: "NUMBER" } } } };
        baseSchema.properties.geoData = { type: "ARRAY", items: { type: "OBJECT", properties: { region: { type: "STRING" }, revenueShare: { type: "NUMBER" }, riskExposure: { type: "STRING" } } } };
        baseSchema.properties.strategicInitiatives = { type: "ARRAY", items: { type: "OBJECT", properties: { initiative: { type: "STRING" }, status: { type: "STRING" }, impact: { type: "STRING" } } } };
      }
      return baseSchema;
    }
    return { type: "OBJECT", properties: {} };
  };

  const showCacheIndicator = () => {
    setCacheIndicator(true);
    setTimeout(() => setCacheIndicator(false), 2000);
  };

  const handleAutoDetectIndustry = async () => {
    if (!entity) return setError("Please provide a Target Entity to detect its industry.");
    const cacheKey = entity.toLowerCase().trim();
    if (cacheStore.industries.has(cacheKey)) {
      const cachedResult = cacheStore.industries.get(cacheKey);
      setIndustryOptions([...cachedResult, 'Other / Custom']);
      setIndustry(cachedResult[0]);
      showCacheIndicator();
      return;
    }
    setDetectingIndustry(true);
    setError('');
    try {
      const prompt = `Identify top 3-5 SIC codes and industries most relevant to "${entity}". Return ONLY a JSON array of strings: "Industry Name (SIC Code)".`;
      const result = await callGeminiAPI(prompt, SYSTEM_PROMPT, { type: "ARRAY", items: { type: "STRING" } });
      if (result?.length) {
        setIndustryOptions([...result, 'Other / Custom']);
        setIndustry(result[0]);
        cacheStore.industries.set(cacheKey, result);
      } else setError("Could not detect industries. Please select manually.");
    } catch (err) { setError(err.message); } finally { setDetectingIndustry(false); }
  };

  const handleAutoPopulatePeers = async () => {
    if (!industry && !entity) return setError("Please provide an Entity or Industry.");
    const cacheKey = `${entity}-${industry}`.toLowerCase().trim();
    if (cacheStore.peers.has(cacheKey)) {
      setPeers(cacheStore.peers.get(cacheKey));
      showCacheIndicator();
      return;
    }
    setPopulatingPeers(true);
    setError('');
    try {
      const prompt = `Identify 6-8 direct competitor public companies for "${entity}" in "${industry}". Return ONLY a comma-separated list.`;
      const result = await callGeminiAPI(prompt, SYSTEM_PROMPT);
      const cleanResult = result.replace(/"/g, '').trim();
      setPeers(cleanResult);
      cacheStore.peers.set(cacheKey, cleanResult);
    } catch (err) { setError(err.message); } finally { setPopulatingPeers(false); }
  };

  const loadTab = async (tabIdToLoad) => {
    const cacheKey = `${entity}-${industry}-${peers}-${stakeholder}-${horizon}-${startQuarter}-${startYear}-${endQuarter}-${endYear}`.toLowerCase().trim();
    const cachedFullReport = cacheStore.reports.get(cacheKey) || {};
    
    if (cachedFullReport[tabIdToLoad]) {
      setReportData(prev => ({ ...prev, [tabIdToLoad]: cachedFullReport[tabIdToLoad] }));
      showCacheIndicator();
      return;
    }

    setLoadingTab(tabIdToLoad);
    setError('');
    
    // NARRATIVE ALIGNMENT
    const execContext = cachedFullReport['exec']?.executiveSummary;
    
    // BACKEND GROUNDING INJECTION
    let secContext = "";
    if (useEnterpriseGrounding) {
      let gData = cacheStore.grounding.get(entity.toLowerCase());
      if (!gData) {
        gData = await fetchEnterpriseGrounding(entity);
        if (gData) {
          cacheStore.grounding.set(entity.toLowerCase(), gData);
          setIsGrounded(true);
        }
      }
      if (gData) {
        secContext = `\nCRITICAL SEC EDGAR GROUNDING: You are now connected to the SEC EDGAR API Backend. DO NOT HALLUCINATE FINANCIALS. You MUST explicitly map the provided historical GAAP financials exactly into your output array. Use the provided 10-K Risk Factors text as the absolute ground truth for your qualitative analysis and RAG matrices.\n\nRAW XBRL FINANCIALS: ${JSON.stringify(gData.financials)}\n\n10-K ITEM 1A (RISK FACTORS): ${gData.riskFactorsText}`;
      }
    } else {
      setIsGrounded(false);
    }
    
    let externalContext = "";
    if (tabIdToLoad === 'sentiment') {
      const fredData = await fetchFredData(startYear);
      if (fredData) {
        externalContext = `\nCRITICAL REAL DATA INJECTION: You MUST use the following real historical data pulled live from the Federal Reserve Economic Data (FRED) API for the 'Effective Federal Funds Rate' (FEDFUNDS). Do NOT hallucinate historical macro values. Map these exact dates/values to the historical quarters in your array. Real FRED Data: ${JSON.stringify(fredData.slice(-10))}`;
      }
    }

    const specificPrompt = `
      Perform a highly analytical risk assessment for:
      - Target Entity: ${entity}
      - Industry: ${industry}
      - Peers: ${peers || 'Standard peers'}
      - Stakeholder: ${stakeholder}
      - Timeframe: ${startQuarter} ${startYear} to ${endQuarter} ${endYear}
      ${execContext ? `\nCRITICAL ALIGNMENT INJECTION: To prevent narrative drift across the report, you MUST strictly anchor this tab's data directly to the established core themes in this Executive Summary: "${execContext}"` : ''}
      ${secContext}

      Focus ONLY on generating the data for "${tabIdToLoad}" based on the schema. Format dates exactly like "Q1 24". Do not use repeating text.
      ${tabIdToLoad === 'exec' ? `\nCRITICAL: Include data for the Target AND EVERY PEER listed.` : ''}
      ${tabIdToLoad === 'quarterly' ? `\nCRITICAL: Generate 'financialTrend' with ALL financial KPIs (Revenue, Gross Profit, FCF, Inventory, EPS). Generate 'riskVelocity' for 4 forward quarters.` : ''}
      ${tabIdToLoad === 'sentiment' ? `\nCRITICAL: Generate 'correlationData' matching MD&A sentiment to the Effective Federal Funds Rate. Simulate highly realistic recent 'industryNewsFeed' headlines from relevant industry RSS feeds. ${externalContext}` : ''}
      ${tabIdToLoad === 'stakeholder' ? `\nCRITICAL: Generate 'ragMatrix' tracking risks across 4 forward quarters (fq1Status to fq4Status). Include a Primary Source Citation (e.g., '10-K Item 1A') and Confidence Score (1-100) for every single row. ABSOLUTE REQUIREMENT: The items in 'auditPriorities', 'cyberRisks', or 'strategicInitiatives' MUST perfectly mirror and map to the specific risks identified in the 'ragMatrix'. Do not hallucinate disconnected vulnerabilities.` : ''}
      ${tabIdToLoad === 'preMortem' ? `\nCRITICAL: The pre-mortem failure scenario MUST be the collapse of the safest/most critical assumption underlying the rest of the report.` : ''}
    `;

    try {
      const data = await callGeminiAPI(specificPrompt, SYSTEM_PROMPT, getSchemaForTab(tabIdToLoad, stakeholder));
      
      // MONTE CARLO INJECTION 
      if (tabIdToLoad === 'quarterly' && data.financialTrend) {
        let enriched = [...data.financialTrend];
        ['revenue', 'grossProfit', 'fcf', 'inventory', 'eps'].forEach(k => {
          enriched = runMonteCarlo(enriched, k, 50000);
        });
        data.financialTrend = enriched;
      }

      setReportData(prev => {
        const updated = { ...prev, [tabIdToLoad]: data };
        cacheStore.reports.set(cacheKey, updated);
        return updated;
      });
      if (tabIdToLoad === 'exec') {
        setCooldown(true);
        setTimeout(() => setCooldown(false), 8000);
      }
    } catch (err) { setError(err.message); } finally { setLoadingTab(null); }
  };

  const handleGenerateReport = async () => {
    if (!entity || !industry || !stakeholder || !horizon) return setError("Please fill in primary fields.");
    if (cooldown) return setError("System cooldown active.");
    const cacheKey = `${entity}-${industry}-${peers}-${stakeholder}-${horizon}-${startQuarter}-${startYear}-${endQuarter}-${endYear}`.toLowerCase().trim();
    setReportData({});
    setActiveTab('exec');
    setError('');
    const cachedFullReport = cacheStore.reports.get(cacheKey) || {};
    if (cachedFullReport['exec']) {
      setReportData(cachedFullReport);
      showCacheIndicator();
      return;
    }
    await loadTab('exec');
  };

  const handleTabSwitch = (newTabId) => {
    setActiveTab(newTabId);
    if (!reportData[newTabId] && loadingTab !== newTabId) loadTab(newTabId);
  };

  // ═══════════════════════ EXPORT ENGINE ══════════════════════════════
  const triggerDownload = (content, filename, type) => {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleExportCSV = () => {
    if (!reportData.quarterly || !reportData.quarterly.financialTrend) {
      setError("Please generate the 'Forward / Grey Swan' tab first to export financials.");
      return;
    }
    const data = reportData.quarterly.financialTrend;
    const headers = ["Quarter", "Is_Historical", "Revenue", "Gross_Profit", "Free_Cash_Flow", "Inventory", "EPS", "Bear_P10", "Base_P50", "Bull_P90", "Key_Driver"];
    const rows = data.map(d => [
      d.quarter, d.isHistorical ? 'TRUE' : 'FALSE', d.revenue || '', d.grossProfit || '', 
      d.fcf || '', d.inventory || '', d.eps || '', d.revenue_p10 || '', d.revenue || '', d.revenue_p90 || '', `"${d.keyDriver || ''}"`
    ]);
    const csvContent = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
    triggerDownload(csvContent, `${entity.replace(/\s+/g, '_')}_Financial_Metrics.csv`, 'text/csv;charset=utf-8;');
    setExportMenuOpen(false);
  };

  const handleExportDoc = () => {
    if (!reportData.exec) {
      setError("Please generate the 'Exec Brief' first to export.");
      return;
    }
    
    let docContent = `DENDRAI RISK INTELLIGENCE REPORT\nEntity: ${entity}\nIndustry: ${industry}\nStakeholder Focus: ${stakeholder}\nHorizon: ${horizon}\nTimeframe: ${startQuarter} ${startYear} - ${endQuarter} ${endYear}\n\n`;
    docContent += `=========================================\nEXECUTIVE SUMMARY\n=========================================\n`;
    docContent += `${reportData.exec.executiveSummary}\n\n`;

    if (reportData.sentiment) {
      docContent += `=========================================\nMACRO & SENTIMENT SYNTHESIS\n=========================================\n`;
      docContent += `${reportData.sentiment.synthesisSummary}\n\n`;
    }

    if (reportData.preMortem) {
      docContent += `=========================================\nPRE-MORTEM & FAILURE SCENARIO\n=========================================\n`;
      docContent += `Critical Green Assumption: "${reportData.preMortem.preMortem.criticalGreenAssumption}"\n\nTimeline:\n`;
      reportData.preMortem.preMortem.timeline?.forEach(t => {
        docContent += `- ${t.day}: ${t.event} -> ${t.impact}\n`;
      });
    }

    if (reportData.quarterly && reportData.quarterly.greySwan) {
      docContent += `\n=========================================\nGREY SWAN EVENT\n=========================================\n`;
      docContent += `Event: ${reportData.quarterly.greySwan.event}\nProbability: ${reportData.quarterly.greySwan.probability}\nImpact: ${reportData.quarterly.greySwan.impact}\nTrigger: ${reportData.quarterly.greySwan.trigger}\n`;
    }

    triggerDownload(docContent, `${entity.replace(/\s+/g, '_')}_Executive_Brief.txt`, 'text/plain;charset=utf-8;');
    setExportMenuOpen(false);
  };

  const handleExportPDF = () => {
    setExportMenuOpen(false);
    window.print(); 
  };

  // ═══════════════════════ RENDER VIEWS ══════════════════════════════

  const renderSharedRagMatrix = (ragMatrix, persona) => {
    if (!ragMatrix || ragMatrix.length === 0) return null;
    
    // HITL Functions inside scope
    const toVal = (status) => status?.toLowerCase() === 'red' ? 0 : status?.toLowerCase() === 'amber' ? 1 : 2;
    
    const openOverrideModal = (rowIndex, colKey, currentVal) => {
      const existing = analystOverrides[`${rowIndex}-${colKey}`];
      const newVal = window.prompt(
        `[HUMAN OVERRIDE] Enter new status for ${colKey} (Red, Amber, Green):`, 
        existing?.status || currentVal
      );
      if (!newVal || !['red','amber','green'].includes(newVal.toLowerCase())) return;
      
      const note = window.prompt(`Enter Analyst Justification for overriding AI to ${newVal.toUpperCase()}:`, existing?.note || "");
      if (!note) return;

      setAnalystOverrides(prev => ({
        ...prev,
        [`${rowIndex}-${colKey}`]: { status: newVal.toLowerCase(), note, timestamp: new Date().toLocaleDateString() }
      }));
    };

    const renderCell = (row, rowIndex, colKey) => {
      const override = analystOverrides[`${rowIndex}-${colKey}`];
      const activeStatus = override ? override.status : row[colKey];
      const activeVal = toVal(activeStatus);
      
      return (
        <div className="relative group cursor-pointer" onClick={() => openOverrideModal(rowIndex, colKey, row[colKey])}>
          <RagCell val={activeVal}/>
          {override && (
            <div className="absolute -top-2 -right-2 bg-text rounded-full p-0.5 border border-white z-10" title={`Analyst Override: ${override.note}`}>
              <Edit3 size={10} color={B.card} />
            </div>
          )}
          
          <div className="absolute hidden group-hover:block bottom-full left-1/2 transform -translate-x-1/2 mb-2 w-48 bg-card border border-borderLt rounded p-2 text-[9px] shadow-lg z-50">
            <div className="font-bold text-muted mb-1 flex items-center justify-between">
              <span>{override ? "HUMAN OVERRIDE" : "AI INFERENCE"}</span>
              <span className="text-[8px] bg-bg px-1 rounded">{row.aiConfidenceScore}% CONF</span>
            </div>
            {override ? (
              <>
                <div className="text-text font-bold mb-1">Analyst Note:</div>
                <div className="text-muted leading-tight mb-2">"{override.note}"</div>
                <div className="text-dim">Original AI: {row[colKey]?.toUpperCase()}</div>
              </>
            ) : (
              <div className="text-muted leading-tight">Click cell to override AI projection and log an analyst justification.</div>
            )}
          </div>
        </div>
      );
    };

    return (
      <Card>
        <div className="flex justify-between items-start mb-4">
          <Lbl sub={`Quarterly Operational and Compliance Risk Forecasts for ${persona}`}>ENTERPRISE RAG MATRIX (FORWARD-LOOKING)</Lbl>
          <div className="flex gap-2 text-[9px] text-muted font-bold uppercase tracking-widest no-print">
            <div className="flex items-center"><div className="w-2 h-2 rounded bg-mint mr-1"></div> AI Default</div>
            <div className="flex items-center"><Edit3 size={10} className="mr-1 text-text"/> Human Override</div>
          </div>
        </div>
        <div style={{overflowX:"auto"}}>
          <table style={{borderCollapse:"separate", borderSpacing:"3px 3px", width:"100%"}}>
            <thead><tr>
              <th style={{textAlign:"left", fontSize:10, color:B.muted, paddingRight:12, paddingBottom:4, fontWeight:600}}>CATEGORY / SOURCE</th>
              <th style={{textAlign:"left", fontSize:10, color:B.muted, paddingRight:12, paddingBottom:4, fontWeight:600}}>RISK DESCRIPTION</th>
              <th style={{textAlign:"center", fontSize:10, color:B.text, paddingBottom:4, fontWeight:800}}>FQ1</th>
              <th style={{textAlign:"center", fontSize:10, color:B.text, paddingBottom:4, fontWeight:800}}>FQ2</th>
              <th style={{textAlign:"center", fontSize:10, color:B.text, paddingBottom:4, fontWeight:800}}>FQ3</th>
              <th style={{textAlign:"center", fontSize:10, color:B.text, paddingBottom:4, fontWeight:800}}>FQ4</th>
            </tr></thead>
            <tbody>{ragMatrix.map((row, i) => (
              <tr key={i} className="hover:bg-bg/50 transition-colors">
                <td style={{paddingRight:12, paddingBottom:6, paddingTop:6}}>
                  <div style={{fontSize:11, color:B.text, fontWeight:700, marginBottom: 2}}>{row.category}</div>
                  <div className="inline-flex items-center gap-1 bg-bg border border-borderLt rounded px-1.5 py-0.5 text-[8px] font-bold text-muted uppercase">
                    <Database size={8}/> {row.primarySourceCitation || 'AI Synthesized'}
                  </div>
                </td>
                <td style={{fontSize:11, color:B.muted, paddingRight:12, paddingBottom:6, paddingTop:6}}>{row.riskDescription}</td>
                <td style={{paddingBottom:6, paddingTop:6}}><div className="flex justify-center">{renderCell(row, i, 'fq1Status')}</div></td>
                <td style={{paddingBottom:6, paddingTop:6}}><div className="flex justify-center">{renderCell(row, i, 'fq2Status')}</div></td>
                <td style={{paddingBottom:6, paddingTop:6}}><div className="flex justify-center">{renderCell(row, i, 'fq3Status')}</div></td>
                <td style={{paddingBottom:6, paddingTop:6}}><div className="flex justify-center">{renderCell(row, i, 'fq4Status')}</div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
    );
  };

  const renderExecDashboard = (data) => {
    let qoqChartData = [], qoqCompanies = [], qoqForwardStart = null, targetEntityKey = null;
    if (data.qoqData) {
      qoqForwardStart = data.qoqData.find(d => d.isHistorical === false)?.quarter;
      qoqChartData = data.qoqData.map(q => {
        const obj = { quarter: q.quarter, isHistorical: q.isHistorical };
        q.metrics?.forEach(m => {
          obj[m.company] = m.qoqPercent;
          if (q.isHistorical || q.quarter === qoqForwardStart) obj[`${m.company}_hist`] = m.qoqPercent;
          if (!q.isHistorical || q.quarter === qoqForwardStart) obj[`${m.company}_fcst`] = m.qoqPercent;
        });
        return obj;
      });
      qoqCompanies = Array.from(new Set(data.qoqData.flatMap(q => q.metrics?.map(m => m.company) || [])));
      targetEntityKey = qoqCompanies.find(c => c.toLowerCase() === entity.toLowerCase()) || qoqCompanies[0];
    }
    const lineColors = [B.mint, B.amber, B.sic, B.red, B.borderLt, "#4A90E2", "#F5A623", "#9013FE", "#50E3C2"];

    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        <Card>
          <Lbl sub={`Entity: ${entity} | Horizon: ${horizon}`}>EXECUTIVE INTELLIGENCE BRIEF</Lbl>
          <div style={{fontSize:12, color:B.muted, lineHeight:1.6}}>{data.executiveSummary}</div>
        </Card>

        {data.peerComparison && (
          <Card>
            <Lbl sub="Negative delta indicates lower systemic risk vs sector average">PEER RELATIVE RISK DELTA</Lbl>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={data.peerComparison} layout="vertical" margin={{left:0, right:30, top:4}}>
                <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
                <XAxis type="number" tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
                <YAxis type="category" dataKey="company" tick={{fill:B.text, fontSize:11, fontWeight:600}} stroke="none" width={80}/>
                <Tooltip content={<ChartTip fmt={(v)=>v.toFixed(2)}/>}/>
                <ReferenceLine x={0} stroke={B.borderLt}/>
                <Bar dataKey="riskDelta" name="Risk Delta" radius={[0,3,3,0]}>
                  {data.peerComparison.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.riskDelta > 0 ? B.red : B.mint} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        )}

        {qoqCompanies.length > 0 && (
          <Card>
            <Lbl sub="Quarter-over-Quarter % Growth vs Peers (Historical Solid | Forecast Dotted)">QoQ % GROWTH (TARGET VS PEERS)</Lbl>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={qoqChartData} margin={{left:0, right:20, top:4}}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
                <YAxis tick={{fill:B.muted,fontSize:10}} stroke="none" tickFormatter={(v)=>`${v}%`}/>
                <Tooltip content={<ChartTip fmt={(v)=>`${v}%`}/>}/>
                <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                {qoqCompanies.map((comp, idx) => (
                  <React.Fragment key={comp}>
                    {/* Render historical portion (Solid) */}
                    <Line 
                      type="monotone" dataKey={`${comp}_hist`} name={comp} 
                      stroke={comp.toLowerCase() === entity.toLowerCase() ? B.text : lineColors[idx % lineColors.length]} 
                      strokeWidth={comp.toLowerCase() === entity.toLowerCase() ? 3 : 1.5} 
                      dot={{r: 3, fill: B.bg}} activeDot={{r: 5}} connectNulls
                    />
                    {/* Render forecast portion (Dashed) - Visual Lineage */}
                    <Line 
                      type="monotone" dataKey={`${comp}_fcst`} name={`${comp} (Forecast)`} 
                      stroke={comp.toLowerCase() === entity.toLowerCase() ? B.text : lineColors[idx % lineColors.length]} 
                      strokeWidth={comp.toLowerCase() === entity.toLowerCase() ? 3 : 1.5} 
                      strokeDasharray="5 5"
                      dot={false} activeDot={{r: 5}} legendType="none" connectNulls
                    />
                  </React.Fragment>
                ))}
                {qoqForwardStart && <ReferenceLine x={qoqForwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'AI Forecast', fill: B.amber, fontSize: 10 }} />}
              </LineChart>
            </ResponsiveContainer>
            
            {targetEntityKey && (
              <StatRibbon 
                data={qoqChartData.filter(d => d.isHistorical)} 
                dataKey={targetEntityKey} 
                label={targetEntityKey} 
                format={(v) => `${v?.toFixed(2)}%`} 
              />
            )}
          </Card>
        )}
      </div>
    );
  };

  const renderAuditDashboard = (data) => {
    if (!data.financialScores) return null;
    const params = data.financialScores.mScoreParams || {};
    const paramVisuals = [
      { label: "DSRI", value: params.DSRI, threshold: 1.031, desc: "Receivables Index" },
      { label: "GMI", value: params.GMI, threshold: 1.014, desc: "Gross Margin Index" },
      { label: "AQI", value: params.AQI, threshold: 1.039, desc: "Asset Quality Index" },
      { label: "TATA", value: params.TATA, threshold: 0.1, desc: "Total Accruals" }
    ];

    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card style={{borderLeft:`4px solid ${B.mint}`}}>
            <Lbl sub={data.financialScores.interpretation}>FINANCIAL FORESIGHT SCORES</Lbl>
            <div className="flex justify-around items-center mt-6">
              <div className="text-center">
                <div style={{fontSize:32, fontWeight:800, color: data.financialScores.altmanZ < 1.8 ? B.red : B.text}}>{data.financialScores.altmanZ?.toFixed(2)}</div>
                <div style={{fontSize:10, color:B.muted, fontWeight:700, letterSpacing:"0.1em", marginTop:4}}>ALTMAN Z-SCORE</div>
              </div>
              <div className="text-center">
                <div style={{fontSize:32, fontWeight:800, color: data.financialScores.beneishM > -2.22 ? B.red : B.text}}>{data.financialScores.beneishM?.toFixed(2)}</div>
                <div style={{fontSize:10, color:B.muted, fontWeight:700, letterSpacing:"0.1em", marginTop:4}}>BENEISH M-SCORE</div>
              </div>
            </div>
          </Card>
          <Card>
            <div className="flex justify-between items-start">
              <Lbl sub="Key metric flags against standard thresholds">M-SCORE FORENSICS</Lbl>
              <button onClick={() => setMethodologyDrawerOpen(true)} className="no-print text-muted hover:text-text transition-colors"><Info size={16} /></button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {paramVisuals.map((p, i) => {
                const isFlagged = p.value > p.threshold;
                return (
                  <div key={i} style={{background: isFlagged ? B.redBg : B.greenBg, border:`1px solid ${isFlagged ? B.red : B.mint}44`, borderRadius:6, padding:"10px 12px"}}>
                    <div className="flex justify-between items-center mb-1"><span style={{fontSize:10, fontWeight:700, color:B.muted, letterSpacing:"0.1em"}}>{p.label}</span></div>
                    <div style={{fontSize:18, fontWeight:800, color: isFlagged ? B.red : B.mint}}>{p.value?.toFixed(3) || 'N/A'}</div>
                    <div style={{fontSize:9, color:B.muted, marginTop:2}}>{p.desc}</div>
                  </div>
                )
              })}
            </div>
          </Card>
        </div>

        {renderSharedRagMatrix(data.ragMatrix, 'Audit / ERM')}
        <AuditPriorityHeatmap priorities={data.auditPriorities} />

        {data.auditVulnerabilities && (
          <Card>
            <Lbl sub="Identified control and tracking gaps" color={B.red}>AUDIT VULNERABILITIES</Lbl>
            <div style={{display:"flex", flexDirection:"column", gap:8}}>
              {data.auditVulnerabilities.map((vuln, i) => (
                <div key={i} style={{display:"flex", alignItems:"flex-start", padding:"10px 12px", background:B.redBg, borderRadius:5, border:`1px solid ${B.red}33`}}>
                  <AlertTriangle size={14} color={B.red} style={{marginTop:2, marginRight:8, flexShrink:0}} />
                  <span style={{fontSize:11, color:B.text}}>{vuln}</span>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    );
  };

  const renderCFODashboard = (data) => {
    if (!data.scenarios) return null;
    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        {renderSharedRagMatrix(data.ragMatrix, 'CFO / Finance')}
        
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <Lbl sub="Bear, Base, and Bull scenario outputs">REVENUE & EPS SCENARIOS</Lbl>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={data.scenarios} margin={{left:-10, right:10, top:4}}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                <XAxis dataKey="scenarioName" tick={{fill:B.text,fontSize:10,fontWeight:600}} stroke={B.border}/>
                <YAxis yAxisId="left" orientation="left" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                <YAxis yAxisId="right" orientation="right" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                <Tooltip content={<ChartTip />}/>
                <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                <Bar yAxisId="left" dataKey="revenueEstimate" name="Rev (M)" fill={B.text} radius={[3,3,0,0]} />
                <Bar yAxisId="right" dataKey="epsEstimate" name="EPS" fill={B.mint} radius={[3,3,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
          {data.yieldSensitivityChart && (
            <Card>
              <Lbl sub="Impact on Margins and EPS given yield fluctuations">YIELD SENSITIVITY CURVE</Lbl>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={data.yieldSensitivityChart} margin={{left:-10, right:10, top:4}}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                  <XAxis dataKey="yieldChangePercent" tick={{fill:B.text,fontSize:10,fontWeight:600}} stroke={B.border}/>
                  <YAxis yAxisId="left" orientation="left" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                  <YAxis yAxisId="right" orientation="right" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                  <Tooltip content={<ChartTip />}/>
                  <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                  <Line yAxisId="left" type="monotone" dataKey="marginImpact" name="Margin (bps)" stroke={B.text} strokeWidth={2.5} dot={{r: 3, fill:B.bg}} activeDot={{r: 5}} />
                  <Line yAxisId="right" type="monotone" dataKey="epsImpact" name="EPS Impact ($)" stroke={B.red} strokeWidth={2.5} dot={{r: 3, fill:B.bg}} activeDot={{r: 5}} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
          )}
        </div>
      </div>
    );
  };

  const renderCIODashboard = (data) => {
    if (!data.cyberRisks) return null;
    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        {renderSharedRagMatrix(data.ragMatrix, 'CIO / IT / CISO')}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          {data.radarData && (
            <Card className="lg:col-span-1 flex flex-col items-center justify-center">
              <Lbl color={B.red} sub="Multi-vector vulnerability surface">THREAT EXPOSURE RADAR</Lbl>
              <ResponsiveContainer width="100%" height={260}>
                <RadarChart cx="50%" cy="50%" outerRadius="70%" data={data.radarData}>
                  <PolarGrid stroke={B.borderLt} />
                  <PolarAngleAxis dataKey="vector" tick={{fill: B.text, fontSize: 10, fontWeight: 600}} />
                  <PolarRadiusAxis angle={30} domain={[0, 10]} tick={{fontSize: 9, fill: B.muted}} axisLine={false} />
                  <Radar name="Vulnerability" dataKey="vulnerabilityScore" stroke={B.red} strokeWidth={2} fill={B.red} fillOpacity={0.15} />
                  <Tooltip content={<ChartTip />}/>
                </RadarChart>
              </ResponsiveContainer>
            </Card>
          )}
          <div className="lg:col-span-2 space-y-4">
            <Lbl sub="Identified IP and Systems Threats">SYSTEMIC CYBER THREATS</Lbl>
            {data.cyberRisks.map((risk, i) => (
              <Card key={i} style={{padding:"14px", borderLeft:`3px solid ${B.text}`}}>
                <div style={{fontSize:12, fontWeight:800, color:B.text, textTransform:"uppercase", marginBottom:4}}>{risk.riskType}</div>
                <div style={{fontSize:11, color:B.muted, marginBottom:10}}>{risk.assessment}</div>
                <div style={{background:B.greenBg, padding:"10px", borderRadius:5, border:`1px solid ${B.mint}44`}}>
                  <div style={{fontSize:9, fontWeight:800, color:B.text, textTransform:"uppercase", marginBottom:6, display:"flex", alignItems:"center"}}>
                    <ShieldAlert size={12} className="mr-1" color={B.mint} /> Guardrails
                  </div>
                  <ul className="list-disc pl-4 space-y-1" style={{fontSize:11, color:B.text}}>
                    {risk.guardrails?.map((guard, j) => <li key={j}>{guard}</li>)}
                  </ul>
                </div>
              </Card>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderBoardDashboard = (data) => {
    if (!data.segmentData) return null;
    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        {renderSharedRagMatrix(data.ragMatrix, 'Board / Audit Committee')}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <Lbl sub="Revenue (Bar) and Operating Margin (Line) by Business Segment">SEGMENT PERFORMANCE</Lbl>
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={data.segmentData} margin={{left:-10, right:10, top:4}}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                <XAxis dataKey="segmentName" tick={{fill:B.text,fontSize:10,fontWeight:600}} stroke={B.border}/>
                <YAxis yAxisId="left" orientation="left" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                <YAxis yAxisId="right" orientation="right" tick={{fill:B.muted,fontSize:10}} stroke="none" tickFormatter={(v)=>`${v}%`}/>
                <Tooltip content={<ChartTip />}/>
                <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                <Bar yAxisId="left" dataKey="revenue" name="Revenue (M)" fill={B.text} radius={[3,3,0,0]} />
                <Line yAxisId="right" type="monotone" dataKey="margin" name="Margin (%)" stroke={B.mint} strokeWidth={2.5} dot={{r: 4, fill:B.bg}} />
              </ComposedChart>
            </ResponsiveContainer>
          </Card>
          <Card>
            <Lbl sub="Revenue concentration and geopolitical risk exposure">GEOGRAPHIC EXPOSURE</Lbl>
            <ResponsiveContainer width="100%" height={235}>
              <BarChart data={data.geoData} layout="vertical" margin={{left:0, right:30, top:4}}>
                <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
                <XAxis type="number" tick={{fill:B.muted, fontSize:10}} stroke={B.border} tickFormatter={(v)=>`${v}%`}/>
                <YAxis type="category" dataKey="region" tick={{fill:B.text, fontSize:11, fontWeight:600}} stroke="none" width={80}/>
                <Tooltip content={<ChartTip fmt={(v)=>`${v}%`}/>}/>
                <Bar dataKey="revenueShare" name="Revenue Share %" radius={[0,3,3,0]}>
                  {data.geoData?.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.riskExposure?.toLowerCase() === 'high' ? B.red : entry.riskExposure?.toLowerCase() === 'medium' ? B.amber : B.mint} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>
      </div>
    );
  };

  const ForwardGreySwanView = ({ data }) => {
    const [activeKpiTab, setActiveKpiTab] = useState('revenue');
    if (!data.financialTrend) return null;

    const processedData = data.financialTrend.map((d, i, arr) => {
      const isLastHist = d.isHistorical && arr[i+1] && !arr[i+1].isHistorical;
      const isFcst = !d.isHistorical || isLastHist;
      
      const obj = { ...d };
      ['revenue', 'grossProfit', 'fcf', 'inventory', 'eps'].forEach(k => {
          obj[`${k}_hist`] = (d.isHistorical || isLastHist) ? d[k] : null;
          obj[`${k}_fcst`] = isFcst ? d[k] : null;
      });

      const p10Key = `${activeKpiTab}_p10`;
      const p90Key = `${activeKpiTab}_p90`;
      
      const p50Val = d[activeKpiTab];
      const p10Val = d[p10Key] ?? d[activeKpiTab];
      const p90Val = d[p90Key] ?? d[activeKpiTab];

      obj.range = isFcst ? [p10Val, p90Val] : null;
      obj.p50_fcst = isFcst ? p50Val : null;
      obj.p10_fcst = isFcst ? p10Val : null;
      obj.p90_fcst = isFcst ? p90Val : null;
      
      return obj;
    });
    const forwardStart = processedData.find(d => !d.isHistorical)?.quarter;

    const kpiTabs = [
      { id: 'revenue', label: 'Revenue (P10/50/90)' },
      { id: 'grossProfit', label: 'Gross Profit' },
      { id: 'fcf', label: 'Free Cash Flow' },
      { id: 'inventory', label: 'Inventory' },
      { id: 'eps', label: 'EPS' }
    ];

    const renderSecondaryKpiChart = (dataKey, name, color) => (
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={processedData} margin={{left:-10, right:20, top:4}}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
          <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border} />
          <YAxis tick={{fill:B.muted,fontSize:10}} stroke="none"/>
          <Tooltip content={<ChartTip />}/>
          <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
          
          <Line type="monotone" dataKey={`${dataKey}_hist`} name={`${name} (Historical)`} stroke={color} strokeWidth={2.5} dot={{r: 4, fill: B.bg}} activeDot={{r: 6}} connectNulls />
          <Line type="monotone" dataKey={`${dataKey}_fcst`} name={`${name} (Forecast)`} stroke={color} strokeWidth={2.5} strokeDasharray="5 5" dot={false} activeDot={{r: 6}} connectNulls />
          
          {forwardStart && <ReferenceLine x={forwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'Forecast', fill: B.amber, fontSize: 10 }} />}
        </ComposedChart>
      </ResponsiveContainer>
    );

    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        <Card>
          <div className="flex justify-between items-start mb-4 flex-wrap gap-4">
            <div>
              <Lbl sub="Historical & Forward Projections across primary financial KPIs">QUANTITATIVE KPI TRACKER</Lbl>
              <div className="flex items-center gap-2 mt-1 px-2 py-1 bg-bg2 border border-borderLt rounded text-[9px] text-text font-bold tracking-widest uppercase w-max">
                <Zap size={10} className="text-amber" /> 50,000 Iteration Monte Carlo Simulation Active
              </div>
            </div>
            <div className="no-print" style={{display:"flex", gap:4, background:B.bg2, padding:4, borderRadius:6}}>
              {kpiTabs.map(t => (
                <button 
                  key={t.id} onClick={() => setActiveKpiTab(t.id)}
                  style={{
                    background: activeKpiTab === t.id ? B.card : 'transparent',
                    border: `1px solid ${activeKpiTab === t.id ? B.borderLt : 'transparent'}`,
                    padding: "4px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                    color: activeKpiTab === t.id ? B.text : B.muted, transition: "all 0.2s"
                  }}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          <div style={{marginTop: 16}}>
            {activeKpiTab === 'revenue' && (
              <ResponsiveContainer width="100%" height={320}>
                <ComposedChart data={processedData} margin={{left:-10, right:20, top:4}}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                  <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border} />
                  <YAxis tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                  <Tooltip content={<ChartTip />}/>
                  <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                  
                  <Line type="monotone" dataKey={`${activeKpiTab}_hist`} name="Historical" stroke={B.text} strokeWidth={2.5} dot={{r: 4, fill: B.text}} connectNulls />
                  
                  {/* Forecast Lines */}
                  <Area type="monotone" dataKey="range" name="P10-P90 MC Range" stroke="none" fill={B.mint} fillOpacity={0.15} connectNulls />
                  <Line type="monotone" dataKey="p90_fcst" name="MC Bull (P90)" stroke={B.mint} strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls />
                  <Line type="monotone" dataKey="p50_fcst" name="AI Base (P50)" stroke={B.amber} strokeWidth={2.5} strokeDasharray="5 5" dot={{r: 4, fill: B.bg}} connectNulls />
                  <Line type="monotone" dataKey="p10_fcst" name="MC Bear (P10)" stroke={B.red} strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls />
                  
                  {forwardStart && <ReferenceLine x={forwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'Forecast', fill: B.amber, fontSize: 10 }} />}
                </ComposedChart>
              </ResponsiveContainer>
            )}
            {activeKpiTab === 'grossProfit' && renderSecondaryKpiChart('grossProfit', 'Gross Profit', B.mint)}
            {activeKpiTab === 'fcf' && renderSecondaryKpiChart('fcf', 'Free Cash Flow', B.text)}
            {activeKpiTab === 'inventory' && renderSecondaryKpiChart('inventory', 'Inventory Levels', B.amber)}
            {activeKpiTab === 'eps' && renderSecondaryKpiChart('eps', 'Earnings Per Share', B.sic)}
          </div>
          
          <StatRibbon 
            data={data.financialTrend.filter(d => d.isHistorical)} 
            dataKey={activeKpiTab} 
            label={kpiTabs.find(t => t.id === activeKpiTab)?.label} 
          />
          
          {activeKpiTab === 'revenue' && (
            <div style={{display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:8, marginTop:16}}>
              {data.financialTrend.filter(q => !q.isHistorical).slice(0, 4).map((q, i) => (
                <div key={i} style={{background:B.bg2, border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"8px 12px"}}>
                  <div style={{fontSize:9, color:B.muted, textTransform:"uppercase", marginBottom:4, fontWeight:700}}>{q.quarter} DRIVER</div>
                  <div style={{fontSize:10, color:B.text, fontWeight:600}}>{q.keyDriver || '—'}</div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {data.operationalTrend && data.operationalMetadata && (
          <Card>
            <Lbl sub={`Metrics dynamically selected for the ${stakeholder} persona`}>PERSONA-SPECIFIC OPERATIONAL KPIs</Lbl>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart 
                data={data.operationalTrend.map((d, i, arr) => {
                  const isLastHist = d.isHistorical && arr[i+1] && !arr[i+1].isHistorical;
                  const isFcst = !d.isHistorical || isLastHist;
                  return {
                    ...d,
                    kpi1_hist: (d.isHistorical || isLastHist) ? d.kpi1Value : null,
                    kpi1_fcst: isFcst ? d.kpi1Value : null,
                    kpi2_hist: (d.isHistorical || isLastHist) ? d.kpi2Value : null,
                    kpi2_fcst: isFcst ? d.kpi2Value : null,
                  };
                })} 
                margin={{left:-10, right:20, top:4}}
              >
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border} />
                <YAxis yAxisId="left" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                <YAxis yAxisId="right" orientation="right" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                <Tooltip content={<ChartTip />}/>
                <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                
                <Line yAxisId="left" type="monotone" dataKey="kpi1_hist" name={`${data.operationalMetadata.kpi1Label} (Hist)`} stroke={B.text} strokeWidth={2.5} dot={{r: 4, fill: B.bg}} activeDot={{r: 6}} connectNulls />
                <Line yAxisId="left" type="monotone" dataKey="kpi1_fcst" name={`${data.operationalMetadata.kpi1Label} (FCST)`} stroke={B.text} strokeWidth={2.5} strokeDasharray="5 5" dot={false} activeDot={{r: 6}} legendType="none" connectNulls />
                
                <Line yAxisId="right" type="monotone" dataKey="kpi2_hist" name={`${data.operationalMetadata.kpi2Label} (Hist)`} stroke={B.amber} strokeWidth={2.5} dot={{r: 4, fill: B.bg}} activeDot={{r: 6}} connectNulls />
                <Line yAxisId="right" type="monotone" dataKey="kpi2_fcst" name={`${data.operationalMetadata.kpi2Label} (FCST)`} stroke={B.amber} strokeWidth={2.5} strokeDasharray="5 5" dot={false} activeDot={{r: 6}} legendType="none" connectNulls />
                
                {forwardStart && <ReferenceLine yAxisId="left" x={forwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'Forecast', fill: B.amber, fontSize: 10 }} />}
              </LineChart>
            </ResponsiveContainer>
          </Card>
        )}

        {data.riskVelocity && (
          <Card>
            <Lbl sub="Forward-looking speed of risk materialization and compounding impacts">QUARTERLY RISK VELOCITY</Lbl>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
              {data.riskVelocity.map((rv, i) => {
                const TrendIcon = rv.trend.toLowerCase() === 'increasing' ? TrendingUp : (rv.trend.toLowerCase() === 'decreasing' ? TrendingDown : Minus);
                const trendColor = rv.trend.toLowerCase() === 'increasing' ? B.red : (rv.trend.toLowerCase() === 'decreasing' ? B.mint : B.amber);
                const trendBg = rv.trend.toLowerCase() === 'increasing' ? B.redBg : (rv.trend.toLowerCase() === 'decreasing' ? B.greenBg : B.amberBg);

                return (
                  <div key={i} style={{background: B.bg2, border: `1px solid ${B.borderLt}`, borderRadius: 6, padding: "14px"}}>
                    <div className="flex justify-between items-center mb-4">
                      <div style={{fontSize: 12, fontWeight: 800, color: B.text}}>{rv.quarter}</div>
                      <div style={{background: trendBg, color: trendColor, border: `1px solid ${trendColor}44`, padding: "4px 8px", borderRadius: 4, display: "flex", alignItems: "center"}}>
                        <TrendIcon size={14} style={{marginRight: 6}} />
                        <span style={{fontSize: 11, fontWeight: 800}}>{rv.velocityScore}/10</span>
                      </div>
                    </div>
                    <div style={{fontSize: 9, color: B.muted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4, fontWeight: 700}}>Primary Catalyst</div>
                    <div style={{fontSize: 11, color: B.text, lineHeight: 1.5, fontWeight: 600}}>{rv.primaryDriver}</div>
                  </div>
                )
              })}
            </div>
          </Card>
        )}

        {data.greySwan && (
          <Card style={{background:B.text, border:`1px solid ${B.text}`, position:"relative", overflow:"hidden"}}>
            <div style={{position:"absolute", top:-50, right:-50, opacity:0.03}}><Activity size={200} color={B.card} /></div>
            <Lbl color={B.amber}>GREY SWAN EVENT ANALYSIS</Lbl>
            <div style={{fontSize:24, fontWeight:800, color:B.card, marginBottom:16, position:"relative", zIndex:10}}>{data.greySwan.event}</div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 relative z-10">
              <div style={{background:"rgba(255,255,255,0.08)", padding:14, borderRadius:6, border:"1px solid rgba(255,255,255,0.1)"}}>
                <div style={{fontSize:9, color:B.mintAccent, textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:4}}>Probability</div>
                <div style={{fontSize:14, fontWeight:700, color:B.card}}>{data.greySwan.probability}</div>
              </div>
              <div style={{background:"rgba(255,255,255,0.08)", padding:14, borderRadius:6, border:"1px solid rgba(255,255,255,0.1)"}}>
                <div style={{fontSize:9, color:B.mintAccent, textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:4}}>Impact Radius</div>
                <div style={{fontSize:14, fontWeight:700, color:B.card}}>{data.greySwan.impact}</div>
              </div>
              <div style={{background:"rgba(255,255,255,0.08)", padding:14, borderRadius:6, border:"1px solid rgba(255,255,255,0.1)"}}>
                <div style={{fontSize:9, color:B.mintAccent, textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:4}}>Catalyst / Trigger</div>
                <div style={{fontSize:12, color:B.card, lineHeight:1.3}}>{data.greySwan.trigger}</div>
              </div>
            </div>
          </Card>
        )}
      </div>
    );
  };

  const renderSentimentDashboard = (data) => {
    if (!data.correlationData) return null;
    const forwardStart = data.correlationData.find(d => !d.isHistorical)?.quarter;

    const sentData = data.correlationData.map((d, i, arr) => {
        const isLastHist = d.isHistorical && arr[i+1] && !arr[i+1].isHistorical;
        const isFcst = !d.isHistorical || isLastHist;
        return {
            ...d,
            mda_hist: (d.isHistorical || isLastHist) ? d.mdaSentimentScore : null,
            mda_fcst: isFcst ? d.mdaSentimentScore : null,
        };
    });

    return (
      <div className="space-y-6 animate-fadeIn" style={{animation: "fadeIn 0.5s ease-in-out"}}>
        <Card style={{borderLeft:`4px solid ${B.mint}`}}>
          <Lbl icon={MessageSquare} sub="Synthesis of MD&A tone and simulated industry news against macro trends">EXECUTIVE SENTIMENT SUMMARY</Lbl>
          <div style={{fontSize: 12, lineHeight: 1.6, color: B.text}}>{data.synthesisSummary}</div>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <Card>
              <Lbl icon={Globe} sub="Correlation between Management Tone and Macro Indicators">MD&A SENTIMENT VS. MACRO FORECAST</Lbl>
              <ResponsiveContainer width="100%" height={320}>
                <ComposedChart data={sentData} margin={{left:-10, right:20, top:4}}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                  <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border} />
                  <YAxis yAxisId="left" tick={{fill:B.muted,fontSize:10}} stroke="none" domain={[-100, 100]}/>
                  <YAxis yAxisId="right" orientation="right" tick={{fill:B.muted,fontSize:10}} stroke="none"/>
                  <Tooltip content={<ChartTip />}/>
                  <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                  
                  <Bar yAxisId="right" dataKey="macroValue" name={data.macroIndicatorName} fill={B.dim} radius={[3,3,0,0]} opacity={0.6}/>
                  
                  <Line yAxisId="left" type="monotone" dataKey="mda_hist" name="MD&A Tone (Historical)" stroke={B.text} strokeWidth={3} dot={{r: 4, fill: B.bg}} activeDot={{r: 6}} connectNulls />
                  <Line yAxisId="left" type="monotone" dataKey="mda_fcst" name="MD&A Tone (FCST)" stroke={B.text} strokeWidth={3} strokeDasharray="5 5" dot={false} activeDot={{r: 6}} legendType="none" connectNulls />
                  
                  {forwardStart && <ReferenceLine yAxisId="left" x={forwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'Forecast', fill: B.amber, fontSize: 10 }} />}
                </ComposedChart>
              </ResponsiveContainer>
              
              <StatRibbon 
                data={data.correlationData.filter(d => d.isHistorical)} 
                dataKey="mdaSentimentScore" 
                label="Historical MD&A Sentiment" 
              />
            </Card>

            <Card>
              <Lbl sub="Thematic shifts in Management Discussion & Analysis over time">QUARTERLY MD&A THEMES</Lbl>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                {data.correlationData.slice(-4).map((q, i) => (
                  <div key={i} style={{background:B.bg2, border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"10px"}}>
                    <div style={{fontSize:9, color:B.muted, textTransform:"uppercase", marginBottom:4, fontWeight:700}}>{q.quarter} THEME {q.isHistorical ? "" : "(E)"}</div>
                    <div style={{fontSize:11, color:B.text, fontWeight:600, lineHeight:1.4}}>{q.mdaTheme}</div>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div className="lg:col-span-1">
            <Card style={{height: "100%", background: B.card}}>
              <Lbl icon={Rss} sub="AI Synthesized RSS Feed relevant to entity context">INDUSTRY RSS SIGNAL</Lbl>
              <div className="space-y-4 mt-4 h-full overflow-y-auto pr-2" style={{maxHeight: "500px"}}>
                {data.industryNewsFeed?.map((item, i) => {
                  const s = item.sentiment?.toLowerCase();
                  const c = s === 'positive' ? B.mint : (s === 'negative' ? B.red : B.amber);
                  const bg = s === 'positive' ? B.greenBg : (s === 'negative' ? B.redBg : B.amberBg);
                  return (
                    <div key={i} style={{borderBottom: `1px solid ${B.borderLt}`, paddingBottom: 12}}>
                      <div className="flex justify-between items-start mb-2">
                        <span style={{fontSize: 9, color: B.muted, fontWeight: 700, textTransform: "uppercase"}}>{item.date} {item.publication ? `• ${item.publication}` : ''}</span>
                        <span style={{fontSize: 9, background: bg, color: c, padding: "2px 6px", borderRadius: 3, fontWeight: 800, border: `1px solid ${c}55`}}>{item.sentiment}</span>
                      </div>
                      <div style={{fontSize: 12, fontWeight: 700, color: B.text, lineHeight: 1.4, marginBottom: 6}}>{item.headline}</div>
                      <div style={{display: "inline-block", fontSize: 9, color: B.muted, background: B.bg, padding: "2px 8px", borderRadius: 10, border: `1px solid ${B.borderLt}`}}>🏷️ {item.macroTag}</div>
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>
        </div>
      </div>
    );
  };

  const renderPreMortem = (data) => {
    if (!data.preMortem) return null;
    return (
      <div style={{display:"flex", flexDirection:"column", gap:16, animation:"fadeIn 0.3s ease-in-out"}}>
        <Card style={{borderLeft:`4px solid ${B.mint}`}}>
          <Lbl sub="The load-bearing assumption" color={B.text}>CRITICAL GREEN ASSUMPTION</Lbl>
          <div style={{padding:"14px 18px", background:B.greenBg, borderRadius:5, fontSize:13, color:B.text, fontStyle:"italic", border:`1px solid ${B.mint}44`}}>
            "{data.preMortem.criticalGreenAssumption}"
          </div>
        </Card>
        
        <Card style={{borderLeft:`4px solid ${B.red}`}}>
          <Lbl sub="Chronological Failure Scenario over the next 90 days" color={B.red}>FAILURE SCENARIO TIMELINE</Lbl>
          <div className="space-y-4 relative mt-6">
            <div style={{position:"absolute", left:15, top:10, bottom:10, width:2, background:B.redBg, zIndex:0}}></div>
            
            {data.preMortem.timeline?.map((point, i) => (
              <div key={i} className="flex relative z-10">
                <div style={{width:32, height:32, borderRadius:16, background:B.redBg, border:`2px solid ${B.red}`, display:"flex", alignItems:"center", justifyContent:"center", flexShrink:0, marginRight:16}}>
                  <Clock size={14} color={B.red} />
                </div>
                <div style={{flex:1, background:B.bg, padding:14, borderRadius:6, border:`1px solid ${B.borderLt}`}}>
                  <div style={{fontSize:10, color:B.red, fontWeight:800, textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:4}}>{point.day}</div>
                  <div style={{fontSize:13, fontWeight:700, color:B.text, marginBottom:6}}>{point.event}</div>
                  <div style={{fontSize:11, color:B.muted, lineHeight:1.5, display:"flex", alignItems:"flex-start"}}>
                    <ArrowRight size={12} style={{marginTop:3, marginRight:6, color:B.dim, flexShrink:0}} />
                    {point.impact}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    );
  };

  const APP_TABS = [
    {label:"Exec Brief", id:'exec'},
    {label:`${stakeholder}`, id:'stakeholder'},
    {label:"Forward / Grey Swan", id:'quarterly'},
    {label:"Sentiment & Macro", id:'sentiment'},
    {label:"Pre-Mortem", id:'preMortem'},
  ];

  return (
    <div style={{background:B.bg, minHeight:"100vh", fontFamily:"'IBM Plex Mono','Courier New',monospace", color:B.text}}>
      <style>{`
        @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
        .animate-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }
        @keyframes fadeInOut {
          0% { opacity: 0; transform: translateY(-10px); }
          15% { opacity: 1; transform: translateY(0); }
          85% { opacity: 1; transform: translateY(0); }
          100% { opacity: 0; transform: translateY(-10px); }
        }
        @media print {
          body { background: white !important; color: black !important; }
          .no-print { display: none !important; }
          .print-full-width { width: 100% !important; max-width: 100% !important; grid-column: span 12 !important; padding: 0 !important; }
          .print-break-inside-avoid { break-inside: avoid; margin-bottom: 20px; border: 1px solid #ccc !important; box-shadow: none !important; }
          * { -webkit-print-color-adjust: exact !important; color-adjust: exact !important; }
        }
      `}</style>

      {/* METHODOLOGY DRAWER MODAL */}
      {methodologyDrawerOpen && (
        <div className="fixed inset-0 bg-black/50 z-[9999] flex justify-end">
          <div className="bg-card w-full max-w-md h-full border-l border-border p-6 shadow-2xl overflow-y-auto transform transition-transform animate-fadeIn">
            <div className="flex justify-between items-center mb-6 pb-4 border-b border-borderLt">
              <div className="font-bold text-text tracking-widest text-sm">METHODOLOGY & PROVENANCE</div>
              <button onClick={() => setMethodologyDrawerOpen(false)} className="text-muted hover:text-red"><X size={20}/></button>
            </div>
            
            <div className="space-y-6 text-sm text-text">
              <div>
                <h4 className="font-bold text-text mb-2 flex items-center"><Server size={16} className="mr-2"/> SEC EDGAR Data Grounding</h4>
                <p className="text-muted text-xs leading-relaxed mb-2">When "Enterprise SEC Backend" is active, historical financial arrays and qualitative Risk Factors (Item 1A) are extracted directly from authenticated SEC XBRL API endpoints via the Python FastAPI microservice layer, ensuring 0% hallucination on historical metrics.</p>
              </div>

              <div>
                <h4 className="font-bold text-amber mb-2 flex items-center"><Zap size={16} className="mr-2"/> Monte Carlo Engine (GBM)</h4>
                <p className="text-muted text-xs leading-relaxed mb-2">The financial P10/P90 forecast curves are generated via a 50,000-iteration Monte Carlo simulation using Geometric Brownian Motion (GBM). This math engine bypasses the AI completely, utilizing the entity's historical quarter-over-quarter volatility to calculate true probability distributions.</p>
              </div>

              <div>
                <h4 className="font-bold text-mint mb-2 flex items-center"><Search size={16} className="mr-2"/> Beneish M-Score</h4>
                <p className="text-muted text-xs leading-relaxed mb-2">A mathematical model created by Messod Beneish to detect financial statement manipulation. A score greater than -2.22 signals a high probability of earnings manipulation.</p>
              </div>

              <div>
                <h4 className="font-bold text-amber mb-2 flex items-center"><Activity size={16} className="mr-2"/> Altman Z-Score</h4>
                <p className="text-muted text-xs leading-relaxed">A formula to predict the probability that a firm will go into bankruptcy within two years. A score below 1.8 indicates severe financial distress, while above 3.0 indicates a safe zone.</p>
              </div>
            </div>
          </div>
        </div>
      )}
      
      <header className="no-print" style={{background:B.card, borderBottom:`1px solid ${B.border}`, padding:"14px 24px", display:"flex", justifyContent:"space-between", alignItems:"center", position: "relative"}}>
        <div className="flex items-center space-x-3">
          <div className="relative w-10 h-10 flex items-center justify-center bg-gray-100 rounded-md border overflow-hidden" style={{borderColor: B.border}}>
            <Cpu className="absolute z-0 opacity-20" size={24} color={B.text} />
          </div>
          <div>
            <div style={{color:B.mint,fontSize:9,letterSpacing:"0.26em",textTransform:"uppercase",marginBottom:2, fontWeight:800}}>▸ DENDRAI QUANT_ENGINE</div>
            <div style={{fontSize:16,fontWeight:800,color:B.text}}>Risk & Intelligence Synthesizer</div>
          </div>
        </div>
        
        <div className="flex items-center space-x-4">
          {/* Provenance Badge */}
          {isGrounded ? (
             <div className="hidden md:flex items-center space-x-1" style={{fontSize:9, color:B.mint, background:B.greenBg, padding:"4px 8px", borderRadius:4, fontWeight:700, border:`1px solid ${B.mint}44`}}>
               <CheckCircle2 size={12} /> <span>SEC EDGAR GROUNDED</span>
             </div>
          ) : (
             <div className="hidden md:flex items-center space-x-1" style={{fontSize:9, color:B.amber, background:B.amberBg, padding:"4px 8px", borderRadius:4, fontWeight:700, border:`1px solid ${B.amber}44`}}>
               <Activity size={12} /> <span>AI SIMULATED</span>
             </div>
          )}

          <div className="hidden md:flex items-center space-x-2 mr-4" style={{fontSize:10, color:B.muted}}>
            <Database size={14} /> <span>ONLINE</span>
          </div>

          <div style={{position: "relative"}}>
            <button 
              onClick={() => setExportMenuOpen(!exportMenuOpen)}
              style={{display: "flex", alignItems: "center", gap: 6, background: B.text, color: B.card, fontSize: 11, fontWeight: 700, padding: "8px 14px", borderRadius: 5, transition: "all 0.2s"}}
            >
              <Download size={14} /> EXPORT REPORT <ChevronDown size={14} />
            </button>
            
            {exportMenuOpen && (
              <div style={{position: "absolute", top: "110%", right: 0, width: 240, background: B.card, border: `1px solid ${B.border}`, borderRadius: 6, boxShadow: "0 10px 25px rgba(0,0,0,0.1)", zIndex: 100, overflow: "hidden"}}>
                <button onClick={handleExportDoc} style={{width: "100%", display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", fontSize: 11, fontWeight: 600, color: B.text, textAlign: "left", borderBottom: `1px solid ${B.bg}`, background: "transparent", cursor: "pointer"}} onMouseOver={(e)=>e.target.style.background=B.bg} onMouseOut={(e)=>e.target.style.background="transparent"}>
                  <FileText size={16} color={B.text} /> Export for Google Docs (.txt)
                </button>
                <button onClick={handleExportCSV} style={{width: "100%", display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", fontSize: 11, fontWeight: 600, color: B.text, textAlign: "left", borderBottom: `1px solid ${B.bg}`, background: "transparent", cursor: "pointer"}} onMouseOver={(e)=>e.target.style.background=B.bg} onMouseOut={(e)=>e.target.style.background="transparent"}>
                  <FileSpreadsheet size={16} color={B.mint} /> Export for Google Sheets (.csv)
                </button>
                <button onClick={handleExportPDF} style={{width: "100%", display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", fontSize: 11, fontWeight: 600, color: B.text, textAlign: "left", background: "transparent", cursor: "pointer"}} onMouseOver={(e)=>e.target.style.background=B.bg} onMouseOut={(e)=>e.target.style.background="transparent"}>
                  <Printer size={16} color={B.amber} /> Print / Save as PDF
                </button>
              </div>
            )}
          </div>
        </div>
        
        {cacheIndicator && (
          <div style={{position: "absolute", right: 24, top: 56, background: B.text, color: B.mintAccent, padding: "6px 12px", borderRadius: 4, fontSize: 10, fontWeight: 700, display: "flex", alignItems: "center", boxShadow: "0 4px 12px rgba(0,0,0,0.15)", animation: "fadeInOut 2s ease-in-out forwards", zIndex: 50}}>
            <CheckCircle2 size={12} className="mr-2" /> LOADED FROM CACHE
          </div>
        )}
      </header>

      <main className="max-w-7xl mx-auto p-4 md:p-6 grid grid-cols-1 lg:grid-cols-12 gap-6 print-full-width">
        <div className="lg:col-span-4 space-y-6 no-print">
          <Card>
            <Lbl icon={Crosshair}>TARGETING PARAMETERS</Lbl>
            <div className="space-y-4 mt-4">
              <div>
                <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase", marginBottom:4}}>1. Target Entity</label>
                <input 
                  type="text" value={entity} onChange={(e) => setEntity(e.target.value)}
                  placeholder="e.g., AAPL, NVDA, Startup X"
                  style={{width:"100%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                />
              </div>
              <div>
                <div className="flex justify-between items-end mb-1">
                  <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase"}}>Industry / SIC</label>
                  <button 
                    onClick={handleAutoDetectIndustry} disabled={detectingIndustry || !entity}
                    style={{fontSize:9, background:B.greenBg, color:B.mint, border:`1px solid ${B.mint}66`, borderRadius:3, padding:"2px 8px", cursor:(!entity || detectingIndustry) ? "default" : "pointer", opacity: (!entity || detectingIndustry) ? 0.5 : 1}}
                  >
                    {detectingIndustry ? 'Detecting...' : 'Auto-Detect (AI)'}
                  </button>
                </div>
                <select 
                  value={industry} onChange={(e) => setIndustry(e.target.value)}
                  style={{width:"100%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                >
                  <option value="" disabled>Select or Detect Industry...</option>
                  {industryOptions.map((opt, idx) => <option key={idx} value={opt}>{opt}</option>)}
                </select>
              </div>
              <div>
                <div className="flex justify-between items-end mb-1">
                  <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase"}}>Peer Companies</label>
                  <button 
                    onClick={handleAutoPopulatePeers} disabled={populatingPeers}
                    style={{fontSize:9, background:B.greenBg, color:B.mint, border:`1px solid ${B.mint}66`, borderRadius:3, padding:"2px 8px", cursor:populatingPeers ? "default" : "pointer", opacity: populatingPeers ? 0.5 : 1}}
                  >
                    {populatingPeers ? 'Fetching...' : 'Auto-Populate (AI)'}
                  </button>
                </div>
                <textarea 
                  value={peers} onChange={(e) => setPeers(e.target.value)} rows="2" placeholder="Comma separated list..."
                  style={{width:"100%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                />
              </div>
              <div>
                <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase", marginBottom:4}}>2. Target Stakeholder</label>
                <select 
                  value={stakeholder} onChange={(e) => setStakeholder(e.target.value)}
                  style={{width:"100%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                >
                  <option value="Audit / ERM">Audit / ERM</option>
                  <option value="CFO / Finance">CFO / Finance</option>
                  <option value="CIO / IT / CISO">CIO / IT / CISO</option>
                  <option value="Board / Audit Committee">Board / Audit Committee</option>
                </select>
              </div>
              <div>
                <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase", marginBottom:4}}>3. Analysis Horizon</label>
                <select 
                  value={horizon} onChange={(e) => setHorizon(e.target.value)}
                  style={{width:"100%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text, marginBottom: 12}}
                >
                  <option value="1-Quarter Forward">1-Quarter Forward (Tactical)</option>
                  <option value="4-Quarter Forward">4-Quarter Forward (Operational)</option>
                  <option value="3-Year Strategic">3-Year Strategic (Long-term)</option>
                </select>
                <div className="grid grid-cols-2 gap-3 mt-2">
                  <div>
                    <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase", marginBottom:4}}>Start</label>
                    <div className="flex gap-2">
                      <select 
                        value={startQuarter} onChange={(e) => setStartQuarter(e.target.value)}
                        style={{width:"50%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                      >
                        {['Q1','Q2','Q3','Q4'].map(q => <option key={q} value={q}>{q}</option>)}
                      </select>
                      <select 
                        value={startYear} onChange={(e) => setStartYear(e.target.value)}
                        style={{width:"50%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                      >
                        {[2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030].map(y => <option key={y} value={y}>{y}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase", marginBottom:4}}>End</label>
                    <div className="flex gap-2">
                      <select 
                        value={endQuarter} onChange={(e) => setEndQuarter(e.target.value)}
                        style={{width:"50%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                      >
                        {['Q1','Q2','Q3','Q4'].map(q => <option key={q} value={q}>{q}</option>)}
                      </select>
                      <select 
                        value={endYear} onChange={(e) => setEndYear(e.target.value)}
                        style={{width:"50%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                      >
                        {[2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030].map(y => <option key={y} value={y}>{y}</option>)}
                      </select>
                    </div>
                  </div>
                </div>
              </div>

              {/* ARCHITECTURE TOGGLE */}
              <div style={{marginTop: 16, background: B.bg2, padding: "10px", borderRadius: 6, border: `1px solid ${B.borderLt}`}}>
                <label className="flex items-center space-x-2 cursor-pointer">
                  <input 
                    type="checkbox" 
                    checked={useEnterpriseGrounding} 
                    onChange={(e) => setUseEnterpriseGrounding(e.target.checked)}
                    className="accent-mint"
                  />
                  <span style={{fontSize: 11, fontWeight: 700, color: B.text}}>Enable Enterprise SEC Grounding</span>
                </label>
                <div style={{fontSize: 9, color: B.muted, marginTop: 4, lineHeight: 1.4}}>
                  If checked, system intercepts via FastAPI proxy to inject true SEC EDGAR XBRL financials and 10-K text to prevent hallucination.
                </div>
              </div>

              {error && (
                <div style={{background:B.redBg, color:B.red, fontSize:11, padding:10, borderRadius:4, border:`1px solid ${B.red}44`}}>
                  {error}
                </div>
              )}

              <button 
                onClick={handleGenerateReport} disabled={loadingTab === 'exec' || cooldown}
                style={{width:"100%", background: cooldown ? B.dim : B.text, color: cooldown ? B.text : B.card, fontWeight:700, fontSize:12, padding:"12px", borderRadius:4, marginTop:10, cursor:(loadingTab === 'exec' || cooldown) ? "default" : "pointer", opacity:(loadingTab === 'exec' || cooldown) ? 0.7 : 1, display:"flex", justifyContent:"center", alignItems:"center", transition:"all 0.2s"}}
              >
                {loadingTab === 'exec' ? <span className="flex items-center"><Activity className="animate-spin mr-2" size={16} /> SYNTHESIZING...</span> : cooldown ? 'SYSTEM COOLDOWN ACTIVE...' : 'GENERATE INTELLIGENCE'}
              </button>
            </div>
          </Card>
        </div>

        <div className="lg:col-span-8 print-full-width">
          {Object.keys(reportData).length === 0 && loadingTab !== 'exec' && (
            <div className="no-print" style={{height:"100%", display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", color:B.muted, background:"rgba(255,255,255,0.4)", border:`1px dashed ${B.borderLt}`, borderRadius:8, minHeight:400}}>
              <Activity size={48} style={{color:B.dim, marginBottom:16}} />
              <p style={{fontSize:11, textTransform:"uppercase", letterSpacing:"0.1em", fontWeight:700}}>Awaiting Synthesis Directives</p>
            </div>
          )}

          {Object.keys(reportData).length === 0 && loadingTab === 'exec' && (
            <Card style={{height:"100%", display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", minHeight:400}} className="animate-pulse no-print">
              <div className="w-16 h-16 border-4 rounded-full animate-spin mb-6" style={{borderColor: B.bg2, borderTopColor: B.mint}}></div>
              <p style={{fontSize:12, fontWeight:800, color:B.text, textTransform:"uppercase", letterSpacing:"0.1em"}}>Processing Data Streams...</p>
              <p style={{fontSize:10, color:B.muted, marginTop:8}}>Aligning to {stakeholder} protocols</p>
            </Card>
          )}

          {Object.keys(reportData).length > 0 && (
            <div className="space-y-6">
              <div className="no-print" style={{display:"flex", gap:4, padding:"10px 16px", background:B.card, border:`1px solid ${B.border}`, borderRadius:8, overflowX:"auto"}}>
                {APP_TABS.map((t, i) => (
                  <button key={i} onClick={()=>handleTabSwitch(t.id)} style={{
                    background: activeTab===t.id ? B.greenBg : "transparent", 
                    border:`1px solid ${activeTab===t.id ? B.mint : "transparent"}`, 
                    color: activeTab===t.id ? B.text : B.muted, 
                    borderRadius:4, padding:"8px 16px", cursor:"pointer", fontSize:10, fontWeight:700, whiteSpace:"nowrap", transition: "all 0.2s"
                  }}>
                    {t.label.toUpperCase()}
                  </button>
                ))}
              </div>

              {loadingTab && loadingTab === activeTab && activeTab !== 'exec' && (
                <Card style={{display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", minHeight:300}} className="animate-pulse no-print">
                  <div className="w-12 h-12 border-4 rounded-full animate-spin mb-4" style={{borderColor: B.bg2, borderTopColor: B.mint}}></div>
                  <p style={{fontSize:12, fontWeight:800, color:B.text, textTransform:"uppercase", letterSpacing:"0.1em"}}>Generating {APP_TABS.find(t=>t.id === activeTab)?.label} Data...</p>
                  <p style={{fontSize:10, color:B.muted, marginTop:8}}>Optimizing API tokens with lazy loading</p>
                </Card>
              )}

              {!loadingTab && activeTab === 'exec' && reportData.exec && renderExecDashboard(reportData.exec)}
              {!loadingTab && activeTab === 'stakeholder' && reportData.stakeholder && (
                <>
                  {stakeholder === 'Audit / ERM' && renderAuditDashboard(reportData.stakeholder)}
                  {stakeholder === 'CFO / Finance' && renderCFODashboard(reportData.stakeholder)}
                  {stakeholder === 'CIO / IT / CISO' && renderCIODashboard(reportData.stakeholder)}
                  {stakeholder === 'Board / Audit Committee' && renderBoardDashboard(reportData.stakeholder)}
                </>
              )}
              {!loadingTab && activeTab === 'quarterly' && reportData.quarterly && <ForwardGreySwanView data={reportData.quarterly} />}
              {!loadingTab && activeTab === 'sentiment' && reportData.sentiment && renderSentimentDashboard(reportData.sentiment)}
              {!loadingTab && activeTab === 'preMortem' && reportData.preMortem && renderPreMortem(reportData.preMortem)}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}