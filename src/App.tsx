import { useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, LineChart, Line,
  Cell, Legend, ComposedChart
} from 'recharts'

const B = {
  mint:"#2BCC99", mintAccent:"#3DFFC0", ivory:"#E8F5F0",
  bg:"#E8F5F0", bg2:"#DCEFE7", card:"#FFFFFF",
  border:"#BFD9CF", borderLt:"#A6C9BB",
  text:"#1A1F1D", textLt:"#2E3733", muted:"#5A6B65", dim:"#C8DDD2",
  red:"#C8412E", redBg:"#FBE7E3",
  amber:"#C77A1F", amberBg:"#FAF0DA",
  greenBg:"#D5F2E5",
}
const RC = [B.red, B.amber, B.mint]
const RBG = [B.redBg, B.amberBg, B.greenBg]
const RL = ["RED","AMB","GRN"]

const SECTOR = [
  {co:"NVDA",d:188.2,color:B.mint+"DD"},{co:"MU",d:62.4,color:B.mint+"99"},
  {co:"AVGO",d:44.1,color:B.mint+"66"},{co:"TSM",d:34.0,color:B.mint+"55"},
  {co:"ARM",d:30.2,color:B.mint+"44"},{co:"MRVL",d:27.5,color:B.amber+"66"},
  {co:"AMD",d:21.3,color:B.amber+"88"},{co:"QCOM",d:9.1,color:B.amber+"AA"},
  {co:"INTC",d:-7.8,color:B.red+"99"},
]

