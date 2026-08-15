"use client";

import { useEffect, useRef } from "react";
import cytoscape, { type ElementDefinition } from "cytoscape";

const COLORS: Record<string, string> = {
  Person: "#e85d4c",
  Case: "#d4a017",
  Drug: "#7c5cbf",
  Location: "#3dba7e",
  Org: "#4a90d9",
};

type Props = {
  elements: { nodes: ElementDefinition[]; edges: ElementDefinition[] };
};

export function GraphCanvas({ elements }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const cy = cytoscape({
      container: ref.current,
      elements: [...elements.nodes, ...elements.edges],
      layout: { name: "cose", animate: false, padding: 24, nodeOverlap: 24 },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            color: "#d5deea",
            "font-size": 10,
            "font-family": "IBM Plex Sans",
            "text-wrap": "wrap",
            "text-max-width": "90px",
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
          style: { "background-color": color, width: kind === "Case" ? 28 : 20, height: kind === "Case" ? 28 : 20 },
        })),
      ],
      minZoom: 0.3,
      maxZoom: 2.4,
    });
    return () => {
      cy.destroy();
    };
  }, [elements]);

  return <div ref={ref} style={{ width: "100%", height: "100%" }} />;
}
