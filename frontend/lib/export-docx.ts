"use client";

import cytoscape from "cytoscape";
import {
  Document,
  Packer,
  Paragraph,
  TextRun,
  HeadingLevel,
  ImageRun,
  ExternalHyperlink,
  PageBreak,
} from "docx";
import { api } from "@/lib/api";
import type { AskResponse } from "@/lib/workspace";

type Network = {
  nodes: { data: { id: string; label: string; kind: string } }[];
  edges: { data: { id: string; source: string; target: string; label: string } }[];
};

const COLORS: Record<string, string> = {
  Person: "#e85d4c",
  Case: "#d4a017",
  Drug: "#7c5cbf",
  Location: "#3dba7e",
  Org: "#4a90d9",
};

function slug(text: string): string {
  const cut = (text || "ask-response").slice(0, 60).replace(/[^\w]+/g, "-").replace(/^-|-$/g, "");
  return cut || "ask-response";
}

function p(text: string, opts?: { bold?: boolean; size?: number; color?: string }): Paragraph {
  return new Paragraph({
    spacing: { after: 160 },
    children: [
      new TextRun({
        text,
        font: "Calibri",
        size: opts?.size || 22,
        bold: opts?.bold,
        color: opts?.color,
      }),
    ],
  });
}

// function heading(text: string, level = HeadingLevel.HEADING_1) {
//   return new Paragraph({
//     heading: level,
//     spacing: { before: 280, after: 120 },
//     children: [new TextRun({ text, font: "Calibri" })],
//   });
// }
function heading(text: string, level: HeadingLevel = HeadingLevel.HEADING_1) {
  return new Paragraph({
    heading: level,
    spacing: { before: 280, after: 120 },
    children: [new TextRun({ text, font: "Calibri" })],
  });
}

async function fetchNetwork(result: AskResponse): Promise<{ focus: string; network: Network } | null> {
  const names = [
    ...result.entities.filter((e) => e.label === "Person").map((e) => e.name),
    ...result.entities.map((e) => e.name),
    ...(result.related_cases[0]?.persons || []),
  ].filter(Boolean);
  const seen = new Set<string>();
  for (const name of names) {
    if (seen.has(name)) continue;
    seen.add(name);
    try {
      const payload = await api<{ elements: Network }>(
        "/api/graph/entity?name=" + encodeURIComponent(name),
        {},
        20000,
      );
      if (payload.elements?.nodes?.length) {
        return { focus: name, network: payload.elements };
      }
    } catch {
      continue;
    }
  }
  return null;
}

function renderNetworkPng(network: Network): Promise<Uint8Array | null> {
  return new Promise((resolve) => {
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:-9999px;top:0;width:960px;height:580px;";
    document.body.appendChild(host);
    const cy = cytoscape({
      container: host,
      elements: [...network.nodes, ...network.edges],
      layout: { name: "cose", animate: false, padding: 28, nodeOverlap: 20 },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            color: "#d5deea",
            "font-size": 11,
            "text-wrap": "wrap",
            "text-max-width": "100px",
            "background-color": "#4a90d9",
            width: 22,
            height: 22,
            "border-width": 1,
            "border-color": "#0b1016",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": "#2a3b4f",
            "target-arrow-color": "#2a3b4f",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.7,
          },
        },
        ...Object.entries(COLORS).map(([kind, color]) => ({
          selector: `node[kind = "${kind}"]`,
          style: { "background-color": color },
        })),
      ],
    });

    const finish = () => {
      try {
        const dataUrl = cy.png({ full: true, scale: 2, bg: "#0c1118" });
        const comma = dataUrl.indexOf(",");
        const binary = atob(dataUrl.slice(comma + 1));
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        resolve(bytes.length > 32 ? bytes : null);
      } catch {
        resolve(null);
      } finally {
        cy.destroy();
        host.remove();
      }
    };

    const timer = window.setTimeout(finish, 2500);
    cy.one("layoutstop", () => {
      window.clearTimeout(timer);
      window.setTimeout(finish, 120);
    });
  });
}

