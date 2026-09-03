import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  ScanSearch,
  GitPullRequest,
  ClipboardCheck,
  RotateCcw,
  TrendingUp,
  AlertTriangle,
  Bell,
  Globe,
  Compass,
  Menu,
} from 'lucide-react';
import ScopeSelector from './ScopeSelector';
import ThemeToggle from './ThemeToggle';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';

const NAV_ITEMS = [
  { label: 'Overview',     to: '/',             icon: LayoutDashboard },
  { label: 'Scan',         to: '/scan',         icon: ScanSearch      },
  { label: 'PR Queue',     to: '/pr-queue',     icon: GitPullRequest  },
  { label: 'Approvals',    to: '/approvals',    icon: ClipboardCheck  },
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
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { search } = useLocation();

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      {/* ── Sidebar ── */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-sidebar md:flex">
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
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3 sm:px-4 gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              aria-label="Open navigation"
              onClick={() => setMobileNavOpen(true)}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground md:hidden"
            >
              <Menu size={17} />
            </button>
            <ScopeSelector />
          </div>
          <ThemeToggle />
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>

      <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <SheetContent side="left" className="w-64 bg-sidebar p-0">
          <SheetHeader className="h-14 border-b border-sidebar-border px-4 text-left">
            <SheetTitle className="text-sm text-sidebar-foreground">Drift</SheetTitle>
          </SheetHeader>
          <nav className="py-3">
            <ul className="space-y-0.5 px-2">
              {NAV_ITEMS.map(({ label, to, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={{ pathname: to, search }}
                    end={to === '/'}
                    onClick={() => setMobileNavOpen(false)}
                    className={({ isActive }) => [
                      'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
                      isActive
                        ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                        : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
                    ].join(' ')}
                  >
                    <Icon size={15} className="shrink-0" />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </SheetContent>
      </Sheet>
    </div>
  );
}