const NVDA = {
  name:"NVIDIA Corporation", ticker:"NVDA", accent:B.mint,
  mScore:-1.42, mScoreAdj:-2.10, mScoreFlag:"GROWTH-INFLATED",
  zScore:27.0, hsExp:"~42%", chinaExp:"~12%", capexRatio:"4%",
  aqi:1.08, tata:0.062, rdRatio:"9%", gmTtm:"74.5%",
  headline:"Blackwell Ramp | Rubin Tape-Out | Hyperscaler Concentration | BIS H20 Watch",
  mScorePeers:[
    {co:"INTC",s:-2.85,f:2},{co:"QCOM",s:-2.71,f:2},{co:"ARM",s:-2.62,f:2},
    {co:"AMD",s:-2.45,f:2},{co:"MRVL",s:-2.30,f:2},{co:"AVGO",s:-2.18,f:2},
    {co:"MU",s:-1.95,f:1},{co:"NVDA★",s:-1.42,f:1},
  ],
  zScorePeers:[
    {co:"INTC",s:2.10,z:1},{co:"MU",s:3.60,z:2},{co:"TSM",s:4.80,z:2},
    {co:"QCOM",s:5.40,z:2},{co:"AMD",s:6.20,z:2},{co:"AVGO",s:8.50,z:2},
    {co:"ARM",s:12.00,z:2},{co:"NVDA★",s:27.00,z:2},
  ],
  mComp:[
    {v:"SGI",c:1.588,note:"Hyper-growth inflator — known model failure mode"},
    {v:"DSRI",c:1.049,note:"AR growth lagging revenue acceleration"},
    {v:"GMI",c:0.512,note:"GM stable >74%"},
    {v:"AQI",c:0.436,note:"CoWoS prepay, HBM commits"},
    {v:"DEPI",c:0.117,note:"Depreciation stable"},
    {v:"TATA",c:0.290,note:"⚠ Elevated accruals — watch item"},
    {v:"SGAI",c:-0.162,note:"✓ Operating leverage"},
    {v:"LVGI",c:-0.298,note:"✓ De-leveraging"},
  ],
  fwd:[
    {q:"Q1'27A",actual:48.0,gmA:73.5,fcfA:23.0},
    {q:"Q2'27",bear:46,base:54,bull:62,gmBear:70,gmBase:73,gmBull:76,fcfBear:22,fcfBase:28,fcfBull:34},
    {q:"Q3'27",bear:48,base:58,bull:68,gmBear:70,gmBase:73,gmBull:76,fcfBear:24,fcfBase:30,fcfBull:37},
    {q:"Q4'27",bear:50,base:62,bull:72,gmBear:69,gmBase:73,gmBull:76,fcfBear:26,fcfBase:33,fcfBull:40},
    {q:"Q1'28",bear:50,base:65,bull:78,gmBear:69,gmBase:73,gmBull:75,fcfBear:25,fcfBase:35,fcfBull:43},
    {q:"Q2'28",bear:52,base:70,bull:84,gmBear:68,gmBase:73,gmBull:75,fcfBear:27,fcfBase:37,fcfBull:46},
    {q:"Q3'28",bear:54,base:75,bull:90,gmBear:68,gmBase:73,gmBull:75,fcfBear:28,fcfBase:40,fcfBull:50},
    {q:"Q4'28",bear:56,base:80,bull:96,gmBear:67,gmBase:72,gmBull:74,fcfBear:30,fcfBase:43,fcfBull:54},
  ],
  annual:[
    {yr:"FY24",rev:60.9,gm:72.7,fcf:27.0,capex:1.1},
    {yr:"FY25",rev:130.5,gm:75.0,fcf:60.7,capex:3.2},
    {yr:"FY26",rev:195.0,gm:74.5,fcf:95.0,capex:5.5},
    {yr:"FY27E",rev:238.0,gm:73.0,fcf:125.0,capex:8.5},
    {yr:"FY28E",rev:295.0,gm:73.5,fcf:158.0,capex:11.0},
    {yr:"FY29E",rev:330.0,gm:72.5,fcf:175.0,capex:13.0},
  ],
  revAnnualScen:[
    {yr:"FY27",bear:168,base:215,bull:258},
    {yr:"FY28",bear:172,base:268,bull:345},
    {yr:"FY29",bear:155,base:295,bull:410},
  ],
  epsScen:[
    {yr:"FY27",bear:4.10,base:5.65,bull:7.20},
    {yr:"FY28",bear:4.00,base:7.20,bull:9.85},
    {yr:"FY29",bear:3.40,base:7.90,bull:12.10},
  ],
  scenDrivers:[
    {driver:"Hyperscaler Capex YoY",bear:"−15%",base:"+18%",bull:"+35%"},
    {driver:"China Re-entry",bear:"Blocked",base:"Partial (B30/H20-succ.)",bull:"Full + Sovereign"},
    {driver:"Gross Margin %",bear:"68%",base:"73%",bull:"75%"},
    {driver:"ASIC Share of AI TAM",bear:"35%",base:"18%",bull:"10%"},
  ],
  sensitivity:[
    {lever:"Hyperscaler capex ±10%",impact:14.6,epsImpact:0.55,gmImpact:0.15},
    {lever:"CoWoS-L capacity ±10%",impact:11.2,epsImpact:0.42,gmImpact:0.20},
    {lever:"Blackwell ASP ±5%",impact:8.4,epsImpact:0.38,gmImpact:0.90},
    {lever:"HBM3e/HBM4 yield ±5%",impact:5.8,epsImpact:0.31,gmImpact:0.55},
    {lever:"China-cleared SKU ±$1B/qtr",impact:4.0,epsImpact:0.14,gmImpact:-0.30},
    {lever:"NVLink/Networking ±200bp",impact:3.1,epsImpact:0.18,gmImpact:0.25},
  ],
  irPivots:[
    {n:1,title:"From GPU Vendor to AI Factory OEM",txt:"Recode revenue as platform-recurring (NVL72, Spectrum-X, BlueField, AI Enterprise). Defends multiple against hyperscaler-ASIC bear thesis."},
    {n:2,title:"Sovereign AI as the Second S-Curve",txt:"EU / GCC / India / Japan / ASEAN demand decoupled from top-4 hyperscaler concentration — structurally non-correlated growth pillar."},
    {n:3,title:"Capital Returns at Scale",txt:"Formalize ≥80% FCF buyback framework + initiate progressive dividend to signal discipline and blunt 'peak earnings' narrative."},
  ],
  qs:["Q2'27","Q3'27","Q4'27","Q1'28","Q2'28","Q3'28","Q4'28"],
  ragFin:[
    {r:"Hyperscaler Concentration",v:[0,0,0,0,1,1,1],vel:"up",vl:"DIVERSIFYING"},
    {r:"Gross Margin Defense",v:[2,2,1,1,1,1,1],vel:"dn",vl:"COMPRESSING"},
    {r:"FCF Generation",v:[2,2,2,2,2,2,2],vel:"up",vl:"ACCELERATING"},
    {r:"Inventory & Purchase Commits",v:[1,1,1,2,2,2,2],vel:"up",vl:"NORMALIZING"},
    {r:"Capital Return Discipline",v:[1,1,2,2,2,2,2],vel:"up",vl:"FORMALIZING"},
    {r:"Customer Financing",v:[1,1,1,1,1,1,1],vel:"st",vl:"WATCH"},
  ],
  ragOps:[
    {r:"CoWoS-L Capacity",v:[1,1,2,2,2,2,2],vel:"up",vl:"RAMPING"},
    {r:"HBM3e / HBM4 Supply",v:[1,1,1,2,2,2,2],vel:"up",vl:"EXPANDING"},
    {r:"Rubin / Rubin Ultra Ramp",v:[2,2,1,1,2,2,2],vel:"up",vl:"ON-TRACK"},
    {r:"Hyperscaler ASIC Sub.",v:[1,1,0,0,0,0,0],vel:"dn",vl:"ACCELERATING"},
    {r:"TSMC Fab Concentration",v:[1,1,1,0,0,0,0],vel:"dn",vl:"WORSENING"},
    {r:"NVLink / Spectrum-X",v:[2,2,2,2,2,2,2],vel:"up",vl:"PLATFORM"},
  ],
  ragComp:[
    {r:"BIS Export Controls",v:[0,0,0,1,1,1,1],vel:"up",vl:"PERSISTENT"},
    {r:"EU AI Act / Antitrust",v:[1,1,0,0,0,0,0],vel:"dn",vl:"ESCALATING"},
    {r:"SEC Forward Guidance",v:[1,1,1,2,2,2,2],vel:"up",vl:"IMPROVING"},
    {r:"Taiwan Strait",v:[1,1,1,1,0,0,0],vel:"dn",vl:"STRUCTURAL"},
    {r:"Related-Party Disc.",v:[1,1,1,2,2,2,2],vel:"up",vl:"CLARIFYING"},
    {r:"ASC 606 Bundles",v:[1,1,2,2,2,2,2],vel:"up",vl:"RESOLVING"},
  ],
  china:[
    {co:"US Hyperscalers (Top 4)",pct:42,f:0},
    {co:"US Enterprise / Sovereign",pct:18,f:1},
    {co:"Sovereign AI (EU/GCC/JP/IN)",pct:14,f:2},
    {co:"China (H20-class only)",pct:12,f:0},
    {co:"Asia ex-CN",pct:9,f:2},
    {co:"ROW",pct:5,f:2},
  ],
  localThreat:[
    {name:"Huawei Ascend 910C",seg:"Training",now:62,y27:78,risk:1200},
    {name:"Cambricon Siyuan 590",seg:"Inference",now:55,y27:72,risk:480},
    {name:"Biren BR104P",seg:"Training",now:48,y27:68,risk:420},
    {name:"Moore Threads S4000",seg:"Mixed",now:42,y27:65,risk:280},
    {name:"Iluvatar Tianlong",seg:"Inference",now:50,y27:70,risk:320},
  ],
  capexPeer:[
    {co:"NVDA★",r:9},{co:"MU",r:11},{co:"AVGO",r:17},{co:"QCOM",r:22},
    {co:"AMD",r:24},{co:"INTC",r:25},{co:"MRVL",r:28},{co:"ARM",r:36},
  ],
  macro:[
    {n:"Hyperscaler AI Capex YoY",corr:0.94,lead:1,sig:2,val:"+38% Q1'26"},
    {n:"TSMC CoWoS-L Capacity",corr:0.91,lead:2,sig:2,val:"+45% YoY"},
    {n:"AI Token Consumption",corr:0.88,lead:1,sig:2,val:"+8.5× YoY"},
    {n:"HBM3e / HBM4 Supply",corr:0.86,lead:2,sig:1,val:"SK Hynix tight"},
    {n:"GPU Cloud Spot Pricing",corr:0.74,lead:1,sig:2,val:"H100 $2.10/hr"},
    {n:"US DC Power Permits",corr:0.71,lead:3,sig:1,val:"+22% YoY"},
    {n:"BIS Export Controls",corr:-0.66,lead:1,sig:0,val:"Tightening"},
    {n:"EU AI Act Enforcement",corr:-0.52,lead:2,sig:1,val:"Aug'26"},
  ],
  audit:[
    {ref:"N01",title:"Hyperscaler Concentration Disc.",impact:9.5,detect:3.5,urg:0,domain:"Disclosure"},
    {ref:"N02",title:"ASC 606 AI Enterprise Bundles",impact:9.2,detect:3.8,urg:0,domain:"Revenue"},
    {ref:"N03",title:"Related-Party (CoreWeave)",impact:9.0,detect:3.0,urg:0,domain:"Revenue"},
    {ref:"N04",title:"BIS H20 Compliance",impact:8.8,detect:4.0,urg:0,domain:"Compliance"},
    {ref:"N05",title:"CoWoS Purchase Commits",impact:8.5,detect:4.5,urg:0,domain:"Commits"},
    {ref:"N06",title:"Inv. Reserve Hopper→Rubin",impact:8.3,detect:5.0,urg:0,domain:"Inventory"},
    {ref:"N07",title:"SEC AI Guidance Caveat",impact:8.0,detect:4.2,urg:1,domain:"Disclosure"},
    {ref:"N08",title:"Mellanox Goodwill Test",impact:7.4,detect:6.0,urg:1,domain:"Forensic"},
    {ref:"N09",title:"Customer Financing Loop",impact:7.2,detect:4.5,urg:1,domain:"Revenue"},
    {ref:"N10",title:"SBC / Non-GAAP Recon.",impact:6.8,detect:7.5,urg:1,domain:"Disclosure"},
    {ref:"N11",title:"Transfer Pricing",impact:6.5,detect:6.0,urg:1,domain:"Tax"},
    {ref:"N12",title:"DGX Cloud Multi-Element",impact:6.2,detect:5.5,urg:2,domain:"Revenue"},
  ],
  hsBreakdown:[
    {item:"Microsoft (Azure)",pct:13.5,c:B.mint},{item:"Meta",pct:11.0,c:B.mint},
    {item:"Alphabet (GCP)",pct:9.5,c:B.amber},{item:"Amazon (AWS)",pct:8.0,c:B.amber},
    {item:"Oracle / CoreWeave",pct:7.5,c:B.amber},{item:"Tesla / xAI",pct:4.5,c:B.mint},
    {item:"Sovereign + Ent.",pct:32.0,c:B.mint},{item:"China (H20)",pct:12.0,c:B.red},
    {item:"Other",pct:2.0,c:B.muted},
  ],
  archCadence:[
    {gen:"Hopper (H100/H200)",ship:"FY24",asp:25,status:"Tailing"},
    {gen:"Blackwell (B100/B200)",ship:"FY25",asp:42,status:"Ramping"},
    {gen:"Blackwell Ultra (B300)",ship:"FY26",asp:55,status:"Active"},
    {gen:"Rubin (R100)",ship:"FY27",asp:68,status:"Tape-Out"},
    {gen:"Rubin Ultra (R200)",ship:"FY28",asp:85,status:"Design"},
    {gen:"Feynman (F100)",ship:"FY29",asp:105,status:"Concept"},
  ],
  threats:[
    {cls:"IP Theft",v:"CUDA / cuDNN source exfiltration via insider",lk:"Med",imp:"Catastrophic",rag:0},
    {cls:"IP Theft",v:"Mask/GDSII leakage from TSMC partner interface",lk:"Low",imp:"Catastrophic",rag:1},
    {cls:"IP Theft",v:"Mellanox/NVLink RTL reverse engineering",lk:"Med",imp:"High",rag:1},
    {cls:"Espionage",v:"Nation-state recruitment of R&D talent",lk:"High",imp:"High",rag:0},
    {cls:"Espionage",v:"Supply chain implant (interposer/HBM)",lk:"Med",imp:"High",rag:1},
    {cls:"Espionage",v:"Pre-release Rubin spec leakage",lk:"High",imp:"Med",rag:1},
    {cls:"Agentic Drift",v:"Internal AI coding agents · IP-tainted commits",lk:"Med",imp:"High",rag:0},
    {cls:"Agentic Drift",v:"Customer-deployed agent harmful outputs",lk:"High",imp:"Med",rag:1},
    {cls:"Agentic Drift",v:"Autonomous treasury agents · unbounded scope",lk:"Low",imp:"High",rag:1},
    {cls:"Systemic Cyber",v:"Ransomware on EDA/CAD pipelines",lk:"Med",imp:"Catastrophic",rag:0},
    {cls:"Systemic Cyber",v:"Driver/firmware update channel compromise",lk:"Low",imp:"Catastrophic",rag:1},
    {cls:"Systemic Cyber",v:"DGX Cloud multi-tenant escape",lk:"Med",imp:"High",rag:1},
  ],
  guardrails:[
    {ctrl:"Zero-Trust R&D Enclaves",dom:"IP Theft",pri:"P0"},
    {ctrl:"Air-Gapped Tape-Out Vault",dom:"IP Theft",pri:"P0"},
    {ctrl:"Agent Identity & Scope Registry",dom:"Agentic Drift",pri:"P0"},
    {ctrl:"Code Provenance Gating (SBOM)",dom:"Agentic Drift",pri:"P1"},
    {ctrl:"Counter-Insider Program",dom:"Espionage",pri:"P1"},
    {ctrl:"Confidential Computing · DGX Cloud",dom:"Systemic Cyber",pri:"P1"},
    {ctrl:"Out-of-Band Firmware Signing",dom:"Supply Chain",pri:"P2"},
    {ctrl:"EDA Pipeline Hardening",dom:"Systemic Cyber",pri:"P2"},
  ],
  premortem:[
    {label:"Hyperscaler",before:3,after:3},{label:"TSMC",before:2,after:3},
    {label:"GM defense",before:1,after:3},{label:"SEC disc.",before:2,after:3},
    {label:"Export ctrl",before:3,after:3},{label:"Composite",before:2,after:3},
  ],
  cascade:[
    {d:"Day 0 – 15",c:B.amber,txt:"One of MSFT/META/GOOGL pre-announces AI capex deceleration + accelerated internal-ASIC roadmap (MTIA v3 / TPU v7)."},
    {d:"Day 15 – 35",c:B.amber,txt:"Taiwan Strait incident — PLA exercises escalate to partial maritime quarantine; CoWoS shipments paused 2–4 weeks."},
    {d:"Day 35 – 55",c:B.red,txt:"US BIS expands export controls to Tier-2 jurisdictions (ME/ASEAN); B200-class re-export restricted."},
    {d:"Day 55 – 75",c:B.red,txt:"NVDA issues mid-quarter pre-announcement: −12% vs. guide. Inventory reserve charge $2.5–4B telegraphed."},
    {d:"Day 75 – 90",c:B.red,txt:"Forward P/E compresses 30× → 18×. Market cap −40%+. Class-action filings; SEC inquiry; Audit Committee independent review."},
  ],
  defense:[
    {role:"CFO",c:B.mint,txt:"Pre-stage capex-normalization narrative. Secure board pre-approval for accelerated buyback tranche deployable on >25% drawdown."},
    {role:"AUDIT / ERM",c:B.amber,txt:"Pre-build inventory reserve methodology workpapers. Tabletop pre-announcement disclosure decision tree with outside counsel."},
    {role:"CIO / CISO",c:B.red,txt:"Activate Taiwan-contingency data replication to US/JP/DE regions now. Rehearse air-gap procedures for in-flight tape-outs."},
  ],
}

