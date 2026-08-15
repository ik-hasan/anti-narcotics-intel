"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Flag = {
  name: string;
  score: number;
  band: string;
  rules: { id: string; detail: string }[];
};

type Report = {
  disclaimer: string;
  rules: { id: string; name: string; severity: string; description: string }[];
  counts: Record<string, number>;
  persons: Flag[];
  locations: Flag[];
  pairs: { person_a: string; person_b: string; shared: number; cases: string[] }[];
  seizures: { title: string; drug: string; grams: number; persons: string[] }[];
};

export default function RiskPage() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Report>("/api/risk/flags")
      .then(setReport)
      .catch((err) => setError(err instanceof Error ? err.message : "Risk load failed"));
  }, []);

  return (
    <>
      <p className="kicker">Pattern matches</p>
      <h2>Risk flags</h2>
      <p className="lede">
        {report?.disclaimer ||
          "Flags fire only when two or more reports join in the graph. They are a queue for an analyst, not a verdict."}
      </p>
      {error && <p className="err">{error}</p>}
      {report && (
        <>
          <div className="grid-3" style={{ marginBottom: 16 }}>
            {report.rules.map((rule) => (
              <div className="card" key={rule.id}>
                <div className={`pill ${rule.severity}`}>{rule.id}</div>
                <h3 style={{ marginTop: 8 }}>{rule.name}</h3>
                <div className="muted">{rule.description}</div>
              </div>
            ))}
          </div>
          <div className="grid-2">
            <div className="panel">
              <h3>Scored people</h3>
              {report.persons.map((p) => (
                <div key={p.name} className="card" style={{ marginTop: 10 }}>
                  <div className="row">
                    <strong>{p.name}</strong>
                    <span className={`pill ${p.band}`}>{p.score}</span>
                  </div>
                  {p.rules.map((r) => (
                    <div className="muted" key={r.id + r.detail}>
                      {r.id}: {r.detail}
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="list">
              <div className="panel">
                <h3>Recurring pairs</h3>
                {report.pairs.map((pair) => (
                  <div key={pair.person_a + pair.person_b} className="muted" style={{ marginTop: 8 }}>
                    {pair.person_a} — {pair.person_b} ({pair.shared} shared cases)
                  </div>
                ))}
              </div>
              <div className="panel">
                <h3>High-volume seizures</h3>
                {report.seizures.map((s) => (
                  <div key={s.title} className="muted" style={{ marginTop: 8 }}>
                    {s.drug} · {Math.round(s.grams)} g · {s.title}
                  </div>
                ))}
              </div>
              <div className="panel">
                <h3>Location hubs</h3>
                {report.locations.map((l) => (
                  <div key={l.name} className="muted" style={{ marginTop: 8 }}>
                    {l.name} · {l.rules[0]?.detail}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
