"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

type Hit = { title: string; url: string; snippet: string; host: string; rank: number };
type Job = {
  id: string;
  query: string;
  status: string;
  stage: string;
  urls_found: number;
  urls_crawled: number;
  urls_ingested: number;
  error: string;
  provider: string;
};

export default function DiscoverPage() {
  const { discover, setDiscover } = useWorkspace();
  const query = discover.query;
  const [hits, setHits] = useState<Hit[]>([]);
  const [provider, setProvider] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function preview(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = await api<{ hits: Hit[]; provider: string }>(
        "/api/osint/search",
        { method: "POST", body: JSON.stringify({ query, max_urls: 15 }) },
        30000,
      );
      setHits(payload.hits);
      setProvider(payload.provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  async function crawl() {
    setBusy(true);
    setError("");
    try {
      const created = await api<Job>(
        "/api/osint/crawl",
        { method: "POST", body: JSON.stringify({ query, max_urls: 8 }) },
        20000,
      );
      setJob(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Crawl failed to start");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    if (job && (job.status === "queued" || job.status === "running")) {
      timer = setInterval(() => {
        api<Job>(`/api/osint/jobs/${job.id}`)
          .then(setJob)
          .catch(() => undefined);
      }, 2500);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [job]);

  useEffect(() => {
    api<{ jobs: Job[] }>("/api/osint/jobs")
      .then((p) => setJobs(p.jobs))
      .catch(() => undefined);
  }, [job]);

  return (
    <>
      <p className="kicker">Mode B · Discover</p>
      <h2>OSINT loop</h2>
      <p className="lede">
        Search the public web, keep trusted and keyword-relevant URLs, crawl main content, extract
        entities, and merge them into the same graph. Preview first; crawl spends LLM quota.
      </p>
      <form className="panel" onSubmit={preview}>
        <input value={query} onChange={(e) => setDiscover({ query: e.target.value })} placeholder="Person, city, seizure…" />
        <div className="row" style={{ marginTop: 12 }}>
          <button type="submit" disabled={busy}>
            {busy ? "Working…" : "Preview URLs"}
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void crawl()}>
            Crawl & ingest
          </button>
          {provider && <span className="pill">{provider}</span>}
        </div>
      </form>
      {error && <p className="err">{error}</p>}
      {job && (
        <div className="panel" style={{ marginTop: 14 }}>
          <div className="row">
            <span className={`pill ${job.status === "done" ? "ok" : job.status === "failed" ? "high" : "warn"}`}>
              {job.status} · {job.stage}
            </span>
            <span className="muted">
              found {job.urls_found} · crawled {job.urls_crawled} · ingested {job.urls_ingested}
            </span>
          </div>
          {job.error && <p className="err">{job.error}</p>}
        </div>
      )}
      {hits.length > 0 && (
        <div className="panel" style={{ marginTop: 14 }}>
          <table>
            <thead>
              <tr>
                <th>Rank</th>
                <th>Title</th>
                <th>Host</th>
              </tr>
            </thead>
            <tbody>
              {hits.map((hit) => (
                <tr key={hit.url}>
                  <td className="mono">{hit.rank.toFixed(2)}</td>
                  <td>
                    <a href={hit.url} target="_blank" rel="noreferrer">
                      {hit.title || hit.url}
                    </a>
                    <div className="muted">{hit.snippet}</div>
                  </td>
                  <td className="mono">{hit.host}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {jobs.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <p className="kicker">Recent jobs</p>
          {jobs.map((item) => (
            <div className="muted" key={item.id}>
              {item.query} — {item.status} ({item.urls_ingested || 0} ingested)
            </div>
          ))}
        </div>
      )}
    </>
  );
}
