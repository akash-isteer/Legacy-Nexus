import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import ELK from "elkjs/lib/elk.bundled.js";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { TYPE_COLORS } from "../lib/format";
import type { Graph } from "../lib/types";

const elk = new ELK();

export type GraphHandle = { fit: () => void; zoom: (factor: number) => void; png: () => string | null; focus: (id: string) => void };

type Props = {
  graph: Graph;
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  direction?: "RIGHT" | "DOWN";
  nodeAccent?: Record<string, string>; // id -> css colour for a ring (severity, root)
  showEdgeLabels?: boolean;
  dense?: boolean;
};

const EDGE_LABEL: Record<string, string> = {
  EXECUTES: "runs", CALLS: "calls", READ_BY: "read by", WRITES: "writes", EXPOSED_AS: "exposed as",
  INCLUDED_IN: "included in", REFERENCES: "FK", HAS_COLUMN: "", DATA: "", CONTROL: "controls", PART_OF: "part of",
  UNRESOLVED_CALL: "calls at runtime", USES: "uses", MAPS_TO: "maps to", CONFIGURES: "connects to",
};

function nodeSize(label: string, dense?: boolean) {
  const w = Math.min(260, Math.max(dense ? 76 : 100, label.length * (dense ? 6.8 : 7.8) + 30));
  return { width: w, height: dense ? 30 : 38 };
}

