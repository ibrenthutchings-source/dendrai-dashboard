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
import '../cem.jsx'
import '../forecasts.jsx'
import '../scenarios.jsx'
import '../scenario-analysis.jsx'
import '../flow.jsx'
import '../rail.jsx'
import '../governance.jsx'
import '../report.jsx'
import '../risk-approval.jsx'
import '../rss.jsx'
import '../audit-scope-review.jsx'
import '../nav.jsx'
import '../config-screen.jsx'
import '../ubo-config.jsx'
import '../ai-chat-panel.jsx'
import '../audit-scope.jsx'
import '../auth.jsx'
import '../code-screens.jsx'
import '../risk-register-review.jsx'
import '../sox-hitl.jsx'
import '../sox-scope.jsx'
import '../coverage-gap.jsx'
import '../approval-inbox.jsx'
import '../tweaks.jsx'

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
