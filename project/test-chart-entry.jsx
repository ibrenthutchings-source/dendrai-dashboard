// Standalone chart test entry — served by vite at /test-chart-entry.jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
window.React = React

import './mock-data.js'
import './live-data.js'
import './forecasting.js'
import './backtesting.js'
import './charts.jsx'

const history = [
  {q:'Q1-23',v:1823.45},{q:'Q2-23',v:1956.78},{q:'Q3-23',v:2134.56},{q:'Q4-23',v:2287.90},
  {q:'Q1-24',v:2103.23},{q:'Q2-24',v:2456.78},{q:'Q3-24',v:2678.90},{q:'Q4-24',v:2891.23},
]
const forecast = [
  {q:'Q1-25',base:2934.56,lo:2756.34,hi:3112.78},
  {q:'Q2-25',base:3012.89,lo:2834.67,hi:3190.11},
  {q:'Q3-25',base:3089.12,lo:2911.90,hi:3266.34},
  {q:'Q4-25',base:3145.67,lo:2967.45,hi:3323.89},
]

const FC = window.ForecastChart

function TestApp() {
  return (
    <div style={{padding: 24, maxWidth: 640, fontFamily: 'system-ui, sans-serif'}}>
      <h2 style={{fontSize:15, marginBottom:4}}>ForecastChart — feature test</h2>
      <p style={{fontSize:11, color:'#666', margin:'0 0 20px'}}>
        Verifying: 2dp on revenue · RMSE/MAPE/R²/MAE/TME caption · MAE-band confidence interval
      </p>

      <div style={{border:'1px solid #e5e7eb', borderRadius:10, padding:'14px 18px', marginBottom:20}}>
        <div style={{fontSize:12, fontWeight:600, marginBottom:8}}>Revenue TTM ($M) — decimals=2, green MAPE caption</div>
        <FC history={history} forecast={forecast} unit="$M" color="var(--acc)" decimals={2}
          chartMetrics={{rmse:145.23, mape:4.87, r2:0.923, mae:112.45, tme:-23.12}} />
      </div>

      <div style={{border:'1px solid #e5e7eb', borderRadius:10, padding:'14px 18px', marginBottom:20}}>
        <div style={{fontSize:12, fontWeight:600, marginBottom:8}}>Revenue TTM ($M) — amber MAPE (5–15%)</div>
        <FC history={history} forecast={forecast} unit="$M" color="var(--acc)" decimals={2}
          chartMetrics={{rmse:289.45, mape:11.4, r2:0.71, mae:234.12, tme:45.67}} />
      </div>

      <div style={{border:'1px solid #e5e7eb', borderRadius:10, padding:'14px 18px', marginBottom:20}}>
        <div style={{fontSize:12, fontWeight:600, marginBottom:8}}>Gross Margin (%) — no caption (no chartMetrics)</div>
        <FC history={history.map(d=>({...d,v:+(d.v/50).toFixed(1)}))}
            forecast={forecast.map(d=>({...d,base:+(d.base/50).toFixed(1),lo:+(d.lo/50).toFixed(1),hi:+(d.hi/50).toFixed(1)}))}
            unit="%" color="var(--violet)" />
      </div>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(<TestApp />)
