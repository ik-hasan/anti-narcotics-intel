"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { downloadAskDocx } from "@/lib/export-docx";
import { useWorkspace, type AskResponse } from "@/lib/workspace";

type Job = {
  id: string;
  status: string;
  stage: string;
  urls_found?: number;
  urls_crawled?: number;
  urls_ingested: number;
  error: string;
  provider?: string;
};

const STAGE_LABEL: Record<string, string> = {
  queued: "Queued — waiting to search",
  search: "Searching the web",
  crawl: "Crawling pages",
  ingest: "Extracting entities and writing Neo4j",
  embed: "Embedding with Voyage AI",
  done: "Answering from the updated graph",
  empty: "No relevant pages found",
};

async function waitForJob(id: string, onTick: (job: Job) => void): Promise<Job> {
  for (let i = 0; i < 72; i += 1) {
    const job = await api<Job>(`/api/osint/jobs/${id}`, {}, 20000);
    onTick(job);
    if (job.status === "done" || job.status === "failed") return job;
    await new Promise((resolve) => setTimeout(resolve, 2500));
  }
  return { id, status: "timeout", stage: "timeout", urls_ingested: 0, error: "timed out" };
}

export default function AskPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { ask, setAsk, setGraph } = useWorkspace();
  const { query, discover, result, error, busy } = ask;
  const pipeline = ask.pipeline;
  const loading = busy;
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  async function runAsk(question: string, allowDiscover: boolean): Promise<AskResponse> {
    return api<AskResponse>(
      "/api/ask",
      { method: "POST", body: JSON.stringify({ query: question, discover: allowDiscover, top_n: 15 }) },
      90000,
    );
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const question = query.trim();
    if (question.length < 3) return;
    setAsk({
      error: "",
      jobNote: "",
      result: null,
      busy: true,
      pipeline: "Checking the knowledge graph…",
    });
    try {
      let payload = await runAsk(question, isAdmin && discover);
      if (payload.job_id) {
        setAsk({
          pipeline: "Graph evidence is thin. Searching, crawling and ingesting public reporting…",
        });
        const job = await waitForJob(payload.job_id, (tick) => {
          const stage = STAGE_LABEL[tick.stage] || tick.stage;
          const counts = `found ${tick.urls_found ?? 0} · crawled ${tick.urls_crawled ?? 0} · ingested ${tick.urls_ingested || 0}`;
          setAsk({
            pipeline: `${stage} — ${counts}${tick.provider ? ` · ${tick.provider}` : ""}`,
          });
        });
        if (job.status === "done") {
          setAsk({ pipeline: "Answering from the updated Neo4j graph…" });
          payload = await runAsk(question, false);
        } else {
          setAsk({
            error: job.error || `Discovery ${job.status}. Showing the graph as it stands.`,
          });
        }
      }
      setAsk({ result: payload, jobNote: "", busy: false, pipeline: "" });
    } catch (err) {
      setAsk({
        error: err instanceof Error ? err.message : "Ask failed",
        busy: false,
        pipeline: "",
      });
    }
  }

  const focusPerson = result?.entities.find((e) => e.label === "Person")?.name || "";

  async function onDownload() {
    if (!result || exporting) return;
    setExporting(true);
    setExportError("");
    try {
      await downloadAskDocx(result);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <p className="kicker">Mode A · Graph-RAG</p>
      <h2>Ask the graph</h2>
      <p className="lede">
        {isAdmin
          ? "Ask any narcotics question. The graph is searched first (Voyage + Neo4j). If fewer than 10 on-topic chunks score at least 0.80, you can search the web (SearXNG), crawl pages, ingest them into Neo4j, then answer from the updated graph."
          : "Ask any narcotics question against the existing Neo4j graph. Answers and sources come only from ingested reporting. Web crawl and ingest are admin-only."}
      </p>
      <form className="panel" onSubmit={onSubmit}>
        <textarea
          value={query}
          onChange={(e) => setAsk({ query: e.target.value })}
          placeholder="Ask about a person, place, drug or case…"
        />
        <div className="row" style={{ marginTop: 12 }}>
          {isAdmin && (
            <label className="toggle">
              <input
                type="checkbox"
                checked={discover}
                onChange={(e) => setAsk({ discover: e.target.checked })}
              />
              Allow web discovery if the graph has fewer than 10 high-confidence matches
            </label>
          )}
          <button type="submit" disabled={loading || query.trim().length < 3}>
            {loading ? "Retrieving…" : "Ask"}
          </button>
        </div>
      </form>
      {error && <p className="err">{error}</p>}
      {loading && (
        <div className="panel loader-panel">
          <div className="spinner" aria-hidden />
          <h3 style={{ margin: 0 }}>Working this question</h3>
          <p className="muted" style={{ margin: 0 }}>
            {pipeline || "Checking the knowledge graph…"}
          </p>
          {isAdmin && (
            <p className="muted" style={{ margin: 0 }}>
              If web discovery runs, the answer appears after search, crawl, ingest and the final graph query finish.
            </p>
          )}
        </div>
      )}
      {!loading && result && (
        <div className="grid-2" style={{ marginTop: 18 }} key={result.query}>
          <div className="panel">
            <div className="row" style={{ marginBottom: 12 }}>
              <span className={`pill ${result.sufficient ? "ok" : "warn"}`}>
                {result.sufficient ? "Graph · ≥10 hits at ≥0.80" : isAdmin ? "Below gate · web discovery" : "Below gate · graph only"}
              </span>
              <span className="pill">{result.retrieval_mode}</span>
              <span className="muted">
                {result.high_confidence_chunks}/{result.required_high_confidence} chunks ≥ 0.80 ·{" "}
                {result.corpus_chunks} in graph
              </span>
            </div>
            <p className="muted" style={{ marginTop: 0 }}>
              Question: {result.query}
            </p>
            <div className="answer">{result.answer}</div>
          </div>
          <div className="list">
            {result.risk_flags.length > 0 && (
              <div className="panel">
                <h3>Risk overlay</h3>
                <p className="muted">Flags for people named in this answer’s evidence only.</p>
                {result.risk_flags.map((flag) => (
                  <div key={flag.name} style={{ marginTop: 8 }}>
                    <span className={`pill ${flag.band}`}>
                      {flag.name} · {flag.score}
                    </span>
                    <div className="muted">{flag.rules.map((r) => r.detail).join(" · ")}</div>
                  </div>
                ))}
              </div>
            )}
            <div className="panel">
              <h3>Entities</h3>
              {result.entities.length === 0 ? (
                <p className="muted">None in the graph for this question.</p>
              ) : (
                <>
                  <div className="chips" style={{ marginTop: 8 }}>
                    {result.entities.map((e) => (
                      <span className="chip" key={`${e.label}-${e.name}`}>
                        {e.name} · {e.label}
                      </span>
                    ))}
                  </div>
                  {focusPerson && (
                    <p className="muted" style={{ marginTop: 10 }}>
                      <Link
                        href={`/graph?name=${encodeURIComponent(focusPerson)}`}
                        onClick={() => setGraph({ name: focusPerson, focus: focusPerson })}
                      >
                        Open network for this question
                      </Link>
                    </p>
                  )}
                </>
              )}
            </div>
            <div className="panel">
              <h3>Sources</h3>
              {result.sources.length === 0 ? (
                <p className="muted">No matching sources in the current corpus.</p>
              ) : (
                <div className="list" style={{ marginTop: 8 }}>
                  {result.sources.map((s) => (
                    <div key={s.url || s.n} style={{ marginBottom: 8 }}>
                      <div>
                        [{s.n}] {s.title || "Untitled source"}
                        {s.date ? <span className="muted"> · {s.date}</span> : null}
                      </div>
                      {s.url ? (
                        s.url.startsWith("http") ? (
                          <a
                            className="muted"
                            href={s.url}
                            target="_blank"
                            rel="noreferrer"
                            style={{ wordBreak: "break-all" }}
                          >
                            {s.url}
                          </a>
                        ) : (
                          <div className="muted" style={{ wordBreak: "break-all" }}>
                            {s.url}
                          </div>
                        )
                      ) : (
                        <div className="muted">No URL stored for this chunk.</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {!loading && result && (
        <div style={{ marginTop: 16 }} key={`${result.query}-graph`}>
          <p className="kicker">Graph connections for this question</p>
          {result.related_cases.length === 0 ? (
            <div className="panel muted">
              No linked cases for this question. The cards from a previous search are not reused.
            </div>
          ) : (
            <div className="grid-3">
              {result.related_cases.map((c) => (
                <article className="card" key={c.id}>
                  <h3>{c.title}</h3>
                  <div className="muted">{c.date}</div>
                  <p className="muted">{c.summary}</p>
                  <div className="chips">
                    {(c.persons || []).slice(0, 4).map((p) => (
                      <span className="chip" key={p}>
                        {p}
                      </span>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      )}
      {!loading && result && (
        <div style={{ marginTop: 20, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12 }}>
          {exportError && <p className="err" style={{ margin: 0 }}>{exportError}</p>}
          <button type="button" className="secondary" onClick={() => void onDownload()} disabled={exporting}>
            {exporting ? "Preparing…" : "Download .docx"}
          </button>
        </div>
      )}
    </>
  );
}
