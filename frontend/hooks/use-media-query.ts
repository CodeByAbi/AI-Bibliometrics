"use client";

import { useEffect, useState } from "react";

/**
 * SSR-safe media query. `false` on server/first paint (no `window` read at
 * module or render level); subscribes after mount with proper cleanup.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    setMatches(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
