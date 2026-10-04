"use client";

import { useReportWebVitals } from "next/web-vitals";
import { useRef } from "react";

const ENDPOINT = process.env.NEXT_PUBLIC_VITALS_ENDPOINT;

function send(name: string, value: number, id: string, rating: string) {
  const body = JSON.stringify({ name, value, id, rating, path: window.location.pathname });
  if (navigator.sendBeacon) {
    navigator.sendBeacon(ENDPOINT!, new Blob([body], { type: "application/json" }));
    return;
  }
  void fetch(ENDPOINT!, { method: "POST", body, keepalive: true }).catch(() => {});
}

/**
 * Reports Core Web Vitals for `/`. The sink is opt-in: with no
 * NEXT_PUBLIC_VITALS_ENDPOINT this adds no network request at all. Defaulting
 * it on would silently couple the frontend to a backend route it does not own,
 * and would make every page view pay a request the backend never agreed to
 * absorb. In development the vitals go to the console instead.
 */
export function WebVitals() {
  // StrictMode mounts effects twice in development, so the buffered entries
  // replay and every metric arrives two or three times under different ids.
  // Reporting the same name/value once keeps the dev output trustworthy.
  const reported = useRef<Set<string>>(new Set());

  useReportWebVitals((metric) => {
    const key = `${metric.name}:${metric.value}`;
    if (reported.current.has(key)) return;
    reported.current.add(key);

    if (process.env.NODE_ENV === "development") {
      console.info(`[web-vitals] ${metric.name} = ${Math.round(metric.value)} (${metric.id})`);
    }
    if (ENDPOINT) send(metric.name, metric.value, metric.id, metric.rating);
  });

  return null;
}