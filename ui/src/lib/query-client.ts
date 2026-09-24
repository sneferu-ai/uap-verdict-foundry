// Single QueryClient instance (spec §8.4: staleTime 0 — polling cadence is
// the single source of refetch truth). Shared module so the ErrorBoundary
// can clear the cache on catch (§8.7) without a circular import via main.

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
      retry: 0,
    },
  },
});
