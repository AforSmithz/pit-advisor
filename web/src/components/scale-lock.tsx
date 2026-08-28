"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

type Lock = { at: number | null; pinned: boolean; from: string | null };

type LockApi = Lock & {
  hover: (at: number | null, from: string | null) => void;
  toggle: (at: number, from: string) => void;
};

const ScaleLockContext = createContext<LockApi | null>(null);

export function ScaleLock({ children }: { children: React.ReactNode }) {
  const [lock, setLock] = useState<Lock>({ at: null, pinned: false, from: null });

  const hover = useCallback((at: number | null, from: string | null) => {
    setLock((current) => (current.pinned ? current : { at, pinned: false, from }));
  }, []);

  const toggle = useCallback((at: number, from: string) => {
    setLock((current) =>
      current.pinned && current.from === from
        ? { at: null, pinned: false, from: null }
        : { at, pinned: true, from },
    );
  }, []);

  const api = useMemo(() => ({ ...lock, hover, toggle }), [lock, hover, toggle]);
  return <ScaleLockContext.Provider value={api}>{children}</ScaleLockContext.Provider>;
}

export function useScaleLock(): LockApi {
  const found = useContext(ScaleLockContext);
  if (!found) throw new Error("a track was rendered outside its scale lock");
  return found;
}