export async function downloadAskDocx(result: AskResponse): Promise<void> {
  const children: Paragraph[] = [
    heading("Narco-Graph Intel — Ask report"),
    p(`Generated ${new Date().toISOString().slice(0, 19).replace("T", " ")} UTC`, { size: 18, color: "666666" }),
    heading("Question", HeadingLevel.HEADING_2),
    p(result.query),
    heading("Answer", HeadingLevel.HEADING_2),
    ...result.answer.split(/\n+/).filter(Boolean).map((line) => p(line)),
    p(
      `${result.retrieval_mode} · ${result.high_confidence_chunks}/${result.required_high_confidence} chunks ≥ 0.80 · ${result.corpus_chunks} in graph`,
      { size: 18, color: "666666" },
    ),
  ];

  children.push(heading("Risk overlay", HeadingLevel.HEADING_2));
  if (!result.risk_flags.length) {
    children.push(p("None for people named in this answer’s evidence."));
  } else {
    for (const flag of result.risk_flags) {
      children.push(p(`${flag.name} — score ${flag.score} (${flag.band})`, { bold: true }));
      children.push(p(flag.rules.map((r) => r.detail).join(" · "), { size: 20, color: "555555" }));
    }
  }

  children.push(heading("Entities", HeadingLevel.HEADING_2));
  if (!result.entities.length) {
    children.push(p("None in the graph for this question."));
  } else {
    children.push(p(result.entities.map((e) => `${e.name} (${e.label})`).join("; ")));
  }

  children.push(heading("Sources", HeadingLevel.HEADING_2));
  if (!result.sources.length) {
    children.push(p("No matching sources in the current corpus."));
  } else {
    for (const source of result.sources) {
      children.push(p(`[${source.n}] ${source.title || "Untitled source"}${source.date ? ` · ${source.date}` : ""}`));
      if (source.url?.startsWith("http")) {
        children.push(
          new Paragraph({
            spacing: { after: 160 },
            children: [
              new ExternalHyperlink({
                link: source.url,
                children: [new TextRun({ text: source.url, style: "Hyperlink", font: "Calibri", size: 20 })],
              }),
            ],
          }),
        );
      } else if (source.url) {
        children.push(p(source.url, { size: 20, color: "555555" }));
      }
    }
  }

  children.push(heading("Graph connections", HeadingLevel.HEADING_2));
  if (!result.related_cases.length) {
    children.push(p("No linked cases for this question."));
  } else {
    for (const item of result.related_cases) {
      children.push(p(`${item.title}${item.date ? ` (${item.date})` : ""}`, { bold: true }));
      if (item.summary) children.push(p(item.summary, { size: 20 }));
      const bits = [
        item.persons?.length ? `People: ${item.persons.join(", ")}` : "",
        item.drugs?.length ? `Drugs: ${item.drugs.join(", ")}` : "",
        item.locations?.length ? `Places: ${item.locations.join(", ")}` : "",
      ].filter(Boolean);
      if (bits.length) children.push(p(bits.join(" · "), { size: 20, color: "555555" }));
    }
  }

  const captured = await fetchNetwork(result);
  children.push(heading("Network for this question", HeadingLevel.HEADING_2));
  if (captured) {
    children.push(p(`Neighbourhood around “${captured.focus}”.`));
    const png = await renderNetworkPng(captured.network);
    if (png) {
      children.push(
        new Paragraph({
          spacing: { after: 200 },
          children: [
            new ImageRun({
              type: "png",
              data: png,
              transformation: { width: 520, height: 314 },
              altText: { title: "Network graph", description: `Graph around ${captured.focus}`, name: "network" },
            }),
          ],
        }),
      );
    } else {
      children.push(p("A graph image could not be rendered; entity list above is the neighbourhood."));
    }
    const nodes = captured.network.nodes.map((n) => n.data.label).filter(Boolean);
    if (nodes.length) {
      children.push(p(`Nodes: ${nodes.slice(0, 40).join("; ")}${nodes.length > 40 ? "…" : ""}`, { size: 18, color: "555555" }));
    }
  } else {
    children.push(p("No entity neighbourhood was available to attach for this answer."));
  }

  children.push(new Paragraph({ children: [new PageBreak()] }));
  children.push(
    p(
      "Flags and extracted names are from public reporting in the graph. They are not findings of guilt.",
      { size: 18, color: "666666" },
    ),
  );

  const doc = new Document({
    creator: "Narco-Graph Intel",
    title: result.query.slice(0, 120),
    description: "Ask report from Narco-Graph Intel",
    sections: [{ properties: {}, children }],
  });

  const blob = await Packer.toBlob(doc);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `narco-graph-${slug(result.query)}.docx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1500);
}
