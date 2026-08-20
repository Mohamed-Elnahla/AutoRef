import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowRight,
  BookOpen,
  Check,
  Download,
  FileText,
  LoaderCircle,
  Moon,
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
};

type Analysis = {
  job_id: string;
  source_name: string;
  detected_style: string;
  summary: Summary;
  warnings: string[];
};

type Conversion = {
  report: { converted_citations: number; skipped_citations: string[] };
  artifacts: Record<"document" | "library" | "report", string>;
};

const apiError = async (response: Response) => {
  const body = await response.json().catch(() => ({}));
  return body.detail || `Request failed (${response.status})`;
};

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

  const reset = () => {
    setFile(null);
    setAnalysis(null);
    setConversion(null);
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
          <div className="eyebrow"><span /> Private by design · Open source</div>
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
            <label
              className={`dropzone ${dragging ? "dragging" : ""}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                choose(event.dataTransfer.files[0]);
              }}
            >
              <input type="file" accept=".docx" onChange={(event) => choose(event.target.files?.[0])} />
              <span className="upload-icon"><UploadCloud size={27} /></span>
              <strong>Drop your research paper here</strong>
              <span>or click to choose a DOCX · up to 30 MB</span>
              <button className="secondary" type="button">Choose document</button>
            </label>
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
                    <div><b>{Math.round(analysis.summary.match_rate * 100)}%</b><span>Matched safely</span></div>
                    <div><b>{analysis.detected_style}</b><span>Detected system</span></div>
                  </div>
                  {analysis.summary.match_rate < 1 && (
                    <p className="notice">Only unambiguous matches are converted. Unmatched text stays untouched and is listed in the report.</p>
                  )}
                  {!conversion ? (
                    <button className="primary wide" onClick={convert} disabled={busy !== null}>
                      {busy === "convert" ? <LoaderCircle className="spin" size={18} /> : <RefreshCcw size={18} />}
                      {busy === "convert" ? "Building Zotero fields…" : "Convert matched citations"}
                    </button>
                  ) : (
                    <div className="downloads">
                      <a className="primary" href={conversion.artifacts.document}><Download size={18} /> Zotero DOCX</a>
                      <a className="secondary" href={conversion.artifacts.library}><Download size={18} /> CSL-JSON library</a>
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
          <div><span>01</span><h2>Read</h2><p>Locate reference sections and citation callouts across author-date and numeric styles.</p></div>
          <div><span>02</span><h2>Resolve</h2><p>Parse metadata, match each callout conservatively, and surface ambiguity instead of guessing.</p></div>
          <div><span>03</span><h2>Return</h2><p>Patch only citation spans and deliver the DOCX, Zotero import file, and an audit report.</p></div>
        </section>
      </main>

      <footer><span>AutoRef · Phase one</span><span>Your files expire automatically after 24 hours.</span></footer>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
