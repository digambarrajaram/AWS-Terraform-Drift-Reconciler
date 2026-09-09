import { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { CheckCircle2, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { useAppConfig, type AppConfig } from '@/api/config';
import {
  resendSignupConfirmation,
  resetPasswordForEmail,
  signIn,
  signInWithGoogle,
  signUp,
} from '@/api/supabaseClient';
import {
  establishSession,
  setSupabaseAccessToken,
} from '@/api/apiFetch';
import { useAuth } from '@/hooks/useAuth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { errorMessage } from '@/lib/errorUtils';
import {
  DUPLICATE_EMAIL_MESSAGE,
  isEmailNotConfirmedError,
  isNetworkFailure,
  isValidEmail,
  mapAuthNetworkError,
  mapResetPasswordRequestError,
  mapSignInError,
  mapSignUpError,
  RESET_EMAIL_SENT_MESSAGE,
  SIGNUP_SUCCESS_MESSAGE,
} from '@/lib/authErrors';
import { authRedirectUrl } from '@/lib/appUrl';

type Mode = 'signin' | 'signup' | 'forgot';

function GoogleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  );
}

function loginRedirectUrl(config: AppConfig): string {
  return authRedirectUrl('login', config);
}

function googleOAuthRedirectUrl(): string {
  return `${window.location.origin}/dashboard`;
}

function resetRedirectUrl(config: AppConfig): string {
  return authRedirectUrl('reset-password', config);
}

function clearFormFeedback(
  setters: {
    setError: (v: string) => void;
    setStatusMessage: (v: string) => void;
    setEmailError: (v: string) => void;
    setPasswordError: (v: string) => void;
    setShowResendConfirm: (v: boolean) => void;
  },
) {
  setters.setError('');
  setters.setStatusMessage('');
  setters.setEmailError('');
  setters.setPasswordError('');
  setters.setShowResendConfirm(false);
}