const Card = ({children, style={}}: any) => <div style={{background:B.card, border:`1px solid ${B.border}`, borderRadius:8, padding:18, boxShadow:"0 1px 2px rgba(26,31,29,0.04)", ...style}}>{children}</div>
const Lbl = ({children, sub, color}: any) => (
  <div style={{marginBottom:12}}>
    <div style={{color:color||B.text, fontSize:10, letterSpacing:"0.16em", textTransform:"uppercase", marginBottom:3, fontWeight:700}}>{children}</div>
    {sub && <div style={{color:B.muted, fontSize:10, lineHeight:1.4}}>{sub}</div>}
  </div>
)
const Tag = ({children, color}: any) => <span style={{background:color+"22", border:`1px solid ${color}66`, color, borderRadius:3, fontSize:8, padding:"2px 7px", letterSpacing:"0.07em", whiteSpace:"nowrap", fontWeight:700}}>{children}</span>
const ChartTip = ({active, payload, label, fmt}: any) => {
  if (!active || !payload?.length) return null
  return <div style={{background:B.card, border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"8px 12px", fontSize:10, boxShadow:"0 4px 12px rgba(26,31,29,0.10)"}}>
    <div style={{color:B.text, marginBottom:5, fontWeight:700}}>{label}</div>
    {payload.map((p: any, i: number) => <div key={i} style={{color:p.color||B.text, marginBottom:2}}>{p.name}: {fmt && p.value != null ? fmt(p.value) : p.value ?? "—"}</div>)}
  </div>
}
const RagCell = ({val}: any) => <div style={{background:RBG[val], borderRadius:3, width:52, height:22, display:"flex", alignItems:"center", justifyContent:"center", fontSize:9, color:RC[val], fontWeight:800, letterSpacing:"0.06em", border:`1px solid ${RC[val]}55`}}>{RL[val]}</div>
const VBadge = ({vel, vl}: any) => {
  const c: any = {up:B.mint, dn:B.red, st:B.amber}[vel] || B.amber
  const icon: any = {up:"↑",dn:"↓",st:"→"}[vel]
  return <span style={{fontSize:8, color:c, marginLeft:6, fontWeight:700}}>{icon} {vl}</span>
}
const RagGrid = ({data, qs}: any) => (
  <div style={{overflowX:"auto"}}>
    <table style={{borderCollapse:"separate", borderSpacing:"3px 3px"}}>
      <thead><tr>
        <th style={{textAlign:"left", fontSize:9, color:B.muted, paddingRight:12, paddingBottom:4, minWidth:220, fontWeight:600}}>RISK FACTOR / VELOCITY</th>
        {qs.map((q: string) => <th key={q} style={{fontSize:9, color:B.muted, paddingBottom:4, textAlign:"center", fontWeight:600, minWidth:56}}>{q}</th>)}
      </tr></thead>
      <tbody>{data.map((row: any, i: number) => (
        <tr key={i}>
          <td style={{fontSize:10.5, color:B.text, paddingRight:12, paddingBottom:3, whiteSpace:"nowrap"}}>{row.r}<VBadge vel={row.vel} vl={row.vl}/></td>
          {row.v.map((val: any, j: number) => <td key={j} style={{paddingBottom:3, textAlign:"center"}}><RagCell val={val}/></td>)}
        </tr>
      ))}</tbody>
    </table>
  </div>
)

const KPI = ({label, value, color, sub}: any) => (
  <Card style={{textAlign:"center", padding:"12px 8px", border:`1px solid ${color}55`}}>
    <div style={{fontSize:18, fontWeight:800, color}}>{value}</div>
    <div style={{fontSize:9, color:B.muted, marginTop:3, fontWeight:600}}>{label}</div>
    {sub && <div style={{fontSize:8, color, marginTop:3}}>{sub}</div>}
  </Card>
)

