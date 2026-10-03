"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  fallback?: ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
    console.error("Unhandled React error:", error, info);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <section className="error-card" role="alert" aria-labelledby="boundary-err-title">
          <h2 id="boundary-err-title">Something went wrong rendering this view</h2>
          <p>Please retry. If the problem persists, start fresh research.</p>
          <button type="button" className="btn-retry" onClick={this.handleReset}>
            Retry view
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}
