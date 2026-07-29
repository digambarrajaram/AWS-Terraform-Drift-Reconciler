import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  ScanSearch,
  GitPullRequest,
  RotateCcw,
  TrendingUp,
  AlertTriangle,
  Bell,
  Globe,
  Compass,
} from 'lucide-react';
import ScopeSelector from './ScopeSelector';
import ThemeToggle from './ThemeToggle';

const NAV_ITEMS = [
  { label: 'Overview',     to: '/',             icon: LayoutDashboard },
  { label: 'Scan',         to: '/scan',         icon: ScanSearch      },
  { label: 'PR Queue',     to: '/pr-queue',     icon: GitPullRequest  },
  { label: 'Rollback',     to: '/rollback',     icon: RotateCcw       },
  { label: 'Trends',       to: '/trends',       icon: TrendingUp      },
  { label: 'Exceptions',   to: '/exceptions',   icon: AlertTriangle   },
  { label: 'Alerts',       to: '/alerts',       icon: Bell            },
  { label: 'Environments', to: '/environments', icon: Globe           },
  { label: 'Explorer',     to: '/explorer',     icon: Compass         },
];

/**
 * A NavLink that preserves the current search string (?scope=...) when
 * navigating between pages. Without this, clicking any nav item strips
 * the scope param and every page re-defaults to the first environment.
 */
function ScopeNavLink({
  to,
  end,
  children,
}: {
  to: string;
  end?: boolean;
  children: (props: { isActive: boolean }) => React.ReactNode;
}) {
  const { search } = useLocation();
  return (
    <NavLink to={{ pathname: to, search }} end={end}>
      {children}
    </NavLink>
  );
}

export default function AppShell() {
  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      {/* ── Sidebar ── */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-sidebar">
        {/* Logo / wordmark */}
        <div className="flex h-14 items-center px-4 border-b border-sidebar-border">
          <span className="text-sm font-semibold tracking-tight text-sidebar-foreground">
            Drift
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-3">
          <ul className="space-y-0.5 px-2">
            {NAV_ITEMS.map(({ label, to, icon: Icon }) => (
              <li key={to}>
                <ScopeNavLink to={to} end={to === '/'}>
                  {({ isActive }) => (
                    <span
                      className={[
                        'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors',
                        isActive
                          ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                          : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
                      ].join(' ')}
                    >
                      <Icon size={15} className="shrink-0" />
                      {label}
                    </span>
                  )}
                </ScopeNavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      {/* ── Main column ── */}
      <div className="flex flex-1 flex-col overflow-hidden min-w-0">
        {/* Header */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4 gap-4">
          <ScopeSelector />
          <ThemeToggle />
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
