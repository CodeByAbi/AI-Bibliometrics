"use client";

import { History } from "lucide-react";
import type { AskResponse } from "../../lib/api";
import { formatMs } from "../../lib/format";

export interface DebugInspectorProps {
  response: AskResponse;
  latencyRows: Array<[string, number]>;
  defaultOpen: boolean;
}

/**
 * Technical grounding inspector: the SQL that ran, why the router chose its
 * route, per-stage latency, and any citation the verifier stripped. Collapsed
 * by default and only reachable when the payload actually carries debug data.
 */
export function DebugInspector({ response, latencyRows, defaultOpen }: DebugInspectorProps) {
  return (
    <details className="debug" open={defaultOpen}>
      <summary>
        <History size={13} aria-hidden /> Debug inspector
        <span className="mono debug-id">{response.request_id ?? "—"}</span>
      </summary>
      <div className="debug-body">
        {response.debug?.route_reasoning && (
          <div>
            <div className="mono debug-label">route_reasoning</div>
            <pre>{response.debug.route_reasoning}</pre>
          </div>
        )}
        {response.debug?.sql_executed && (
          <div>
            <div className="mono debug-label">sql_executed</div>
            <pre>{response.debug.sql_executed}</pre>
          </div>
        )}
        {latencyRows.length > 0 && (
          <table className="lat-table">
            <caption className="sr-only">Latency breakdown per retrieval stage, in milliseconds</caption>
            <thead>
              <tr>
                <th scope="col">stage</th>
                <th scope="col">ms</th>
              </tr>
            </thead>
            <tbody>
              {latencyRows.map(([k, v]) => (
                <tr key={k}>
                  <th scope="row">{k}</th>
                  <td>{formatMs(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {response.unverified_citations.length > 0 && (
          <pre>unverified_citations: {JSON.stringify(response.unverified_citations, null, 2)}</pre>
        )}
      </div>
    </details>
  );
}