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

/**
 * How much room a node's box takes either side of its centre.
 *
 * `NODE` in the canvas is `w-36` with two lines of text in `p-2.5`, so the box
 * is 144 wide and about 56 tall; the extra few pixels are the gap an arrowhead
 * needs to sit outside the card rather than under it.
 */
export const NODE_HALF = { w: 74, h: 32 };

/**
 * Where an edge has to stop so its arrowhead is visible.
 *
 * An edge is drawn between two centres, and both cards are opaque and painted
 * over the line - so a line that ends at the centre loses its last 70 pixels,
 * arrowhead included. This walks back along the line to the box's edge, on
 * whichever side it leaves through: the horizontal one on a mostly-horizontal
 * edge, the vertical one on a mostly-vertical edge.
 */
export function arrowStop(
  from: { x: number; y: number },
  to: { x: number; y: number },
  half: { w: number; h: number } = NODE_HALF,
): { x: number; y: number } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (dx === 0 && dy === 0) return to;
  const alongX = dx === 0 ? Number.POSITIVE_INFINITY : half.w / Math.abs(dx);
  const alongY = dy === 0 ? Number.POSITIVE_INFINITY : half.h / Math.abs(dy);
  // The first box side the ray reaches. `t >= 1` means the other centre is
  // inside that box already (two nodes closer than a card width), and there is
  // nothing to trim that would not put the head back under a card.
  const t = Math.min(alongX, alongY);
  if (t >= 1) return to;
  return { x: Math.round(to.x - dx * t), y: Math.round(to.y - dy * t) };
}