export const GraphCanvas = forwardRef<GraphHandle, Props>(function GraphCanvas(
  { graph, selected, onSelect, direction = "RIGHT", nodeAccent, showEdgeLabels = true, dense },
  ref,
) {
  const el = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useImperativeHandle(ref, () => ({
    fit: () => cyRef.current?.animate({ fit: { eles: cyRef.current.elements(), padding: 60 }, duration: 250 }),
    zoom: (f) => {
      const cy = cyRef.current;
      if (cy) cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
    },
    png: () => cyRef.current?.png({ full: true, scale: 2, bg: "#ffffff" }) ?? null,
    focus: (id) => {
      const cy = cyRef.current;
      const n = cy?.getElementById(id);
      if (cy && n && n.nonempty()) cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1), duration: 300 });
    },
  }));

  useEffect(() => {
    if (!el.current) return;
    let cancelled = false;
    const ids = new Set(graph.nodes.map((n) => n.id));
    const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target);

    const elements: ElementDefinition[] = [
      ...graph.nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label,
          type: n.type,
          color: TYPE_COLORS[n.type] ?? "#6f7c8e",
          accent: nodeAccent?.[n.id] ?? "",
          ...nodeSize(n.label, dense),
        },
      })),
      ...edges.map((e, i) => ({
        data: {
          id: `e${i}`,
          source: e.source,
          target: e.target,
          label: showEdgeLabels ? (e.how ?? EDGE_LABEL[e.type] ?? e.type.toLowerCase()) : "",
          kind: e.type,
        },
      })),
    ];

    const cy = cytoscape({
      container: el.current,
      elements,
      wheelSensitivity: 0.25,
      minZoom: 0.15,
      maxZoom: 3,
      style: [
        {
          selector: "node",
          style: {
            shape: "round-rectangle",
            width: "data(width)",
            height: "data(height)",
            "background-color": "#ffffff",
            "border-width": 2,
            "border-color": "data(color)",
            label: "data(label)",
            "font-family": "IBM Plex Sans, system-ui, sans-serif",
            "font-size": dense ? 11 : 13,
            "font-weight": 500,
            color: "#18222f",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "ellipsis",
            "text-max-width": "data(width)",
          },
        },
        { selector: "node[type = 'job']", style: { "background-color": "#ece9fa" } },
        { selector: "node[type = 'table'], node[type = 'column']", style: { "background-color": "#e2f2f0", shape: "barrel" } },
        { selector: "node[type = 'dataset']", style: { "background-color": "#fdf2df", shape: "tag" } },
        { selector: "node[type = 'api']", style: { "background-color": "#f8e6ef", shape: "hexagon" } },
        { selector: "node[type = 'external']", style: { "border-style": "dashed", "background-color": "#f3f5f8" } },
        { selector: "node[type = 'unresolved']", style: { "border-style": "dashed", "background-color": "#fff1e8", shape: "diamond" } },
        { selector: "node[type = 'config']", style: { "background-color": "#f1f5f9", shape: "cut-rectangle" } },
        { selector: "node[type = 'field']", style: { "background-color": "#f3f5f8", "font-family": "IBM Plex Mono, monospace", "font-size": 10.5 } },
        { selector: "node[accent != '']", style: { "border-width": 4, "border-color": "data(accent)" } },
        { selector: "node:selected", style: { "border-width": 4, "border-color": "#e9a23b", "overlay-opacity": 0 } },
        { selector: ".faded", style: { opacity: 0.18 } },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#9aa8ba",
            "target-arrow-color": "#9aa8ba",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.9,
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 9.5,
            color: "#7a8698",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
            "text-rotation": "autorotate",
          },
        },
        { selector: "edge[kind = 'CONTROL']", style: { "line-style": "dashed", "line-color": "#c9821b", "target-arrow-color": "#c9821b" } },
        { selector: "edge[kind = 'WRITES']", style: { "line-color": "#15807a", "target-arrow-color": "#15807a" } },
        { selector: "edge[kind = 'UNRESOLVED_CALL']", style: { "line-style": "dashed", "line-color": "#c2410c", "target-arrow-color": "#c2410c" } },
        { selector: "edge[kind = 'USES'], edge[kind = 'MAPS_TO'], edge[kind = 'CONFIGURES']", style: { "line-style": "dotted" } },
        { selector: "edge[kind = 'HAS_COLUMN']", style: { "line-style": "dotted", "target-arrow-shape": "none" } },
        { selector: "edge.hl", style: { width: 2.6, "line-color": "#e9a23b", "target-arrow-color": "#e9a23b", "z-index": 5 } },
      ],
      layout: { name: "preset" },
    });
    cyRef.current = cy;

    cy.on("tap", "node", (evt) => selectRef.current?.(evt.target.id()));
    cy.on("tap", (evt) => {
      if (evt.target === cy) selectRef.current?.(null);
    });

    elk
      .layout({
        id: "root",
        layoutOptions: {
          "elk.algorithm": "layered",
          "elk.direction": direction,
          "elk.layered.spacing.nodeNodeBetweenLayers": dense ? "50" : "64",
          "elk.spacing.nodeNode": dense ? "16" : "22",
          "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
          "elk.separateConnectedComponents": "true",
          "elk.spacing.componentComponent": "50",
        },
        children: graph.nodes.map((n) => ({ id: n.id, ...nodeSize(n.label, dense) })),
        edges: edges.map((e, i) => ({ id: `e${i}`, sources: [e.source], targets: [e.target] })),
      })
      .then((res) => {
        if (cancelled) return;
        cy.batch(() => {
          res.children?.forEach((c) => {
            cy.getElementById(c.id).position({ x: (c.x ?? 0) + (c.width ?? 0) / 2, y: (c.y ?? 0) + (c.height ?? 0) / 2 });
          });
        });
        cy.fit(undefined, 50);
        if (cy.zoom() > 1.3) cy.zoom(1.3);
        const anchor = cy.nodes("[accent != '']").first();
        const minZoom = dense ? 0.95 : 0.62;
        if (cy.zoom() < minZoom) {
          // Too dense to read when fitted: keep labels legible and start from the selected element.
          cy.zoom(minZoom);
          const target = anchor.nonempty() ? anchor : cy.nodes().first();
          const pos = target.renderedPosition();
          cy.panBy({ x: 40 + target.renderedWidth() / 2 - pos.x, y: cy.height() / 2 - pos.y });
        } else {
          cy.center();
        }
      })
      .catch(() => cy.layout({ name: "breadthfirst", directed: true, padding: 40 }).run());

    return () => {
      cancelled = true;
      cy.destroy();
      cyRef.current = null;
    };
  }, [graph, direction, nodeAccent, showEdgeLabels, dense]);

  // Selection highlight without relayout
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("faded hl");
      cy.nodes().unselect();
      if (!selected) return;
      const n = cy.getElementById(selected);
      if (n.empty()) return;
      n.select();
      const hood = n.closedNeighborhood();
      cy.elements().not(hood).addClass("faded");
      n.connectedEdges().addClass("hl");
    });
  }, [selected, graph]);

  return <div ref={el} className="graph-canvas" />;
});
