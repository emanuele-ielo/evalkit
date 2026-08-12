import { useEffect, useState } from 'react'
import Campaigns from './views/Campaigns'
import Campaign from './views/Campaign'
import Attempt from './views/Attempt'

type Route =
  | { name: 'campaigns' }
  | { name: 'campaign'; id: string }
  | { name: 'attempt'; id: string; scenario: string; round: number }

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const parts = raw.split('/').filter(Boolean).map(decodeURIComponent)
  if (parts[0] === 'c' && parts[1]) {
    if (parts[2] === 'a' && parts[3] && parts[4]) {
      return { name: 'attempt', id: parts[1], scenario: parts[3], round: Number(parts[4]) || 1 }
    }
    return { name: 'campaign', id: parts[1] }
  }
  return { name: 'campaigns' }
}

export function go(path: string): void {
  window.location.hash = path
}

/** Dark is the design's canonical look, so it is also the default. */
function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => localStorage.getItem('evalkit-theme') || 'dark')
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('evalkit-theme', theme)
  }, [theme])
  const cycle = () => setTheme((current) => (current === 'dark' ? 'light' : current === 'light' ? 'system' : 'dark'))
  return [theme, cycle]
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash)
  const [theme, cycleTheme] = useTheme()

  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-dot" />
          evalkit
        </a>
        <nav className="crumbs">
          {route.name !== 'campaigns' && (
            <>
              <span className="sep">/</span>
              <a href={`#/c/${encodeURIComponent(route.id)}`} className={route.name === 'campaign' ? 'current' : ''}>
                {route.id}
              </a>
            </>
          )}
          {route.name === 'attempt' && (
            <>
              <span className="sep">/</span>
              <span className="current">
                {route.scenario} · round {route.round}
              </span>
            </>
          )}
        </nav>
        <span className="spacer" />
        <button className="ghost" onClick={cycleTheme} title="dark / light / system">
          {theme}
        </button>
      </header>

      <main className={`content${route.name === 'attempt' ? ' split' : ''}`}>
        {route.name === 'campaigns' && <Campaigns />}
        {route.name === 'campaign' && <Campaign id={route.id} />}
        {route.name === 'attempt' && <Attempt id={route.id} scenario={route.scenario} round={route.round} />}
      </main>
    </div>
  )
}
