"use client";

import { FormEvent, useEffect, useState } from "react";
import { GraphCanvas } from "@/components/GraphCanvas";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

type Network = {
  nodes: { data: { id: string; label: string; kind: string } }[];
  edges: { data: { id: string; source: string; target: string; label: string } }[];
  stats?: Record<string, number>;
};

export default function GraphPage() {
  const { graph, setGraph, hydrated } = useWorkspace();
  const [network, setNetwork] = useState<Network | null>(null);
  const [error, setError] = useState("");
  const name = graph.name;
  const focus = graph.focus;

  async function loadAll() {
    setError("");
    try {
      setNetwork(await api<Network>("/api/graph/network"));
      setGraph({ focus: "full graph" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Graph load failed");
    }
  }

  async function focusEntity(wanted: string) {
    setError("");
    const payload = await api<{ elements: Network }>("/api/graph/entity?name=" + encodeURIComponent(wanted));
    setNetwork(payload.elements);
    setGraph({ name: wanted, focus: wanted });
  }

  async function onSearch(event: FormEvent) {
    event.preventDefault();
    try {
      await focusEntity(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Entity lookup failed");
    }
  }

  useEffect(() => {
    if (!hydrated) return;
    const wanted = new URLSearchParams(window.location.search).get("name") || graph.name;
    if (wanted) {
      void focusEntity(wanted).catch((err) =>
        setError(err instanceof Error ? err.message : "Entity lookup failed"),
      );
      return;
    }
    void loadAll();
    // Restore once after workspace hydration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated]);

  return (
    <>
      <p className="kicker">Constrained walk</p>
      <h2>Network</h2>
      <p className="lede">
        Cases, people, drugs, places and agencies joined by the relationships the extractor wrote.
        Search focuses the canvas on one entity’s 2-hop neighbourhood.
      </p>
      <form className="row" onSubmit={onSearch} style={{ marginBottom: 14 }}>
        <input
          value={name}
          onChange={(e) => setGraph({ name: e.target.value })}
          placeholder="Person, city, drug…"
          style={{ maxWidth: 360 }}
        />
        <button type="submit">Focus entity</button>
        <button type="button" className="secondary" onClick={() => void loadAll()}>
          Full graph
        </button>
        {focus && <span className="pill">{focus}</span>}
      </form>
      {error && <p className="err">{error}</p>}
      <div className="canvas-wrap">{network && <GraphCanvas elements={network} />}</div>
      <div className="legend">
        <span><i className="dot" style={{ background: "#e85d4c" }} /> Person</span>
        <span><i className="dot" style={{ background: "#d4a017" }} /> Case</span>
        <span><i className="dot" style={{ background: "#7c5cbf" }} /> Drug</span>
        <span><i className="dot" style={{ background: "#3dba7e" }} /> Location</span>
        <span><i className="dot" style={{ background: "#4a90d9" }} /> Agency / org</span>
      </div>
    </>
  );
}
