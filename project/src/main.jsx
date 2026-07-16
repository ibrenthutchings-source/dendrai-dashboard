import React from 'react'
import ReactDOM from 'react-dom/client'

window.React = React

import '../mock-data.js'
import '../live-data.js'
import '../mcp-data.js'
import '../risk-engine.js'
import '../forecasting.js'
import '../backtesting.js'
import '../rss-engine.js'
import '../tweaks-panel.jsx'
import '../components.jsx'
import '../charts.jsx'
import '../sidebar.jsx'
import '../pipeline.jsx'
import '../forecasts.jsx'
import '../rail.jsx'
import '../risk-approval.jsx'
import '../rss.jsx'
import '../audit-scope-review.jsx'
import '../report.jsx'
import '../coverage-gap.jsx'
import '../risk-register-review.jsx'
import '../cem.jsx'
import '../nav.jsx'
import '../ai-chat-panel.jsx'
import '../auth.jsx'
import '../tweaks.jsx'

// The remaining screen-level files (scenarios, scenario-analysis, flow,
// governance, config-screen, ubo-config, audit-scope, code-screens,
// sox-hitl, sox-scope, approval-inbox, user-config, token-usage,
// model-health, continuous-monitoring, ai-inventory) are no longer
// imported here — they are lazy-loaded on first navigation via
// React.lazy() in app.jsx (see src/lazy-screen.js).
//
// Everything above stays eager because it's reachable outside the
// activeScreen Suspense boundary (pipeline.jsx is the landing screen and
// unconditionally/guardedly renders Rail, RSSPanel, CoverageGapPanel;
// app.jsx renders OverrideModal, AdjustRiskModal, AdjustObjectiveModal
// unconditionally at the root; audit-scope-review.jsx and risk-approval.jsx
// read window.MASTER_CONTROLS / window.FW_MOCK_RISKS from
// risk-register-review.jsx during the core Gate 1/2 HITL flow; app.jsx's
// live-mode 8-K event ingestion uses cem.jsx's TIERS/notifMsgFor directly,
// unguarded, independent of which screen is active) — verified via direct
// grep, not assumed.

import App from '../app.jsx'

class RootBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (this.state.err) {
      return (
        <div style={{padding: 48, fontFamily: 'system-ui, sans-serif', background: '#fff', minHeight: '100vh'}}>
          <div style={{fontSize: 14, fontWeight: 600, color: '#111', marginBottom: 8}}>Application error — something went wrong.</div>
          <div style={{fontSize: 11, color: '#888', fontFamily: 'monospace', marginBottom: 20, whiteSpace: 'pre-wrap', maxWidth: 640}}>
            {this.state.err?.message || 'Unknown error'}
            {this.state.err?.stack ? '\n\n' + this.state.err.stack.slice(0, 600) : ''}
          </div>
          <button style={{fontSize: 12, padding: '6px 18px', borderRadius: 6, border: '1px solid #ddd', cursor: 'pointer', background: '#f8f8f8'}}
            onClick={() => this.setState({ err: null })}>
            Dismiss and retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const { AuthProvider } = window;

const root = ReactDOM.createRoot(document.getElementById('root'))
root.render(
  <RootBoundary>
    <AuthProvider>
      <App />
    </AuthProvider>
  </RootBoundary>
)
