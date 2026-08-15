"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

type CaseRow = {
  id: string;
  title: string;
  date: string;
  summary: string;
  locations: string[];
  drugs: string[];
  persons: string[];
  sources: string[];
};

export default function CasesPage() {
  const { cases: filters, setCases } = useWorkspace();
  const city = filters.city;
  const drug = filters.drug;
  const [rows, setRows] = useState<CaseRow[]>([]);
  const [error, setError] = useState("");

  async function loadWith(cityVal: string, drugVal: string, event?: FormEvent) {
    event?.preventDefault();
    setError("");
    const params = new URLSearchParams();
    params.set("limit", "40");
    if (cityVal) params.set("city", cityVal);
    if (drugVal) params.set("drug", drugVal);
    try {
      const payload = await api<{ cases: CaseRow[] }>(`/api/graph/cases?${params}`);
      setRows(payload.cases);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load cases");
    }
  }

  async function load(event?: FormEvent) {
    await loadWith(city, drug, event);
  }

  async function reset() {
    setCases({ city: "", drug: "" });
    await loadWith("", "");
  }

  useEffect(() => {
    void loadWith(city, drug);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <p className="kicker">Case file</p>
      <h2>Cases</h2>
      <p className="lede">Each row is one ingested report, with the entities the extractor attached to it.</p>
      <form className="row" onSubmit={load} style={{ marginBottom: 16 }}>
        <input placeholder="City" value={city} onChange={(e) => setCases({ city: e.target.value })} style={{ maxWidth: 200 }} />
        <input placeholder="Drug" value={drug} onChange={(e) => setCases({ drug: e.target.value })} style={{ maxWidth: 200 }} />
        <button type="submit">Filter</button>
        <button type="button" className="secondary" onClick={() => void reset()}>
          Reset
        </button>
      </form>
      {error && <p className="err">{error}</p>}
      <div className="panel" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Case</th>
              <th>People</th>
              <th>Drugs</th>
              <th>Places</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="mono">{row.date || "—"}</td>
                <td>
                  <strong>{row.title}</strong>
                  <div className="muted">{row.summary}</div>
                </td>
                <td>{(row.persons || []).join(", ")}</td>
                <td>{(row.drugs || []).join(", ")}</td>
                <td>{(row.locations || []).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
