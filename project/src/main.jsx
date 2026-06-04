import React from 'react'
import ReactDOM from 'react-dom/client'

window.React = React

import '../mock-data.js'
import '../live-data.js'
import '../forecasting.js'
import '../backtesting.js'
import '../rss-engine.js'
import '../tweaks-panel.jsx'
import '../components.jsx'
import '../charts.jsx'
import '../sidebar.jsx'
import '../pipeline.jsx'
import '../cem.jsx'
import '../forecasts.jsx'
import '../scenarios.jsx'
import '../flow.jsx'
import '../rail.jsx'
import '../report.jsx'
import '../risk-approval.jsx'
import '../rss.jsx'
import '../audit-scope-review.jsx'
import '../tweaks.jsx'

import App from '../app.jsx'

const root = ReactDOM.createRoot(document.getElementById('root'))
root.render(<App />)
