import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  NO_REASONING,
  ReasoningModelFields,
  reasoningIncomplete,
  reasoningPayload,
  type ReasoningChoice,
  type ReasoningProvider,
} from "./reasoning-model-fields";
import { apiClient } from "@/lib/api-client";

/**
 * The reading model, asked for the way the document form asks for an embedding
 * model - with one difference: the model id is typed.
 *
 * What is worth pinning is the shape of the answer rather than the pixels: a
 * provider with no model is not a model, a keyed provider with no key is not
 * one either, and "off" has to be an answer the API accepts rather than four
 * empty strings.
 */

vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

// Typed entries rather than an index lookup: with `noUncheckedIndexedAccess`,
// `PROVIDERS[0]` is `ReasoningProvider | undefined` and every spread of it would
// lose `id` and `name`.
const HOSTED: ReasoningProvider = {
  id: "openrouter",
  name: "OpenRouter",
  keyless: false,
  supports_base_url: true,
};
const KEYLESS: ReasoningProvider = {
  id: "ollama",
  name: "Ollama",
  keyless: true,
  supports_base_url: true,
};
const PROVIDERS = [HOSTED, KEYLESS];

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** A controlled field that reports what it holds, so the payload is assertable. */
function Harness({ initial = NO_REASONING }: { initial?: ReasoningChoice }) {
  const [value, setValue] = useState<ReasoningChoice>(initial);
  return (
    <>
      <ReasoningModelFields value={value} onChange={setValue} idPrefix="test" />
      <output data-testid="payload">{JSON.stringify(reasoningPayload(value))}</output>
    </>
  );
}

function payload(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("payload").textContent ?? "{}");
}

describe("choosing the model that reads workbooks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiClient.get).mockImplementation(async (path: string) => {
      if (path === "/kb/datasets/reasoning-providers") return { items: PROVIDERS };
      if (path === "/me/permissions")
        return { organization_id: "org-1", role: "member", is_app_admin: false, permissions: [] };
      return { items: [], total: 0 };
    });
  });

  it("starts on no model, and says so without asking for anything else", async () => {
    render(<Harness />, { wrapper });

    expect(await screen.findByText("Reading model")).toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
    expect(payload()).toEqual({
      reasoning_provider: null,
      reasoning_model: null,
      reasoning_secret_id: null,
      reasoning_base_url: null,
    });
  });

  it("asks for a model id and an address once a hosted provider is chosen", async () => {
    render(<Harness />, { wrapper });
    await screen.findByText("Reading model");

    await userEvent.click(screen.getByLabelText("Provider"));
    await userEvent.click(await screen.findByRole("option", { name: /OpenRouter/ }));

    // The model is a text field: providers ship ids faster than a list follows.
    const model = await screen.findByLabelText("Model");
    expect(model.tagName).toBe("INPUT");
    expect(screen.getByLabelText("Endpoint")).toBeInTheDocument();
    // A hosted provider needs a key, and there is none in this vault yet.
    expect(screen.getByLabelText("Key")).toBeInTheDocument();
  });

  it("sends the model id it was given, and nothing it was not", async () => {
    render(<Harness />, { wrapper });
    await screen.findByText("Reading model");
    await userEvent.click(screen.getByLabelText("Provider"));
    await userEvent.click(await screen.findByRole("option", { name: /OpenRouter/ }));
    await userEvent.type(await screen.findByLabelText("Model"), "openai/gpt-5");

    await waitFor(() => expect(payload()["reasoning_model"]).toBe("openai/gpt-5"));
    expect(payload()["reasoning_provider"]).toBe("openrouter");
    // An endpoint nobody typed is absent rather than empty, so the provider's
    // own address is what the request goes to.
    expect(payload()["reasoning_base_url"]).toBeNull();
  });

  it("asks a self-hosted provider for its address instead of a key", async () => {
    render(<Harness />, { wrapper });
    await screen.findByText("Reading model");
    await userEvent.click(screen.getByLabelText("Provider"));
    await userEvent.click(await screen.findByRole("option", { name: /Ollama/ }));

    expect(await screen.findByLabelText("Server")).toBeInTheDocument();
    expect(screen.queryByLabelText("Key")).not.toBeInTheDocument();
  });
});

describe("when a model is complete enough to save", () => {
  const hostedEntry = HOSTED;
  const keylessEntry = KEYLESS;

  it("accepts a base with no model at all", () => {
    expect(reasoningIncomplete(NO_REASONING, undefined)).toBe(false);
  });

  it("wants a model id", () => {
    expect(reasoningIncomplete({ ...NO_REASONING, provider: "openrouter" }, hostedEntry)).toBe(
      true,
    );
  });

  it("wants a key for a provider that needs one", () => {
    expect(
      reasoningIncomplete(
        { provider: "openrouter", model: "openai/gpt-5", secretId: null, baseUrl: "" },
        hostedEntry,
      ),
    ).toBe(true);
  });

  it("wants an address for a provider that has none of its own", () => {
    expect(
      reasoningIncomplete(
        { provider: "ollama", model: "qwen3:32b", secretId: null, baseUrl: "" },
        keylessEntry,
      ),
    ).toBe(true);
  });

  it("is satisfied by a key, or by an address on a self-hosted provider", () => {
    expect(
      reasoningIncomplete(
        { provider: "openrouter", model: "openai/gpt-5", secretId: "secret-1", baseUrl: "" },
        hostedEntry,
      ),
    ).toBe(false);
    expect(
      reasoningIncomplete(
        {
          provider: "ollama",
          model: "qwen3:32b",
          secretId: null,
          baseUrl: "http://10.0.0.9:11434/v1",
        },
        keylessEntry,
      ),
    ).toBe(false);
  });
});
