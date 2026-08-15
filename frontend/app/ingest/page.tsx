"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

type IngestResult = {
  status: string;
  article_url: string;
  chunks: number;
  relevance: number;
  reason: string;
  extraction?: {
    title: string;
    summary: string;
    persons: { name: string; role: string }[];
    drugs: { name: string; quantity: number; unit: string }[];
    locations: { name: string; city: string }[];
  };
};

export default function IngestPage() {
  const { ingest, setIngest } = useWorkspace();
  const { title, text, force } = ingest;
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = await api<IngestResult>(
        "/api/ingest/text",
        { method: "POST", body: JSON.stringify({ title, text, source: "manual", force }) },
        60000,
      );
      setResult(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="kicker">Manual ingest</p>
      <h2>Paste an article</h2>
      <p className="lede">
        Lexicon gate, then LLM extraction, then MERGE into Neo4j. Re-pasting the same text is
        idempotent.
      </p>
      <form className="panel" onSubmit={onSubmit}>
        <input placeholder="Title (optional)" value={title} onChange={(e) => setIngest({ title: e.target.value })} />
        <textarea
          style={{ marginTop: 10, minHeight: 220 }}
          placeholder="Paste public reporting here…"
          value={text}
          onChange={(e) => setIngest({ text: e.target.value })}
        />
        <div className="row" style={{ marginTop: 12 }}>
          <label className="toggle">
            <input type="checkbox" checked={force} onChange={(e) => setIngest({ force: e.target.checked })} />
            Force ingest even if the lexicon score is low
          </label>
          <button type="submit" disabled={busy || text.length < 50}>
            {busy ? "Extracting…" : "Ingest"}
          </button>
        </div>
      </form>
      {error && <p className="err">{error}</p>}
      {result && (
        <div className="panel" style={{ marginTop: 16 }}>
          <div className="row">
            <span className={`pill ${result.status === "ingested" ? "ok" : "warn"}`}>{result.status}</span>
            <span className="muted">relevance {result.relevance} · {result.chunks} chunks</span>
          </div>
          {result.reason && <p className="muted">{result.reason}</p>}
          {result.extraction && (
            <>
              <h3 style={{ marginTop: 12 }}>{result.extraction.title}</h3>
              <p className="muted">{result.extraction.summary}</p>
              <div className="chips">
                {result.extraction.persons.map((p) => (
                  <span className="chip" key={p.name}>
                    {p.name} · {p.role}
                  </span>
                ))}
                {result.extraction.drugs.map((d) => (
                  <span className="chip" key={d.name}>
                    {d.name} {d.quantity}
                    {d.unit}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
