import { useState, useEffect } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, LineChart, Line,
  Cell, Legend, ComposedChart
} from "recharts";

// ═══════════════════════ BRAND ══════════════════════════════
const B = {
  mint:"#3DFFC0", ivory:"#E8F5F0",
  bg:"#070E0A",   bg2:"#0B1610",    card:"#0E1A12",
  border:"#182C1E", borderLt:"#253E2A", 
  text:"#C4DFD0",  muted:"#3E6E52",  dim:"#1C3023",
  red:"#FF4444",   redBg:"#1A0808",
  amber:"#FFB020", amberBg:"#1C1300",
  greenBg:"#062010",
  sic:"#8B7FFF",
};
const RC=[B.red,B.amber,B.mint];
const RBG=[B.redBg,B.amberBg,B.greenBg];
const RL=["RED","AMB","GRN"];

// ═══════════════════════ COMPANY DATA ═══════════════════════

const SECTOR=[
  {co:"WOLF", d:-71.2,color:B.red+"CC"},
  {co:"MCHP", d:-45.8,color:B.red+"99"},
  {co:"TXN",  d:-21.9,color:B.amber},
  {co:"ADI",  d:-21.5,color:B.amber+"99"},
  {co:"STM",  d:-17.7,color:B.amber+"77"},
  {co:"ON",   d:-15.1,color:B.amber+"55"},
  {co:"NXPI", d:-4.5, color:B.muted},
  {co:"RNS",  d:3.9,  color:B.mint+"77"},
  {co:"IFX",  d:5.2,  color:B.mint+"99"},
];

