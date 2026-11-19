import React from 'react'
import ReactDOM from 'react-dom/client'

window.React = React

import './styles.css'
import './styles-modules.css'
// These modules register their components on `window` as a side effect.
import './components.jsx'
import './auth.jsx'
import './risk-register-review.jsx'

const { AuthProvider, useAuth, LoginScreen, RiskRegisterReviewScreen } = window

// Standalone host: no pipeline run is in flight, so the register loads its
// risks, controls and saved assignments straight from the database.
function Shell() {
  const auth = useAuth()
  if (auth?.user === undefined) return null          // session check in flight
  if (!auth?.user) return <LoginScreen />
  return (
    <div style={{ padding: 24 }}>
      <RiskRegisterReviewScreen risks={null} runId={null} ticker="" onConverted={() => {}} />
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <AuthProvider><Shell /></AuthProvider>
)
