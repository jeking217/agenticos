import { describe, expect, it } from "vitest";

import { CANVAS, NODE_HALF, arrowStop, edgeLabel, layoutRelations } from "./relation-layout";

/**
 * Where the cards of the relation canvas go.
 *
 * A canvas is only readable if the same dataset keeps the same place, and a line
 * drawn between two cards has to meet them both. Both are geometry, so both are
 * pinned here rather than eyeballed in a browser.
 */

const focal = { id: "ds-1", name: "Orders" };

describe("layoutRelations", () => {
  it("puts the dataset whose page this is in the middle", () => {
    const [centre] = layoutRelations(focal, [{ id: "ds-2", name: "Customers" }]);

    expect(centre).toMatchObject({
      id: "ds-1",
      focal: true,
      x: CANVAS.width / 2,
      y: CANVAS.height / 2,
    });
  });

  it("places one related dataset above the centre, not beside it", () => {
    // A single neighbour sitting at three o'clock reads as an arrow, not a join.
    const [, other] = layoutRelations(focal, [{ id: "ds-2", name: "Customers" }]);

    expect(other?.x).toBe(CANVAS.width / 2);
    expect(other?.y).toBeLessThan(CANVAS.height / 2);
  });

  it("spreads the rest evenly, all of them inside the canvas", () => {
    const others = ["a", "b", "c", "d"].map((id) => ({ id, name: id }));
    const nodes = layoutRelations(focal, others);

    expect(nodes).toHaveLength(5);
    for (const node of nodes) {
      expect(node.x).toBeGreaterThanOrEqual(0);
      expect(node.x).toBeLessThanOrEqual(CANVAS.width);
      expect(node.y).toBeGreaterThanOrEqual(0);
      expect(node.y).toBeLessThanOrEqual(CANVAS.height);
    }
    // Four siblings: two above the centre, two below, none on top of another.
    const ys = nodes.slice(1).map((node) => node.y);
    expect(new Set(ys).size).toBeGreaterThan(1);
  });

  it("keeps every node in the order it was given", () => {
    const others = [
      { id: "a", name: "A" },
      { id: "b", name: "B" },
    ];

    expect(layoutRelations(focal, others).map((node) => node.id)).toEqual(["ds-1", "a", "b"]);
  });
});

describe("edgeLabel", () => {
  it("sits above the middle of the line, clear of it", () => {
    const a = { id: "a", name: "A", x: 0, y: 100, focal: true };
    const b = { id: "b", name: "B", x: 200, y: 100, focal: false };

    expect(edgeLabel(a, b)).toEqual({ x: 100, y: 92 });
  });
});

describe("arrowStop", () => {
  const centre = { x: 300, y: 170 };

  it("stops at the side of the box on a horizontal edge", () => {
    // Straight right: the arrowhead belongs on the left edge of the card, not
    // under its middle, which is where the card would paint over it.
    expect(arrowStop(centre, { x: 600, y: 170 })).toEqual({ x: 600 - NODE_HALF.w, y: 170 });
  });

  it("stops at the top of the box on a vertical edge", () => {
    expect(arrowStop(centre, { x: 300, y: 20 })).toEqual({ x: 300, y: 20 + NODE_HALF.h });
  });

  it("takes the horizontal side on a shallow diagonal, the vertical one on a steep one", () => {
    const shallow = arrowStop(centre, { x: 600, y: 200 });
    const steep = arrowStop(centre, { x: 330, y: 20 });

    // A mostly-horizontal line leaves through the left/right edge, so what is
    // trimmed is the x distance.
    expect(shallow.x).toBe(600 - NODE_HALF.w);
    expect(shallow.y).toBeGreaterThan(170);
    expect(steep.y).toBe(20 + NODE_HALF.h);
  });

  it("leaves an edge alone when the two centres are closer than a box", () => {
    // Nothing sensible to trim: two nodes that overlap stay where they are.
    expect(arrowStop({ x: 10, y: 10 }, { x: 20, y: 10 })).toEqual({ x: 20, y: 10 });
    expect(arrowStop(centre, centre)).toEqual(centre);
  });
});
