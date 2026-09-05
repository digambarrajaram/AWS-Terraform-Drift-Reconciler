/** Supabase Auth error mapping and client-side validation for login flows. */

export type AuthErr = {
  message?: string;
  status?: number | null;
  code?: string;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(email: string): boolean {
  return EMAIL_RE.test(email.trim());
}

export function isNetworkFailure(err: unknown): boolean {
  if (err instanceof TypeError) {
    const msg = err.message.toLowerCase();
    if (msg.includes('failed to fetch') || msg.includes('network')) return true;
  }
  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();
  return /fetch|network|connection|econnrefused|timeout|unreachable/.test(msg);
}

export function mapAuthNetworkError(): string {
  return "Can't reach the server. Check your connection and try again.";
}

function isRateLimited(error: AuthErr): boolean {
  const code = (error.code || '').toLowerCase();
  const msg = (error.message || '').toLowerCase();
  return (
    error.status === 429 ||
    code === 'over_request_rate_limit' ||
    msg.includes('rate limit') ||
    msg.includes('too many') ||
    msg.includes('once every')
  );
}

export function isEmailNotConfirmedError(error: AuthErr): boolean {
  const code = (error.code || '').toLowerCase();
  const msg = (error.message || '').toLowerCase();
  return code === 'email_not_confirmed' || msg.includes('email not confirmed');
}

export function mapSignInError(error: AuthErr): string {
  if (isEmailNotConfirmedError(error)) {
    return 'Please confirm your email before signing in. Check your inbox for the confirmation link.';
  }

  const code = (error.code || '').toLowerCase();
  const msg = (error.message || '').toLowerCase();

  if (
    code === 'invalid_credentials' ||
    msg.includes('invalid login credentials') ||
    msg.includes('invalid email or password')
  ) {
    return 'Incorrect email or password.';
  }

  if (isRateLimited(error)) {
    return 'Too many attempts. Please wait a moment and try again.';
  }

  return error.message || 'Sign-in failed. Please try again.';
}

export function mapSignUpError(error: AuthErr): string {
  const code = (error.code || '').toLowerCase();
  const msg = (error.message || '').toLowerCase();

  if (
    (code === 'validation_failed' && msg.includes('email')) ||
    msg.includes('unable to validate email') ||
    msg.includes('invalid email')
  ) {
    return 'Enter a valid email address.';
  }

  if (code === 'weak_password' || (msg.includes('password') && !msg.includes('required'))) {
    return error.message || 'Password does not meet the minimum requirements.';
  }

  if (isRateLimited(error)) {
    return 'Too many attempts. Please wait a moment and try again.';
  }

  return error.message || 'Sign-up failed. Please try again.';
}

export function mapResetPasswordRequestError(error: AuthErr): string {
  const code = (error.code || '').toLowerCase();
  const msg = (error.message || '').toLowerCase();

  if (msg.includes('redirect') || msg.includes('redirect_to')) {
    return (
      'Password reset redirect URL is not allowed. Set PUBLIC_APP_URL on the server ' +
      'and add the /reset-password URL to Supabase Authentication → Redirect URLs.'
    );
  }

  if (isRateLimited(error)) {
    return 'Too many reset emails requested. Wait about 60 seconds and try again.';
  }
  return error.message || 'Could not send reset email. Please try again.';
}

export function mapUpdatePasswordError(error: AuthErr): string {
  const code = (error.code || '').toLowerCase();
  const msg = (error.message || '').toLowerCase();

  if (
    msg.includes('session') &&
    (msg.includes('expired') || msg.includes('invalid') || msg.includes('missing'))
  ) {
    return 'Reset link is invalid or expired. Request a new one from the sign-in page.';
  }

  if (code === 'weak_password' || (msg.includes('password') && !msg.includes('session'))) {
    return error.message || 'Password does not meet the minimum requirements.';
  }

  if (isRateLimited(error)) {
    return 'Too many attempts. Please wait a moment and try again.';
  }

  return error.message || 'Could not update password. Please try again.';
}

/** Shown after sign-up when Supabase does not return a session (email confirmation enabled). */
export const SIGNUP_SUCCESS_MESSAGE =
  'Account created. Check your email for a confirmation link, then sign in.';

/** Enumeration-safe copy after a password-reset email is requested. */
export const RESET_EMAIL_SENT_MESSAGE =
  'If an account exists for that email, a password reset link has been sent. Check your inbox.';

export const DUPLICATE_EMAIL_MESSAGE =
  "An account with this email already exists. Try signing in, or use 'Forgot password' if needed.";
