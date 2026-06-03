import { NavLink, Outlet } from 'react-router-dom'

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    'px-3 py-1 rounded transition-colors',
    isActive
      ? 'bg-neutral-800 text-neutral-100'
      : 'text-neutral-400 hover:text-neutral-100 hover:bg-neutral-900',
  ].join(' ')

export function AppShell() {
  return (
    <div className="h-screen flex flex-col bg-neutral-950 text-neutral-100 overflow-hidden">
      <header className="border-b border-neutral-800">
        <div className="flex items-center gap-6 px-6 h-12">
          <span className="font-medium tracking-tight">openPoly</span>
          <nav className="flex gap-1 text-sm">
            <NavLink to="/strategy" className={navLinkClass}>
              Strategy
            </NavLink>
            <NavLink to="/activity" className={navLinkClass}>
              Activity
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="flex-1 min-h-0">
        <Outlet />
      </main>
    </div>
  )
}
