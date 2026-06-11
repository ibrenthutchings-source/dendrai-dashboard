import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider, SignIn, useAuth } from '@clerk/react'
import './index.css'
import App from './App.jsx'

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

function AuthGate() {
  const { isSignedIn, isLoaded } = useAuth()

  if (!isLoaded) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#E8F5F0', fontFamily: "'IBM Plex Mono','Courier New',monospace" }}>
        <div style={{ fontSize: 11, color: '#5A6B65' }}>Loading...</div>
      </div>
    )
  }

  if (!isSignedIn) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: '#E8F5F0', fontFamily: "'IBM Plex Mono','Courier New',monospace" }}>
        <div style={{ marginBottom: 32, textAlign: 'center' }}>
          <div style={{ color: '#2BCC99', fontSize: 9, letterSpacing: '0.26em', textTransform: 'uppercase', fontWeight: 800, marginBottom: 4 }}>
            ▸ DENDRAI QUANT_ENGINE
          </div>
          <div style={{ fontSize: 20, fontWeight: 800, color: '#1A1F1D' }}>Risk & Intelligence Synthesizer</div>
        </div>
        <SignIn routing="hash" />
      </div>
    )
  }

  return <App />
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ClerkProvider publishableKey={publishableKey}>
      <AuthGate />
    </ClerkProvider>
  </StrictMode>,
)