const COMPANIES = {
  TXN: {
    name:"Texas Instruments", ticker:"TXN", accent:B.mint,
    mScore:-2.659, mScorePrev:-2.696, mScoreFlag:"LOW RISK",
    zScore:6.57,   zScorePrev:6.55,
    autoExp:"~20%", chinaExp:"~20%", capexRatio:"32%",
    aqi:0.945, tata:-0.039,
    headline:"IDM Super-Cycle | CapEx Inversion | China BIS Watch",

    mScorePeers:[
      {co:"RNS",  s:-3.22,f:2},{co:"IFX",  s:-3.12,f:2},
      {co:"NXPI", s:-2.88,f:2},{co:"TXN★", s:-2.66,f:2},
      {co:"ON",   s:-2.45,f:2},{co:"STM",  s:-2.38,f:1},
      {co:"ADI",  s:-2.32,f:0},{co:"MCHP", s:-1.90,f:0},
    ],
    zScorePeers:[
      {co:"MCHP",s:2.45,z:1},{co:"STM",s:3.35,z:2},{co:"ADI",s:3.60,z:2},
      {co:"ON",  s:4.20,z:2},{co:"RNS",s:4.80,z:2},{co:"IFX",s:4.87,z:2},
      {co:"NXPI",s:5.40,z:2},{co:"TXN★",s:6.57,z:3},
    ],
    mComp:[
      {v:"SGI",  c:0.970,pos:true, note:"Revenue recovery SGI>1"},
      {v:"DSRI", c:0.916,pos:true, note:"AR days stable"},
      {v:"GMI",  c:0.506,pos:true, note:"↑ Margins recovering (GMI<1 now)"},
      {v:"AQI",  c:0.382,pos:true, note:"Asset intensity stable"},
      {v:"DEPI", c:0.124,pos:true, note:"Depreciation rate marginal"},
      {v:"TATA", c:-0.183,pos:false,note:"✓ Cash > accruals"},
      {v:"SGAI", c:-0.174,pos:false,note:"✓ SG&A controlled"},
      {v:"LVGI", c:-0.360,pos:false,note:"Leverage creeping — watch"},
    ],

    // Normalized FWD: all in one array, nulls where inapplicable
    fwd:[
      {q:"Q1'26A",actual:4.17, gmA:59.0,fcfA:0.31},
      {q:"Q2'26", bear:4.00,base:4.45,bull:4.90, gmBear:58,gmBase:61,gmBull:65, fcfBear:-0.1,fcfBase:0.6,fcfBull:1.2},
      {q:"Q3'26", bear:4.10,base:4.75,bull:5.30, gmBear:59,gmBase:63,gmBull:67, fcfBear:0.1, fcfBase:0.9,fcfBull:1.6},
      {q:"Q4'26", bear:4.20,base:5.00,bull:5.60, gmBear:59,gmBase:65,gmBull:68, fcfBear:0.2, fcfBase:1.3,fcfBull:2.0},
      {q:"Q1'27", bear:4.10,base:5.00,bull:5.80, gmBear:59,gmBase:65,gmBull:68, fcfBear:0.2, fcfBase:1.2,fcfBull:2.1},
      {q:"Q2'27", bear:4.30,base:5.25,bull:6.10, gmBear:60,gmBase:66,gmBull:69, fcfBear:0.4, fcfBase:1.4,fcfBull:2.4},
      {q:"Q3'27", bear:4.40,base:5.50,bull:6.30, gmBear:61,gmBase:67,gmBull:70, fcfBear:0.5, fcfBase:1.6,fcfBull:2.6},
      {q:"Q4'27", bear:4.50,base:5.75,bull:6.50, gmBear:62,gmBase:68,gmBull:71, fcfBear:0.7, fcfBase:1.8,fcfBull:2.9},
    ],
    annual:[
      {yr:"FY22",rev:20.03,gm:69.4,fcf:8.5,capex:2.5},
      {yr:"FY23",rev:17.52,gm:65.5,fcf:5.0,capex:4.0},
      {yr:"FY24",rev:15.64,gm:58.5,fcf:1.4,capex:4.9},
      {yr:"FY25E",rev:17.0, gm:61.0,fcf:2.8,capex:5.0},
      {yr:"FY26E",rev:18.5, gm:63.0,fcf:4.5,capex:4.3},
      {yr:"FY27E",rev:21.5, gm:66.0,fcf:7.0,capex:3.5},
    ],

    qs:["Q2'26","Q3'26","Q4'26","Q1'27","Q2'27","Q3'27","Q4'27"],
    ragFin:[
      {r:"CapEx Burn vs FCF",       v:[1,1,2,2,2,2,2],vel:"up",vl:"IMPROVING"},
      {r:"Gross Margin Recovery",   v:[2,2,2,2,2,2,2],vel:"up",vl:"RECOVERED"},
      {r:"Dividend Sustainability",  v:[1,2,2,2,2,2,2],vel:"up",vl:"IMPROVING"},
      {r:"Debt Covenant Headroom",   v:[2,2,2,2,2,2,2],vel:"st",vl:"STABLE"},
      {r:"Revenue Concentration",   v:[1,1,2,2,2,2,2],vel:"up",vl:"IMPROVING"},
      {r:"CHIPS Act Subsidy",        v:[2,2,2,2,2,2,2],vel:"up",vl:"RESOLVED"},
    ],
    ragOps:[
      {r:"Fab Utilization Rate",       v:[1,2,2,2,2,2,2],vel:"up",vl:"RECOVERING"},
      {r:"Industrial End-Market",      v:[2,2,2,2,2,2,2],vel:"up",vl:"RECOVERED"},
      {r:"Inventory Destocking",       v:[2,2,2,2,2,2,2],vel:"up",vl:"NORMALIZED"},
      {r:"Node Transition Risk",       v:[1,1,2,2,2,2,2],vel:"up",vl:"PROGRESSING"},
      {r:"ERP / IT Systems",           v:[1,1,2,2,2,2,2],vel:"up",vl:"PROGRESSING"},
      {r:"Supply Chain Single-Source", v:[1,1,1,1,1,1,1],vel:"dn",vl:"PERSISTENT"},
    ],
    ragComp:[
      {r:"BIS Export Controls",      v:[0,1,1,1,1,1,1],vel:"dn",vl:"PERSISTENT"},
      {r:"China Revenue Re-routing", v:[1,1,1,1,1,1,1],vel:"dn",vl:"PERSISTENT"},
      {r:"CHIPS Act Compliance",     v:[2,2,2,2,2,2,2],vel:"up",vl:"RESOLVED"},
      {r:"SEC Climate Disclosure",   v:[1,1,1,2,2,2,2],vel:"up",vl:"IMPROVING"},
      {r:"IP / Patent Litigation",   v:[2,2,2,2,2,2,2],vel:"st",vl:"CLEAR"},
    ],

    china:[
      {co:"NXPI",pct:35,f:0},{co:"IFX",pct:28,f:0},{co:"TXN★",pct:20,f:0},
      {co:"ON",  pct:20,f:1},{co:"RNS",pct:15,f:1},{co:"ADI", pct:15,f:1},
      {co:"MCHP",pct:15,f:1},{co:"STM", pct:12,f:2},
    ],
    localThreat:[
      {name:"Will Semi",  seg:"Power Mgmt",      now:70,y27:85,risk:750},
      {name:"Novosense",  seg:"Signal Chain",    now:75,y27:88,risk:500},
      {name:"3Peak",      seg:"Amplifiers",      now:80,y27:92,risk:275},
      {name:"Chipsea",    seg:"ADC / DAC",       now:72,y27:87,risk:275},
      {name:"SiEn IC",    seg:"General Purpose", now:65,y27:80,risk:400},
    ],
    chinaAtRisk:"$1.85B – $3.0B",

    capexPeer:[
      {co:"MCHP",r:8},{co:"ADI",r:10},{co:"RNS",r:15},{co:"NXPI",r:15},
      {co:"STM",r:22},{co:"IFX",r:25},{co:"ON",r:25},{co:"TXN★",r:32},
    ],
    capexConv:[
      {q:"Q2'26",co:25.3,avg:13.5},{q:"Q3'26",co:23.4,avg:13.2},
      {q:"Q4'26",co:21.4,avg:13.0},{q:"Q1'27",co:19.5,avg:12.8},
      {q:"Q2'27",co:17.8,avg:12.8},{q:"Q3'27",co:16.8,avg:13.0},
      {q:"Q4'27",co:16.4,avg:13.0},
    ],
    capexLabel:"TXN CapEx/Rev",

    macro:[
      {n:"Philadelphia Fed Semi", corr:0.88,lead:1,sig:2,val:"Recovering +4.1%"},
      {n:"ISM Manufacturing PMI", corr:0.81,lead:2,sig:2,val:"50.8 — Expansionary"},
      {n:"US Industrial Production",corr:0.79,lead:1,sig:2,val:"+3.4% YoY"},
      {n:"Capacity Utilization",  corr:0.74,lead:2,sig:1,val:"78.9% (near 80%)"},
      {n:"Global Auto Production",corr:0.71,lead:2,sig:1,val:"Stabilizing +1.2%"},
      {n:"Real Private Fixed Invest.",corr:0.69,lead:3,sig:1,val:"Positive, firming"},
      {n:"China Industrial Output",corr:0.66,lead:1,sig:2,val:"+6.1% YoY"},
      {n:"Trade-Weighted USD",    corr:-0.62,lead:1,sig:1,val:"Softening (tailwind)"},
    ],

    audit:[
      {ref:"I01",title:"Revenue Channel Stuffing",  impact:9.2,detect:4.5,urg:0,domain:"Revenue"},
      {ref:"I02",title:"China Revenue Attribution", impact:9.0,detect:3.8,urg:0,domain:"Revenue"},
      {ref:"02", title:"CHIPS Act Grant Accounting",impact:8.5,detect:3.5,urg:0,domain:"Compliance"},
      {ref:"04", title:"EAR Classification Review", impact:8.2,detect:4.0,urg:0,domain:"Compliance"},
      {ref:"I03",title:"CapEx Impairment Trigger",  impact:8.0,detect:6.0,urg:0,domain:"CapEx"},
      {ref:"01", title:"CapEx Capitalization",      impact:7.5,detect:5.0,urg:0,domain:"CapEx"},
      {ref:"I06",title:"CHIPS Guardrail Compliance",impact:7.2,detect:4.2,urg:1,domain:"Compliance"},
      {ref:"03", title:"ASC 606 Dist. Reserve",     impact:7.0,detect:5.5,urg:1,domain:"Revenue"},
      {ref:"I04",title:"Warranty Reserve Benchmark",impact:6.5,detect:7.0,urg:1,domain:"Ops"},
      {ref:"I05",title:"Transfer Pricing / BEPS",   impact:6.0,detect:6.2,urg:1,domain:"Tax"},
      {ref:"07", title:"Inventory OH Absorption",   impact:6.0,detect:5.2,urg:1,domain:"Inventory"},
      {ref:"06", title:"Fab Depreciation Estimates",impact:5.8,detect:8.2,urg:2,domain:"CapEx"},
    ],

    specialType:"capex",
    divBridge:[
      {item:"Annual Dividend",    val:-4.4,type:"out"},
      {item:"CapEx (FY26E)",      val:-4.3,type:"out"},
      {item:"CFO (FY26E Base)",   val:7.8, type:"in"},
      {item:"Net FCF",            val:-0.9,type:"net"},
    ],
    divBridge27:[
      {item:"Annual Dividend",    val:-4.6,type:"out"},
      {item:"CapEx (FY27E)",      val:-3.5,type:"out"},
      {item:"CFO (FY27E Base)",   val:10.5,type:"in"},
      {item:"Net FCF",            val:2.4, type:"net"},
    ],
  },

  ON: {
    name:"onsemi", ticker:"ON", accent:B.sic,
    mScore:-2.738, mScorePrev:-2.740, mScoreFlag:"AQI FLAG",
    zScore:4.08,   zScorePrev:4.05,
    autoExp:"~55%", chinaExp:"~23%", capexRatio:"21%",
    aqi:1.214, tata:-0.064,
    headline:"SiC Ramp | EV Demand Recovery | AQI Goodwill Flag | BYD Exposure",

    mScorePeers:[
      {co:"RNS",  s:-3.22,f:2},{co:"IFX",  s:-3.12,f:2},
      {co:"NXPI", s:-2.88,f:2},{co:"ON★",  s:-2.74,f:2},
      {co:"TXN",  s:-2.66,f:2},{co:"STM",  s:-2.38,f:1},
      {co:"MCHP", s:-1.84,f:0},{co:"WOLF", s:-1.52,f:0},
    ],
    zScorePeers:[
      {co:"WOLF", s:1.31,z:0},{co:"MCHP",s:2.31,z:1},{co:"STM",s:3.11,z:2},
      {co:"TXN",  s:3.44,z:2},{co:"ON★", s:4.08,z:2},{co:"RNS",s:4.63,z:2},
      {co:"IFX",  s:4.87,z:2},{co:"NXPI",s:5.21,z:2},
    ],
    mComp:[
      {v:"AQI",  c:0.490,pos:true, note:"⚠ FLAG: Goodwill/intangibles growing"},
      {v:"DSRI", c:0.975,pos:true, note:"AR days crept +6% — mild watch"},
      {v:"SGI",  c:0.765,pos:true, note:"Revenue declining (SGI=0.857)"},
      {v:"GMI",  c:0.536,pos:true, note:"Margins barely held (47%→46.3%)"},
      {v:"DEPI", c:0.118,pos:true, note:"Depreciation rate stable"},
      {v:"TATA", c:-0.300,pos:false,note:"✓✓ Cash > accruals strongly"},
      {v:"SGAI", c:-0.179,pos:false,note:"✓ SG&A slightly higher"},
      {v:"LVGI", c:-0.303,pos:false,note:"✓ Leverage declining"},
    ],

    fwd:[
      {q:"Q1'26A",actual:1.45,gmA:45.8,fcfA:0.22},
      {q:"Q2'26", bear:1.65,base:1.88,bull:2.15, gmBear:44,gmBase:47,gmBull:51, fcfBear:-0.1,fcfBase:0.3,fcfBull:0.7},
      {q:"Q3'26", bear:1.70,base:2.00,bull:2.30, gmBear:45,gmBase:48,gmBull:52, fcfBear:0.0, fcfBase:0.4,fcfBull:0.8},
      {q:"Q4'26", bear:1.75,base:2.12,bull:2.50, gmBear:45,gmBase:50,gmBull:54, fcfBear:0.1, fcfBase:0.5,fcfBull:1.0},
      {q:"Q1'27", bear:1.65,base:2.10,bull:2.55, gmBear:45,gmBase:50,gmBull:54, fcfBear:0.0, fcfBase:0.4,fcfBull:0.9},
      {q:"Q2'27", bear:1.75,base:2.20,bull:2.70, gmBear:46,gmBase:51,gmBull:55, fcfBear:0.1, fcfBase:0.5,fcfBull:1.1},
      {q:"Q3'27", bear:1.80,base:2.35,bull:2.90, gmBear:46,gmBase:52,gmBull:56, fcfBear:0.2, fcfBase:0.6,fcfBull:1.2},
      {q:"Q4'27", bear:1.85,base:2.50,bull:3.10, gmBear:47,gmBase:53,gmBull:57, fcfBear:0.2, fcfBase:0.7,fcfBull:1.4},
    ],
    annual:[
      {yr:"FY22", rev:8.33,gm:53.1,fcf:2.1,capex:1.1,sic:0.2},
      {yr:"FY23", rev:8.25,gm:47.0,fcf:1.8,capex:1.6,sic:0.8},
      {yr:"FY24", rev:7.07,gm:46.3,fcf:1.1,capex:1.5,sic:0.9},
      {yr:"FY25E",rev:7.80,gm:47.5,fcf:1.3,capex:1.4,sic:1.2},
      {yr:"FY26E",rev:8.00,gm:49.0,fcf:1.8,capex:1.3,sic:1.8},
      {yr:"FY27E",rev:9.15,gm:52.0,fcf:2.5,capex:1.1,sic:2.5},
    ],

    qs:["Q2'26","Q3'26","Q4'26","Q1'27","Q2'27","Q3'27","Q4'27"],
    ragFin:[
      {r:"SiC Revenue vs Guidance",  v:[0,1,1,1,2,2,2],vel:"up",vl:"CRITICAL→IMPROVING"},
      {r:"Gross Margin Expansion",   v:[1,1,2,2,2,2,2],vel:"up",vl:"IMPROVING"},
      {r:"Customer Concentration",   v:[0,0,0,1,1,1,1],vel:"dn",vl:"PERSISTENT HIGH"},
      {r:"FCF Generation",           v:[1,1,2,2,2,2,2],vel:"up",vl:"IMPROVING"},
      {r:"Balance Sheet / Debt",     v:[2,2,2,2,2,2,2],vel:"st",vl:"STABLE"},
    ],
    ragOps:[
      {r:"SiC Yield Rate",          v:[0,0,1,1,2,2,2],vel:"up",vl:"RAMPING"},
      {r:"EV Demand Realization",   v:[1,1,2,2,2,2,2],vel:"up",vl:"RECOVERING"},
      {r:"Legacy Fab Exit Risk",    v:[1,1,1,2,2,2,2],vel:"up",vl:"PROGRESSING"},
      {r:"Intelligent Sensing",     v:[1,1,1,1,1,1,1],vel:"dn",vl:"PERSISTENT"},
      {r:"Mfg Footprint Transition",v:[1,1,1,2,2,2,2],vel:"up",vl:"PROGRESSING"},
    ],
    ragComp:[
      {r:"BIS Controls (CN EVs)",    v:[0,0,0,1,1,1,1],vel:"dn",vl:"CRITICAL RISK"},
      {r:"China EV Re-routing",      v:[1,1,1,1,1,1,1],vel:"dn",vl:"PERSISTENT"},
      {r:"Environmental (Fabs)",     v:[1,1,1,1,1,2,2],vel:"up",vl:"IMPROVING"},
      {r:"SiC IP / Patent Risk",     v:[1,1,1,1,1,1,1],vel:"st",vl:"CONTESTED"},
      {r:"CHIPS Act Compliance",     v:[2,2,2,2,2,2,2],vel:"st",vl:"COMPLIANT"},
    ],

    china:[
      {co:"BYD Group",       pct:13,f:0,note:"Largest customer — BIS watch"},
      {co:"Other CN EV",     pct:10,f:0,note:"SAIC, Geely, Li Auto"},
      {co:"Tesla / NA OEMs", pct:11,f:1,note:"EV concentration"},
      {co:"European OEMs",   pct:16,f:1,note:"VW, BMW, Stellantis"},
      {co:"Korean OEMs",     pct:10,f:2,note:"Hyundai/Kia — stable"},
      {co:"Industrial",      pct:22,f:2,note:"Diversified — lower risk"},
      {co:"Other",           pct:18,f:2,note:"Consumer, comms"},
    ],
    localThreat:[
      {name:"BYD Semi",  seg:"Auto Power",  now:58,y27:75,risk:225},
      {name:"CXMT",      seg:"Power MOSFET",now:52,y27:70,risk:180},
      {name:"Starpower",  seg:"IGBT Modules",now:60,y27:78,risk:200},
      {name:"SiEn IC",   seg:"General Pwr", now:55,y27:72,risk:160},
      {name:"Times Semi",seg:"Switching",   now:50,y27:68,risk:140},
    ],
    chinaAtRisk:"~$1.8B (BIS-proximate)",

    capexPeer:[
      {co:"MCHP",r:8},{co:"TXN",r:15},{co:"NXPI",r:15},{co:"RNS",r:15},
      {co:"ON★",r:21},{co:"STM",r:22},{co:"IFX",r:25},{co:"WOLF",r:95},
    ],
    capexConv:[
      {q:"Q2'26",co:21.0,avg:14.2},{q:"Q3'26",co:20.5,avg:14.0},
      {q:"Q4'26",co:19.8,avg:13.8},{q:"Q1'27",co:18.5,avg:13.5},
      {q:"Q2'27",co:17.2,avg:13.2},{q:"Q3'27",co:16.0,avg:13.0},
      {q:"Q4'27",co:15.5,avg:13.0},
    ],
    capexLabel:"ON CapEx/Rev",

    macro:[
      {n:"Global EV Sales Growth",   corr:0.88,lead:1,sig:2,val:"Chinese NEV +28% YoY"},
      {n:"Auto Production Volumes",  corr:0.75,lead:2,sig:2,val:"Recovering +3.2% YoY"},
      {n:"China NEV Penetration",    corr:0.71,lead:1,sig:2,val:"45%+ in Q1 2026"},
      {n:"ISM Manufacturing PMI",    corr:0.68,lead:2,sig:2,val:"50.8 — Expansionary"},
      {n:"European EV Sales",        corr:0.65,lead:2,sig:1,val:"+6.1% YoY"},
      {n:"US EV Sales",              corr:0.61,lead:1,sig:2,val:"+18% YoY Q1'26"},
      {n:"Lithium Carbonate Price",  corr:-0.58,lead:2,sig:1,val:"Declining — EV tailwind"},
      {n:"CAFE / Emissions Stds",    corr:0.52,lead:4,sig:1,val:"Tightening — structural"},
    ],

    audit:[
      {ref:"A01",title:"SiC Revenue vs Take-or-Pay",  impact:9.5,detect:3.5,urg:0,domain:"Revenue"},
      {ref:"A02",title:"Goodwill Impairment (AQI)",   impact:9.0,detect:4.0,urg:0,domain:"Forensic"},
      {ref:"A03",title:"BIS Controls — CN EV OEMs",   impact:9.0,detect:3.8,urg:0,domain:"Compliance"},
      {ref:"A04",title:"SiC Yield Cost Capitalization",impact:8.5,detect:4.5,urg:0,domain:"Inventory"},
      {ref:"A05",title:"Customer Concentration Disc.", impact:8.0,detect:5.0,urg:0,domain:"Disclosure"},
      {ref:"A06",title:"Legacy Fab Env. Indemnity",   impact:7.5,detect:4.0,urg:0,domain:"Legal"},
      {ref:"A07",title:"EV Contract Renegotiation",   impact:7.5,detect:4.5,urg:0,domain:"Revenue"},
      {ref:"A08",title:"SiC IP / Patent Exposure",    impact:7.0,detect:5.5,urg:1,domain:"IP"},
      {ref:"A09",title:"Inventory — SiC Wafer Val.",  impact:6.8,detect:5.8,urg:1,domain:"Inventory"},
      {ref:"A10",title:"Transfer Pricing Korea/EU",   impact:6.5,detect:6.0,urg:1,domain:"Tax"},
      {ref:"A11",title:"CHIPS Act Use-of-Proceeds",   impact:6.0,detect:6.5,urg:1,domain:"Compliance"},
      {ref:"A12",title:"Revenue Recog — Dist. Chan.", impact:5.8,detect:7.0,urg:2,domain:"Revenue"},
    ],

    specialType:"sic",
    sicRamp:[
      {yr:"FY22",actual:0.20},{yr:"FY23",actual:0.80},
      {yr:"FY24",actual:0.90,mgmt:1.50},
      {yr:"FY25E",bear:1.00,base:1.20,bull:1.50,mgmt:2.00},
      {yr:"FY26E",bear:1.30,base:1.80,bull:2.50,mgmt:3.00},
      {yr:"FY27E",bear:1.80,base:2.50,bull:4.00,mgmt:4.50},
    ],
    wolfMonitor:[
      {metric:"Revenue",      wolf:"$0.9B",  on:"$7.1B",  verdict:"ON: 7.9× larger"},
      {metric:"Gross Margin", wolf:"~8%",    on:"~46%",   verdict:"WOLF near breakeven"},
      {metric:"FCF",          wolf:"−$1.1B", on:"+$1.1B", verdict:"WOLF burning cash"},
      {metric:"Z-Score",      wolf:"1.31",   on:"4.08",   verdict:"WOLF near-distress"},
      {metric:"SiC Capacity", wolf:"200mm",  on:"150/200mm",verdict:"ON: diversified"},
    ],
  },
};

