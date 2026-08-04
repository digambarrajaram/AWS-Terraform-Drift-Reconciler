import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';

interface Props {
  children: React.ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * Root-level error boundary. Catches unhandled render errors across the whole
 * app and shows a recovery screen instead of a blank page.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  handleReset = () => {
    this.setState({ error: null });
    // Hard-reload so the QueryClient and all module state is fresh.
    window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex h-screen flex-col items-center justify-center gap-6 bg-background px-6 text-center">
        <div className="flex items-center justify-center h-14 w-14 rounded-full bg-destructive/10">
          <AlertTriangle size={26} className="text-destructive" />
        </div>

        <div className="space-y-1.5 max-w-md">
          <h1 className="text-lg font-semibold text-foreground">Something went wrong</h1>
          <p className="text-sm text-muted-foreground">
            An unexpected error crashed the application. The details below may help if you
            need to report this.
          </p>
        </div>

        <pre className="max-w-xl w-full rounded-xl border border-border bg-muted px-4 py-3 text-left text-[11px] font-mono text-muted-foreground whitespace-pre-wrap break-all overflow-auto max-h-40">
          {error.message}
          {error.stack ? `\n\n${error.stack}` : ''}
        </pre>

        <button
          type="button"
          onClick={this.handleReset}
          className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
        >
          <RotateCcw size={14} /> Reload application
        </button>
      </div>
    );
  }
}
