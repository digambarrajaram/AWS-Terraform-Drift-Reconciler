import { useEffect, useState } from 'react';

/**
 * Show a background "updating…" hint only after a fetch runs briefly, and
 * clear immediately when the fetch settles — avoids flicker from fast polls
 * and stuck flags when isRefetching overlaps interval refetches.
 */
export function useQueryUpdating(isFetching: boolean, isLoading: boolean): boolean {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (isLoading || !isFetching) {
      setShow(false);
      return;
    }
    const id = window.setTimeout(() => setShow(true), 300);
    return () => window.clearTimeout(id);
  }, [isFetching, isLoading]);

  return show;
}