// ═══════════════════════ SHARED COMPONENTS ══════════════════

const Card = ({children, style={}}) => (
  <div style={{background:B.card, border:`1px solid ${B.border}`, borderRadius:8, padding:18, ...style}}>
    {children}
  </div>
);

const Lbl = ({children, sub, color}) => (
  <div style={{marginBottom:12}}>
    <div style={{color:color||B.mint, fontSize:9, letterSpacing:"0.16em", textTransform:"uppercase", marginBottom:3}}>
      {children}
    </div>
    {sub && <div style={{color:B.muted, fontSize:10, lineHeight:1.4}}>{sub}</div>}
  </div>
);

const Tag = ({children, color}) => (
  <span style={{background:color+"22", border:`1px solid ${color}55`, color, borderRadius:3,
    fontSize:8, padding:"2px 7px", letterSpacing:"0.07em", whiteSpace:"nowrap"}}>{children}</span>
);

const ChartTip = ({active, payload, label, fmt}) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{background:"#080F0A", border:`1px solid ${B.borderLt}`, borderRadius:6, padding:"8px 12px", fontSize:10}}>
      <div style={{color:B.mint, marginBottom:5, fontWeight:600}}>{label}</div>
      {payload.map((p,i) => (
        <div key={i} style={{color:p.color||B.text, marginBottom:2}}>
          {p.name}: {fmt && p.value != null ? fmt(p.value) : p.value ?? "—"}
        </div>
      ))}
    </div>
  );
};

const RagCell = ({val}) => (
  <div style={{background:RBG[val], borderRadius:3, width:52, height:22, display:"flex",
    alignItems:"center", justifyContent:"center", fontSize:9, color:RC[val], fontWeight:700,
    letterSpacing:"0.06em", border:`1px solid ${RC[val]}44`}}>
    {RL[val]}
  </div>
);

const VBadge = ({vel, vl}) => {
  const c = {up:B.mint, dn:B.red, st:B.amber}[vel] || B.amber;
  const icon = {up:"↑",dn:"↓",st:"→"}[vel];
  return <span style={{fontSize:8, color:c, marginLeft:6}}>{icon} {vl}</span>;
};

