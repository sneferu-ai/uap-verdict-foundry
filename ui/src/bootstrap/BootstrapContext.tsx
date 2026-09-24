// Server-provided bootstrap (spec §3.5) + optional preview identity.

import {
  createContext,
  useContext,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { readBootstrapFromDom } from "../lib/api";
import { readPreviewBootstrap } from "../lib/preview";
import type { Bootstrap, PreviewBootstrap } from "../lib/types";

export interface BootstrapContextValue {
  bootstrap: Bootstrap;
  preview: PreviewBootstrap | null;
  setBootstrap: Dispatch<SetStateAction<Bootstrap>>;
}

const BootstrapContext = createContext<BootstrapContextValue | null>(null);

export function BootstrapProvider({ children }: { children: ReactNode }) {
  const [bootstrap, setBootstrap] = useState<Bootstrap>(() =>
    readBootstrapFromDom(),
  );
  const [preview] = useState<PreviewBootstrap | null>(() =>
    readPreviewBootstrap(),
  );
  const value: BootstrapContextValue = { bootstrap, preview, setBootstrap };
  return (
    <BootstrapContext.Provider value={value}>
      {children}
    </BootstrapContext.Provider>
  );
}

export function useBootstrap(): BootstrapContextValue {
  const ctx = useContext(BootstrapContext);
  if (!ctx) {
    throw new Error("useBootstrap must be used within BootstrapProvider");
  }
  return ctx;
}
