import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { UsedBy } from "./used-by";

const agent = (name: string) => ({ id: name, name });

describe("UsedBy", () => {
  it("says nothing when the response did not say", () => {
    const { container } = render(<UsedBy />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says plainly that nothing uses it yet", () => {
    render(<UsedBy agents={[]} />);
    expect(screen.getByText("Not used by any agent yet")).toBeInTheDocument();
  });

  it("names up to two agents and counts the rest", () => {
    render(<UsedBy agents={[agent("Billing"), agent("Sales"), agent("Support")]} />);
    expect(screen.getByText("Used by Billing, Sales and 1 more")).toBeInTheDocument();
  });

  it("names one agent alone", () => {
    render(<UsedBy agents={[agent("Support")]} />);
    expect(screen.getByText("Used by Support")).toBeInTheDocument();
  });
});

describe("UsedBy's removal", () => {
  it("takes one agent off in a single click", async () => {
    const onRemove = vi.fn();
    render(<UsedBy agents={[agent("Support")]} onRemove={onRemove} />);

    await userEvent.click(screen.getByRole("button", { name: "Remove from Support" }));

    expect(onRemove).toHaveBeenCalledWith(agent("Support"));
  });

  it("asks which agent when several have it, and names the one picked", async () => {
    // Guessing would be silent: the removal is a draft write with no undo, and
    // the toast is the only thing that says which agent lost it.
    const onRemove = vi.fn();
    render(<UsedBy agents={[agent("Billing"), agent("Sales")]} onRemove={onRemove} />);

    await userEvent.click(screen.getByRole("button", { name: "Remove from an agent" }));
    expect(await screen.findByText("Which agent should stop using this?")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove from Sales" }));

    expect(onRemove).toHaveBeenCalledWith(agent("Sales"));
    await waitFor(() =>
      expect(screen.queryByText("Which agent should stop using this?")).not.toBeInTheDocument(),
    );
  });

  it("hides the control where the reader may not edit agents", () => {
    render(<UsedBy agents={[agent("Support")]} />);

    expect(screen.queryByTestId("used-by-remove")).not.toBeInTheDocument();
  });

  it("offers nothing to remove when nobody uses it", () => {
    render(<UsedBy agents={[]} onRemove={vi.fn()} />);

    expect(screen.queryByTestId("used-by-remove")).not.toBeInTheDocument();
  });

  it("cannot be pressed twice while the first removal is in flight", () => {
    render(<UsedBy agents={[agent("Support")]} onRemove={vi.fn()} removing="Support" />);

    expect(screen.getByTestId("used-by-remove")).toBeDisabled();
  });
});
