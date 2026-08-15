"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type AskResponse = {
  query: string;
  answer: string;
  sufficient: boolean;
  retrieval_mode: string;
  high_confidence_chunks: number;
  required_high_confidence: number;
  corpus_chunks: number;
  sources: { n: number; title: string; url: string; date: string; score?: number }[];
  entities: { label: string; name: string; mentions: number }[];
  related_cases: {
    id: string;
    title: string;
    date: string;
    summary: string;
    persons: string[];
    drugs: string[];
    locations: string[];
  }[];
  risk_flags: { name: string; score: number; band: string; rules: { id: string; detail: string }[] }[];
  discover_recommended: boolean;
  job_id: string | null;
  mode: string;
  focus?: { phrases: string[]; tokens: string[] };
  on_topic?: boolean;
};

export type AskSnapshot = {
  query: string;
  discover: boolean;
  result: AskResponse | null;
  error: string;
  jobNote: string;
  busy: boolean;
  pipeline: string;
};

export type GraphSnapshot = {
  name: string;
  focus: string;
};

export type CasesSnapshot = {
  city: string;
  drug: string;
};

export type DiscoverSnapshot = {
  query: string;
};

export type IngestSnapshot = {
  title: string;
  text: string;
  force: boolean;
};

type WorkspaceState = {
  ask: AskSnapshot;
  graph: GraphSnapshot;
  cases: CasesSnapshot;
  discover: DiscoverSnapshot;
  ingest: IngestSnapshot;
};

const EMPTY: WorkspaceState = {
  ask: { query: "", discover: true, result: null, error: "", jobNote: "", busy: false, pipeline: "" },
  graph: { name: "", focus: "" },
  cases: { city: "", drug: "" },
  discover: { query: "" },
  ingest: { title: "", text: "", force: false },
};

const STORAGE_KEY = "narcograph.workspace.v1";

type WorkspaceContextValue = WorkspaceState & {
  hydrated: boolean;
  setAsk: (patch: Partial<AskSnapshot>) => void;
  setGraph: (patch: Partial<GraphSnapshot>) => void;
  setCases: (patch: Partial<CasesSnapshot>) => void;
  setDiscover: (patch: Partial<DiscoverSnapshot>) => void;
  setIngest: (patch: Partial<IngestSnapshot>) => void;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function readStorage(): WorkspaceState | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkspaceState>;
    return {
      ask: { ...EMPTY.ask, ...parsed.ask, busy: false, pipeline: "" },
      graph: { ...EMPTY.graph, ...parsed.graph },
      cases: { ...EMPTY.cases, ...parsed.cases },
      discover: { ...EMPTY.discover, ...parsed.discover },
      ingest: { ...EMPTY.ingest, ...parsed.ingest },
    };
  } catch {
    return null;
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<WorkspaceState>(EMPTY);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const saved = readStorage();
    if (saved) setState(saved);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Quota or private-mode failures should not break Ask.
    }
  }, [state, ready]);

  const setAsk = useCallback((patch: Partial<AskSnapshot>) => {
    setState((prev) => ({ ...prev, ask: { ...prev.ask, ...patch } }));
  }, []);
  const setGraph = useCallback((patch: Partial<GraphSnapshot>) => {
    setState((prev) => ({ ...prev, graph: { ...prev.graph, ...patch } }));
  }, []);
  const setCases = useCallback((patch: Partial<CasesSnapshot>) => {
    setState((prev) => ({ ...prev, cases: { ...prev.cases, ...patch } }));
  }, []);
  const setDiscover = useCallback((patch: Partial<DiscoverSnapshot>) => {
    setState((prev) => ({ ...prev, discover: { ...prev.discover, ...patch } }));
  }, []);
  const setIngest = useCallback((patch: Partial<IngestSnapshot>) => {
    setState((prev) => ({ ...prev, ingest: { ...prev.ingest, ...patch } }));
  }, []);

  const value = useMemo(
    () => ({ ...state, hydrated: ready, setAsk, setGraph, setCases, setDiscover, setIngest }),
    [state, ready, setAsk, setGraph, setCases, setDiscover, setIngest],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return ctx;
}
