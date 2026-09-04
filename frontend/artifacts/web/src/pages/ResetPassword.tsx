import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { useAppConfig } from '@/api/config';
import { updatePassword } from '@/api/supabaseClient';
import { useAuth } from '@/hooks/useAuth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { errorMessage } from '@/lib/errorUtils';
import {
  isNetworkFailure,
  mapAuthNetworkError,
  mapUpdatePasswordError,
} from '@/lib/authErrors';

const EXPIRED_LINK_MESSAGE =
  'Reset link is invalid or expired. Request a new one from the sign-in page.';

export default function ResetPassword() {
  const { session, loading: authLoading } = useAuth();
  const { data: config, isLoading: configLoading, error: configError } = useAppConfig();
  const navigate = useNavigate();

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [confirmError, setConfirmError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const linkInvalid = !authLoading && !configLoading && !session;

  if (authLoading || configLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!config) {
      setError(configError ? errorMessage(configError) : 'App config unavailable.');
      return;
    }
    if (!session) {
      setError(EXPIRED_LINK_MESSAGE);
      return;
    }

    const pwdErr = !password ? 'Password is required.' : '';
    const confirmErr = !confirm
      ? 'Please confirm your password.'
      : password !== confirm
        ? 'Passwords do not match.'
        : '';

    setPasswordError(pwdErr);
    setConfirmError(confirmErr);
    setError('');

    if (pwdErr || confirmErr) return;

    setSubmitting(true);
    try {
      const result = await updatePassword(config, password);
      if (result.error) {
        if (isNetworkFailure(result.error)) {
          setError(mapAuthNetworkError());
        } else {
          setError(mapUpdatePasswordError(result.error));
        }
        return;
      }
      toast.success('Password updated successfully.');
      navigate('/', { replace: true });
    } catch (err) {
      setError(isNetworkFailure(err) ? mapAuthNetworkError() : errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-xl">
        <h1 className="mb-1 text-lg font-semibold text-card-foreground">
          Set new password
        </h1>
        <p className="mb-4 text-sm text-muted-foreground">
          {session
            ? 'Choose a new password for your Drift account.'
            : 'Open the reset link from your email to continue.'}
        </p>

        {linkInvalid && (
          <Alert variant="destructive" className="mb-4 py-2">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="text-xs">
              {EXPIRED_LINK_MESSAGE}{' '}
              <Link
                to="/login"
                className="font-medium underline underline-offset-4"
              >
                Request a new reset link
              </Link>
            </AlertDescription>
          </Alert>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <Input
              type="password"
              autoFocus={!!session}
              autoComplete="new-password"
              placeholder="New password"
              value={password}
              disabled={!session}
              aria-invalid={!!passwordError}
              onChange={(e) => {
                setPassword(e.target.value);
                setPasswordError('');
                setError('');
              }}
            />
            {passwordError && (
              <p className="text-xs text-destructive">{passwordError}</p>
            )}
          </div>

          <div className="space-y-1">
            <Input
              type="password"
              autoComplete="new-password"
              placeholder="Confirm new password"
              value={confirm}
              disabled={!session}
              aria-invalid={!!confirmError}
              onChange={(e) => {
                setConfirm(e.target.value);
                setConfirmError('');
                setError('');
              }}
            />
            {confirmError && (
              <p className="text-xs text-destructive">{confirmError}</p>
            )}
          </div>

          {error && session && (
            <Alert variant="destructive" className="py-2">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription className="text-xs">{error}</AlertDescription>
            </Alert>
          )}

          <Button type="submit" className="w-full" disabled={submitting || !session}>
            {submitting ? (
              <>
                <Spinner />
                Updating…
              </>
            ) : (
              'Update password'
            )}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm text-muted-foreground">
          <Link
            to="/login"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Back to sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
