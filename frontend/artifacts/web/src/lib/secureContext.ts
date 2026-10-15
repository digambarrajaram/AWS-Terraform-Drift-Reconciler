/** True when the browser will not treat this origin as secure (no TLS / not localhost). */
export function credentialsBlockedOnThisOrigin(): boolean {
  if (typeof window === 'undefined') return false;
  return !window.isSecureContext;
}