export default function Login() {
  const { session, loading: authLoading } = useAuth();
  const { data: config, isLoading: configLoading, error: configError } = useAppConfig();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname || '/';

  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [statusMessage, setStatusMessage] = useState('');
  const [emailError, setEmailError] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [showResendConfirm, setShowResendConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [oauthLoading, setOauthLoading] = useState(false);
  const [resendingConfirm, setResendingConfirm] = useState(false);

  const feedbackSetters = {
    setError,
    setStatusMessage,
    setEmailError,
    setPasswordError,
    setShowResendConfirm,
  };

  if (authLoading || configLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (session) {
    return <Navigate to={from} replace />;
  }

  function validateEmailField(value: string): string {
    const trimmed = value.trim();
    if (!trimmed) return 'Email is required.';
    if (!isValidEmail(trimmed)) return 'Enter a valid email address.';
    return '';
  }

  function validatePasswordField(value: string, required: boolean): string {
    if (!required) return '';
    if (!value) return 'Password is required.';
    return '';
  }

  function switchMode(next: Mode) {
    setMode(next);
    clearFormFeedback(feedbackSetters);
    if (next === 'forgot') setPassword('');
  }

  async function handleGoogleSignIn() {
    if (!config) {
      const message = configError ? errorMessage(configError) : 'App config unavailable.';
      setError(message);
      toast.error(message);
      return;
    }

    clearFormFeedback(feedbackSetters);
    setOauthLoading(true);
    try {
      const result = await signInWithGoogle(config, googleOAuthRedirectUrl());
      if (result.error) {
        const message = isNetworkFailure(result.error)
          ? mapAuthNetworkError()
          : result.error.message || 'Google sign-in failed. Please try again.';
        setError(message);
        toast.error(message);
        setOauthLoading(false);
        return;
      }
      // Browser redirects to Google; keep loading until navigation leaves the page.
    } catch (err) {
      const message = isNetworkFailure(err) ? mapAuthNetworkError() : errorMessage(err);
      setError(message);
      toast.error(message);
      setOauthLoading(false);
    }
  }

  async function handleResendConfirmation() {
    if (!config) return;
    const trimmedEmail = email.trim();
    const emailErr = validateEmailField(trimmedEmail);
    if (emailErr) {
      setEmailError(emailErr);
      return;
    }

    setResendingConfirm(true);
    setError('');
    setStatusMessage('');
    try {
      const result = await resendSignupConfirmation(
        config,
        trimmedEmail,
        loginRedirectUrl(config),
      );
      if (result.error) {
        if (isNetworkFailure(result.error)) {
          setError(mapAuthNetworkError());
        } else {
          setError(result.error.message || 'Could not resend confirmation email.');
        }
        return;
      }
      setStatusMessage('Confirmation email sent. Check your inbox.');
      setShowResendConfirm(false);
    } catch (err) {
      setError(isNetworkFailure(err) ? mapAuthNetworkError() : errorMessage(err));
    } finally {
      setResendingConfirm(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!config) {
      setError(configError ? errorMessage(configError) : 'App config unavailable.');
      return;
    }

    const trimmedEmail = email.trim();
    const emailErr = validateEmailField(trimmedEmail);
    const passwordErr =
      mode === 'forgot' ? '' : validatePasswordField(password, true);

    setEmailError(emailErr);
    setPasswordError(passwordErr);
    setError('');
    setStatusMessage('');
    setShowResendConfirm(false);

    if (emailErr || passwordErr) return;

    if (mode === 'forgot') {
      setSubmitting(true);
      try {
        const result = await resetPasswordForEmail(
          config,
          trimmedEmail,
          resetRedirectUrl(config),
        );
        if (result.error) {
          if (isNetworkFailure(result.error)) {
            setError(mapAuthNetworkError());
          } else {
            setError(mapResetPasswordRequestError(result.error));
          }
          return;
        }
        setStatusMessage(RESET_EMAIL_SENT_MESSAGE);
        setMode('signin');
        setPassword('');
      } catch (err) {
        setError(isNetworkFailure(err) ? mapAuthNetworkError() : errorMessage(err));
      } finally {
        setSubmitting(false);
      }
      return;
    }

    setSubmitting(true);
    try {
      const result =
        mode === 'signin'
          ? await signIn(config, trimmedEmail, password)
          : await signUp(config, trimmedEmail, password, loginRedirectUrl(config));

      if (result.error) {
        if (isNetworkFailure(result.error)) {
          setError(mapAuthNetworkError());
        } else if (mode === 'signin') {
          setError(mapSignInError(result.error));
          if (isEmailNotConfirmedError(result.error)) {
            setShowResendConfirm(true);
          }
        } else {
          setError(mapSignUpError(result.error));
        }
        return;
      }

      if (mode === 'signup' && !result.data.session) {
        if (result.data.user?.identities?.length === 0) {
          setError(DUPLICATE_EMAIL_MESSAGE);
          setMode('signin');
          return;
        }
        setStatusMessage(SIGNUP_SUCCESS_MESSAGE);
        setMode('signin');
        setPassword('');
        return;
      }

      const accessToken = result.data.session?.access_token;
      if (accessToken) {
        setSupabaseAccessToken(accessToken);
        await establishSession();
      }

      navigate(from, { replace: true });
    } catch (err) {
      setError(isNetworkFailure(err) ? mapAuthNetworkError() : errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const title =
    mode === 'signin'
      ? 'Sign in'
      : mode === 'signup'
        ? 'Create account'
        : 'Reset password';
  const subtitle =
    mode === 'signin'
      ? 'Sign in with your email to access Drift.'
      : mode === 'signup'
        ? 'Create an account to use this Drift instance.'
        : 'Enter your email and we will send a reset link.';

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-xl">
        <h1 className="mb-1 text-lg font-semibold text-card-foreground">{title}</h1>
        <p className="mb-4 text-sm text-muted-foreground">{subtitle}</p>

        {mode !== 'forgot' && (
          <div className="mb-4 space-y-3">
            <Button
              type="button"
              variant="outline"
              className="w-full rounded-md"
              disabled={oauthLoading || submitting}
              onClick={handleGoogleSignIn}
            >
              {oauthLoading ? (
                <>
                  <Spinner />
                  Redirecting…
                </>
              ) : (
                <>
                  <GoogleIcon className="size-4" />
                  Continue with Google
                </>
              )}
            </Button>
            <div className="relative flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">or</span>
              <div className="h-px flex-1 bg-border" />
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <Input
              type="email"
              autoFocus
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              aria-invalid={!!emailError}
              onChange={(e) => {
                setEmail(e.target.value);
                setEmailError('');
                setError('');
                setStatusMessage('');
                setShowResendConfirm(false);
              }}
            />
            {emailError && (
              <p className="text-xs text-destructive">{emailError}</p>
            )}
          </div>

          {mode !== 'forgot' && (
            <div className="space-y-1">
              <Input
                type="password"
                autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                placeholder="Password"
                value={password}
                aria-invalid={!!passwordError}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setPasswordError('');
                  setError('');
                  setStatusMessage('');
                  setShowResendConfirm(false);
                }}
              />
              {passwordError && (
                <p className="text-xs text-destructive">{passwordError}</p>
              )}
            </div>
          )}

          {error && (
            <Alert variant="destructive" className="py-2">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription className="text-xs">{error}</AlertDescription>
            </Alert>
          )}

          {statusMessage && (
            <div
              role="status"
              className="flex items-start gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-800 dark:text-emerald-300"
            >
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{statusMessage}</span>
            </div>
          )}

          {showResendConfirm && mode === 'signin' && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="w-full"
              disabled={resendingConfirm || submitting || oauthLoading}
              onClick={handleResendConfirmation}
            >
              {resendingConfirm ? (
                <>
                  <Spinner />
                  Sending…
                </>
              ) : (
                'Resend confirmation email'
              )}
            </Button>
          )}

          <Button type="submit" className="w-full" disabled={submitting || oauthLoading}>
            {submitting ? (
              <>
                <Spinner />
                {mode === 'signin'
                  ? 'Signing in…'
                  : mode === 'signup'
                    ? 'Creating account…'
                    : 'Sending link…'}
              </>
            ) : mode === 'signin' ? (
              'Sign in'
            ) : mode === 'signup' ? (
              'Sign up'
            ) : (
              'Send reset link'
            )}
          </Button>
        </form>

        {mode === 'signin' && (
          <p className="mt-3 text-center text-sm">
            <button
              type="button"
              className="font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              onClick={() => switchMode('forgot')}
            >
              Forgot password?
            </button>
          </p>
        )}

        <p className="mt-4 text-center text-sm text-muted-foreground">
          {mode === 'signin' ? (
            <>
              No account?{' '}
              <button
                type="button"
                className="font-medium text-foreground underline-offset-4 hover:underline"
                onClick={() => switchMode('signup')}
              >
                Sign up
              </button>
            </>
          ) : (
            <>
              Remember your password?{' '}
              <button
                type="button"
                className="font-medium text-foreground underline-offset-4 hover:underline"
                onClick={() => switchMode('signin')}
              >
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
