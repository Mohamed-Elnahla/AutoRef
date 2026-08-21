import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowRight,
  BookOpen,
  Check,
  Download,
  FileText,
  LoaderCircle,
  Moon,
  KeyRound,
  RefreshCcw,
  ShieldCheck,
  Sun,
  UploadCloud,
  X,
} from "lucide-react";
import "./styles.css";

type Summary = {
  references: number;
  citations: number;
  matched_citations: number;
  match_rate: number;
  captions: number;
  cross_references: number;
};

type Analysis = {
  job_id: string;
  source_name: string;
  detected_style: string;
  summary: Summary;
  warnings: string[];
};

type Conversion = {
  report: {
    converted_citations: number;
    detected_captions: number;
    converted_cross_references: number;
    skipped_citations: string[];
    skipped_cross_references: string[];
    converted_bibliography: boolean;
    bibliography_entries: number;
  };
  artifacts: { document: string; report: string; library?: string };
};

type ZoteroLibrary = { type: "user" | "group"; id: number; name: string };
type ZoteroConnection = {
  connection_id: string;
  expires_in_minutes: number;
  libraries: ZoteroLibrary[];
};
type ZoteroPlan = {
  plan_id: string;
  summary: { create: number; reuse: number; update: number };
  entries: Array<{ reference_id: string; title: string; action: "create" | "reuse" | "update"; reason?: string }>;
};
type UnresolvedDoi = { reference_id: string; title: string; doi: string; reason: string };
type DoiReview = { status: "needs_doi_review"; unresolved: UnresolvedDoi[]; message: string };

const apiError = async (response: Response) => {
  const body = await response.json().catch(() => ({}));
  return body.detail || `Request failed (${response.status})`;
};

function GitHubIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M12 2C6.477 2 2 6.477 2 12c0 4.418 2.865 8.167 6.839 9.49.5.092.683-.217.683-.483 0-.237-.009-1.025-.013-1.859-2.782.604-3.369-1.18-3.369-1.18-.455-1.156-1.11-1.464-1.11-1.464-.908-.62.069-.608.069-.608 1.004.07 1.532 1.03 1.532 1.03.892 1.529 2.341 1.087 2.91.831.091-.646.349-1.087.635-1.337-2.22-.253-4.555-1.11-4.555-4.942 0-1.092.39-1.985 1.029-2.685-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.026A9.56 9.56 0 0 1 12 6.756a9.56 9.56 0 0 1 2.504.337c1.909-1.295 2.748-1.026 2.748-1.026.545 1.377.202 2.394.1 2.647.64.7 1.028 1.593 1.028 2.685 0 3.842-2.339 4.686-4.566 4.934.359.31.679.919.679 1.852 0 1.338-.012 2.417-.012 2.747 0 .268.18.58.688.482A10.003 10.003 0 0 0 22 12c0-5.523-4.477-10-10-10Z" /></svg>;
}

function LinkedInIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.34V8.99h3.42v1.57h.05c.48-.9 1.64-1.85 3.37-1.85 3.61 0 4.27 2.37 4.27 5.46v6.28ZM5.32 7.42a2.07 2.07 0 1 1 0-4.14 2.07 2.07 0 0 1 0 4.14Zm1.78 13.03H3.54V8.99H7.1v11.46Z" /></svg>;
}

