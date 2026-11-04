import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DramaEntry } from "./drama-entry";

/**
 * The entry is a button that starts a program, so the two facts worth testing
 * are where it exists and what it says when the program is not there.
 *
 * It must not exist outside the desktop shell: a browser has nothing to start,
 * and an entry that could only fail is a broken button on every deployment's
 * website. Keys are the assertions, as in the sidebar's own tests - see
 * `@/test-utils/intl`.
 */

const launch = vi.fn();
vi.mock("@/lib/desktop/drama", () => ({ launchDramaApp: () => launch() }));
vi.mock("next-intl", async () => ({
  useTranslations: (await import("@/test-utils/intl")).keyTranslations(),
}));

function withShell(platform: "windows" | "macos" | "android" = "windows") {
  window.__AGENTICOS_SHELL__ = {
    platform,
    version: "0.2.0",
    window: "main",
    vibrancy: false,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  delete window.__AGENTICOS_SHELL__;
});

describe("DramaEntry", () => {
  it("is not drawn in a browser", () => {
    render(<DramaEntry />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("is not drawn on a phone, which has no local program to start", () => {
    withShell("android");
    render(<DramaEntry />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("is drawn in the desktop shell", async () => {
    withShell("macos");
    render(<DramaEntry />);

    expect(await screen.findByRole("button", { name: "drama" })).toBeTruthy();
  });

  it("explains where to get the app when it is not installed", async () => {
    withShell();
    launch.mockResolvedValue({ status: "not-installed" });
    render(<DramaEntry />);

    fireEvent.click(await screen.findByRole("button", { name: "drama" }));

    expect(await screen.findByText("notInstalledTitle")).toBeTruthy();
    // The three steps are the instructions, so their presence is the guidance.
    expect(screen.getByText("stepDownload")).toBeTruthy();
    expect(screen.getByText("stepInstall")).toBeTruthy();
    expect(screen.getByText("stepRetry")).toBeTruthy();
  });

  it("says the sign-in could not be handed over, not that the app is missing", async () => {
    withShell();
    launch.mockResolvedValue({ status: "ticket-failed", detail: "500" });
    render(<DramaEntry />);

    fireEvent.click(await screen.findByRole("button", { name: "drama" }));

    expect(await screen.findByText("ticketTitle")).toBeTruthy();
    expect(screen.queryByText("notInstalledTitle")).toBeNull();
  });

  it("shows no dialog when the app simply opened", async () => {
    withShell();
    launch.mockResolvedValue({ status: "launched" });
    render(<DramaEntry />);

    fireEvent.click(await screen.findByRole("button", { name: "drama" }));

    await waitFor(() => expect(launch).toHaveBeenCalled());
    expect(screen.queryByText("failedTitle")).toBeNull();
    expect(screen.queryByText("notInstalledTitle")).toBeNull();
  });
});
