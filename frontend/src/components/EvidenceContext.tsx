import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { EvidenceSpan } from '../types';
import { PdfEvidenceViewer } from './PdfEvidenceViewer';

interface EvidenceContextValue {
  /** Uploaded plan the current permit was read from, if any. */
  documentId: string | null;
  /** Open the viewer at a citation. Undefined outside a provider: chips render read-only. */
  openEvidence?: (span: EvidenceSpan) => void;
}

const EvidenceContext = createContext<EvidenceContextValue>({ documentId: null });

export const useEvidence = () => useContext(EvidenceContext);

interface EvidenceProviderProps {
  documentId: string | null | undefined;
  children: React.ReactNode;
}

/** Holds the evidence viewer so any chip on the page (form, banner, drawer) can open it. */
export const EvidenceProvider: React.FC<EvidenceProviderProps> = ({ documentId, children }) => {
  const [active, setActive] = useState<EvidenceSpan | null>(null);
  const openEvidence = useCallback((span: EvidenceSpan) => setActive(span), []);
  const value = useMemo(
    () => ({ documentId: documentId ?? null, openEvidence }),
    [documentId, openEvidence],
  );

  return (
    <EvidenceContext.Provider value={value}>
      {children}
      {active && (
        <PdfEvidenceViewer documentId={documentId ?? null} span={active} onClose={() => setActive(null)} />
      )}
    </EvidenceContext.Provider>
  );
};
