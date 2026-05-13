import React, { useState } from 'react';
import { 
  Activity, 
  ShieldAlert, 
  Briefcase, 
  Cpu, 
  Search, 
  AlertTriangle, 
  TrendingDown, 
  TrendingUp, 
  Minus,
  Crosshair,
  Lock,
  Database,
  BarChart2
} from 'lucide-react';
import { 
  BarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  Legend, 
  ResponsiveContainer,
  ReferenceLine,
  ComposedChart,
  LineChart,
  Line,
  Area,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Cell
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

// ═══════════════════════ UI COMPONENTS ══════════════════════════════
const Card = ({children, className="", style={}}) => (
  <div className={className} style={{background:B.card, border:`1px solid ${B.border}`, borderRadius:8, padding:18, boxShadow:"0 1px 2px rgba(26,31,29,0.04)", ...style}}>
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
    <div style={{background:B.card, border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"8px 12px", fontSize:11, boxShadow:"0 4px 12px rgba(26,31,29,0.10)"}}>
      <div style={{color:B.text, marginBottom:5, fontWeight:700}}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{color:p.color||B.text, marginBottom:2}}>
          {p.name}: {fmt && p.value != null ? fmt(p.value) : p.value ?? "—"}
        </div>
      ))}
    </div>
  );
};

const RagCell = ({val}) => (
  <div style={{background:RBG[val], borderRadius:3, width:42, height:22, display:"flex", alignItems:"center", justifyContent:"center", fontSize:10, color:RC[val], fontWeight:800, letterSpacing:"0.06em", border:`1px solid ${RC[val]}55`}}>
    {RL[val]}
  </div>
);

