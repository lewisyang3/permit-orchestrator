import React, { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, FileSearch, Loader2, X, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { EvidenceSpan } from '../types';
import { documentUrl } from '../utils/api';
import { evidenceTone, TONE_LABEL } from '../utils/evidence';

type PdfJs = typeof import('pdfjs-dist');
type PdfDoc = Awaited<ReturnType<PdfJs['getDocument']>['promise']>;

let pdfjsPromise: Promise<PdfJs> | null = null;

/** Load pdf.js (and its worker) only when a citation is first opened. */
function loadPdfJs(): Promise<PdfJs> {
  if (!pdfjsPromise) {
    pdfjsPromise = Promise.all([
      import('pdfjs-dist'),
      import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
    ]).then(([pdfjs, worker]) => {
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

const VIEW_WIDTH = 640; // CSS px for the rendered page

interface PdfEvidenceViewerProps {
  documentId: string | null;
  span: EvidenceSpan;
  onClose: () => void;
}

export const PdfEvidenceViewer: React.FC<PdfEvidenceViewerProps> = ({ documentId, span, onClose }) => {
  const tone = evidenceTone(span);
  const isPdf = span.page != null || (span.line == null && span.status === 'unverified');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 bg-black/40 backdrop-blur-xs z-[60]" />
      <div
        role="dialog"
        aria-label={`Source for ${span.field_path}`}
        className="fixed inset-y-4 left-1/2 -translate-x-1/2 w-[min(720px,calc(100vw-2rem))] bg-white rounded-3xl shadow-2xl z-[70] flex flex-col overflow-hidden text-slate-800"
      >
        <div className="p-4 border-b border-slate-200 bg-slate-50/80 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-bold text-[#1d1d1f]">
              <FileSearch className="h-4 w-4 text-[#0071e3] shrink-0" />
              <span className="font-mono truncate">{span.field_path}</span>
            </div>
            <div
              className={`mt-1.5 flex items-center gap-1.5 text-[11px] font-semibold ${
                tone === 'verified' ? 'text-emerald-700' : tone === 'mismatch' ? 'text-rose-700' : 'text-amber-700'
              }`}
            >
              {tone === 'verified' ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
              <span>{TONE_LABEL[tone]}</span>
              <span className="text-slate-400 font-normal">· read by {span.method}</span>
            </div>
            <blockquote className="mt-2 text-xs font-mono bg-white border border-slate-200 rounded-xl px-3 py-2 text-slate-800 break-words">
              “{span.quote}”
              <span className="text-slate-400">
                {span.page ? ` — page ${span.page}` : ''}
                {span.line ? `, line ${span.line}` : ''}
              </span>
            </blockquote>
          </div>
          <button
            onClick={onClose}
            aria-label="Close source viewer"
            className="apple-press p-2 rounded-xl text-slate-400 hover:text-[#1d1d1f] hover:bg-slate-200/80 shrink-0"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-auto bg-slate-100 p-4">
          {!documentId ? (
            <p className="text-xs text-slate-500 text-center py-10">
              The original document isn't available for this permit, so only the quoted text can be shown.
            </p>
          ) : isPdf ? (
            <PdfPage documentId={documentId} span={span} />
          ) : (
            <TextLines documentId={documentId} span={span} />
          )}
        </div>
      </div>
    </>
  );
};

const PdfPage: React.FC<{ documentId: string; span: EvidenceSpan }> = ({ documentId, span }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);
  const [doc, setDoc] = useState<PdfDoc | null>(null);
  const [pageNo, setPageNo] = useState(span.page ?? 1);
  const [scale, setScale] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let loaded: PdfDoc | null = null;
    setError(null);
    loadPdfJs()
      .then((pdfjs) => pdfjs.getDocument(documentUrl(documentId)).promise)
      .then((d) => {
        loaded = d;
        if (!cancelled) setDoc(d);
      })
      .catch((err) => !cancelled && setError(`Could not open the document: ${err?.message ?? err}`));
    return () => {
      cancelled = true;
      loaded?.destroy();
    };
  }, [documentId]);

  useEffect(() => setPageNo(span.page ?? 1), [span]);

  useEffect(() => {
    if (!doc || !canvasRef.current) return;
    let cancelled = false;
    setRendering(true);
    let task: { cancel: () => void; promise: Promise<unknown> } | null = null;
    doc.getPage(pageNo).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const base = page.getViewport({ scale: 1 });
      const s = VIEW_WIDTH / base.width;
      const dpr = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: s * dpr });
      const canvas = canvasRef.current;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${base.width * s}px`;
      canvas.style.height = `${base.height * s}px`;
      setScale(s);
      task = page.render({ canvasContext: canvas.getContext('2d')!, viewport });
      task.promise
        .then(() => {
          if (cancelled) return;
          setRendering(false);
          highlightRef.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
        })
        .catch(() => undefined);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNo]);

  if (error) return <p className="text-xs text-rose-700 text-center py-10">{error}</p>;

  const numPages = doc?.numPages ?? 0;
  // Boxes are in PDF points from the page's top-left, the same space as pdf.js at scale 1.
  const boxes = pageNo === span.page ? span.bboxes : [];
  const pad = 2;

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="flex items-center gap-3 text-xs text-slate-600">
        <button
          className="apple-press p-1.5 rounded-lg hover:bg-slate-200 disabled:opacity-30"
          disabled={pageNo <= 1}
          onClick={() => setPageNo((p) => p - 1)}
          aria-label="Previous page"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="font-mono">
          page {pageNo} / {numPages || '…'}
        </span>
        <button
          className="apple-press p-1.5 rounded-lg hover:bg-slate-200 disabled:opacity-30"
          disabled={!numPages || pageNo >= numPages}
          onClick={() => setPageNo((p) => p + 1)}
          aria-label="Next page"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        {span.page && pageNo !== span.page && (
          <button className="text-[#0071e3] font-semibold" onClick={() => setPageNo(span.page!)}>
            back to cited page
          </button>
        )}
      </div>
      {span.status !== 'verified' && (
        <p className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2 max-w-[640px]">
          This text wasn't found in the document, so nothing is highlighted. Look for the value yourself
          {span.page ? ` (the parser pointed to page ${span.page})` : ''}.
        </p>
      )}
      <div className="relative bg-white shadow-md">
        <canvas ref={canvasRef} />
        {rendering && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
          </div>
        )}
        {!rendering &&
          boxes.map((b, i) => (
            <div
              key={i}
              ref={i === 0 ? highlightRef : undefined}
              data-testid="evidence-highlight"
              className="absolute rounded-sm bg-yellow-300/40 ring-2 ring-yellow-500/80 pointer-events-none"
              style={{
                left: b.x0 * scale - pad,
                top: b.top * scale - pad,
                width: (b.x1 - b.x0) * scale + 2 * pad,
                height: (b.bottom - b.top) * scale + 2 * pad,
              }}
            />
          ))}
      </div>
    </div>
  );
};

const TextLines: React.FC<{ documentId: string; span: EvidenceSpan }> = ({ documentId, span }) => {
  const [lines, setLines] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hitRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(documentUrl(documentId))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => !cancelled && setLines(t.split('\n')))
      .catch((err) => !cancelled && setError(`Could not open the document: ${err.message}`));
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    hitRef.current?.scrollIntoView?.({ block: 'center' });
  }, [lines]);

  if (error) return <p className="text-xs text-rose-700 text-center py-10">{error}</p>;
  if (!lines) return <Loader2 className="h-6 w-6 animate-spin text-slate-400 mx-auto my-10" />;
  return (
    <div className="bg-white rounded-xl border border-slate-200 font-mono text-[11px] leading-5 overflow-x-auto">
      {lines.map((ln, i) => {
        const hit = span.status === 'verified' && span.line === i + 1;
        return (
          <div
            key={i}
            ref={hit ? hitRef : undefined}
            className={`flex ${hit ? 'bg-yellow-200/70' : ''}`}
            data-testid={hit ? 'evidence-highlight' : undefined}
          >
            <span className="w-10 shrink-0 text-right pr-3 text-slate-400 select-none">{i + 1}</span>
            <span className="whitespace-pre">{ln}</span>
          </div>
        );
      })}
    </div>
  );
};
