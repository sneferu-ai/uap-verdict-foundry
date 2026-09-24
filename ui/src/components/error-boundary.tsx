// Error boundary (spec §8.7): catches render errors from malformed server
// data, clears the query cache, logs to console, and offers "Back to
// Cases" + "Retry". Query errors (401/404/500) are NOT caught here — each
// query renders its own error state.

import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "./ui";
import { queryClient } from "../lib/query-client";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Evidence over politeness: surface the component stack for the
    // operator to include in a bug report.
    console.error("[uapvf] UI crash:", error, info.componentStack);
    // §8.7: the malformed payload may have polluted parsed query state;
    // drop the cache before any recovery path remounts children.
    queryClient.clear();
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-xl px-4 py-12">
          <div className="flex flex-col gap-3 rounded-md border border-line-2 bg-raised px-4 py-3">
            <h1 className="text-sm font-medium text-ink">
              This screen stopped rendering.
            </h1>
            <p className="text-xs text-ink-3">
              The server sent data this console could not display. The query
              cache has been cleared; the case data itself lives on the
              server and is unaffected.
            </p>
            {this.state.error.message && (
              <p className="overflow-x-auto rounded-md bg-inset px-3 py-2 font-mono text-xs text-ink-2">
                {this.state.error.message}
              </p>
            )}
            <div className="mt-1 flex flex-wrap gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  queryClient.clear();
                  window.location.assign("/ui/cases");
                }}
              >
                Back to Cases
              </Button>
              <Button
                onClick={() => {
                  queryClient.clear();
                  this.setState({ error: null });
                }}
              >
                Retry
              </Button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