const RagGrid = ({data, qs}) => (
  <div style={{overflowX:"auto"}}>
    <table style={{borderCollapse:"separate", borderSpacing:"3px 3px"}}>
      <thead>
        <tr>
          <th style={{textAlign:"left", fontSize:9, color:B.muted, paddingRight:12, paddingBottom:4, minWidth:200, fontWeight:400}}>
            RISK FACTOR / VELOCITY
          </th>
          {qs.map(q => (
            <th key={q} style={{fontSize:8, color:B.muted, paddingBottom:4, textAlign:"center", fontWeight:400, minWidth:56}}>{q}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {data.map((row, i) => (
          <tr key={i}>
            <td style={{fontSize:10, color:B.text, paddingRight:12, paddingBottom:3, whiteSpace:"nowrap"}}>
              {row.r}<VBadge vel={row.vel} vl={row.vl}/>
            </td>
            {row.v.map((val, j) => (
              <td key={j} style={{paddingBottom:3, textAlign:"center"}}><RagCell val={val}/></td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// ═══════════════════════ TABS ════════════════════════════════

function T0_Overview({co}) {
  const sorted = [...SECTOR].sort((a,b) => a.d - b.d);
  const highlight = co.ticker;
  return (
    <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub="FY2022 Peak → FY2024 Trough | Power & Analog semiconductor cohort">
          SECTOR CYCLE — PEAK TO TROUGH DECLINE
        </Lbl>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={sorted} layout="vertical" margin={{left:8, right:50, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-80,12]} tickFormatter={v=>`${v}%`} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text, fontSize:11}} stroke="none" width={46}/>
            <Tooltip content={<ChartTip fmt={v=>`${v.toFixed(1)}%`}/>}/>
            <ReferenceLine x={0} stroke={B.borderLt}/>
            <Bar dataKey="d" name="Revenue Change" radius={[0,3,3,0]}
              label={{position:"right", formatter:v=>`${v}%`, fill:B.muted, fontSize:9}}>
              {sorted.map((d,i) => (
                <Cell key={i} fill={d.co===highlight ? co.accent : d.color}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <Lbl sub="Key structural dimensions vs primary peers">COMPETITIVE POSITIONING</Lbl>
        {co.ticker==="TXN" ? (
          <div style={{display:"flex", flexDirection:"column", gap:8}}>
            {[
              {dim:"Fab Ownership",  val:"~95% owned",  c:B.mint,  note:"Lowest Taiwan dependency in cohort"},
              {dim:"Auto Exposure",  val:"~20% Rev",    c:B.mint,  note:"Conservative — upside optionality vs ON/IFX"},
              {dim:"CapEx/Rev",      val:"32%",         c:B.red,   note:"2.6× sector benchmark — FCF under pressure"},
              {dim:"China Revenue",  val:"~20%",        c:B.amber, note:"Threshold exposure — localization risk"},
              {dim:"Z-Score",        val:"6.57",        c:B.mint,  note:"Best-in-class solvency"},
              {dim:"Dividend",       val:"FCF<Div (FY26E)",c:B.amber,note:"Debt-funded until FY27E FCF recovery"},
            ].map((d,i) => (
              <div key={i} style={{display:"flex", justifyContent:"space-between", alignItems:"center",
                padding:"7px 10px", background:B.bg2, borderRadius:5, border:`1px solid ${d.c}22`}}>
                <span style={{fontSize:9, color:B.muted}}>{d.dim}</span>
                <span style={{fontSize:11, fontWeight:700, color:d.c}}>{d.val}</span>
                <span style={{fontSize:8, color:B.muted, maxWidth:160, textAlign:"right"}}>{d.note}</span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{display:"flex", flexDirection:"column", gap:8}}>
            {[
              {dim:"Auto Exposure",  val:"~55% Rev",   c:B.amber, note:"Highest auto concentration in cohort"},
              {dim:"SiC Position",   val:"#3 Global",  c:B.sic,   note:"17% share — growing vs STM, IFX, WOLF"},
              {dim:"BYD Customer",   val:"~13% Rev",   c:B.red,   note:"Single largest customer — BIS watch"},
              {dim:"AQI Flag",       val:"1.214",      c:B.amber, note:"Goodwill/intangibles growing — audit now"},
              {dim:"WOLF Z-Score",   val:"1.31",       c:B.red,   note:"Near-distress: risk AND opportunity for ON"},
              {dim:"FCF",            val:"$1.1B FY24", c:B.mint,  note:"Self-funding unlike TXN — smaller CapEx"},
            ].map((d,i) => (
              <div key={i} style={{display:"flex", justifyContent:"space-between", alignItems:"center",
                padding:"7px 10px", background:B.bg2, borderRadius:5, border:`1px solid ${d.c}22`}}>
                <span style={{fontSize:9, color:B.muted}}>{d.dim}</span>
                <span style={{fontSize:11, fontWeight:700, color:d.c}}>{d.val}</span>
                <span style={{fontSize:8, color:B.muted, maxWidth:160, textAlign:"right"}}>{d.note}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <Lbl sub="Annual revenue $B | Gross Margin % overlay">FINANCIAL TRAJECTORY FY22→FY27E</Lbl>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={co.annual} margin={{left:8, right:40, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} vertical={false}/>
            <XAxis dataKey="yr" tick={{fill:B.text, fontSize:9}} stroke={B.border}/>
            <YAxis yAxisId="l" tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={v=>`$${v}B`}/>
            <YAxis yAxisId="r" orientation="right" domain={[40,75]} tick={{fill:B.muted, fontSize:9}} stroke="none" tickFormatter={v=>`${v}%`}/>
            <Tooltip content={<ChartTip/>}/>
            <Bar yAxisId="l" dataKey="rev" name="Revenue $B" fill={co.accent+"44"} radius={[3,3,0,0]}/>
            <Line yAxisId="r" type="monotone" dataKey="gm" name="Gross Margin %" stroke={co.accent} strokeWidth={2.5} dot={{fill:co.accent, r:4}}/>
            <Line yAxisId="l" type="monotone" dataKey="fcf" name="FCF $B" stroke={B.amber} strokeWidth={1.5} strokeDasharray="5 3" dot={{fill:B.amber, r:3}}/>
          </ComposedChart>
        </ResponsiveContainer>
      </Card>
    </div>
  );
}

function T1_Forensic({co}) {
  const mSorted = [...co.mScorePeers].sort((a,b) => b.s - a.s);
  return (
    <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
      <Card>
        <Lbl sub={`Threshold −2.22 | ${co.ticker}★ score: ${co.mScore} | ${co.mScoreFlag}`}>
          BENEISH M-SCORE — COHORT
        </Lbl>
        <ResponsiveContainer width="100%" height={265}>
          <BarChart data={mSorted} layout="vertical" margin={{left:8, right:40, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-4.2,0]} tickFormatter={v=>v.toFixed(1)} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text, fontSize:11}} stroke="none" width={50}/>
            <Tooltip content={<ChartTip fmt={v=>v.toFixed(2)}/>}/>
            <ReferenceLine x={-2.22} stroke={B.amber} strokeWidth={1.5} strokeDasharray="5 4"
              label={{value:"−2.22",position:"insideTopRight",fill:B.amber,fontSize:8}}/>
            <Bar dataKey="s" name="M-Score" radius={[0,3,3,0]}>
              {mSorted.map((d,i) => (
                <Cell key={i} fill={d.co.includes("★") ? co.accent : d.co==="WOLF" ? B.red : RC[d.f]}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <Lbl sub="Safe Zone: >2.99 | Grey Zone: 1.81–2.99">
          ALTMAN Z-SCORE — SOLVENCY
        </Lbl>
        <ResponsiveContainer width="100%" height={265}>
          <BarChart data={co.zScorePeers} layout="vertical" margin={{left:8, right:30, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[0,7]} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text, fontSize:11}} stroke="none" width={50}/>
            <Tooltip content={<ChartTip fmt={v=>v.toFixed(2)}/>}/>
            <ReferenceLine x={2.99} stroke={B.mint} strokeWidth={1.5} strokeDasharray="5 4"
              label={{value:"SAFE",position:"insideTopRight",fill:B.mint,fontSize:8}}/>
            <ReferenceLine x={1.81} stroke={B.red} strokeWidth={1} strokeDasharray="4 4"/>
            <Bar dataKey="s" name="Z-Score" radius={[0,3,3,0]}>
              {co.zScorePeers.map((d,i) => (
                <Cell key={i} fill={d.co.includes("★") ? co.accent : d.co==="WOLF" ? B.red : d.z===0 ? B.red : d.z===1 ? B.amber : B.muted+"66"}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub={`${co.ticker} M-Score: ${co.mScore} | Weighted contribution per variable`}>
          M-SCORE COMPONENT DECOMPOSITION
        </Lbl>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={co.mComp} layout="vertical" margin={{left:14, right:70, top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-5.5,1.2]} tickFormatter={v=>v.toFixed(1)} tick={{fill:B.muted, fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="v" tick={{fill:B.text, fontSize:11}} stroke="none" width={54}/>
            <Tooltip content={<ChartTip fmt={v=>`${v>0?"+":""}${v.toFixed(3)}`}/>}/>
            <ReferenceLine x={0} stroke={B.borderLt}/>
            <Bar dataKey="c" name="Contribution" radius={[0,3,3,0]}>
              {co.mComp.map((d,i) => (
                <Cell key={i} fill={d.v==="AQI" && co.aqi>1.1 ? B.amber : d.pos ? B.amber+"99" : B.mint}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:12, marginTop:12}}>
          {co.ticker==="ON" && (
            <div style={{padding:"10px 14px",background:B.amberBg,border:`1px solid ${B.amber}`,borderRadius:6,fontSize:9}}>
              <div style={{color:B.amber,fontWeight:800,marginBottom:4}}>⚠ AQI FLAG: {co.aqi}</div>
              <div style={{color:B.text,lineHeight:1.5}}>Non-current soft assets growing faster than total assets — driven by goodwill from GTAT/Coherent SiC acquisitions and take-or-pay contract assets. Goodwill impairment test (ASC 350) required if SiC misses plan by &gt;15%.</div>
            </div>
          )}
          {co.ticker==="TXN" && (
            <div style={{padding:"10px 14px",background:B.greenBg,border:`1px solid ${B.mint}33`,borderRadius:6,fontSize:9}}>
              <div style={{color:B.mint,fontWeight:800,marginBottom:4}}>↑ GMI IMPROVED: {co.aqi}</div>
              <div style={{color:B.text,lineHeight:1.5}}>Gross margin recovering (58.5%→61%) drives GMI below 1.0 — the primary forensic improvement in this rerun. GMI was the #1 risk driver in FY2024. Now normalized.</div>
            </div>
          )}
          <div style={{padding:"10px 14px",background:B.greenBg,border:`1px solid ${B.mint}33`,borderRadius:6,fontSize:9}}>
            <div style={{color:B.mint,fontWeight:800,marginBottom:4}}>✓ TATA: {co.tata}</div>
            <div style={{color:B.text,lineHeight:1.5}}>Strongly negative TATA confirms cash earnings exceed accrual-based earnings — positive forensic signal for both companies. No accruals manipulation pattern detected.</div>
          </div>
        </div>
      </Card>
    </div>
  );
}

function T2_Forward({co}) {
  const [view, setView] = useState("revenue");
  const fwd = co.fwd;

  const kpis = co.ticker==="TXN" ? [
    {label:"FY2026E Bear",val:"$16.6B",c:B.red,  sub:"−10% vs FY25E"},
    {label:"FY2026E Base",val:"$18.2B",c:B.amber,sub:"+7.1% vs FY25E"},
    {label:"FY2026E Bull",val:"$21.4B",c:B.mint, sub:"+25.9% vs FY25E"},
    {label:"FY2027E Bear",val:"$17.3B",c:B.red,  sub:"Stagnant recovery"},
    {label:"FY2027E Base",val:"$21.5B",c:B.amber,sub:"FY22 peak recovered"},
    {label:"FY2027E Bull",val:"$26.7B",c:B.mint, sub:"New all-time high"},
  ] : [
    {label:"FY2026E Bear",val:"$6.9B",c:B.red,  sub:"SiC stalls"},
    {label:"FY2026E Base",val:"$8.0B",c:B.amber,sub:"+2.6% vs FY25E"},
    {label:"FY2026E Bull",val:"$9.5B",c:B.mint, sub:"+21.8% vs FY25E"},
    {label:"FY2027E Bear",val:"$7.1B",c:B.red,  sub:"SiC ramp fails"},
    {label:"FY2027E Base",val:"$9.2B",c:B.amber,sub:"SiC $2.5B run-rate"},
    {label:"FY2027E Bull",val:"$11.3B",c:B.mint,sub:"New ON record"},
  ];

  const yDomain = co.ticker==="TXN" ? [3.5,7.5] : [1.2,3.5];
  const gmRef   = co.ticker==="TXN" ? 65 : 53;
  const gmLabel = co.ticker==="TXN" ? "Pre-cycle peak (65%)" : "FY22 peak (53%)";
  const gmDomain= co.ticker==="TXN" ? [55,75] : [40,62];

  return (
    <div style={{display:"flex", flexDirection:"column", gap:16}}>
      <div style={{display:"grid", gridTemplateColumns:"repeat(6,1fr)", gap:10}}>
        {kpis.map((k,i) => (
          <Card key={i} style={{textAlign:"center",padding:"12px 8px",border:`1px solid ${k.c}33`}}>
            <div style={{fontSize:15,fontWeight:800,color:k.c}}>{k.val}</div>
            <div style={{fontSize:8,color:B.muted,marginTop:3}}>{k.label}</div>
            <div style={{fontSize:8,color:k.c,marginTop:3}}>{k.sub}</div>
          </Card>
        ))}
      </div>

      <Card>
        <div style={{display:"flex", gap:6, marginBottom:14, alignItems:"center"}}>
          {["revenue","margin","fcf"].map(v => (
            <button key={v} onClick={() => setView(v)} style={{
              background:view===v ? B.greenBg : "transparent",
              border:`1px solid ${view===v ? B.mint : B.border}`,
              color:view===v ? B.mint : B.muted,
              borderRadius:4, padding:"4px 12px", cursor:"pointer",
              fontSize:9, fontFamily:"inherit", letterSpacing:"0.08em",
            }}>{v.toUpperCase()}</button>
          ))}
          <div style={{marginLeft:"auto", fontSize:8, color:B.muted}}>
            Bear / Base / Bull · Q1'26 Actual included
          </div>
        </div>

        {view==="revenue" && (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={fwd} margin={{left:10,right:20,top:4}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim}/>
              <XAxis dataKey="q" tick={{fill:B.muted,fontSize:9}} stroke={B.border}/>
              <YAxis domain={yDomain} tickFormatter={v=>`$${v}B`} tick={{fill:B.muted,fontSize:9}} stroke="none"/>
              <Tooltip content={<ChartTip fmt={v=>v!=null?`$${v.toFixed(2)}B`:"—"}/>}/>
              <Legend wrapperStyle={{fontSize:9}}/>
              <Line type="monotone" dataKey="actual" name="Actual" stroke={B.ivory} strokeWidth={2.5} connectNulls={false} dot={(props)=>props.payload.actual!=null?<circle cx={props.cx} cy={props.cy} r={5} fill={B.ivory}/>:<g/>}/>
              <Line type="monotone" dataKey="bull" name="Bull" stroke={B.mint} strokeWidth={1.5} strokeDasharray="5 3" connectNulls={false} dot={(props)=>props.payload.bull!=null?<circle cx={props.cx} cy={props.cy} r={3} fill={B.mint}/>:<g/>}/>
              <Line type="monotone" dataKey="base" name="Base" stroke={B.amber} strokeWidth={2.5} connectNulls={false} dot={(props)=>props.payload.base!=null?<circle cx={props.cx} cy={props.cy} r={4} fill={B.amber}/>:<g/>}/>
              <Line type="monotone" dataKey="bear" name="Bear" stroke={B.red} strokeWidth={1.5} strokeDasharray="5 3" connectNulls={false} dot={(props)=>props.payload.bear!=null?<circle cx={props.cx} cy={props.cy} r={3} fill={B.red}/>:<g/>}/>
            </LineChart>
          </ResponsiveContainer>
        )}
        {view==="margin" && (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={fwd} margin={{left:10,right:20,top:4}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim}/>
              <XAxis dataKey="q" tick={{fill:B.muted,fontSize:9}} stroke={B.border}/>
              <YAxis domain={gmDomain} tickFormatter={v=>`${v}%`} tick={{fill:B.muted,fontSize:9}} stroke="none"/>
              <Tooltip content={<ChartTip fmt={v=>v!=null?`${v}%`:"—"}/>}/>
              <ReferenceLine y={gmRef} stroke={B.borderLt} strokeDasharray="4 4" label={{value:gmLabel,position:"insideTopRight",fill:B.muted,fontSize:8}}/>
              <Legend wrapperStyle={{fontSize:9}}/>
              <Line type="monotone" dataKey="gmA" name="Actual GM" stroke={B.ivory} strokeWidth={2.5} connectNulls={false} dot={(props)=>props.payload.gmA!=null?<circle cx={props.cx} cy={props.cy} r={5} fill={B.ivory}/>:<g/>}/>
              <Line type="monotone" dataKey="gmBull" name="Bull GM" stroke={B.mint} strokeWidth={1.5} strokeDasharray="5 3" connectNulls={false} dot={(props)=>props.payload.gmBull!=null?<circle cx={props.cx} cy={props.cy} r={3} fill={B.mint}/>:<g/>}/>
              <Line type="monotone" dataKey="gmBase" name="Base GM" stroke={B.amber} strokeWidth={2.5} connectNulls={false} dot={(props)=>props.payload.gmBase!=null?<circle cx={props.cx} cy={props.cy} r={4} fill={B.amber}/>:<g/>}/>
              <Line type="monotone" dataKey="gmBear" name="Bear GM" stroke={B.red} strokeWidth={1.5} strokeDasharray="5 3" connectNulls={false} dot={(props)=>props.payload.gmBear!=null?<circle cx={props.cx} cy={props.cy} r={3} fill={B.red}/>:<g/>}/>
            </LineChart>
          </ResponsiveContainer>
        )}
        {view==="fcf" && (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={fwd} margin={{left:10,right:20,top:4}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim}/>
              <XAxis dataKey="q" tick={{fill:B.muted,fontSize:9}} stroke={B.border}/>
              <YAxis tickFormatter={v=>`$${v}B`} tick={{fill:B.muted,fontSize:9}} stroke="none"/>
              <Tooltip content={<ChartTip fmt={v=>v!=null?`$${v.toFixed(2)}B`:"—"}/>}/>
              <ReferenceLine y={0} stroke={B.borderLt}/>
              {co.ticker==="TXN" && <ReferenceLine y={1.1} stroke={B.amber} strokeDasharray="4 4" label={{value:"Dividend (quarterly)",position:"insideTopRight",fill:B.amber,fontSize:8}}/>}
              <Legend wrapperStyle={{fontSize:9}}/>
              <Line type="monotone" dataKey="fcfA" name="Actual FCF" stroke={B.ivory} strokeWidth={2.5} connectNulls={false} dot={(props)=>props.payload.fcfA!=null?<circle cx={props.cx} cy={props.cy} r={5} fill={B.ivory}/>:<g/>}/>
              <Line type="monotone" dataKey="fcfBull" name="Bull FCF" stroke={B.mint} strokeWidth={1.5} strokeDasharray="5 3" connectNulls={false} dot={(props)=>props.payload.fcfBull!=null?<circle cx={props.cx} cy={props.cy} r={3} fill={B.mint}/>:<g/>}/>
              <Line type="monotone" dataKey="fcfBase" name="Base FCF" stroke={B.amber} strokeWidth={2.5} connectNulls={false} dot={(props)=>props.payload.fcfBase!=null?<circle cx={props.cx} cy={props.cy} r={4} fill={B.amber}/>:<g/>}/>
              <Line type="monotone" dataKey="fcfBear" name="Bear FCF" stroke={B.red} strokeWidth={1.5} strokeDasharray="5 3" connectNulls={false} dot={(props)=>props.payload.fcfBear!=null?<circle cx={props.cx} cy={props.cy} r={3} fill={B.red}/>:<g/>}/>
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>
    </div>
  );
}

function T3_Special({co}) {
  if (co.specialType==="capex") {
    return (
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
        <Card style={{gridColumn:"1 / -1"}}>
          <Lbl sub="Dividend funded from debt until FY2027E | FCF crossover is the defining inflection point">
            CAPEX vs FCF vs DIVIDEND — THE INVERSION CHART
          </Lbl>
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={co.annual} margin={{left:10,right:30,top:4}}>
              <CartesianGrid strokeDasharray="3 3" stroke={B.dim} vertical={false}/>
              <XAxis dataKey="yr" tick={{fill:B.text,fontSize:9}} stroke={B.border}/>
              <YAxis tickFormatter={v=>`$${v}B`} tick={{fill:B.muted,fontSize:9}} stroke="none" domain={[0,11]}/>
              <Tooltip content={<ChartTip fmt={v=>v!=null?`$${v.toFixed(1)}B`:"—"}/>}/>
              <ReferenceLine y={4.4} stroke={B.amber} strokeDasharray="4 4" label={{value:"Dividend ($4.4B/yr)",position:"insideTopRight",fill:B.amber,fontSize:8}}/>
              <Bar dataKey="capex" name="CapEx $B" fill={B.red+"66"} radius={[3,3,0,0]}/>
              <Line type="monotone" dataKey="fcf" name="FCF $B" stroke={B.mint} strokeWidth={2.5} dot={{fill:B.mint,r:4}}/>
            </ComposedChart>
          </ResponsiveContainer>
        </Card>
        {[{label:"FY2026E",bridge:co.divBridge},{label:"FY2027E",bridge:co.divBridge27}].map((yr,yi) => (
          <Card key={yi}>
            <Lbl sub={`Dividend coverage bridge — ${yr.label} base case`}>{yr.label} DIVIDEND BRIDGE</Lbl>
            {yr.bridge.map((item,i) => {
              const c = item.type==="in" ? B.mint : item.type==="out" ? B.red : item.val>=0 ? B.mint : B.red;
              return (
                <div key={i} style={{display:"flex",justifyContent:"space-between",alignItems:"center",
                  padding:"8px 12px",background:B.bg2,borderRadius:5,marginBottom:6,
                  borderLeft:`3px solid ${c}`}}>
                  <span style={{fontSize:10,color:B.text}}>{item.item}</span>
                  <span style={{fontSize:12,fontWeight:700,color:c}}>{item.val>0?"+":""}{item.val.toFixed(1)}B</span>
                </div>
              );
            })}
          </Card>
        ))}
      </div>
    );
  }

  // ON: SiC Strategy
  return (
    <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:16}}>
      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub="Bear / Base / Bull vs Management guidance | Guidance gap is primary audit disclosure risk" color={B.sic}>
          ⚡ SiC REVENUE RAMP — ON vs GUIDANCE vs SCENARIOS
        </Lbl>
        <ResponsiveContainer width="100%" height={250}>
          <LineChart data={co.sicRamp} margin={{left:10,right:20,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim}/>
            <XAxis dataKey="yr" tick={{fill:B.text,fontSize:10}} stroke={B.border}/>
            <YAxis domain={[0,5.5]} tickFormatter={v=>`$${v}B`} tick={{fill:B.muted,fontSize:9}} stroke="none"/>
            <Tooltip content={<ChartTip fmt={v=>v!=null?`$${v.toFixed(2)}B`:"—"}/>}/>
            <Legend wrapperStyle={{fontSize:9}}/>
            <Line type="monotone" dataKey="mgmt" name="Mgmt Target" stroke={B.red} strokeWidth={1.5} strokeDasharray="6 3" connectNulls={false} dot={(p)=>p.payload.mgmt!=null?<circle cx={p.cx} cy={p.cy} r={4} fill={B.red}/>:<g/>}/>
            <Line type="monotone" dataKey="actual" name="Actual" stroke={B.ivory} strokeWidth={3} connectNulls={false} dot={(p)=>p.payload.actual!=null?<circle cx={p.cx} cy={p.cy} r={5} fill={B.ivory}/>:<g/>}/>
            <Line type="monotone" dataKey="bull" name="Bull" stroke={B.mint} strokeWidth={1.5} strokeDasharray="4 3" connectNulls={false} dot={(p)=>p.payload.bull!=null?<circle cx={p.cx} cy={p.cy} r={3} fill={B.mint}/>:<g/>}/>
            <Line type="monotone" dataKey="base" name="Base" stroke={B.amber} strokeWidth={2} connectNulls={false} dot={(p)=>p.payload.base!=null?<circle cx={p.cx} cy={p.cy} r={4} fill={B.amber}/>:<g/>}/>
            <Line type="monotone" dataKey="bear" name="Bear" stroke={B.red+"AA"} strokeWidth={1.5} strokeDasharray="4 3" connectNulls={false} dot={(p)=>p.payload.bear!=null?<circle cx={p.cx} cy={p.cy} r={3} fill={B.red}/>:<g/>}/>
          </LineChart>
        </ResponsiveContainer>
        <div style={{marginTop:10,padding:"8px 12px",background:B.redBg,border:`1px solid ${B.red}44`,borderRadius:5,fontSize:9,color:B.red}}>
          ⚠ AUDIT DISCLOSURE RISK: Even bull scenario misses original $4–5B target. Base case gap = $2.0B vs guidance — material for SEC disclosure. MD&A forward-looking statements require updated caveat language.
        </div>
      </Card>

      <Card>
        <Lbl sub="Wolfspeed financial health vs ON — WOLF distress creates risk AND opportunity" color={B.red}>
          WOLFSPEED THREAT / OPPORTUNITY MONITOR
        </Lbl>
        <div style={{display:"flex",flexDirection:"column",gap:7,marginTop:4}}>
          {co.wolfMonitor.map((w,i) => (
            <div key={i} style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:6,
              padding:"8px 10px",background:B.bg2,borderRadius:5,border:`1px solid ${B.border}`}}>
              <span style={{fontSize:9,color:B.muted}}>{w.metric}</span>
              <span style={{fontSize:9}}>
                <span style={{color:B.red,fontWeight:600}}>WOLF: {w.wolf}</span>
                <span style={{color:B.mint,marginLeft:6}}>ON: {w.on}</span>
              </span>
              <span style={{fontSize:8,color:B.amber}}>{w.verdict}</span>
            </div>
          ))}
        </div>
        <div style={{marginTop:10,display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
          <div style={{padding:"8px 10px",background:B.greenBg,border:`1px solid ${B.mint}33`,borderRadius:5,fontSize:8,color:B.mint}}>
            ↑ OPPORTUNITY: WOLF restructure → ON captures ~16% share = ~$850M additional SiC revenue
          </div>
          <div style={{padding:"8px 10px",background:B.redBg,border:`1px solid ${B.red}33`,borderRadius:5,fontSize:8,color:B.red}}>
            ↓ RISK: WOLF collapse disrupts common SiC substrate vendors — ON must audit wafer supply chain dependency
          </div>
        </div>
      </Card>

      <Card>
        <Lbl sub="SiC scenario outcomes by FY2027E" color={B.sic}>SiC FY2027E SCENARIO OUTCOMES</Lbl>
        <div style={{display:"flex",flexDirection:"column",gap:8,marginTop:4}}>
          {[
            {label:"BEAR  $1.8B",c:B.red,  gap:"−$2.7B vs target",note:"EV adoption stalls, yield unsolved"},
            {label:"BASE  $2.5B",c:B.amber,gap:"−$2.0B vs target",note:"Gradual recovery, yield Q3'26"},
            {label:"BULL  $4.0B",c:B.mint, gap:"−$0.5B vs target",note:"EV surge + WOLF share capture"},
          ].map((s,i) => (
            <div key={i} style={{padding:"12px 14px",background:RBG[i===0?0:i===1?1:2],border:`1px solid ${s.c}44`,borderRadius:6}}>
              <div style={{fontSize:14,fontWeight:800,color:s.c}}>{s.label}</div>
              <div style={{fontSize:8,color:s.c,marginTop:2}}>{s.gap}</div>
              <div style={{fontSize:8,color:B.muted,marginTop:4,lineHeight:1.4}}>{s.note}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function T4_Rag({co}) {
  const [dom, setDom] = useState("fin");
  const doms = {
    fin:{label:"Financial",data:co.ragFin,c:B.mint},
    ops:{label:"Operational",data:co.ragOps,c:B.amber},
    comp:{label:"Compliance",data:co.ragComp,c:B.red},
  };
  const cur = doms[dom];
  const all = [...co.ragFin,...co.ragOps,...co.ragComp];
  const cnt = v => all.flatMap(r=>r.v).filter(x=>x===v).length;
  const vc = v => all.filter(r=>r.vel===v).length;
  return (
    <div style={{display:"flex",flexDirection:"column",gap:16}}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:10}}>
        {[
          {label:"RED Cells",    val:cnt(0),c:B.red,  note:"Immediate action"},
          {label:"AMBER Cells",  val:cnt(1),c:B.amber,note:"Elevated monitoring"},
          {label:"GREEN Cells",  val:cnt(2),c:B.mint, note:"Within tolerance"},
          {label:"↑ Improving",  val:vc("up"),c:B.mint, note:"Risk reducing"},
          {label:"→ Stable",     val:vc("st"),c:B.amber,note:"Unchanged"},
          {label:"↓ Persistent", val:vc("dn"),c:B.red, note:"Not reducing"},
        ].map((s,i) => (
          <Card key={i} style={{textAlign:"center",padding:"12px 8px",border:`1px solid ${s.c}33`}}>
            <div style={{fontSize:26,fontWeight:800,color:s.c}}>{s.val}</div>
            <div style={{fontSize:8,color:B.muted,marginTop:3}}>{s.label}</div>
            <div style={{fontSize:8,color:s.c,marginTop:3}}>{s.note}</div>
          </Card>
        ))}
      </div>
      <Card>
        <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:16}}>
          {Object.entries(doms).map(([k,v]) => (
            <button key={k} onClick={()=>setDom(k)} style={{
              background:dom===k?v.c+"22":"transparent",
              border:`1px solid ${dom===k?v.c:B.border}`,
              color:dom===k?v.c:B.muted,
              borderRadius:4,padding:"5px 14px",cursor:"pointer",
              fontSize:9,fontFamily:"inherit",letterSpacing:"0.08em",transition:"all 0.15s",
            }}>{v.label.toUpperCase()}</button>
          ))}
          <div style={{marginLeft:"auto",fontSize:8,color:B.muted}}>Q2 2026 → Q4 2027 | 7 quarters | With velocity</div>
        </div>
        <RagGrid data={cur.data} qs={co.qs}/>
        <div style={{display:"flex",gap:16,fontSize:8,color:B.muted,marginTop:4}}>
          <span>■ <span style={{color:B.red}}>RED</span> = Act now</span>
          <span>■ <span style={{color:B.amber}}>AMB</span> = Monitor</span>
          <span>■ <span style={{color:B.mint}}>GRN</span> = Clear</span>
          <span>·</span><span style={{color:B.mint}}>↑</span>
          <span style={{color:B.amber}}>→</span>
          <span style={{color:B.red}}>↓</span>
        </div>
      </Card>
    </div>
  );
}

function T5_China({co}) {
  return (
    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
      <Card>
        <Lbl sub="Revenue exposure % | 20% = audit risk threshold">
          {co.ticker==="TXN" ? "CHINA COHORT EXPOSURE" : "CUSTOMER CONCENTRATION MAP"}
        </Lbl>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={co.china} layout="vertical" margin={{left:8,right:50,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[0,45]} tickFormatter={v=>`${v}%`} tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text,fontSize:10}} stroke="none" width={82}/>
            <Tooltip content={<ChartTip fmt={v=>`${v}%`}/>}/>
            <ReferenceLine x={20} stroke={B.amber} strokeWidth={1.5} strokeDasharray="5 4"
              label={{value:"20% THRESHOLD",position:"insideTopRight",fill:B.amber,fontSize:8}}/>
            <Bar dataKey="pct" name={co.ticker==="TXN"?"China Rev %":"Revenue %"} radius={[0,3,3,0]}
              label={{position:"right",formatter:v=>`${v}%`,fill:B.muted,fontSize:9}}>
              {co.china.map((d,i) => (
                <Cell key={i} fill={d.co.includes("★")||d.co.includes(co.ticker)?co.accent:RC[d.f]}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <Lbl sub="Localization / substitution threat by competitor segment | Revenue at risk by Q4 2027" color={B.red}>
          {co.ticker==="TXN" ? "CHINA LOCALIZATION TRACKER" : "EV OEM SUBSTITUTION RISK"}
        </Lbl>
        <div style={{display:"flex",flexDirection:"column",gap:12,marginTop:4}}>
          {co.localThreat.map((c,i) => (
            <div key={i}>
              <div style={{display:"flex",justifyContent:"space-between",fontSize:10,color:B.text,marginBottom:4}}>
                <span><span style={{color:co.accent,fontWeight:700}}>{c.name}</span>
                  <span style={{color:B.muted}}> · {c.seg}</span></span>
                <span style={{color:B.red}}>${c.risk}M at risk</span>
              </div>
              <div style={{position:"relative",background:B.dim,borderRadius:3,height:8}}>
                <div style={{background:B.amber+"BB",borderRadius:3,height:"100%",width:`${c.now}%`,position:"absolute"}}/>
                <div style={{background:B.red+"55",borderRadius:3,height:"100%",width:`${c.y27}%`,position:"absolute"}}/>
              </div>
              <div style={{display:"flex",justifyContent:"space-between",fontSize:8,color:B.muted,marginTop:3}}>
                <span style={{color:B.amber}}>Now: {c.now}% parity</span>
                <span style={{color:B.red}}>Q4'27: {c.y27}% parity</span>
              </div>
            </div>
          ))}
        </div>
        <div style={{marginTop:12,padding:"8px 12px",background:B.redBg,border:`1px solid ${B.red}44`,borderRadius:5,fontSize:9,color:B.red}}>
          ▸ TOTAL REVENUE AT RISK: <span style={{fontWeight:700}}>{co.chinaAtRisk}</span>
        </div>
      </Card>
    </div>
  );
}

function T6_Capex({co}) {
  const peerSorted = [...co.capexPeer].sort((a,b)=>a.r-b.r);
  return (
    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
      <Card>
        <Lbl sub={`${co.ticker} at ${co.capexRatio} | Sector benchmark: 13–15%`}>
          CAPEX INTENSITY — PEER COMPARISON
        </Lbl>
        <ResponsiveContainer width="100%" height={270}>
          <BarChart data={peerSorted} layout="vertical" margin={{left:8,right:45,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[0,110]} tickFormatter={v=>`${v}%`} tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="co" tick={{fill:B.text,fontSize:11}} stroke="none" width={50}/>
            <Tooltip content={<ChartTip fmt={v=>`${v}%`}/>}/>
            <ReferenceLine x={15} stroke={B.mint} strokeWidth={1.5} strokeDasharray="5 4"
              label={{value:"BENCHMARK",position:"insideTopRight",fill:B.mint,fontSize:8}}/>
            <Bar dataKey="r" name="CapEx/Rev %" radius={[0,3,3,0]}
              label={{position:"right",formatter:v=>`${v}%`,fill:B.muted,fontSize:9}}>
              {peerSorted.map((d,i) => (
                <Cell key={i} fill={d.co.includes("★")?co.accent:d.co==="WOLF"?B.red:d.r>30?B.red:d.r>20?B.amber:B.muted+"55"}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <Lbl sub={`${co.capexLabel} converging to cohort average by Q4'27`}>
          CAPEX / REVENUE CONVERGENCE MODEL
        </Lbl>
        <ResponsiveContainer width="100%" height={270}>
          <LineChart data={co.capexConv} margin={{left:10,right:20,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim}/>
            <XAxis dataKey="q" tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
            <YAxis domain={[10,35]} tickFormatter={v=>`${v}%`} tick={{fill:B.muted,fontSize:9}} stroke="none"/>
            <Tooltip content={<ChartTip fmt={v=>`${v}%`}/>}/>
            <ReferenceLine y={15} stroke={B.borderLt} strokeDasharray="3 3"
              label={{value:"Sector Benchmark",position:"insideTopRight",fill:B.muted,fontSize:8}}/>
            <Line type="monotone" dataKey="co" name={co.capexLabel} stroke={co.accent} strokeWidth={2.5} dot={{fill:co.accent,r:4,strokeWidth:0}}/>
            <Line type="monotone" dataKey="avg" name="Cohort Avg" stroke={B.amber} strokeWidth={1.5} strokeDasharray="6 4" dot={{fill:B.amber,r:3,strokeWidth:0}}/>
          </LineChart>
        </ResponsiveContainer>
        <div style={{display:"flex",gap:20,marginTop:8,fontSize:9}}>
          <span style={{color:co.accent}}>——  {co.ticker}</span>
          <span style={{color:B.amber}}>---- Cohort Avg</span>
        </div>
      </Card>
    </div>
  );
}

function T7_Macro({co}) {
  const sorted = [...co.macro].sort((a,b)=>Math.abs(b.corr)-Math.abs(a.corr));
  const greenCnt = co.macro.filter(m=>m.sig===2).length;
  return (
    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub={`${co.ticker}-specific leading indicators | Q2 2026 readings | ${greenCnt} of 8 now GREEN`}>
          MACRO LEADING INDICATORS — Q2 2026
        </Lbl>
        <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:10}}>
          {co.macro.map((m,i) => (
            <div key={i} style={{background:B.bg2,border:`1px solid ${RC[m.sig]}33`,
              borderLeft:`3px solid ${RC[m.sig]}`,borderRadius:6,padding:"10px 12px"}}>
              <div style={{fontSize:8,color:RC[m.sig],textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:5}}>
                {m.sig===2?"🟢 GREEN":"🟡 AMBER"}
              </div>
              <div style={{fontSize:9,color:B.text,fontWeight:600,marginBottom:4,lineHeight:1.3}}>{m.n}</div>
              <div style={{fontSize:11,color:B.ivory,marginBottom:6}}>{m.val}</div>
              <div style={{display:"flex",justifyContent:"space-between",fontSize:8,color:B.muted,marginBottom:5}}>
                <span>r = {m.corr>0?"+":""}{m.corr}</span>
                <span>{m.lead}Q lead</span>
              </div>
              <div style={{background:B.dim,borderRadius:2,height:4}}>
                <div style={{background:RC[m.sig],borderRadius:2,height:"100%",width:`${Math.abs(m.corr)*100}%`}}/>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <Lbl sub="Pearson r vs revenue | Sorted by strength">CORRELATION RANKING</Lbl>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={sorted} layout="vertical" margin={{left:10,right:20,top:4}}>
            <CartesianGrid strokeDasharray="3 3" stroke={B.dim} horizontal={false}/>
            <XAxis type="number" domain={[-1,1]} tickFormatter={v=>v.toFixed(1)} tick={{fill:B.muted,fontSize:10}} stroke={B.border}/>
            <YAxis type="category" dataKey="n" tick={{fill:B.text,fontSize:8}} stroke="none" width={145}/>
            <Tooltip content={<ChartTip fmt={v=>`r = ${v>0?"+":""}${v.toFixed(2)}`}/>}/>
            <ReferenceLine x={0} stroke={B.borderLt}/>
            <Bar dataKey="corr" name="Pearson r" radius={[0,3,3,0]}>
              {sorted.map((d,i) => <Cell key={i} fill={d.corr>0?co.accent:B.amber}/>)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <Lbl sub="Summary composite vs prior analysis">COMPOSITE SIGNAL READ</Lbl>
        <div style={{padding:"12px 14px",background:B.greenBg,border:`1px solid ${B.mint}`,borderRadius:6,marginBottom:10}}>
          <div style={{color:B.mint,fontSize:13,fontWeight:800}}>🟢 COMPOSITE: {greenCnt} OF 8 GREEN</div>
          <div style={{color:B.text,fontSize:10,marginTop:6,lineHeight:1.5}}>
            {co.ticker==="TXN"
              ? "ISM PMI crossed 50 into expansionary territory — the most significant macro shift since the prior analysis. Industrial recovery confirmed but below-consensus pace."
              : "EV-weighted indicators strongly positive. Chinese NEV +28%, US EV +18%, auto production recovering. ON's macro environment is more favorable than TXN's industrial-weighted read."}
          </div>
        </div>
        <div style={{padding:"10px 14px",background:B.redBg,border:`1px solid ${B.red}33`,borderRadius:6,fontSize:9}}>
          <div style={{color:B.red,fontWeight:700}}>▸ AUDIT RISK</div>
          <div style={{color:B.text,marginTop:4,lineHeight:1.5}}>
            {co.ticker==="TXN"
              ? "If internal planning embeds a V-shaped recovery vs. the amber-dominant composite, guidance carries undisclosed downside risk. Verify internal assumptions vs. FRED readings."
              : "BYD/Chinese NEV growth is simultaneously the strongest revenue signal AND the highest BIS compliance risk. These cannot be managed as independent concerns."}
          </div>
        </div>
      </Card>
    </div>
  );
}

function T8_Audit({co}) {
  const [hov, setHov] = useState(null);
  const W=720, H=300;
  const toX = d => 60 + ((d-1)/9)*(W-100);
  const toY = d => H - 20 - ((d-5)/5)*(H-60);

  return (
    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub="X: Detectability (1=hard, 10=easy) | Y: Impact (higher=more material) | Hover for detail">
          AUDIT PRIORITY MATRIX — IMPACT × DETECTABILITY
        </Lbl>
        <div style={{display:"flex",gap:16}}>
          <div style={{flex:1}}>
            <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{overflow:"visible"}}>
              <rect x={60} y={20} width={(W-100)/2} height={(H-40)/2} fill={B.red} opacity={0.04} rx={3}/>
              <rect x={60+(W-100)/2} y={20} width={(W-100)/2} height={(H-40)/2} fill={B.amber} opacity={0.04} rx={3}/>
              <text x={175} y={40} textAnchor="middle" fill={B.red} fontSize={8} opacity={0.5}>HIGH IMPACT / HARD TO DETECT → Act Now</text>
              <text x={565} y={40} textAnchor="middle" fill={B.amber} fontSize={8} opacity={0.5}>HIGH IMPACT / EASY TO DETECT</text>
              <text x={175} y={H-8} textAnchor="middle" fill={B.muted} fontSize={7} opacity={0.5}>LOW IMPACT / HARD TO DETECT</text>
              <text x={565} y={H-8} textAnchor="middle" fill={B.muted} fontSize={7} opacity={0.5}>LOW IMPACT / EASY TO DETECT</text>
              <line x1={60+(W-100)/2} y1={16} x2={60+(W-100)/2} y2={H-10} stroke={B.borderLt} strokeWidth={1} strokeDasharray="4 4"/>
              <line x1={55} y1={20+(H-40)/2} x2={W-30} y2={20+(H-40)/2} stroke={B.borderLt} strokeWidth={1} strokeDasharray="4 4"/>
              {co.audit.map((a,i) => {
                const x=toX(a.detect), y=toY(a.impact), c=RC[a.urg], isH=hov===i;
                return (
                  <g key={i} style={{cursor:"pointer"}} onMouseEnter={()=>setHov(i)} onMouseLeave={()=>setHov(null)}>
                    <circle cx={x} cy={y} r={isH?17:13} fill={c+"33"} stroke={c} strokeWidth={isH?2:1}/>
                    <text x={x} y={y+4} textAnchor="middle" fill={c} fontSize={8} fontWeight={700}>{a.ref}</text>
                  </g>
                );
              })}
            </svg>
          </div>
          <div style={{width:200,flexShrink:0}}>
            {hov!==null ? (
              <div style={{background:B.bg2,border:`1px solid ${RC[co.audit[hov].urg]}`,borderRadius:6,padding:"12px 14px",fontSize:10}}>
                <div style={{color:RC[co.audit[hov].urg],fontWeight:800,marginBottom:6}}>ATV-{co.audit[hov].ref}</div>
                <div style={{color:B.text,marginBottom:6,lineHeight:1.4}}>{co.audit[hov].title}</div>
                <div style={{color:B.muted,marginBottom:3}}>Domain: {co.audit[hov].domain}</div>
                <div style={{color:B.muted,marginBottom:3}}>Impact: {co.audit[hov].impact}/10</div>
                <div style={{color:B.muted}}>Detectability: {co.audit[hov].detect}/10</div>
                <div style={{marginTop:8}}><Tag color={RC[co.audit[hov].urg]}>{["IMMEDIATE","ELEVATED","ROUTINE"][co.audit[hov].urg]}</Tag></div>
              </div>
            ) : (
              <div style={{background:B.bg2,border:`1px solid ${B.border}`,borderRadius:6,padding:"12px 14px",fontSize:9,color:B.muted}}>
                Hover over a bubble to see vulnerability details.
                <div style={{marginTop:12,display:"flex",flexDirection:"column",gap:6}}>
                  <div style={{color:B.red}}>● IMMEDIATE: {co.audit.filter(a=>a.urg===0).length} items</div>
                  <div style={{color:B.amber}}>● ELEVATED: {co.audit.filter(a=>a.urg===1).length} items</div>
                  <div style={{color:B.mint}}>● ROUTINE: {co.audit.filter(a=>a.urg===2).length} items</div>
                </div>
              </div>
            )}
          </div>
        </div>
      </Card>

      <Card style={{gridColumn:"1 / -1"}}>
        <Lbl sub="Immediate-action items only | Highest impact + lowest detectability" color={B.red}>
          IMMEDIATE-ACTION VULNERABILITY REGISTER
        </Lbl>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
          {co.audit.filter(a=>a.urg===0).map((a,i) => (
            <div key={i} style={{display:"flex",gap:10,alignItems:"flex-start",
              padding:"10px 12px",borderRadius:6,background:B.redBg,border:`1px solid ${B.red}33`}}>
              <div style={{minWidth:40,textAlign:"center",paddingTop:2}}>
                <div style={{color:B.red,fontSize:7,textTransform:"uppercase"}}>ATV</div>
                <div style={{color:B.red,fontSize:13,fontWeight:800}}>{a.ref}</div>
              </div>
              <div style={{flex:1}}>
                <div style={{color:B.text,fontSize:10,fontWeight:600,marginBottom:3,lineHeight:1.3}}>{a.title}</div>
                <div style={{display:"flex",gap:6,fontSize:8,color:B.muted}}>
                  <span>{a.domain}</span><span>·</span>
                  <span>Imp {a.impact}</span><span>·</span>
                  <span>Det {a.detect}</span>
                </div>
              </div>
              <Tag color={B.red}>IMMEDIATE</Tag>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

// ═══════════════════════ MAIN APP ═══════════════════════════

const TABS = [
  {label:"Overview",        icon:"📊"},
  {label:"Forensic Scores", icon:"🔬"},
  {label:"Forward Proj.",   icon:"📈"},
  {label:"Special",         icon:"⚡"},
  {label:"RAG Matrix",      icon:"🗺️"},
  {label:"China Risk",      icon:"🌏"},
  {label:"CapEx",           icon:"🏗"},
  {label:"Macro Signals",   icon:"📡"},
  {label:"Audit Priorities",icon:"⚠️"},
];

export default function App() {
  const [company, setCompany] = useState("TXN");
  const [tab, setTab] = useState(0);
  const co = COMPANIES[company];

  useEffect(() => {
    const lnk = document.createElement("link");
    lnk.rel = "stylesheet";
    lnk.href = "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap";
    document.head.appendChild(lnk);
    return () => { try { document.head.removeChild(lnk); } catch(e) {} };
  }, []);

  const tabContent = {
    0:<T0_Overview co={co}/>, 1:<T1_Forensic co={co}/>,
    2:<T2_Forward co={co}/>,  3:<T3_Special co={co}/>,
    4:<T4_Rag co={co}/>,      5:<T5_China co={co}/>,
    6:<T6_Capex co={co}/>,    7:<T7_Macro co={co}/>,
    8:<T8_Audit co={co}/>,
  };

  const specialLabel = co.specialType==="sic" ? "⚡ SiC Strategy" : "💰 CapEx Bridge";

  return (
    <div style={{background:B.bg, minHeight:"100vh", fontFamily:"'IBM Plex Mono','Courier New',monospace", color:B.text}}>

      {/* HEADER */}
      <div style={{background:"#040B06", borderBottom:`1px solid ${B.border}`, padding:"12px 24px",
        display:"flex", alignItems:"center", justifyContent:"space-between"}}>
        <div>
          <div style={{color:B.mint,fontSize:8,letterSpacing:"0.26em",textTransform:"uppercase",marginBottom:4}}>
            ▸ DENDRAI // RISK & INTELLIGENCE SYNTHESIZER
          </div>
          <div style={{fontSize:17,fontWeight:600,color:B.ivory,letterSpacing:"-0.02em"}}>
            {co.name} ({co.ticker}) — Industry Risk Dashboard
          </div>
          <div style={{fontSize:9,color:B.muted,marginTop:2}}>
            Internal Audit | Board | Power & Analog Cohort | Through Q4 2027
          </div>
        </div>
        <div style={{display:"flex",flexDirection:"column",gap:6,alignItems:"flex-end"}}>
          <div style={{display:"flex",gap:4}}>
            {["TXN","ON"].map(c => (
              <button key={c} onClick={()=>{setCompany(c);setTab(0);}} style={{
                background: company===c ? COMPANIES[c].accent+"33" : "transparent",
                border:`1.5px solid ${company===c?COMPANIES[c].accent:B.border}`,
                color: company===c ? COMPANIES[c].accent : B.muted,
                borderRadius:4, padding:"4px 16px", cursor:"pointer",
                fontSize:11, fontFamily:"inherit", fontWeight:700, letterSpacing:"0.08em",
                transition:"all 0.15s",
              }}>{c}</button>
            ))}
          </div>
          <div style={{color:co.accent,fontSize:8,letterSpacing:"0.1em"}}>{co.headline}</div>
        </div>
      </div>

      {/* STATUS BAR */}
      <div style={{background:B.bg2,borderBottom:`1px solid ${B.border}`,padding:"5px 24px",
        display:"flex",gap:20,alignItems:"center",overflowX:"auto"}}>
        {[
          {l:"M-Score",  v:co.mScore.toString(), c:co.mScore>-2.22?B.red:B.mint, d:co.mScoreFlag},
          {l:"Z-Score",  v:co.zScore.toString(),  c:co.zScore>2.99?B.mint:B.amber,d:co.zScore>2.99?"Safe Zone":"Grey Zone"},
          {l:"Auto Exp.", v:co.autoExp, c:co.ticker==="ON"?B.amber:B.mint, d:co.ticker==="ON"?"Highest in cohort":"Conservative"},
          {l:"China Exp.",v:co.chinaExp,c:B.amber, d:"Threshold exposure"},
          {l:"CapEx/Rev", v:co.capexRatio,c:co.ticker==="TXN"?B.red:B.amber,d:"vs 15% benchmark"},
          {l:"AQI",       v:co.aqi.toString(),c:co.aqi>1.1?B.amber:B.mint,d:co.aqi>1.1?"⚠ Audit flag":"Normal"},
          {l:"TATA",      v:co.tata.toString(),c:B.mint,d:"Cash > accruals"},
        ].map((s,i) => (
          <div key={i} style={{display:"flex",flexDirection:"column",flexShrink:0}}>
            <div style={{fontSize:7,color:B.muted,letterSpacing:"0.12em",textTransform:"uppercase"}}>{s.l}</div>
            <div style={{fontSize:11,fontWeight:700,color:s.c}}>{s.v}</div>
            <div style={{fontSize:7,color:s.c,opacity:0.7}}>{s.d}</div>
          </div>
        ))}
      </div>

      {/* TAB NAV */}
      <div style={{display:"flex",gap:3,padding:"8px 24px",background:"#050D07",
        borderBottom:`1px solid ${B.border}`,overflowX:"auto"}}>
        {TABS.map((t,i) => {
          const label = i===3 ? specialLabel : `${t.icon} ${t.label.toUpperCase()}`;
          return (
            <button key={i} onClick={()=>setTab(i)} style={{
              background:tab===i?B.greenBg:"transparent",
              border:`1px solid ${tab===i?co.accent:B.border}`,
              color:tab===i?co.accent:B.muted,
              borderRadius:4,padding:"5px 12px",cursor:"pointer",
              fontSize:9,fontFamily:"inherit",whiteSpace:"nowrap",
              transition:"all 0.15s",letterSpacing:"0.06em",
            }}>{label}</button>
          );
        })}
      </div>

      {/* CONTENT */}
      <div style={{padding:"18px 24px 40px"}}>{tabContent[tab]}</div>
    </div>
  );
}
