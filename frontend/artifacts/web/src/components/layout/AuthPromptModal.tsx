import { useState } from 'react';
import { useAuthStore } from '@/hooks/useAuthStore';
import { saveToken } from '@/api/apiFetch';

export default function AuthPromptModal() {
  const needsToken = useAuthStore((s) => s.needsToken);
  const setNeedsToken = useAuthStore((s) => s.setNeedsToken);
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  if (!needsToken) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) {
      setError('Token cannot be empty.');
      return;
    }
    saveToken(trimmed);
    setNeedsToken(false);
    setValue('');
    setError('');
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-xl">
        <h2 className="mb-1 text-lg font-semibold text-card-foreground">
          API Access Token Required
        </h2>
        <p className="mb-4 text-sm text-muted-foreground">
          Enter the access token for this Drift instance. It will be stored
          locally in your browser.
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            type="password"
            autoFocus
            placeholder="Paste token here…"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError('');
            }}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />

          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}

          <button
            type="submit"
            className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            Save Token
          </button>
        </form>
      </div>
    </div>
  );
}
