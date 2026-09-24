import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import InboxPage from './pages/InboxPage'
import ReviewQueuePage from './pages/ReviewQueuePage'
import FaxDetailPage from './pages/FaxDetailPage'
import RoutingConfigPage from './pages/RoutingConfigPage'
import AccuracyPage from './pages/AccuracyPage'
import FilesPage from './pages/FilesPage'
import { ingestApi, type IngestStatus } from './api/client'
import ThemeToggle from './components/ThemeToggle'
import './styles/tokens.css'
import './App.css'

const NAV_LINKS = [
  { to: '/inbox',    label: 'Inbox' },
  { to: '/review',   label: 'Review Queue' },
  { to: '/files',    label: 'Folders' },
  { to: '/routing',  label: 'Routing Config' },
  { to: '/accuracy', label: 'Accuracy' },
]

export default function App() {
  // The watched folder is the whole product, and nothing in the app used to
  // mention it. Naming it in the chrome means it is on screen on every page,
  // not just the one that happens to poll for status.
  const [status, setStatus] = useState<IngestStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    ingestApi.status()
      .then(s => { if (!cancelled) setStatus(s) })
      .catch(() => { /* automation may be off; the footer just stays quiet */ })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark" aria-hidden="true">C</span>
          <span className="brand-text">
            <span className="brand-name">CBWCHC Fax</span>
            <span className="brand-sub">Triage</span>
          </span>
        </div>

        <ul className="nav-list">
          {NAV_LINKS.map(({ to, label }) => (
            <li key={to}>
              <NavLink
                to={to}
                className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
              >
                <span>{label}</span>
                {label === 'Review Queue' && status && status.backlog > 0 && (
                  <span className="nav-count">{status.backlog}</span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="sidebar-footer">
          {status?.enabled && status.inboundPath && (
            <div>
              <div className="k">Watched folder</div>
              <div className="v">{status.inboundPath}</div>
            </div>
          )}
          <ThemeToggle />
        </div>
      </nav>

      <main className="content">
        <Routes>
          <Route path="/" element={<InboxPage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/review" element={<ReviewQueuePage />} />
          <Route path="/faxes/:trackingId" element={<FaxDetailPage />} />
          <Route path="/files" element={<FilesPage />} />
          <Route path="/routing" element={<RoutingConfigPage />} />
          <Route path="/accuracy" element={<AccuracyPage />} />
        </Routes>
      </main>
    </div>
  )
}
