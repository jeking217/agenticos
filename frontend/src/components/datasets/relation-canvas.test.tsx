import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RelationCanvas } from "./relation-canvas";
import type { Dataset, DatasetRelation, RelationCandidate } from "@/types/datasets";

/**
 * The join canvas (#T43).
 *
 * Three states have to be distinguishable without hovering or opening anything:
 * a registered join, a suggestion the data made, and two datasets with nothing
 * between them. Drawing a line is a request, not a save - it hands the pair to
 * the form, which is where fields and cardinality are decided.
 */

const orders = { id: "ds-1", name: "Orders", fields: [] } as unknown as Dataset;
const customers = { id: "ds-2", name: "Customers", fields: [] } as unknown as Dataset;
const products = { id: "ds-3", name: "Products", fields: [] } as unknown as Dataset;

const relation = {
  id: "rel-1",
  target_dataset_id: "ds-2",
  target_dataset_name: "Customers",
  name: "to_customers",
  cardinality: "n:1",
  local_fields: ["customer_id"],
  foreign_fields: ["id"],
  status: "confirmed",
} as DatasetRelation;

const candidate = {
  name: "to_products",
  target_dataset_id: "ds-3",
  target_dataset_name: "Products",
  local_fields: ["product_id"],
  foreign_fields: ["id"],
  cardinality: "n:1",
  confidence: 0.87,
  already_exists: false,
} as RelationCandidate;

function draw(overrides: Partial<Parameters<typeof RelationCanvas>[0]> = {}) {
  const onConnect = vi.fn();
  const { container } = render(
    <RelationCanvas
      dataset={orders}
      siblings={[customers, products]}
      relations={[relation]}
      candidates={[candidate]}
      onConnect={onConnect}
      {...overrides}
    />,
  );
  return { onConnect, container };
}

const drag = (from: string, to: string) => {
  const source = screen.getByTestId(`canvas-node-${from}`);
  const target = screen.getByTestId(`canvas-node-${to}`);
  fireEvent.dragStart(source, { dataTransfer: { setData: vi.fn(), getData: () => from } });
  fireEvent.dragOver(target, { dataTransfer: { getData: () => from } });
  fireEvent.drop(target, { dataTransfer: { getData: () => from } });
};

describe("the relation canvas", () => {
  it("draws a registered join as a solid line, labelled with its cardinality", () => {
    const { container } = draw();

    expect(container.querySelectorAll('[data-testid="edge-confirmed"]')).toHaveLength(1);
    expect(screen.getByText("n:1")).toBeInTheDocument();
  });

  it("draws a suggestion as a dashed line", () => {
    const { container } = draw();

    const dashed = container.querySelector('[data-testid="edge-candidate"]');
    expect(dashed?.getAttribute("stroke-dasharray")).toBeTruthy();
  });

  it("draws nothing between datasets with no join and no suggestion", () => {
    draw({ siblings: [customers, products], relations: [], candidates: [] });

    expect(screen.queryByTestId("edge-confirmed")).toBeNull();
    expect(screen.queryByTestId("edge-candidate")).toBeNull();
    expect(screen.getAllByText("no join yet")).toHaveLength(2);
  });

  it("says which field a registered join uses", () => {
    draw();

    expect(screen.getByText("joins on customer_id")).toBeInTheDocument();
  });

  it("hands a drawn line to the form instead of saving it", () => {
    const { onConnect } = draw();

    drag("ds-3", "ds-1");

    expect(onConnect).toHaveBeenCalledWith("ds-3");
  });

  it("refuses a line from a dataset to itself", () => {
    const { onConnect } = draw();

    drag("ds-1", "ds-1");

    expect(onConnect).not.toHaveBeenCalled();
  });

  it("marks the card a line is being drawn onto", () => {
    draw();
    const target = screen.getByTestId("canvas-node-ds-2");

    fireEvent.dragOver(target, { dataTransfer: { getData: () => "ds-3" } });

    expect(target.className).toContain("ring-2");
  });
});