function T0_Overview({co}: any) {
  const sorted = [...SECTOR].sort((a,b) => b.d - a.d)
  return (
    <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub="2-year revenue CAGR through FY26 | AI / Datacenter cohort">AI INFRASTRUCTURE COHORT — REVENUE TRAJECTORY</Lbl>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={sorted} layout="vertical" margin={{left:8,right:60,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-20,220]} tickFormatter={(v: any) => `${v}%`} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text, fontSize:11, fontWeight:600}} stroke="none" width={52}/>
            <Tooltip content={<ChartTip fmt={(v: any) => `${v.toFixed(1)}%`}/>}/>
            <ReferenceLine x={0} stroke={B.borderLt}/>
            <Bar dataKey="d" name="2Y Revenue CAGR" radius={[0,3,3,0]}>
              {sorted.map((d, i) => <Cell key={i} fill={d.co===co.ticker ? co.accent : d.color}/>)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <Card>
        <Lbl sub="Annual revenue $B | Gross Margin % overlay">FINANCIAL TRAJECTORY FY24 → FY29E</Lbl>
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={co.annual} margin={{left:8, right:40, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} vertical={false}/>
            <XAxis dataKey="yr" tick={{fill:B.text, fontSize:9, fontWeight:600}} stroke={B.border}/>
            <YAxis yAxisId="l" tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={(v: any)=>`$${v}B`}/>
            <YAxis yAxisId="r" orientation="right" domain={[60,80]} tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={(v: any)=>`${v}%`}/>
            <Tooltip content={<ChartTip/>}/>
            <Bar yAxisId="l" dataKey="rev" name="Revenue $B" fill={co.accent+"55"} radius={[3,3,0,0]}/>
            <Line yAxisId="r" type="monotone" dataKey="gm" name="GM %" stroke={co.accent} strokeWidth={2.5} dot={{fill:co.accent, r:4}}/>
            <Line yAxisId="l" type="monotone" dataKey="fcf" name="FCF $B" stroke={B.amber} strokeWidth={1.8} strokeDasharray="5 3" dot={{fill:B.amber, r:3}}/>
          </ComposedChart>
        </ResponsiveContainer>
      </Card>
      <Card>
        <Lbl sub="Structural dimensions vs cohort">COMPETITIVE POSITIONING</Lbl>
        <div style={{display:"flex", flexDirection:"column", gap:8}}>
          {[
            {dim:"Fab Strategy",val:"Fabless (TSMC)",c:B.amber},
            {dim:"Top-4 HS Exposure",val:"~42% Rev",c:B.red},
            {dim:"R&D / Rev",val:"9%",c:B.mint},
            {dim:"GM TTM",val:"74.5%",c:B.mint},
            {dim:"Z-Score",val:"27.0",c:B.mint},
            {dim:"China",val:"~12%",c:B.amber},
          ].map((d, i) => (
            <div key={i} style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"8px 12px", background:B.bg2, borderRadius:5, border:`1px solid ${d.c}33`}}>
              <span style={{fontSize:10, color:B.muted}}>{d.dim}</span>
              <span style={{fontSize:11, fontWeight:800, color:d.c}}>{d.val}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

function T1_Forensic({co}: any) {
  const mSorted = [...co.mScorePeers].sort((a: any, b: any) => b.s - a.s)
  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <div style={{display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:12}}>
        <KPI label="M-Score (Headline)" value={co.mScore} color={B.amber} sub={co.mScoreFlag}/>
        <KPI label="M-Score (Growth-Adj.)" value={co.mScoreAdj} color={B.mint} sub="Safe Zone"/>
        <KPI label="Altman Z-Score" value={co.zScore} color={B.mint} sub="Fortress · Mkt-Cap dominant"/>
        <KPI label="AQI / TATA" value={`${co.aqi} / ${co.tata}`} color={B.amber} sub="Watch accruals"/>
      </div>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
        <Card>
          <Lbl sub={`Threshold −1.78 | ${co.ticker}: ${co.mScore} (${co.mScoreFlag})`}>BENEISH M-SCORE — COHORT</Lbl>
          <ResponsiveContainer width="100%" height={265}>
            <BarChart data={mSorted} layout="vertical" margin={{left:8,right:40,top:4}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
              <XAxis type="number" domain={[-3.2,-0.8]} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
              <YAxis type="category" dataKey="co" tick={{fill:B.text, fontSize:11, fontWeight:600}} stroke="none" width={60}/>
              <Tooltip content={<ChartTip fmt={(v: any)=>v.toFixed(2)}/>}/>
              <ReferenceLine x={-1.78} stroke={B.amber} strokeWidth={1.5} strokeDasharray="5 4" label={{value:"−1.78", position:"insideTopRight", fill:B.amber, fontSize:9, fontWeight:700}}/>
              <Bar dataKey="s" name="M-Score" radius={[0,3,3,0]}>
                {mSorted.map((d: any, i: number) => <Cell key={i} fill={d.co.includes("★") ? co.accent : RC[d.f]}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <div style={{marginTop:8, padding:"8px 12px", background:B.amberBg, border:`1px solid ${B.amber}55`, borderRadius:5, fontSize:9, color:B.textLt, lineHeight:1.5}}>
            <b style={{color:B.amber}}>⚠ GROWTH ARTIFACT:</b> SGI mechanically inflates score. Growth-adjusted ≈ −2.10 (safe zone).
          </div>
        </Card>
        <Card>
          <Lbl sub="Safe Zone: > 2.99 | Grey Zone: 1.81–2.99">ALTMAN Z-SCORE — SOLVENCY</Lbl>
          <ResponsiveContainer width="100%" height={265}>
            <BarChart data={co.zScorePeers} layout="vertical" margin={{left:8,right:50,top:4}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
              <XAxis type="number" domain={[0,30]} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
              <YAxis type="category" dataKey="co" tick={{fill:B.text, fontSize:11, fontWeight:600}} stroke="none" width={60}/>
              <Tooltip content={<ChartTip fmt={(v: any)=>v.toFixed(2)}/>}/>
              <ReferenceLine x={2.99} stroke={B.mint} strokeWidth={1.5} strokeDasharray="5 4" label={{value:"SAFE", position:"insideTopRight", fill:B.mint, fontSize:9, fontWeight:700}}/>
              <ReferenceLine x={1.81} stroke={B.red} strokeWidth={1} strokeDasharray="4 4"/>
              <Bar dataKey="s" name="Z-Score" radius={[0,3,3,0]} label={{position:"right", formatter:(v: any)=>v.toFixed(1), fill:B.muted, fontSize:9}}>
                {co.zScorePeers.map((d: any, i: number) => <Cell key={i} fill={d.co.includes("★") ? co.accent : d.z===1 ? B.amber : B.muted+"88"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <div style={{marginTop:8, padding:"8px 12px", background:B.greenBg, border:`1px solid ${B.mint}55`, borderRadius:5, fontSize:9, color:B.textLt, lineHeight:1.5}}>
            <b style={{color:B.mint}}>✓ FORTRESS:</b> Z dominated by Mkt-Cap / Total-Liab (~38×). Sensitive to multiple compression.
          </div>
        </Card>
      </div>
      <Card>
        <Lbl sub={`M-Score: ${co.mScore} | Weighted contribution per variable`}>M-SCORE COMPONENT DECOMPOSITION</Lbl>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={co.mComp} layout="vertical" margin={{left:14,right:240,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-0.5,2.0]} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="v" tick={{fill:B.text, fontSize:11, fontWeight:600}} stroke="none" width={54}/>
            <Tooltip content={<ChartTip fmt={(v: any)=>`${v>0?"+":""}${v.toFixed(3)}`}/>}/>
            <ReferenceLine x={0} stroke={B.borderLt}/>
            <Bar dataKey="c" name="Contribution" radius={[0,3,3,0]} label={{position:"right", formatter:(v: any)=>v.toFixed(2), fill:B.muted, fontSize:9}}>
              {co.mComp.map((d: any, i: number) => <Cell key={i} fill={d.v==="SGI"||(d.v==="TATA"&&d.c>0.25) ? B.amber : d.c<0 ? B.mint : B.amber+"AA"}/>)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

function T2_Forward({co}: any) {
  const [view, setView] = useState("revenue")
  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <div style={{display:"grid", gridTemplateColumns:"repeat(6,1fr)", gap:10}}>
        {[
          {l:"FY27 Bear Rev", v:"$168B", c:B.red}, {l:"FY27 Base Rev", v:"$215B", c:B.amber}, {l:"FY27 Bull Rev", v:"$258B", c:B.mint},
          {l:"FY27 Bear EPS", v:"$4.10", c:B.red}, {l:"FY27 Base EPS", v:"$5.65", c:B.amber}, {l:"FY27 Bull EPS", v:"$7.20", c:B.mint},
        ].map((k, i) => <KPI key={i} label={k.l} value={k.v} color={k.c}/>)}
      </div>
      <Card>
        <div style={{display:"flex", gap:6, marginBottom:14, alignItems:"center"}}>
          {["revenue","margin","fcf"].map(v => (
            <button key={v} onClick={() => setView(v)} style={{background:view===v ? B.greenBg : "transparent", border:`1px solid ${view===v ? B.mint : B.border}`, color:view===v ? B.mint : B.muted, borderRadius:4, padding:"5px 14px", cursor:"pointer", fontSize:9, fontWeight:700}}>{v.toUpperCase()}</button>
          ))}
          <div style={{marginLeft:"auto", fontSize:9, color:B.muted}}>7-Quarter Forward · Q2'27 → Q4'28</div>
        </div>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={co.fwd} margin={{left:10,right:20,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim}/>
            <XAxis dataKey="q" tick={{fill:B.muted,fontSize:9}} stroke={B.border}/>
            <YAxis domain={view==="revenue"?[40,100]:view==="margin"?[60,80]:[0,60]} tick={{fill:B.muted,fontSize:9}} stroke="none" tickFormatter={(v: any)=>view==="margin"?`${v}%`:`$${v}B`}/>
            <Tooltip content={<ChartTip/>}/>
            <Legend wrapperStyle={{fontSize:9}}/>
            <Line type="monotone" dataKey={view==="revenue"?"actual":view==="margin"?"gmA":"fcfA"} name="Actual" stroke={B.text} strokeWidth={3} connectNulls={false}/>
            <Line type="monotone" dataKey={view==="revenue"?"bull":view==="margin"?"gmBull":"fcfBull"} name="Bull" stroke={B.mint} strokeWidth={1.8} strokeDasharray="5 3" connectNulls={false}/>
            <Line type="monotone" dataKey={view==="revenue"?"base":view==="margin"?"gmBase":"fcfBase"} name="Base" stroke={B.amber} strokeWidth={2.5} connectNulls={false}/>
            <Line type="monotone" dataKey={view==="revenue"?"bear":view==="margin"?"gmBear":"fcfBear"} name="Bear" stroke={B.red} strokeWidth={1.8} strokeDasharray="5 3" connectNulls={false}/>
          </LineChart>
        </ResponsiveContainer>
      </Card>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
        <Card>
          <Lbl sub="P10 / P50 / P90 by fiscal year">REVENUE SCENARIOS — FY27 / FY28 / FY29</Lbl>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={co.revAnnualScen} margin={{left:10,right:20,top:10}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim} vertical={false}/>
              <XAxis dataKey="yr" tick={{fill:B.text, fontSize:10, fontWeight:600}} stroke={B.border}/>
              <YAxis tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={(v: any)=>`$${v}B`}/>
              <Tooltip content={<ChartTip fmt={(v: any)=>`$${v}B`}/>}/>
              <Legend wrapperStyle={{fontSize:9}}/>
              <Bar dataKey="bear" name="Bear (P10)" fill={B.red} radius={[3,3,0,0]}/>
              <Bar dataKey="base" name="Base (P50)" fill={B.amber} radius={[3,3,0,0]}/>
              <Bar dataKey="bull" name="Bull (P90)" fill={B.mint} radius={[3,3,0,0]}/>
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card>
          <Lbl sub="Non-GAAP diluted EPS by fiscal year">EPS SCENARIOS — FY27 / FY28 / FY29</Lbl>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={co.epsScen} margin={{left:10,right:20,top:10}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim} vertical={false}/>
              <XAxis dataKey="yr" tick={{fill:B.text, fontSize:10, fontWeight:600}} stroke={B.border}/>
              <YAxis tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={(v: any)=>`$${v}`}/>
              <Tooltip content={<ChartTip fmt={(v: any)=>`$${v.toFixed(2)}`}/>}/>
              <Legend wrapperStyle={{fontSize:9}}/>
              <Bar dataKey="bear" name="Bear (P10)" fill={B.red} radius={[3,3,0,0]}/>
              <Bar dataKey="base" name="Base (P50)" fill={B.amber} radius={[3,3,0,0]}/>
              <Bar dataKey="bull" name="Bull (P90)" fill={B.mint} radius={[3,3,0,0]}/>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>
      <Card>
        <Lbl sub="Operational levers ranked by absolute Δ on FY27 revenue">SENSITIVITY TORNADO — FY27 REVENUE ($B)</Lbl>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={[...co.sensitivity].sort((a: any, b: any) => a.impact - b.impact)} layout="vertical" margin={{left:14, right:60, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-16,16]} tick={{fill:B.muted, fontSize:10}} stroke={B.border} tickFormatter={(v: any)=>`$${v}B`}/>
            <YAxis type="category" dataKey="lever" tick={{fill:B.text, fontSize:10}} stroke="none" width={200}/>
            <Tooltip content={<ChartTip fmt={(v: any)=>`$${Math.abs(v).toFixed(1)}B`}/>}/>
            <ReferenceLine x={0} stroke={B.borderLt}/>
            <Bar dataKey={(d: any) => -d.impact} name="Downside" fill={B.amber} radius={[3,0,0,3]}/>
            <Bar dataKey="impact" name="Upside" fill={B.mint} radius={[0,3,3,0]}/>
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
        <Card>
          <Lbl sub="Bear / Base / Bull scenario inputs">SCENARIO DRIVER MATRIX</Lbl>
          <table style={{width:"100%", borderCollapse:"collapse", fontSize:10}}>
            <thead>
              <tr style={{color:B.muted, textAlign:"left"}}>
                <th style={{padding:"6px 8px", fontWeight:700, borderBottom:`1px solid ${B.border}`}}>DRIVER</th>
                <th style={{padding:"6px 8px", fontWeight:700, color:B.red, textAlign:"center", borderBottom:`1px solid ${B.border}`}}>BEAR</th>
                <th style={{padding:"6px 8px", fontWeight:700, color:B.amber, textAlign:"center", borderBottom:`1px solid ${B.border}`}}>BASE</th>
                <th style={{padding:"6px 8px", fontWeight:700, color:B.mint, textAlign:"center", borderBottom:`1px solid ${B.border}`}}>BULL</th>
              </tr>
            </thead>
            <tbody>
              {co.scenDrivers.map((d: any, i: number) => (
                <tr key={i} style={{borderBottom:`1px solid ${B.dim}`}}>
                  <td style={{padding:"8px", color:B.text, fontWeight:600}}>{d.driver}</td>
                  <td style={{padding:"8px", color:B.red, textAlign:"center", fontWeight:700}}>{d.bear}</td>
                  <td style={{padding:"8px", color:B.amber, textAlign:"center", fontWeight:700}}>{d.base}</td>
                  <td style={{padding:"8px", color:B.mint, textAlign:"center", fontWeight:700}}>{d.bull}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card>
          <Lbl sub="Three defensive reframings of the equity story" color={B.mint}>IR NARRATIVE PIVOTS</Lbl>
          {co.irPivots.map((p: any, i: number) => (
            <div key={i} style={{borderLeft:`3px solid ${B.mint}`, padding:"6px 0 6px 14px", marginBottom:10}}>
              <div style={{fontSize:11, color:B.text, fontWeight:700, marginBottom:3}}>{p.n} · {p.title}</div>
              <div style={{fontSize:10, color:B.muted, lineHeight:1.5}}>{p.txt}</div>
            </div>
          ))}
        </Card>
      </div>
    </div>
  )
}

function T3_Special({co}: any) {
  return (
    <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub="FY27E revenue distribution | Top-4 hyperscalers ≈ 42%" color={B.mint}>⚡ HYPERSCALER CONCENTRATION</Lbl>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={co.hsBreakdown} layout="vertical" margin={{left:8,right:50,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[0,35]} tick={{fill:B.muted,fontSize:10}} stroke={B.border} tickFormatter={(v: any)=>`${v}%`}/>
            <YAxis type="category" dataKey="item" tick={{fill:B.text,fontSize:10}} stroke="none" width={160}/>
            <Tooltip content={<ChartTip fmt={(v: any)=>`${v}%`}/>}/>
            <ReferenceLine x={10} stroke={B.amber} strokeDasharray="4 4" label={{value:"10% SEC threshold", position:"insideTopRight", fill:B.amber, fontSize:9, fontWeight:700}}/>
            <Bar dataKey="pct" radius={[0,3,3,0]} label={{position:"right", formatter:(v: any)=>`${v}%`, fill:B.muted, fontSize:9}}>
              {co.hsBreakdown.map((d: any, i: number) => <Cell key={i} fill={d.c}/>)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <Card>
        <Lbl sub="6-gen roadmap | ASP $k per accelerator" color={B.mint}>ARCHITECTURE CADENCE</Lbl>
        <div style={{display:"flex", flexDirection:"column", gap:7}}>
          {co.archCadence.map((g: any, i: number) => {
            const c = g.status==="Active"||g.status==="Ramping" ? B.mint : g.status==="Tape-Out"||g.status==="Design" ? B.amber : B.muted
            return (
              <div key={i} style={{display:"grid", gridTemplateColumns:"1.6fr 0.8fr 0.8fr 1.4fr", gap:6, padding:"8px 10px", background:B.bg2, borderRadius:5, border:`1px solid ${c}44`, alignItems:"center"}}>
                <span style={{fontSize:10, color:B.text, fontWeight:700}}>{g.gen}</span>
                <span style={{fontSize:9, color:B.muted}}>Ship: <b style={{color:B.text}}>{g.ship}</b></span>
                <span style={{fontSize:9, color:B.muted}}>ASP: <b style={{color:B.text}}>${g.asp}k</b></span>
                <span style={{fontSize:9, fontWeight:700, color:c, textAlign:"right"}}>{g.status.toUpperCase()}</span>
              </div>
            )
          })}
        </div>
      </Card>
      <Card>
        <Lbl sub="Hyperscaler captive accelerator threat" color={B.red}>ASIC SUBSTITUTION RISK</Lbl>
        {[
          {co:"Google TPU v7",risk:9.2,share:"~22% GCP"},
          {co:"AWS Trainium v3",risk:8.5,share:"~14% AWS"},
          {co:"Meta MTIA v3",risk:7.8,share:"~10% Meta"},
          {co:"Microsoft Maia v2",risk:6.5,share:"~4% Azure"},
          {co:"Tesla Dojo D2",risk:5.2,share:"Internal"},
        ].map((a, i) => (
          <div key={i} style={{padding:"8px 12px", background:B.bg2, borderRadius:5, border:`1px solid ${B.red}33`, marginBottom:7}}>
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:5}}>
              <span style={{fontSize:10, color:B.text, fontWeight:700}}>{a.co}</span>
              <span style={{fontSize:10, color:B.red, fontWeight:800}}>{a.risk}/10</span>
            </div>
            <div style={{fontSize:8.5, color:B.muted, marginBottom:5}}>Captive: <b style={{color:B.text}}>{a.share}</b></div>
            <div style={{background:B.dim, borderRadius:2, height:5}}>
              <div style={{background:B.red, borderRadius:2, height:"100%", width:`${a.risk*10}%`}}/>
            </div>
          </div>
        ))}
      </Card>
    </div>
  )
}

function T4_Rag({co}: any) {
  const [dom, setDom] = useState("fin")
  const doms: any = {fin:{label:"Financial",data:co.ragFin,c:B.mint},ops:{label:"Operational",data:co.ragOps,c:B.amber},comp:{label:"Compliance",data:co.ragComp,c:B.red}}
  const cur = doms[dom]
  const all = [...co.ragFin, ...co.ragOps, ...co.ragComp]
  const cnt = (v: any) => all.flatMap((r: any) => r.v).filter((x: any) => x === v).length
  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <div style={{display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:10}}>
        <KPI label="RED Cells" value={cnt(0)} color={B.red} sub="Immediate action"/>
        <KPI label="AMBER Cells" value={cnt(1)} color={B.amber} sub="Elevated monitoring"/>
        <KPI label="GREEN Cells" value={cnt(2)} color={B.mint} sub="Within tolerance"/>
      </div>
      <Card>
        <div style={{display:"flex", gap:8, marginBottom:16}}>
          {Object.entries(doms).map(([k, v]: any) => (
            <button key={k} onClick={()=>setDom(k)} style={{background:dom===k?v.c+"22":"transparent", border:`1px solid ${dom===k?v.c:B.border}`, color:dom===k?v.c:B.muted, borderRadius:4, padding:"5px 14px", cursor:"pointer", fontSize:9, fontWeight:700}}>{v.label.toUpperCase()}</button>
          ))}
        </div>
        <RagGrid data={cur.data} qs={co.qs}/>
      </Card>
    </div>
  )
}

function T5_China({co}: any) {
  return (
    <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
      <Card>
        <Lbl sub="Revenue distribution | 10% = SEC threshold">GEOGRAPHIC CONCENTRATION</Lbl>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={co.china} layout="vertical" margin={{left:8,right:50,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[0,50]} tick={{fill:B.muted,fontSize:10}} stroke={B.border} tickFormatter={(v: any)=>`${v}%`}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text,fontSize:10}} stroke="none" width={160}/>
            <Tooltip content={<ChartTip fmt={(v: any)=>`${v}%`}/>}/>
            <ReferenceLine x={10} stroke={B.amber} strokeDasharray="5 4"/>
            <Bar dataKey="pct" radius={[0,3,3,0]} label={{position:"right", formatter:(v: any)=>`${v}%`, fill:B.muted, fontSize:9}}>
              {co.china.map((d: any, i: number) => <Cell key={i} fill={RC[d.f]}/>)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <Card>
        <Lbl sub="Chinese AI accelerator parity vs Hopper-class" color={B.red}>CHINA LOCAL THREAT TRACKER</Lbl>
        {co.localThreat.map((c: any, i: number) => (
          <div key={i} style={{marginBottom:12}}>
            <div style={{display:"flex", justifyContent:"space-between", fontSize:10, marginBottom:4}}>
              <span><span style={{color:co.accent, fontWeight:700}}>{c.name}</span><span style={{color:B.muted}}> · {c.seg}</span></span>
              <span style={{color:B.red, fontWeight:700}}>${c.risk}M</span>
            </div>
            <div style={{background:B.dim, borderRadius:3, height:10, position:"relative"}}>
              <div style={{background:B.amber+"DD", borderRadius:3, height:"100%", width:`${c.now}%`, position:"absolute"}}/>
              <div style={{background:B.red+"55", borderRadius:3, height:"100%", width:`${c.y27}%`, position:"absolute"}}/>
            </div>
            <div style={{display:"flex", justifyContent:"space-between", fontSize:8.5, color:B.muted, marginTop:3}}>
              <span style={{color:B.amber}}>Now: {c.now}%</span>
              <span style={{color:B.red}}>Q4'28: {c.y27}%</span>
            </div>
          </div>
        ))}
      </Card>
    </div>
  )
}

function T6_Capex({co}: any) {
  const peerSorted = [...co.capexPeer].sort((a: any, b: any) => a.r - b.r)
  return (
    <Card>
      <Lbl sub={`${co.ticker} R&D/Rev: ${co.rdRatio} | Fabless — R&D substitutes for CapEx`}>R&amp;D INTENSITY — PEER COMPARISON</Lbl>
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={peerSorted} layout="vertical" margin={{left:8,right:45,top:4}}>
          <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
          <XAxis type="number" domain={[0,45]} tick={{fill:B.muted,fontSize:10}} stroke={B.border} tickFormatter={(v: any)=>`${v}%`}/>
          <YAxis type="category" dataKey="co" tick={{fill:B.text,fontSize:11, fontWeight:600}} stroke="none" width={60}/>
          <Tooltip content={<ChartTip fmt={(v: any)=>`${v}%`}/>}/>
          <ReferenceLine x={15} stroke={B.mint} strokeDasharray="5 4" label={{value:"BENCHMARK", position:"insideTopRight", fill:B.mint, fontSize:9, fontWeight:700}}/>
          <Bar dataKey="r" radius={[0,3,3,0]} label={{position:"right", formatter:(v: any)=>`${v}%`, fill:B.muted, fontSize:9}}>
            {peerSorted.map((d: any, i: number) => <Cell key={i} fill={d.co.includes("★")?co.accent:B.muted+"77"}/>)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

function T7_Macro({co}: any) {
  const greenCnt = co.macro.filter((m: any) => m.sig === 2).length
  return (
    <Card>
      <Lbl sub={`AI / datacenter leading indicators | Q2 2026 | ${greenCnt} of 8 GREEN`}>MACRO LEADING INDICATORS</Lbl>
      <div style={{display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:10}}>
        {co.macro.map((m: any, i: number) => (
          <div key={i} style={{background:B.bg2, border:`1px solid ${RC[m.sig]}55`, borderLeft:`3px solid ${RC[m.sig]}`, borderRadius:6, padding:"10px 12px"}}>
            <div style={{fontSize:8.5, color:RC[m.sig], textTransform:"uppercase", marginBottom:5, fontWeight:800}}>{m.sig===2?"GREEN":m.sig===1?"AMBER":"RED"}</div>
            <div style={{fontSize:9.5, color:B.text, fontWeight:700, marginBottom:4}}>{m.n}</div>
            <div style={{fontSize:11, color:B.text, marginBottom:6}}>{m.val}</div>
            <div style={{fontSize:8.5, color:B.muted, marginBottom:5}}>r = {m.corr>0?"+":""}{m.corr} · {m.lead}Q lead</div>
            <div style={{background:B.dim, borderRadius:2, height:4}}>
              <div style={{background:RC[m.sig], borderRadius:2, height:"100%", width:`${Math.abs(m.corr)*100}%`}}/>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function T8_CIO({co}: any) {
  const groups = ["IP Theft","Espionage","Agentic Drift","Systemic Cyber"]
  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <div style={{display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:10}}>
        {groups.map((g, i) => {
          const items = co.threats.filter((t: any) => t.cls === g)
          const redCnt = items.filter((t: any) => t.rag === 0).length
          return <KPI key={i} label={g} value={`${redCnt} RED`} color={redCnt >= 1 ? B.red : B.amber} sub={`${items.length} vectors`}/>
        })}
      </div>
      <Card>
        <Lbl sub="12 threat vectors across 4 classes | RAG composite by likelihood × impact" color={B.red}>THREAT VECTOR REGISTER</Lbl>
        <table style={{width:"100%", borderCollapse:"collapse", fontSize:10}}>
          <thead>
            <tr style={{color:B.muted, textAlign:"left"}}>
              <th style={{padding:"6px 8px", fontWeight:700, borderBottom:`1px solid ${B.border}`}}>CLASS</th>
              <th style={{padding:"6px 8px", fontWeight:700, borderBottom:`1px solid ${B.border}`}}>VECTOR</th>
              <th style={{padding:"6px 8px", fontWeight:700, textAlign:"center", borderBottom:`1px solid ${B.border}`}}>LIKELIHOOD</th>
              <th style={{padding:"6px 8px", fontWeight:700, textAlign:"center", borderBottom:`1px solid ${B.border}`}}>IMPACT</th>
              <th style={{padding:"6px 8px", fontWeight:700, textAlign:"center", borderBottom:`1px solid ${B.border}`}}>RAG</th>
            </tr>
          </thead>
          <tbody>
            {co.threats.map((t: any, i: number) => (
              <tr key={i} style={{borderBottom:`1px solid ${B.dim}`}}>
                <td style={{padding:"7px 8px"}}><Tag color={B.mint}>{t.cls}</Tag></td>
                <td style={{padding:"7px 8px", color:B.text}}>{t.v}</td>
                <td style={{padding:"7px 8px", textAlign:"center", color:B.muted}}>{t.lk}</td>
                <td style={{padding:"7px 8px", textAlign:"center", color:B.muted}}>{t.imp}</td>
                <td style={{padding:"7px 8px", textAlign:"center"}}><RagCell val={2-t.rag}/></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card>
        <Lbl sub="P0 / P1 / P2 prioritization" color={B.mint}>RECOMMENDED GUARDRAILS</Lbl>
        <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:8}}>
          {co.guardrails.map((g: any, i: number) => {
            const c = g.pri==="P0" ? B.red : g.pri==="P1" ? B.amber : B.mint
            return (
              <div key={i} style={{padding:"10px 12px", background:B.bg2, borderRadius:5, border:`1px solid ${c}55`, borderLeft:`3px solid ${c}`}}>
                <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:5}}>
                  <span style={{fontSize:10.5, color:B.text, fontWeight:700}}>{g.ctrl}</span>
                  <Tag color={c}>{g.pri}</Tag>
                </div>
                <div style={{fontSize:9, color:B.muted}}>Domain: <b style={{color:B.text}}>{g.dom}</b></div>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}

function T9_Audit({co}: any) {
  const [hov, setHov] = useState<number | null>(null)
  const W = 720, H = 320
  const toX = (d: number) => 60 + ((d-1)/9) * (W-100)
  const toY = (d: number) => H - 30 - ((d-5)/5) * (H-70)
  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <Card>
        <Lbl sub="X: Detectability | Y: Impact | Hover for detail">AUDIT PRIORITY MATRIX</Lbl>
        <div style={{display:"flex", gap:16}}>
          <div style={{flex:1}}>
            <svg width="100%" viewBox={`0 0 ${W} ${H}`}>
              <rect x={60} y={20} width={(W-100)/2} height={(H-50)/2} fill={B.red} opacity={0.06} rx={3}/>
              <rect x={60+(W-100)/2} y={20} width={(W-100)/2} height={(H-50)/2} fill={B.amber} opacity={0.06} rx={3}/>
              <text x={175} y={40} textAnchor="middle" fill={B.red} fontSize={9} opacity={0.75} fontWeight={700}>HIGH IMPACT / HARD TO DETECT → Act Now</text>
              <text x={565} y={40} textAnchor="middle" fill={B.amber} fontSize={9} opacity={0.75} fontWeight={700}>HIGH IMPACT / EASY TO DETECT</text>
              <line x1={60+(W-100)/2} y1={16} x2={60+(W-100)/2} y2={H-20} stroke={B.borderLt} strokeDasharray="4 4"/>
              <line x1={55} y1={20+(H-50)/2} x2={W-30} y2={20+(H-50)/2} stroke={B.borderLt} strokeDasharray="4 4"/>
              {co.audit.map((a: any, i: number) => {
                const x = toX(a.detect), y = toY(a.impact), c = RC[a.urg], isH = hov === i
                return (
                  <g key={i} style={{cursor:"pointer"}} onMouseEnter={()=>setHov(i)} onMouseLeave={()=>setHov(null)}>
                    <circle cx={x} cy={y} r={isH?19:14} fill={c+"33"} stroke={c} strokeWidth={isH?2.5:1.5}/>
                    <text x={x} y={y+4} textAnchor="middle" fill={c} fontSize={9} fontWeight={800}>{a.ref}</text>
                  </g>
                )
              })}
            </svg>
          </div>
          <div style={{width:220, flexShrink:0}}>
            {hov !== null ? (
              <div style={{background:B.bg2, border:`1.5px solid ${RC[co.audit[hov].urg]}`, borderRadius:6, padding:"12px 14px", fontSize:10}}>
                <div style={{color:RC[co.audit[hov].urg], fontWeight:800, marginBottom:6, fontSize:11}}>ATV-{co.audit[hov].ref}</div>
                <div style={{color:B.text, marginBottom:8, fontWeight:600}}>{co.audit[hov].title}</div>
                <div style={{color:B.muted, marginBottom:3}}>Domain: <b style={{color:B.text}}>{co.audit[hov].domain}</b></div>
                <div style={{color:B.muted, marginBottom:3}}>Impact: <b style={{color:B.text}}>{co.audit[hov].impact}/10</b></div>
                <div style={{color:B.muted}}>Detectability: <b style={{color:B.text}}>{co.audit[hov].detect}/10</b></div>
                <div style={{marginTop:8}}><Tag color={RC[co.audit[hov].urg]}>{["IMMEDIATE","ELEVATED","ROUTINE"][co.audit[hov].urg]}</Tag></div>
              </div>
            ) : (
              <div style={{background:B.bg2, border:`1px solid ${B.border}`, borderRadius:6, padding:"12px 14px", fontSize:9.5, color:B.muted}}>
                Hover bubble for detail.
                <div style={{marginTop:14, display:"flex", flexDirection:"column", gap:7}}>
                  <div style={{color:B.red, fontWeight:700}}>● IMMEDIATE: {co.audit.filter((a: any)=>a.urg===0).length}</div>
                  <div style={{color:B.amber, fontWeight:700}}>● ELEVATED: {co.audit.filter((a: any)=>a.urg===1).length}</div>
                  <div style={{color:B.mint, fontWeight:700}}>● ROUTINE: {co.audit.filter((a: any)=>a.urg===2).length}</div>
                </div>
              </div>
            )}
          </div>
        </div>
      </Card>
      <Card>
        <Lbl sub="Highest impact + lowest detectability" color={B.red}>IMMEDIATE-ACTION VULNERABILITY REGISTER</Lbl>
        <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:8}}>
          {co.audit.filter((a: any)=>a.urg===0).map((a: any, i: number) => (
            <div key={i} style={{display:"flex", gap:10, alignItems:"flex-start", padding:"10px 12px", borderRadius:6, background:B.redBg, border:`1px solid ${B.red}55`}}>
              <div style={{minWidth:44, textAlign:"center"}}>
                <div style={{color:B.red, fontSize:7, fontWeight:700}}>ATV</div>
                <div style={{color:B.red, fontSize:14, fontWeight:800}}>{a.ref}</div>
              </div>
              <div style={{flex:1}}>
                <div style={{color:B.text, fontSize:10.5, fontWeight:700, marginBottom:3, lineHeight:1.3}}>{a.title}</div>
                <div style={{display:"flex", gap:6, fontSize:8.5, color:B.muted}}>
                  <span>{a.domain}</span><span>·</span><span>Imp {a.impact}</span><span>·</span><span>Det {a.detect}</span>
                </div>
              </div>
              <Tag color={B.red}>IMMEDIATE</Tag>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

function T10_PreMortem({co}: any) {
  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <Card>
        <Lbl sub="The load-bearing assumption — if broken, the entire bull case collapses" color={B.red}>CRITICAL GREEN ASSUMPTION SELECTED FOR INVERSION</Lbl>
        <div style={{padding:"14px 18px", background:B.greenBg, borderLeft:`4px solid ${B.mint}`, borderRadius:5, fontSize:13, color:B.text, lineHeight:1.55, fontStyle:"italic"}}>
          "Hyperscaler AI capex grows +18% in FY27 (Base Case), and TSMC CoWoS capacity scales to meet Blackwell/Rubin demand without disruption."
        </div>
        <div style={{marginTop:10, fontSize:10.5, color:B.muted, lineHeight:1.5}}>This single assumption underpins the Base-Case revenue stack, the Altman Z-Score's market-cap dominance, and the 3Y "AI Factory" IR narrative. <b style={{color:B.text}}>It is the load-bearing wall.</b></div>
      </Card>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
        <Card>
          <Lbl sub="Modeled sequence — joint occurrence is the risk">90-DAY FAILURE CASCADE</Lbl>
          <div style={{position:"relative", paddingLeft:26}}>
            <div style={{position:"absolute", left:8, top:8, bottom:8, width:2, background:B.border}}/>
            {co.cascade.map((s: any, i: number) => (
              <div key={i} style={{position:"relative", marginBottom:16}}>
                <div style={{position:"absolute", left:-22, top:6, width:12, height:12, borderRadius:6, background:s.c, boxShadow:`0 0 0 3px ${B.bg}, 0 0 0 4px ${s.c}66`}}/>
                <div style={{fontSize:9, color:B.muted, textTransform:"uppercase", letterSpacing:"0.08em", fontWeight:700, marginBottom:2}}>{s.d}</div>
                <div style={{fontSize:11, color:B.text, lineHeight:1.45}}>{s.txt}</div>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <Lbl sub="Six load-bearing risk cells | RAG state shift">BEFORE vs AFTER · DAY 90</Lbl>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={co.premortem} margin={{left:10, right:20, top:10}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim} vertical={false}/>
              <XAxis dataKey="label" tick={{fill:B.text, fontSize:9}} stroke={B.border} angle={-12} textAnchor="end" height={64} interval={0}/>
              <YAxis domain={[0,3]} ticks={[0,1,2,3]} tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={(v: any)=>["","GRN","AMB","RED"][v]||""}/>
              <Tooltip content={<ChartTip fmt={(v: any)=>["","Green","Amber","Red"][v]}/>}/>
              <Bar dataKey="before" name="Before" fill={B.amber} radius={[3,3,0,0]}/>
              <Bar dataKey="after" name="After 90d" fill={B.red} radius={[3,3,0,0]}/>
              <Legend wrapperStyle={{fontSize:9}}/>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>
      <Card>
        <Lbl sub="Pre-position now to compress response time at incident onset" color={B.mint}>DEFENSIVE PRE-POSITIONING — ACTIONABLE NOW</Lbl>
        <div style={{display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:12}}>
          {co.defense.map((d: any, i: number) => (
            <div key={i} style={{padding:"12px 14px", background:B.bg2, borderLeft:`3px solid ${d.c}`, borderRadius:5}}>
              <Tag color={d.c}>{d.role}</Tag>
              <div style={{fontSize:10.5, color:B.text, marginTop:8, lineHeight:1.5}}>{d.txt}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

const TABS = [
  {label:"Overview",icon:"▣"},{label:"Forensic",icon:"⊕"},
  {label:"Forward / CFO",icon:"▲"},{label:"AI Concentration",icon:"⚡"},
  {label:"RAG Matrix",icon:"▦"},{label:"Geo",icon:"◐"},
  {label:"R&D",icon:"◇"},{label:"Macro",icon:"⟿"},
  {label:"CIO / Cyber",icon:"◈"},{label:"Audit",icon:"⚠"},{label:"Pre-Mortem",icon:"⌖"},
]

export default function App() {
  const co = NVDA
  const [tab, setTab] = useState(0)
  const tabContent: any = {
    0:<T0_Overview co={co}/>, 1:<T1_Forensic co={co}/>,
    2:<T2_Forward co={co}/>,  3:<T3_Special co={co}/>,
    4:<T4_Rag co={co}/>,      5:<T5_China co={co}/>,
    6:<T6_Capex co={co}/>,    7:<T7_Macro co={co}/>,
    8:<T8_CIO co={co}/>,      9:<T9_Audit co={co}/>,
    10:<T10_PreMortem co={co}/>,
  }
  return (
    <div style={{background:B.bg, minHeight:"100vh", fontFamily:"'IBM Plex Mono','Courier New',monospace", color:B.text}}>
      <div style={{background:B.card, borderBottom:`1px solid ${B.border}`, padding:"14px 24px", display:"flex", justifyContent:"space-between", alignItems:"center"}}>
        <div>
          <div style={{color:B.mint, fontSize:9, letterSpacing:"0.26em", textTransform:"uppercase", marginBottom:4, fontWeight:700}}>▸ DENDRAI // RISK & INTELLIGENCE</div>
          <div style={{fontSize:18, fontWeight:700, color:B.text}}>{co.name} ({co.ticker}) — Tri-Modal Risk Dashboard</div>
          <div style={{fontSize:10, color:B.muted, marginTop:3}}>Audit / ERM · CFO · CIO/CISO | AI Infrastructure Cohort | Through Q4 FY28</div>
        </div>
        <span style={{background:co.accent+"22", border:`1.5px solid ${co.accent}`, color:co.accent, borderRadius:4, padding:"5px 18px", fontSize:12, fontWeight:800, letterSpacing:"0.10em"}}>{co.ticker}</span>
      </div>
      <div style={{background:B.bg2, borderBottom:`1px solid ${B.border}`, padding:"8px 24px", display:"flex", gap:22, overflowX:"auto"}}>
        {[
          {l:"M-Score",v:co.mScore.toString(),c:B.amber},
          {l:"Z-Score",v:co.zScore.toString(),c:B.mint},
          {l:"Top-4 HS",v:co.hsExp,c:B.red},
          {l:"China",v:co.chinaExp,c:B.amber},
          {l:"R&D/Rev",v:co.rdRatio,c:B.mint},
          {l:"GM TTM",v:co.gmTtm,c:B.mint},
          {l:"AQI",v:co.aqi.toString(),c:B.amber},
        ].map((s, i) => (
          <div key={i} style={{display:"flex", flexDirection:"column"}}>
            <div style={{fontSize:8, color:B.muted, letterSpacing:"0.12em", textTransform:"uppercase", fontWeight:600}}>{s.l}</div>
            <div style={{fontSize:12, fontWeight:800, color:s.c}}>{s.v}</div>
          </div>
        ))}
      </div>
      <div style={{display:"flex", gap:3, padding:"10px 24px", background:B.card, borderBottom:`1px solid ${B.border}`, overflowX:"auto"}}>
        {TABS.map((t, i) => (
          <button key={i} onClick={()=>setTab(i)} style={{background:tab===i?B.greenBg:"transparent", border:`1px solid ${tab===i?co.accent:B.border}`, color:tab===i?B.text:B.muted, borderRadius:4, padding:"6px 14px", cursor:"pointer", fontSize:9.5, fontWeight:700, whiteSpace:"nowrap"}}>{t.icon} {t.label.toUpperCase()}</button>
        ))}
      </div>
      <div style={{padding:"18px 24px 60px"}}>{tabContent[tab]}</div>
    </div>
  )
}
