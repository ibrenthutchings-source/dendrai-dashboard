import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider, SignedIn, SignedOut, SignIn } from '@clerk/react'
import './index.css'
import App from './App.jsx'

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ClerkProvider publishableKey={publishableKey}>
      <SignedIn>
        <App />
      </SignedIn>
      <SignedOut>
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#E8F5F0',
          fontFamily: "'IBM Plex Mono','Courier New',monospace",
        }}>
          <div style={{ marginBottom: 32, textAlign: 'center' }}>
            <div style={{ color: '#2BCC99', fontSize: 9, letterSpacing: '0.26em', textTransform: 'uppercase', fontWeight: 800, marginBottom: 4 }}>
              ▸ DENDRAI QUANT_ENGINE
            </div>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#1A1F1D' }}>Risk & Intelligence Synthesizer</div>
          </div>
          <SignIn routing="hash" />
        </div>
      </SignedOut>
    </ClerkProvider>
  </StrictMode>,
)
