import { Link, useLocation } from 'react-router-dom';
import { Compass } from 'lucide-react';

export default function NotFound() {
  const { search } = useLocation(); // preserve ?scope= when linking home

  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 px-6 text-center">
      <div className="flex items-center justify-center h-16 w-16 rounded-full bg-muted">
        <Compass size={28} className="text-muted-foreground" />
      </div>

      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tabular-nums text-foreground">404</h1>
        <p className="text-sm text-muted-foreground">
          This page doesn't exist. Check the URL or navigate back to the dashboard.
        </p>
      </div>

      <Link
        to={{ pathname: '/', search }}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
      >
        Back to Overview
      </Link>
    </div>
  );
}
