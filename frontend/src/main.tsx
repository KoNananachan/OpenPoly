// Must precede every other import: installs the demo fetch mock (when
// __DEMO__) before App's store graph mounts and fires its first poll.
import './demo/bootstrap'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
