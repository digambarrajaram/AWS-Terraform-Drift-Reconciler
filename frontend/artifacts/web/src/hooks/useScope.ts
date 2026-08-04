import { useEffect, useRef } from 'react';
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

  // Track whether we've already set the default scope so we don't depend on
  // `activeEnvironments` (a new array every render from .filter()) in the
  // effect dependency array — that would cause a re-render cascade.
  const defaultSet = useRef(false);

  // Auto-default to the first active environment when no scope is set.
  // Runs only once: when environments first load and scope is still empty.
  useEffect(() => {
    if (!scope && !defaultSet.current && activeEnvironments.length > 0) {
      defaultSet.current = true;
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('scope', activeEnvironments[0].slug);
          return next;
        },
        { replace: true },
      );
    }
  }, [scope, activeEnvironments.length, setSearchParams]);

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
