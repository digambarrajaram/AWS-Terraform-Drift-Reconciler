import { Link } from 'react-router-dom';
import { useScope } from '@/hooks/useScope';
import { useEnvironments } from '@/hooks/useEnvironments';

export default function ScopeSelector() {
  const { scope, setScope } = useScope();
  const { activeEnvironments, isLoading } = useEnvironments();

  if (isLoading) {
    return (
      <div className="h-8 w-32 animate-pulse rounded-md bg-muted" />
    );
  }

  if (activeEnvironments.length === 0) {
    return (
      <span className="text-xs text-muted-foreground">
        No active environments.{' '}
        <Link to="/environments" className="underline underline-offset-2 hover:text-foreground">
          Manage
        </Link>
      </span>
    );
  }

  // Tabs for ≤4 environments; dropdown for more
  if (activeEnvironments.length <= 4) {
    return (
      <div className="flex items-center gap-1 rounded-lg border border-border bg-muted p-0.5">
        {activeEnvironments.map((env) => (
          <button
            key={env.slug}
            type="button"
            onClick={() => setScope(env.slug)}
            className={[
              'rounded-md px-3 py-1 text-xs font-medium transition-colors',
              scope === env.slug
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            ].join(' ')}
          >
            {env.name}
          </button>
        ))}
      </div>
    );
  }

  // Dropdown for >4
  return (
    <select
      value={scope ?? ''}
      onChange={(e) => setScope(e.target.value)}
      className="rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
    >
      {activeEnvironments.map((env) => (
        <option key={env.slug} value={env.slug}>
          {env.name}
        </option>
      ))}
    </select>
  );
}
