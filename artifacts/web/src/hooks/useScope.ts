import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useEnvironments } from './useEnvironments';

/**
 * Reads and writes the ?scope=<slug> URL param.
 * When the param is absent and active environments have loaded,
 * defaults to the first active environment (written as a replace navigation
 * so it doesn't create a back-stack entry).
 */
export function useScope() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { activeEnvironments } = useEnvironments();

  const scope = searchParams.get('scope') ?? null;

  // Auto-default to the first active environment when no scope is set
  useEffect(() => {
    if (!scope && activeEnvironments.length > 0) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('scope', activeEnvironments[0].slug);
          return next;
        },
        { replace: true },
      );
    }
  }, [scope, activeEnvironments, setSearchParams]);

  function setScope(slug: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('scope', slug);
        return next;
      },
      { replace: true },
    );
  }

  return { scope, setScope };
}