function App() {
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("autoref-theme") as "light" | "dark") || "light",
  );
  const [file, setFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [conversion, setConversion] = useState<Conversion | null>(null);
  const [busy, setBusy] = useState<"analyze" | "convert" | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [connection, setConnection] = useState<ZoteroConnection | null>(null);
  const [libraryIndex, setLibraryIndex] = useState(0);
  const [collectionName, setCollectionName] = useState("");
  const [plan, setPlan] = useState<ZoteroPlan | null>(null);
  const [doiReview, setDoiReview] = useState<DoiReview | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("autoref-theme", theme);
  }, [theme]);

  const readableSize = useMemo(() => {
    if (!file) return "";
    return file.size > 1024 * 1024
      ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
      : `${Math.ceil(file.size / 1024)} KB`;
  }, [file]);

  const choose = (next: File | undefined) => {
    if (!next) return;
    if (!next.name.toLowerCase().endsWith(".docx")) {
      setError("Please choose a .docx Word document.");
      return;
    }
    setFile(next);
    setAnalysis(null);
    setConversion(null);
    setPlan(null);
    setDoiReview(null);
    setError("");
  };

  const analyze = async () => {
    if (!file) return;
    setBusy("analyze");
    setError("");
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch("/api/v1/documents/analyze", { method: "POST", body });
      if (!response.ok) throw new Error(await apiError(response));
      setAnalysis(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Analysis failed.");
    } finally {
      setBusy(null);
    }
  };

  const convert = async () => {
    if (!analysis) return;
    setBusy("convert");
    setError("");
    try {
      const response = await fetch(`/api/v1/documents/${analysis.job_id}/convert`, { method: "POST" });
      if (!response.ok) throw new Error(await apiError(response));
      setConversion(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Conversion failed.");
    } finally {
      setBusy(null);
    }
  };

  const connectZotero = async () => {
    setBusy("convert");
    setError("");
    try {
      const response = await fetch("/api/v1/zotero/connections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      setConnection(await response.json());
      setApiKey("");
      setLibraryIndex(0);
      setPlan(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Zotero connection failed.");
    } finally {
      setBusy(null);
    }
  };

  const zoteroRequest = async (
    action: "preview" | "import",
    unverifiedDoiAction: "review" | "use_parsed" | "mark_for_review" | "exclude" = "review",
  ) => {
    if (!analysis || !connection) return;
    const library = connection.libraries[libraryIndex];
    setBusy("convert");
    setError("");
    try {
      const response = await fetch(`/api/v1/documents/${analysis.job_id}/zotero/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          connection_id: connection.connection_id,
          library_type: library.type,
          library_id: library.id,
          collection_name: collectionName.trim() || null,
          ...(action === "import" && plan ? {
            plan_id: plan.plan_id,
            unverified_doi_action: unverifiedDoiAction,
          } : {}),
        }),
      });
      if (!response.ok) {
        const detail = await apiError(response);
        if (action === "import" && typeof detail === "object" && detail?.status === "needs_doi_review") {
          setDoiReview(detail as DoiReview);
          return;
        }
        throw new Error(typeof detail === "string" ? detail : "Zotero import failed.");
      }
      const result = await response.json();
      if (action === "preview") {
        setPlan(result);
        setDoiReview(null);
      } else {
        setConversion(result);
        setDoiReview(null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Zotero import failed.");
    } finally {
      setBusy(null);
    }
  };

  const reset = () => {
    setFile(null);
    setAnalysis(null);
    setConversion(null);
    setPlan(null);
    setDoiReview(null);
    setError("");
  };

  return (
    <div className="app-shell">
      <header className="nav">
        <a className="brand" href="#top" aria-label="AutoRef home">
          <span className="brand-mark"><BookOpen size={20} strokeWidth={2.2} /></span>
          <span>AutoRef</span>
        </a>
        <div className="nav-actions">
          <a className="social-button" href="https://www.linkedin.com/in/mohamed-el-nahla" target="_blank" rel="noreferrer" aria-label="Eng. Mohamed Elnahla on LinkedIn" title="Eng. Mohamed Elnahla on LinkedIn">
            <LinkedInIcon />
          </a>
          <a className="social-button" href="https://github.com/Mohamed-Elnahla/AutoRef" target="_blank" rel="noreferrer" aria-label="AutoRef on GitHub" title="AutoRef on GitHub">
            <GitHubIcon />
          </a>
          <button
            className="icon-button"
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
            aria-label={`Use ${theme === "light" ? "dark" : "light"} theme`}
          >
            {theme === "light" ? <Moon size={19} /> : <Sun size={19} />}
          </button>
        </div>
      </header>

      <main id="top">
        <section className="hero">
          <div className="eyebrow"><span /> Private by design · Source available</div>
          <h1>Turn static citations<br />into <em>living references.</em></h1>
          <p className="hero-copy">
            Upload a Word paper. AutoRef finds the bibliography, links citation callouts,
            and returns a format-preserving DOCX with native Zotero fields.
          </p>
          <div className="trust-row">
            <span><ShieldCheck size={16} /> Original layout preserved</span>
            <span><RefreshCcw size={16} /> Zotero-editable fields</span>
            <span><Download size={16} /> Importable CSL-JSON</span>
          </div>
        </section>

        <section className="workspace" aria-live="polite">
          {!file ? (
            <div
              className={`dropzone ${dragging ? "dragging" : ""}`}
              role="button"
              tabIndex={0}
              aria-label="Choose a DOCX document"
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                choose(event.dataTransfer.files[0]);
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => {
                  choose(event.target.files?.[0]);
                  event.currentTarget.value = "";
                }}
              />
              <span className="upload-icon"><UploadCloud size={27} /></span>
              <strong>Drop your research paper here</strong>
              <span>or click to choose a DOCX · up to 30 MB</span>
              <button
                className="secondary"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  fileInputRef.current?.click();
                }}
              >
                Choose document
              </button>
            </div>
          ) : (
            <div className="job-card">
              <div className="file-row">
                <span className="file-icon"><FileText size={23} /></span>
                <div><strong>{file.name}</strong><span>{readableSize} · Microsoft Word</span></div>
                <button className="icon-button quiet" onClick={reset} aria-label="Remove document"><X size={18} /></button>
              </div>

              {!analysis && (
                <button className="primary wide" onClick={analyze} disabled={busy !== null}>
                  {busy === "analyze" ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />}
                  {busy === "analyze" ? "Reading citations…" : "Analyze document"}
                </button>
              )}

              {analysis && (
                <div className="results">
                  <div className="results-title"><span className="success"><Check size={15} /></span><strong>Analysis complete</strong></div>
                  <div className="metrics">
                    <div><b>{analysis.summary.references}</b><span>References</span></div>
                    <div><b>{analysis.summary.citations}</b><span>Citation callouts</span></div>
                    <div><b>{analysis.summary.captions}</b><span>Figure/table captions</span></div>
                    <div><b>{analysis.summary.cross_references}</b><span>Figure/table cross-refs</span></div>
                    <div><b>{Math.round(analysis.summary.match_rate * 100)}%</b><span>Matched safely</span></div>
                    <div><b>{analysis.detected_style}</b><span>Detected system</span></div>
                  </div>
                  {analysis.summary.match_rate < 1 && (
                    <p className="notice">Only unambiguous matches are converted. Unmatched text stays untouched and is listed in the report.</p>
                  )}
                  {!conversion ? (
                    <div className="conversion-options">
                      <div className="option-card featured">
                        <div className="option-heading"><KeyRound size={18} /><div><strong>Link to Zotero library</strong><span>Creates or reuses real library items</span></div></div>
                        {!connection ? (
                          <div className="connect-row">
                            <input
                              type="password"
                              value={apiKey}
                              onChange={(event) => setApiKey(event.target.value)}
                              placeholder="Zotero API key"
                              autoComplete="off"
                            />
                            <button className="primary" onClick={connectZotero} disabled={busy !== null || apiKey.length < 8}>
                              {busy === "convert" ? <LoaderCircle className="spin" size={17} /> : "Connect"}
                            </button>
                          </div>
                        ) : !plan ? (
                          <div className="zotero-options">
                            <label>Library<select value={libraryIndex} onChange={(event) => { setLibraryIndex(Number(event.target.value)); setPlan(null); setDoiReview(null); }}>
                              {connection.libraries.map((library, index) => <option key={`${library.type}-${library.id}`} value={index}>{library.name} ({library.type})</option>)}
                            </select></label>
                            <label>Collection (optional)<input value={collectionName} onChange={(event) => setCollectionName(event.target.value)} placeholder="AutoRef imports" /></label>
                            <button className="primary wide" onClick={() => zoteroRequest("preview")} disabled={busy !== null}>Preview import</button>
                            <small>Key encrypted in memory and expires after {connection.expires_in_minutes} minutes.</small>
                          </div>
                        ) : (
                          <div className="review-plan">
                            <p><strong>{plan.summary.create}</strong> new item(s) · <strong>{plan.summary.reuse}</strong> exact DOI/title match(es) reused · <strong>{plan.summary.update}</strong> existing item(s) missing a DOI updated</p>
                            <div className="plan-list">{plan.entries.slice(0, 6).map((entry) => <span key={entry.reference_id}><b>{entry.action}</b>{entry.title}</span>)}</div>
                            {plan.entries.length > 6 && <small>Plus {plan.entries.length - 6} more item(s).</small>}
                            {doiReview && (
                              <div className="doi-review" role="alert">
                                <strong>DOI verification needs your review</strong>
                                <p>Crossref could not confirm {doiReview.unresolved.length} DOI{doiReview.unresolved.length === 1 ? "" : "s"}. This can happen when a valid DOI is registered outside Crossref, such as arXiv/DataCite.</p>
                                <div className="doi-list">{doiReview.unresolved.map((item) => <span key={item.reference_id}><b>{item.doi || "No DOI extracted"}</b>{item.title}<small>{item.reason}</small></span>)}</div>
                                <div className="doi-actions">
                                  <button className="secondary" onClick={() => zoteroRequest("import", "use_parsed")} disabled={busy !== null}>Keep parsed data</button>
                                  <button className="secondary" onClick={() => zoteroRequest("import", "mark_for_review")} disabled={busy !== null}>Import & mark to fix</button>
                                  <button className="secondary danger" onClick={() => zoteroRequest("import", "exclude")} disabled={busy !== null}>Remove & don’t import</button>
                                </div>
                              </div>
                            )}
                            <div className="review-actions"><button className="secondary" onClick={() => { setPlan(null); setDoiReview(null); }}>Change options</button><button className="primary" onClick={() => zoteroRequest("import")} disabled={busy !== null}>{busy === "convert" ? <LoaderCircle className="spin" size={17} /> : <Check size={17} />} Confirm & link</button></div>
                          </div>
                        )}
                      </div>
                      <button className="text-link local-convert" onClick={convert} disabled={busy !== null}>Continue without Zotero (embedded metadata only)</button>
                    </div>
                  ) : (
                    <div className="downloads">
                      <a className="primary" href={conversion.artifacts.document}><Download size={18} /> Zotero DOCX</a>
                      {conversion.artifacts.library && <a className="secondary" href={conversion.artifacts.library}><Download size={18} /> CSL-JSON library</a>}
                      <a className="text-link" href={conversion.artifacts.report}>Conversion report</a>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {error && <p className="error" role="alert">{error}</p>}
        </section>

        <section className="how">
          <div><span>01</span><h2>Read</h2><p>Locate reference sections, citation callouts, and figure or table captions.</p></div>
          <div><span>02</span><h2>Resolve</h2><p>Parse metadata, match each callout conservatively, and surface ambiguity instead of guessing.</p></div>
          <div><span>03</span><h2>Return</h2><p>Patch citations and native Word cross-references, then deliver the DOCX, Zotero import file, and an audit report.</p></div>
        </section>
      </main>

      <footer>
        <span>AutoRef · Developed by Eng. Mohamed Elnahla</span>
        <span className="footer-links">
          <a href="https://www.linkedin.com/in/mohamed-el-nahla" target="_blank" rel="noreferrer">LinkedIn</a>
          <a href="https://github.com/Mohamed-Elnahla/AutoRef" target="_blank" rel="noreferrer">GitHub</a>
          <a href="https://github.com/Mohamed-Elnahla/AutoRef/blob/main/LICENSE" target="_blank" rel="noreferrer">License</a>
        </span>
        <span>Your files expire automatically after 24 hours.</span>
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
