/**
 * Where the nodes of the relation canvas sit.
 *
 * The focal dataset is the one whose panel this is, so it takes the middle and
 * the datasets it can be joined to go round it. Positions are a pure function of
 * the order they arrive in: the same dataset always draws in the same place, so
 * a line somebody dragged yesterday still reads the same way today.
 */

export const CANVAS = { width: 720, height: 340 };

export interface CanvasNode {
  id: string;
  name: string;
  /** Centre of the node, which is what an edge is drawn from. */
  x: number;
  y: number;
  focal: boolean;
}

const RADIUS_X = 268;
const RADIUS_Y = 116;

/** The focal dataset at the centre, the others evenly round it from the top. */
export function layoutRelations(
  focal: { id: string; name: string },
  others: { id: string; name: string }[],
): CanvasNode[] {
  const cx = CANVAS.width / 2;
  const cy = CANVAS.height / 2;
  const nodes: CanvasNode[] = [{ id: focal.id, name: focal.name, x: cx, y: cy, focal: true }];

  const count = others.length;
  others.forEach((other, index) => {
    // Starting at twelve o'clock: a row of siblings reads left to right and a
    // single one sits above the centre rather than beside it.
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / Math.max(count, 1);
    nodes.push({
      id: other.id,
      name: other.name,
      x: Math.round(cx + RADIUS_X * Math.cos(angle)),
      y: Math.round(cy + RADIUS_Y * Math.sin(angle)),
      focal: false,
    });
  });

  return nodes;
}

/** A label a little above the middle of an edge, clear of the line itself. */
export function edgeLabel(a: CanvasNode, b: CanvasNode): { x: number; y: number } {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 8 };
}