const AuditPriorityHeatmap = ({ priorities }) => {
  const [hov, setHov] = useState(null);
  if (!priorities || priorities.length === 0) return null;

  const W=720, H=300;
  const toX = (d) => 60 + ((Math.max(1, Math.min(10, d))-1)/9)*(W-100);
  const toY = (d) => H - 20 - ((Math.max(1, Math.min(10, d))-1)/9)*(H-60);

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
            
            {priorities.map((a, i) => {
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
                <div style={{color:B.red, fontWeight: 600}}>● IMMEDIATE: {priorities.filter((a)=>a.urg===0).length} items</div>
                <div style={{color:B.amber, fontWeight: 600}}>● ELEVATED: {priorities.filter((a)=>a.urg===1).length} items</div>
                <div style={{color:B.mint, fontWeight: 600}}>● ROUTINE: {priorities.filter((a)=>a.urg===2).length} items</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
};

// Gemini API Configuration
const callGeminiAPI = async (prompt, systemInstruction, schema = null) => {
const apiKey = import.meta.env.VITE_GEMINI_API_KEY;
  const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.0-flash:generateContent?key=${apiKey}`;
  const payload = {
    contents: [{ parts: [{ text: prompt }] }],
    systemInstruction: { parts: [{ text: systemInstruction }] }
  };

  if (schema) {
    payload.generationConfig = {
      responseMimeType: "application/json",
      responseSchema: schema
    };
  }

  const response = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) throw new Error(`API Error: ${response.status}`);

  const result = await response.json();
  const textResponse = result.candidates?.[0]?.content?.parts?.[0]?.text || '';

  const parseJsonResponse = (text) => {
    try {
      return JSON.parse(text);
    } catch (err) {
      const match = text.match(/\[.*\]/s);
      if (match) return JSON.parse(match[0]);
      throw err;
    }
  };

  if (schema) return parseJsonResponse(textResponse.trim());
  return textResponse;
};

export default function DendraiRiskApp() {
  // Form State
  const [entity, setEntity] = useState('');
  const [industry, setIndustry] = useState('');
  const [peers, setPeers] = useState('');
  const [stakeholder, setStakeholder] = useState('Audit / ERM');
  const [horizon, setHorizon] = useState('4-Quarter Forward');
  const [startQuarter, setStartQuarter] = useState('Q1');
  const [startYear, setStartYear] = useState('2024');
  const [endQuarter, setEndQuarter] = useState('Q4');
  const [endYear, setEndYear] = useState('2027');
  
  // App State
  const [loading, setLoading] = useState(false);
  const [populatingPeers, setPopulatingPeers] = useState(false);
  const [detectingIndustry, setDetectingIndustry] = useState(false);
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
  const [reportData, setReportData] = useState(null);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('exec');

  const SYSTEM_PROMPT = `
# MISSION
You are the Dendrai Risk & Intelligence Synthesizer. Your role is to act as a Senior Enterprise Risk Lead and Financial Quantitative Analyst. You translate complex financial, operational, and semiconductor market data into precise, board-ready insights.

# BRAND & TONE GUARDRAILS
1. Tone: Clinical, authoritative, hyper-focused, and strategic. Avoid filler words and corporate fluff.
2. Structure: Prioritize high-density information.

# THE PRE-MORTEM PROTOCOL (MANDATORY)
Identify the single most critical "Green" (safe) assumption made in your analysis. Generate a realistic scenario outlining exactly what would have to fail, break, or shift in the macro-environment over the next 90 days for that "Green" rating to violently flip to "Red".
  `;

  const getSchema = (targetStakeholder) => {
    const baseSchema = {
      type: "OBJECT",
      properties: {
        executiveSummary: { type: "STRING" },
        peerComparison: {
          type: "ARRAY",
          description: "Relative risk delta compared to 4-5 peer companies",
          items: {
            type: "OBJECT",
            properties: {
              company: { type: "STRING" },
              riskDelta: { type: "NUMBER", description: "Negative means lower risk than average, positive means higher" }
            }
          }
        },
        qoqData: {
          type: "ARRAY",
          description: "8 quarters of historical AND 4 quarters of forecast Quarter-over-Quarter (QoQ) percentage growth for the target entity AND its peers.",
          items: {
            type: "OBJECT",
            properties: {
              quarter: { type: "STRING", description: "e.g., Q1 23, Q2 23... Q4 24" },
              isHistorical: { type: "BOOLEAN", description: "True if historical, false if forecast" },
              metrics: {
                type: "ARRAY",
                items: {
                  type: "OBJECT",
                  properties: {
                    company: { type: "STRING" },
                    qoqPercent: { type: "NUMBER" }
                  }
                }
              }
            }
          }
        },
        revenueTrend: {
          type: "ARRAY",
          description: "8 quarters of historical revenue, followed by 4 quarters of forward projections (p10, p50, p90)",
          items: {
            type: "OBJECT",
            properties: {
              quarter: { type: "STRING", description: "e.g., Q1 23, Q2 23... Q4 24" },
              isHistorical: { type: "BOOLEAN" },
              revenue: { type: "NUMBER", description: "Actual revenue (historical only)" },
              p10: { type: "NUMBER", description: "Bear projection (forward only)" },
              p50: { type: "NUMBER", description: "Base projection (forward only)" },
              p90: { type: "NUMBER", description: "Bull projection (forward only)" },
              keyDriver: { type: "STRING", description: "Key driver (forward only)" }
            }
          }
        },
        greySwan: {
          type: "OBJECT",
          properties: {
            event: { type: "STRING", description: "Name of the Grey Swan event" },
            probability: { type: "STRING" },
            impact: { type: "STRING" },
            trigger: { type: "STRING" }
          }
        },
        preMortem: {
          type: "OBJECT",
          properties: {
            criticalGreenAssumption: { type: "STRING" },
            failureScenario: { type: "STRING" }
          }
        }
      },
      required: ["executiveSummary", "revenueTrend", "greySwan", "preMortem", "qoqData"]
    };

    if (targetStakeholder === 'Audit / ERM') {
      baseSchema.properties.financialScores = {
        type: "OBJECT",
        properties: {
          beneishM: { type: "NUMBER" },
          altmanZ: { type: "NUMBER" },
          mScoreParams: {
            type: "OBJECT",
            properties: {
              DSRI: { type: "NUMBER" }, GMI: { type: "NUMBER" },
              AQI: { type: "NUMBER" }, TATA: { type: "NUMBER" }
            }
          },
          interpretation: { type: "STRING" }
        }
      };
      baseSchema.properties.ragMatrix = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            category: { type: "STRING" },
            riskDescription: { type: "STRING" },
            status: { type: "STRING", description: "Red, Amber, or Green" }
          }
        }
      };
      baseSchema.properties.auditPriorities = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            ref: { type: "STRING", description: "Short ID like A01" },
            title: { type: "STRING" },
            impact: { type: "NUMBER", description: "1-10 scale" },
            detect: { type: "NUMBER", description: "1-10 scale (1=hard, 10=easy)" },
            urg: { type: "NUMBER", description: "0=Immediate, 1=Elevated, 2=Routine" },
            domain: { type: "STRING" }
          }
        }
      };
      baseSchema.properties.auditVulnerabilities = {
        type: "ARRAY",
        items: { type: "STRING" }
      };
    } else if (targetStakeholder === 'CFO / Finance') {
      baseSchema.properties.scenarios = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            scenarioName: { type: "STRING", description: "Bear (P10), Base (P50), Bull (P90)" },
            revenueEstimate: { type: "NUMBER" },
            epsEstimate: { type: "NUMBER" }
          }
        }
      };
      baseSchema.properties.yieldSensitivityChart = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            yieldChangePercent: { type: "STRING", description: "e.g., -10%, -5%, 0%, +5%, +10%" },
            marginImpact: { type: "NUMBER" },
            epsImpact: { type: "NUMBER" }
          }
        }
      };
      baseSchema.properties.irPivots = {
        type: "ARRAY",
        items: { type: "STRING" }
      };
    } else if (targetStakeholder === 'CIO / IT / CISO') {
      baseSchema.properties.radarData = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            vector: { type: "STRING" },
            vulnerabilityScore: { type: "NUMBER" }
          }
        }
      };
      baseSchema.properties.cyberRisks = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            riskType: { type: "STRING" },
            assessment: { type: "STRING" },
            guardrails: { type: "ARRAY", items: { type: "STRING" } }
          }
        }
      };
    } else if (targetStakeholder === 'Board / Audit Committee') {
      baseSchema.properties.segmentData = {
        type: "ARRAY",
        description: "Revenue and margin breakdown by business segment",
        items: {
          type: "OBJECT",
          properties: {
            segmentName: { type: "STRING" },
            revenue: { type: "NUMBER", description: "Revenue in millions" },
            growth: { type: "NUMBER", description: "YoY growth percentage" },
            margin: { type: "NUMBER", description: "Operating margin percentage" }
          }
        }
      };
      baseSchema.properties.geoData = {
        type: "ARRAY",
        description: "Geographic revenue concentration and risk",
        items: {
          type: "OBJECT",
          properties: {
            region: { type: "STRING" },
            revenueShare: { type: "NUMBER", description: "Percentage of total revenue" },
            riskExposure: { type: "STRING", description: "High, Medium, or Low" }
          }
        }
      };
      baseSchema.properties.strategicInitiatives = {
        type: "ARRAY",
        items: {
          type: "OBJECT",
          properties: {
            initiative: { type: "STRING" },
            status: { type: "STRING", description: "On Track, At Risk, or Delayed" },
            impact: { type: "STRING", description: "Strategic impact or consequence" }
          }
        }
      };
    }
    return baseSchema;
  };

  const handleAutoDetectIndustry = async () => {
    if (!entity) return setError("Please provide a Target Entity to detect its industry.");
    setDetectingIndustry(true);
    setError('');
    try {
      const prompt = `Identify the top 3 to 5 Standard Industrial Classification (SIC) codes and industry names most relevant to the entity "${entity}". Return ONLY a JSON array of strings in the format "Industry Name (SIC Code)".`;
      const result = await callGeminiAPI(prompt, SYSTEM_PROMPT, { type: "ARRAY", items: { type: "STRING" } });
      const industries = Array.isArray(result) ? result.filter((item) => typeof item === 'string' && item.trim()) : [];
      if (industries.length) {
        setIndustryOptions([...industries, 'Other / Custom']);
        setIndustry(industries[0]);
      } else {
        setError("Could not detect industries. Please select manually.");
      }
    } catch (err) {
      console.error('Auto-detect industry error:', err);
      setError("Failed to auto-detect industry.");
    } finally { setDetectingIndustry(false); }
  };

  const handleAutoPopulatePeers = async () => {
    if (!industry && !entity) return setError("Please provide an Entity or Industry to auto-populate peers.");
    setPopulatingPeers(true);
    setError('');
    try {
      const prompt = `Identify 4-5 direct publicly traded competitor peer companies for an entity named "${entity}" operating in the "${industry}" industry/SIC. Return ONLY a comma-separated list of their names. No conversational text.`;
      const result = await callGeminiAPI(prompt, SYSTEM_PROMPT);
      setPeers(result.replace(/"/g, '').trim());
    } catch (err) {
      console.error('Auto-populate peers error:', err);
      setError("Failed to auto-populate peers. Please enter manually.");
    } finally { setPopulatingPeers(false); }
  };

  const handleGenerateReport = async () => {
    if (!entity || !industry || !stakeholder || !horizon) return setError("Please fill in all primary fields.");
    setLoading(true);
    setError('');
    setReportData(null);
    setActiveTab('exec');

    const userPrompt = `
      Perform a highly analytical risk assessment for the following parameters:
      - Target Entity: ${entity}
      - Industry / SIC: ${industry}
      - Peer Group: ${peers || 'Standard industry peers'}
      - Target Stakeholder: ${stakeholder}
      - Analysis Horizon: ${horizon}
      - Timeframe: ${startQuarter} ${startYear} to ${endQuarter} ${endYear}
      Adhere STRICTLY to the stakeholder routing logic and output requirements defined in your system prompt.
    `;

    try {
      const data = await callGeminiAPI(userPrompt, SYSTEM_PROMPT, getSchema(stakeholder));
      setReportData(data);
    } catch (err) {
      console.error('Generate report error:', err);
      setError("Synthesis failed. Ensure API quota is available or try adjusting parameters.");
    } finally { setLoading(false); }
  };

  // ═══════════════════════ RENDER VIEWS ══════════════════════════════
  const renderExecDashboard = (data) => {
    let qoqChartData = [];
    let qoqCompanies = [];
    let qoqForwardStart = null;
    
    if (data.qoqData) {
      qoqChartData = data.qoqData.map((q) => {
        const obj = { quarter: q.quarter, isHistorical: q.isHistorical };
        q.metrics.forEach((m) => obj[m.company] = m.qoqPercent);
        return obj;
      });
      qoqCompanies = Array.from(new Set(data.qoqData.flatMap((q) => q.metrics.map((m) => m.company))));
      qoqForwardStart = qoqChartData.find((d) => d.isHistorical === false)?.quarter || null;
    }
    const lineColors = [B.mint, B.amber, B.sic, B.red, B.borderLt];

    return (
      <div className="space-y-6 animate-fadeIn">
        <Card>
          <Lbl sub={`Entity: ${entity} | Horizon: ${horizon}`}>EXECUTIVE INTELLIGENCE BRIEF</Lbl>
          <div style={{fontSize:12, color:B.muted, lineHeight:1.6}}>
            {data.executiveSummary}
          </div>
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
            <Lbl sub="Quarter-over-Quarter % Growth vs Peers (Historical & Forecast)">QoQ % GROWTH (TARGET VS PEERS)</Lbl>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={qoqChartData} margin={{left:0, right:20, top:4}}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
                <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
                <YAxis tick={{fill:B.muted,fontSize:10}} stroke="none" tickFormatter={(v)=>`${v}%`}/>
                <Tooltip content={<ChartTip fmt={(v)=>`${v}%`}/>}/>
                <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
                {qoqCompanies.map((comp, idx) => (
                  <Line 
                    key={comp} 
                    type="monotone" 
                    dataKey={comp} 
                    name={comp} 
                    stroke={comp.toLowerCase() === entity.toLowerCase() ? B.text : lineColors[idx % lineColors.length]} 
                    strokeWidth={comp.toLowerCase() === entity.toLowerCase() ? 3 : 1.5} 
                    strokeDasharray={comp.toLowerCase() === entity.toLowerCase() ? "" : "5 5"}
                    dot={{r: 3, fill: B.bg}} 
                    activeDot={{r: 5}} 
                  />
                ))}
                {qoqForwardStart && <ReferenceLine x={qoqForwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'Forecast', fill: B.amber, fontSize: 10 }} />}
              </LineChart>
            </ResponsiveContainer>
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
      <div className="space-y-6 animate-fadeIn">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card style={{borderLeft:`4px solid ${B.mint}`}}>
            <Lbl sub={data.financialScores.interpretation}>FINANCIAL FORESIGHT SCORES</Lbl>
            <div className="flex justify-around items-center mt-6">
              <div className="text-center">
                <div style={{fontSize:32, fontWeight:800, color: data.financialScores.altmanZ < 1.8 ? B.red : B.text}}>
                  {data.financialScores.altmanZ?.toFixed(2)}
                </div>
                <div style={{fontSize:10, color:B.muted, fontWeight:700, letterSpacing:"0.1em", marginTop:4}}>ALTMAN Z-SCORE</div>
              </div>
              <div className="text-center">
                <div style={{fontSize:32, fontWeight:800, color: data.financialScores.beneishM > -2.22 ? B.red : B.text}}>
                  {data.financialScores.beneishM?.toFixed(2)}
                </div>
                <div style={{fontSize:10, color:B.muted, fontWeight:700, letterSpacing:"0.1em", marginTop:4}}>BENEISH M-SCORE</div>
              </div>
            </div>
          </Card>

          <Card>
            <Lbl sub="Key metric flags against standard thresholds">M-SCORE FORENSICS</Lbl>
            <div className="grid grid-cols-2 gap-3">
              {paramVisuals.map((p, i) => {
                const isFlagged = p.value > p.threshold;
                return (
                  <div key={i} style={{background: isFlagged ? B.redBg : B.greenBg, border:`1px solid ${isFlagged ? B.red : B.mint}44`, borderRadius:6, padding:"10px 12px"}}>
                    <div className="flex justify-between items-center mb-1">
                      <span style={{fontSize:10, fontWeight:700, color:B.muted, letterSpacing:"0.1em"}}>{p.label}</span>
                    </div>
                    <div style={{fontSize:18, fontWeight:800, color: isFlagged ? B.red : B.mint}}>{p.value?.toFixed(3) || 'N/A'}</div>
                    <div style={{fontSize:9, color:B.muted, marginTop:2}}>{p.desc}</div>
                  </div>
                )
              })}
            </div>
          </Card>
        </div>

        <Card>
          <Lbl sub="Operational, Financial, and Compliance Risk Status">ENTERPRISE RAG MATRIX</Lbl>
          <div style={{overflowX:"auto"}}>
            <table style={{borderCollapse:"separate", borderSpacing:"3px 3px", width:"100%"}}>
              <thead><tr>
                <th style={{textAlign:"left", fontSize:10, color:B.muted, paddingRight:12, paddingBottom:4, fontWeight:600}}>CATEGORY</th>
                <th style={{textAlign:"left", fontSize:10, color:B.muted, paddingRight:12, paddingBottom:4, fontWeight:600}}>RISK DESCRIPTION</th>
                <th style={{textAlign:"center", fontSize:10, color:B.muted, paddingBottom:4, fontWeight:600, width:60}}>STATUS</th>
              </tr></thead>
              <tbody>{data.ragMatrix?.map((row, i) => {
                const sIdx = row.status.toLowerCase()==='red'?0 : row.status.toLowerCase()==='amber'?1 : 2;
                return (
                <tr key={i}>
                  <td style={{fontSize:11, color:B.text, paddingRight:12, paddingBottom:3, fontWeight:700}}>{row.category}</td>
                  <td style={{fontSize:11, color:B.muted, paddingRight:12, paddingBottom:3}}>{row.riskDescription}</td>
                  <td style={{paddingBottom:3, display:"flex", justifyContent:"center"}}><RagCell val={sIdx}/></td>
                </tr>
              )})}</tbody>
            </table>
          </div>
        </Card>
        
        <AuditPriorityHeatmap priorities={data.auditPriorities} />

        <Card>
          <Lbl sub="Identified control and tracking gaps" color={B.red}>AUDIT VULNERABILITIES</Lbl>
          <div style={{display:"flex", flexDirection:"column", gap:8}}>
            {data.auditVulnerabilities?.map((vuln, i) => (
              <div key={i} style={{display:"flex", alignItems:"flex-start", padding:"10px 12px", background:B.redBg, borderRadius:5, border:`1px solid ${B.red}33`}}>
                <AlertTriangle size={14} color={B.red} style={{marginTop:2, marginRight:8, flexShrink:0}} />
                <span style={{fontSize:11, color:B.text}}>{vuln}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    );
  };

  const renderCFODashboard = (data) => {
    if (!data.scenarios) return null;
    return (
      <div className="space-y-6 animate-fadeIn">
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

        <Card style={{background:B.text, border:`1px solid ${B.text}`}}>
          <Lbl color={B.mint}>STRATEGIC IR NARRATIVE PIVOTS</Lbl>
          <ul className="list-decimal pl-5 space-y-3" style={{fontSize:12, color:B.card}}>
            {data.irPivots?.map((pivot, i) => (
              <li key={i} className="pl-2 leading-relaxed">{pivot}</li>
            ))}
          </ul>
        </Card>
      </div>
    );
  };

  const renderCIODashboard = (data) => {
    if (!data.cyberRisks) return null;
    return (
      <div className="space-y-6 animate-fadeIn">
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
      <div className="space-y-6 animate-fadeIn">
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
            <div className="flex justify-end gap-3 mt-2 text-[10px] text-gray-600">
               <span className="flex items-center"><div className="w-2 h-2 rounded-full mr-1" style={{background:B.red}}></div> High Risk</span>
               <span className="flex items-center"><div className="w-2 h-2 rounded-full mr-1" style={{background:B.amber}}></div> Med Risk</span>
               <span className="flex items-center"><div className="w-2 h-2 rounded-full mr-1" style={{background:B.mint}}></div> Low Risk</span>
            </div>
          </Card>
        </div>

        <Card>
          <Lbl sub="Board-level tracking of strategic pivots and transformations">STRATEGIC INITIATIVES</Lbl>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {data.strategicInitiatives?.map((init, i) => {
              const isRisk = init.status.toLowerCase().includes('risk') || init.status.toLowerCase().includes('delayed');
              const c = isRisk ? B.red : (init.status.toLowerCase().includes('track') ? B.mint : B.amber);
              return (
                <div key={i} style={{padding:"14px", background: B.bg2, borderTop:`3px solid ${c}`, borderRadius:"0 0 6px 6px"}}>
                  <div style={{fontSize:10, color:c, textTransform:"uppercase", fontWeight:800, marginBottom:4}}>{init.status}</div>
                  <div style={{fontSize:12, fontWeight:700, color:B.text, marginBottom:6}}>{init.initiative}</div>
                  <div style={{fontSize:11, color:B.muted, lineHeight:1.4}}>{init.impact}</div>
                </div>
              )
            })}
          </div>
        </Card>
      </div>
    );
  };

  const renderQuarterlyAndGreySwan = (data) => {
    if (!data.revenueTrend || !data.greySwan) return null;

    const processedData = data.revenueTrend.map((d, i, arr) => {
      const isLastHist = d.isHistorical && arr[i+1] && !arr[i+1].isHistorical;
      return {
        ...d,
        range: (!d.isHistorical && d.p10 != null && d.p90 != null) ? [d.p10, d.p90] : (isLastHist ? [d.revenue, d.revenue] : null),
        p50: isLastHist ? d.revenue : d.p50,
        p10: isLastHist ? d.revenue : d.p10,
        p90: isLastHist ? d.revenue : d.p90
      };
    });
    const forwardStart = processedData.find((d) => !d.isHistorical)?.quarter;

    return (
      <div className="space-y-6 animate-fadeIn">
        <Card>
          <Lbl sub="Historical Revenue & Bear/Base/Bull Forward Projections">REVENUE PROJECTIONS (P10/P50/P90)</Lbl>
          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={processedData} margin={{left:-10, right:20, top:4}}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={B.dim}/>
              <XAxis dataKey="quarter" tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
              <YAxis tick={{fill:B.muted,fontSize:10}} stroke="none"/>
              <Tooltip content={<ChartTip />}/>
              <Legend wrapperStyle={{fontSize:10, color:B.muted}}/>
              <Area type="monotone" dataKey="range" name="P10-P90 Range" stroke="none" fill={B.mint} fillOpacity={0.15} connectNulls />
              <Line type="monotone" dataKey="revenue" name="Historical" stroke={B.text} strokeWidth={2.5} dot={{r: 4, fill: B.text}} connectNulls />
              <Line type="monotone" dataKey="p90" name="Bull (P90)" stroke={B.mint} strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls />
              <Line type="monotone" dataKey="p50" name="Base (P50)" stroke={B.amber} strokeWidth={2.5} dot={{r: 4, fill: B.bg}} connectNulls />
              <Line type="monotone" dataKey="p10" name="Bear (P10)" stroke={B.red} strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls />
              {forwardStart && <ReferenceLine x={forwardStart} stroke={B.amber} strokeDasharray="3 3" label={{ position: 'top', value: 'Forward', fill: B.amber, fontSize: 10 }} />}
            </ComposedChart>
          </ResponsiveContainer>
          <div style={{display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:8, marginTop:16}}>
            {data.revenueTrend.filter((q) => !q.isHistorical).slice(0, 4).map((q, i) => (
               <div key={i} style={{background:B.bg2, border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"8px 12px"}}>
                 <div style={{fontSize:9, color:B.muted, textTransform:"uppercase", marginBottom:4, fontWeight:700}}>{q.quarter} DRIVER</div>
                 <div style={{fontSize:10, color:B.text, fontWeight:600}}>{q.keyDriver || '—'}</div>
               </div>
             ))}
          </div>
        </Card>

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
      </div>
    );
  };

  const APP_TABS = [
    {label:"Exec Brief", id:'exec'},
    {label:`${stakeholder}`, id:'stakeholder'},
    {label:"Forward / Grey Swan", id:'quarterly'},
    {label:"Pre-Mortem", id:'preMortem'},
  ];

  return (
    <div style={{background:B.bg, minHeight:"100vh", fontFamily:"'IBM Plex Mono','Courier New',monospace", color:B.text}}>
      {/* Header */}
      <header style={{background:B.card, borderBottom:`1px solid ${B.border}`, padding:"14px 24px", display:"flex", justifyContent:"space-between", alignItems:"center"}}>
        <div className="flex items-center space-x-3">
          <div className="relative w-10 h-10 flex items-center justify-center bg-gray-100 rounded-md border overflow-hidden" style={{borderColor: B.border}}>
            <Cpu className="absolute z-0 opacity-20" size={24} color={B.text} />
            <img src="Gemini_Generated_Image_.png" alt="Logo" className="w-full h-full object-cover relative z-10" onError={(e) => e.target.style.display = 'none'} />
          </div>
          <div>
            <div style={{color:B.mint,fontSize:9,letterSpacing:"0.26em",textTransform:"uppercase",marginBottom:2, fontWeight:800}}>▸ DENDRAI QUANT_ENGINE</div>
            <div style={{fontSize:16,fontWeight:800,color:B.text}}>Risk & Intelligence Synthesizer</div>
          </div>
        </div>
        <div className="hidden md:flex items-center space-x-2" style={{fontSize:10, color:B.muted}}>
          <Database size={14} /> <span>ONLINE</span>
        </div>
      </header>

      <main className="max-w-7xl mx-auto p-4 md:p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Sidebar: Configuration */}
        <div className="lg:col-span-4 space-y-6">
          <Card>
            <Lbl icon={Crosshair}>TARGETING PARAMETERS</Lbl>

            <div className="space-y-4 mt-4">
              <div>
                <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase", marginBottom:4}}>1. Target Entity</label>
                <input 
                  type="text" value={entity} onChange={(e) => setEntity(e.target.value)}
                  placeholder="e.g., TSMC, Intel, Startup X"
                  style={{width:"100%", background:B.bg, border:`1px solid ${B.borderLt}`, borderRadius:4, padding:"8px 12px", fontSize:12, outline:"none", color:B.text}}
                />
              </div>

              <div>
                <div className="flex justify-between items-end mb-1">
                  <label style={{display:"block", fontSize:10, fontWeight:700, color:B.text, textTransform:"uppercase"}}>Industry / SIC</label>
                  <button 
                    onClick={handleAutoDetectIndustry} disabled={detectingIndustry || !entity}
                    style={{fontSize:9, background:B.greenBg, color:B.mint, border:`1px solid ${B.mint}66`, borderRadius:3, padding:"2px 8px", cursor:"pointer"}}
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
                    style={{fontSize:9, background:B.greenBg, color:B.mint, border:`1px solid ${B.mint}66`, borderRadius:3, padding:"2px 8px", cursor:"pointer"}}
                  >
                    {populatingPeers ? 'Fetching...' : 'Auto-Populate (AI)'}
                  </button>
                </div>
                <textarea 
                  value={peers} onChange={(e) => setPeers(e.target.value)} rows={2} placeholder="Comma separated list..."
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

              {error && (
                <div style={{background:B.redBg, color:B.red, fontSize:11, padding:10, borderRadius:4, border:`1px solid ${B.red}44`}}>
                  {error}
                </div>
              )}

              <button 
                onClick={handleGenerateReport} disabled={loading}
                style={{width:"100%", background:B.text, color:B.card, fontWeight:700, fontSize:12, padding:"12px", borderRadius:4, marginTop:10, cursor:loading?"default":"pointer", opacity:loading?0.7:1, display:"flex", justifyContent:"center", alignItems:"center"}}
              >
                {loading ? <span className="flex items-center"><Activity className="animate-spin mr-2" size={16} /> SYNTHESIZING...</span> : 'GENERATE INTELLIGENCE'}
              </button>
            </div>
          </Card>
        </div>

        {/* Right Area: Dashboard Output */}
        <div className="lg:col-span-8">
          {!reportData && !loading && (
            <div style={{height:"100%", display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", color:B.muted, background:"rgba(255,255,255,0.4)", border:`1px dashed ${B.borderLt}`, borderRadius:8, minHeight:400}}>
              <Activity size={48} style={{color:B.dim, marginBottom:16}} />
              <p style={{fontSize:11, textTransform:"uppercase", letterSpacing:"0.1em", fontWeight:700}}>Awaiting Synthesis Directives</p>
            </div>
          )}

          {loading && (
            <Card style={{height:"100%", display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", minHeight:400}} className="animate-pulse">
              <div className="w-16 h-16 border-4 rounded-full animate-spin mb-6" style={{borderColor: B.bg2, borderTopColor: B.mint}}></div>
              <p style={{fontSize:12, fontWeight:800, color:B.text, textTransform:"uppercase", letterSpacing:"0.1em"}}>Processing Data Streams...</p>
              <p style={{fontSize:10, color:B.muted, marginTop:8}}>Aligning to {stakeholder} protocols</p>
            </Card>
          )}

          {reportData && !loading && (
            <div className="space-y-6">
              
              {/* Tab Navigation */}
              <div style={{display:"flex", gap:4, padding:"10px 16px", background:B.card, border:`1px solid ${B.border}`, borderRadius:8, overflowX:"auto"}}>
                {APP_TABS.map((t, i) => (
                  <button key={i} onClick={()=>setActiveTab(t.id)} style={{
                    background: activeTab===t.id ? B.greenBg : "transparent", 
                    border:`1px solid ${activeTab===t.id ? B.mint : "transparent"}`, 
                    color: activeTab===t.id ? B.text : B.muted, 
                    borderRadius:4, padding:"8px 16px", cursor:"pointer", fontSize:10, fontWeight:700, whiteSpace:"nowrap"
                  }}>
                    {t.label.toUpperCase()}
                  </button>
                ))}
              </div>

              {/* Tab Contents */}
              {activeTab === 'exec' && renderExecDashboard(reportData)}

              {activeTab === 'stakeholder' && (
                <>
                  {stakeholder === 'Audit / ERM' && renderAuditDashboard(reportData)}
                  {stakeholder === 'CFO / Finance' && renderCFODashboard(reportData)}
                  {stakeholder === 'CIO / IT / CISO' && renderCIODashboard(reportData)}
                  {stakeholder === 'Board / Audit Committee' && renderBoardDashboard(reportData)}
                </>
              )}

              {activeTab === 'quarterly' && renderQuarterlyAndGreySwan(reportData)}

              {activeTab === 'preMortem' && (
                <div style={{display:"flex", flexDirection:"column", gap:16, animation:"fadeIn 0.3s ease-in-out"}}>
                  <Card style={{borderLeft:`4px solid ${B.mint}`}}>
                    <Lbl sub="The load-bearing assumption" color={B.text}>CRITICAL GREEN ASSUMPTION</Lbl>
                    <div style={{padding:"14px 18px", background:B.greenBg, borderRadius:5, fontSize:13, color:B.text, fontStyle:"italic", border:`1px solid ${B.mint}44`}}>
                      "{reportData.preMortem?.criticalGreenAssumption}"
                    </div>
                  </Card>
                  <Card style={{borderLeft:`4px solid ${B.red}`}}>
                    <Lbl sub="Failure Scenario over the next 90 days" color={B.red}>FAILURE SCENARIO (DAY 90)</Lbl>
                    <div style={{fontSize:12, color:B.text, lineHeight:1.6}}>
                      {reportData.preMortem?.failureScenario}
                    </div>
                  </Card>
                </div>
              )}

            </div>
          )}
        </div>

      </main>
    </div>
  );
}