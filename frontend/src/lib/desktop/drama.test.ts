import { afterEach, describe, expect, it, vi } from "vitest";

import { launchDramaApp } from "./drama";

/**
 * The order of what happens is the thing worth testing: a ticket is minted
 * before the app is asked to start, and a ticket that was not minted never
 * reaches a process. Everything else here is the three ways it can go wrong
 * arriving as a status rather than an exception, because the page has to say
 * each one differently and an unhandled rejection says none of them.
 */

const shell = (invoke: (command: string, args?: Record<string, unknown>) => unknown) => {
  window.__AGENTICOS_SHELL__ = {
    platform: "windows",
    version: "0.2.0",
    window: "main",
    vibrancy: false,
  };
  window.__TAURI__ = {
    core: { invoke },
    event: { listen: () => Promise.resolve(() => {}) },
  };
};

const ticketAnswer = (ticket = "tk-0123456789abcdef") =>
  Promise.resolve(new Response(JSON.stringify({ ticket }), { status: 200 }));

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.__AGENTICOS_SHELL__;
  delete window.__TAURI__;
});

describe("launchDramaApp", () => {
  it("mints a ticket before asking the shell to start the app", async () => {
    const order: string[] = [];
    const fetchMock = vi.fn(() => {
      order.push("ticket");
      return ticketAnswer();
    });
    vi.stubGlobal("fetch", fetchMock);
    shell((command) => {
      order.push(command);
      return Promise.resolve({ launched: true });
    });

    await expect(launchDramaApp()).resolves.toEqual({ status: "launched" });
    expect(order).toEqual(["ticket", "open_drama"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/drama/ticket",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("passes the ticket to the shell and nothing else", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ticketAnswer("tk-abc123def456")));
    const invoke = vi.fn(() => Promise.resolve({ launched: true }));
    shell(invoke);

    await launchDramaApp();

    expect(invoke).toHaveBeenCalledWith("open_drama", { ticket: "tk-abc123def456" });
  });

  it("reports a missing install instead of an error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ticketAnswer()));
    shell(() => Promise.resolve({ launched: false, reason: "not_installed" }));

    await expect(launchDramaApp()).resolves.toEqual({ status: "not-installed" });
  });

  it("keeps the shell's reason when the app will not start", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ticketAnswer()));
    shell(() => Promise.resolve({ launched: false, reason: "spawn_failed" }));

    await expect(launchDramaApp()).resolves.toEqual({
      status: "failed",
      detail: "spawn_failed",
    });
  });

  it("never starts the app when no ticket was minted", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({}), { status: 500 }))),
    );
    const invoke = vi.fn(() => Promise.resolve({ launched: true }));
    shell(invoke);

    const outcome = await launchDramaApp();

    expect(outcome.status).toBe("ticket-failed");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("refuses a ticket that is not one, whatever the answer said", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ticket: "nope" }), { status: 200 }))),
    );
    const invoke = vi.fn(() => Promise.resolve({ launched: true }));
    shell(invoke);

    const outcome = await launchDramaApp();

    expect(outcome.status).toBe("ticket-failed");
    expect(invoke).not.toHaveBeenCalled();
  });
});
