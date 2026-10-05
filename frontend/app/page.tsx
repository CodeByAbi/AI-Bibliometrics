import Workspace from "@/components/Workspace/Workspace";
import { ErrorBoundary } from "@/components/ErrorBoundary";

/**
 * Stateless entry point.
 *
 * `/` deliberately carries NO session: a visitor landing here can ask a question
 * without creating one, which is the pre-session behaviour and stays supported.
 * The New Research action is what mints a workspace, and it navigates to
 * `/research/{id}`.
 *
 * Keeping `/` stateless also means a shared link to the product never depends on
 * session persistence being configured — a 503 there would make the whole app
 * unreachable rather than only its history feature.
 */
export default function Page() {
  return (
    <ErrorBoundary>
      <Workspace />
    </ErrorBoundary>
  );
}