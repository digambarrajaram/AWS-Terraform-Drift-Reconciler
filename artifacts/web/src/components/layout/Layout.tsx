import { Outlet } from 'react-router-dom';

/**
 * Root layout shell — navigation and chrome go here.
 * Implementation comes later.
 */
export default function Layout() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <Outlet />
    </div>
  );
}
