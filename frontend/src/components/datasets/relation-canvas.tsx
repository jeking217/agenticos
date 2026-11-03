"use client";

import { DragEvent, useState } from "react";
import { Link2, Table2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import type { Dataset, DatasetRelation, RelationCandidate } from "@/types/datasets";

import { CANVAS, arrowStop, edgeLabel, layoutRelations, type CanvasNode } from "./relation-layout";

interface RelationCanvasProps {
  dataset: Dataset;
  siblings: Dataset[];
  relations: DatasetRelation[];
  candidates: RelationCandidate[];
  /** A line was drawn between two datasets: open the join form on that pair. */
  onConnect: (otherId: string) => void;
}

const NODE = "w-36 -translate-x-1/2 -translate-y-1/2";

/**
 * The joins of one dataset, as lines (#T43).
 *
 * Three things a table of relations cannot say at a glance, and a picture can: a
 * confirmed join is a solid line, a suggestion is a dashed one, and the card at
 * the end of either says how many of one go with how many of the other. Drawing
 * a line between two cards is how a person asks for a join; the form it opens is
 * the one that decides the fields, so nothing is saved by a mis-drag.
 */
export function RelationCanvas({
  dataset,
  siblings,
  relations,
  candidates,
  onConnect,
}: RelationCanvasProps) {
  const t = useTranslations("datasets");
  const [over, setOver] = useState<string | null>(null);
  const nodes = layoutRelations(dataset, siblings);
  const focal = nodes.find((node) => node.focal);

  const confirmed = (id: string) => relations.find((relation) => relation.target_dataset_id === id);
  const suggested = (id: string) =>
    candidates.find((candidate) => candidate.target_dataset_id === id && !candidate.already_exists);

  const drop = (event: DragEvent<HTMLDivElement>, targetId: string) => {
    event.preventDefault();
    setOver(null);
    const from = event.dataTransfer.getData("text/plain");
    if (from && from !== targetId) onConnect(from);
  };

  return (
    <div
      data-testid="relation-canvas"
      className="border-border bg-muted/20 relative w-full overflow-x-auto rounded-xl border"
      style={{ minHeight: CANVAS.height }}
    >
      <div className="relative" style={{ width: CANVAS.width, height: CANVAS.height }}>
        <svg
          width={CANVAS.width}
          height={CANVAS.height}
          className="absolute inset-0"
          aria-hidden
          role="presentation"
        >
          {/* An edge says which way the key points: `many_to_one` reads as
              "many of these rows name one row over there", and a line with no
              head leaves that to the label alone. Two heads, because a
              confirmed join and a suggestion are drawn in different weights. */}
          <defs>
            <marker
              id="relation-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-foreground/50" />
            </marker>
            <marker
              id="relation-arrow-suggested"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-foreground/30" />
            </marker>
          </defs>
          {focal &&
            nodes
              .filter((node) => !node.focal)
              .map((node) => {
                const relation = confirmed(node.id);
                const candidate = relation ? undefined : suggested(node.id);
                if (!relation && !candidate) return null;
                const label = edgeLabel(focal, node);
                const stop = arrowStop(focal, node);
                return (
                  <g key={node.id}>
                    <line
                      x1={focal.x}
                      y1={focal.y}
                      x2={stop.x}
                      y2={stop.y}
                      className={relation ? "stroke-foreground/40" : "stroke-foreground/25"}
                      strokeWidth={relation ? 1.6 : 1.2}
                      strokeDasharray={relation ? undefined : "5 4"}
                      markerEnd={`url(#${relation ? "relation-arrow" : "relation-arrow-suggested"})`}
                      data-testid={relation ? "edge-confirmed" : "edge-candidate"}
                    />
                    <text
                      x={label.x}
                      y={label.y}
                      textAnchor="middle"
                      className="fill-muted-foreground font-mono text-[10px]"
                    >
                      {relation ? relation.cardinality : t("suggestedEdge")}
                    </text>
                  </g>
                );
              })}
        </svg>

        {nodes.map((node: CanvasNode) => {
          const relation = confirmed(node.id);
          const candidate = relation ? undefined : suggested(node.id);
          return (
            // Dragging is the shortcut for "set up a join between these two";
            // the form below the canvas is how it is done without a pointer.
            // eslint-disable-next-line jsx-a11y/no-static-element-interactions
            <div
              key={node.id}
              draggable
              onDragStart={(event) => event.dataTransfer.setData("text/plain", node.id)}
              onDragOver={(event) => {
                event.preventDefault();
                setOver(node.id);
              }}
              onDragLeave={() => setOver((current) => (current === node.id ? null : current))}
              onDrop={(event) => drop(event, node.id)}
              style={{ left: node.x, top: node.y }}
              data-testid={`canvas-node-${node.id}`}
              className={cn(
                NODE,
                "rounded-card absolute cursor-grab border p-2.5 shadow-sm transition-colors active:cursor-grabbing",
                node.focal
                  ? "border-foreground/25 bg-background"
                  : relation
                    ? "border-foreground/20 bg-background"
                    : candidate
                      ? "border-foreground/20 bg-background/80 border-dashed"
                      : "border-foreground/12 bg-background/60",
                over === node.id && "border-foreground/50 ring-foreground/10 ring-2",
              )}
            >
              <p className="text-foreground flex items-center gap-1.5 truncate text-xs font-medium">
                {relation ? (
                  <Link2 className="text-muted-foreground size-3 shrink-0" aria-hidden />
                ) : (
                  <Table2 className="text-muted-foreground size-3 shrink-0" aria-hidden />
                )}
                <span className="truncate">{node.name}</span>
              </p>
              <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
                {node.focal
                  ? t("thisDataset")
                  : relation
                    ? t("joinOn", { fields: relation.local_fields.join(",") })
                    : candidate
                      ? t("suggestedConfidence", {
                          value: Math.round(candidate.confidence * 100),
                        })
                      : t("noRelationYet")}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
