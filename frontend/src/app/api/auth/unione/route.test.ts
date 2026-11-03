/**
 * @vitest-environment node
 *
 * The BFF hops under `/api/auth/unione/*`, in the environment they run in (#2101).
 *
 * What matters here is the seam: the body reaches the backend as it arrived, a
 * refusal reaches the browser with the platform's sentence intact, and a session
 * is written where no script can read it.
 */
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET as configHop } from "./config/route";
import { POST as loginHop } from "./login/route";
import { POST as onceHop } from "./once/route";
import { BackendApiError, backendFetch } from "@/lib/server-api";

vi.mock("@/lib/server-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/server-api")>("@/lib/server-api");
  return { ...actual, backendFetch: vi.fn() };
});

function post(path: string, body: unknown): NextRequest {
  return new NextRequest(`http://localhost:3000/api/auth/unione/${path}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function cookie(response: Response, name: string): string | undefined {
  return response.headers.getSetCookie().find((entry) => entry.startsWith(`${name}=`));
}

beforeEach(() => vi.clearAllMocks());

describe("relaying a sign-in", () => {
  it("forwards the body untouched and stores the session in httpOnly cookies", async () => {
    vi.mocked(backendFetch)
      .mockResolvedValueOnce({ access_token: "at", refresh_token: "rt" })
      .mockResolvedValueOnce({ id: "u-1" });

    const response = await loginHop(post("login", { username: "zhangsan", password: "p" }));

    expect(backendFetch).toHaveBeenNthCalledWith(1, "/api/v1/auth/unione/login", {
      method: "POST",
      headers: {},
      body: JSON.stringify({ username: "zhangsan", password: "p" }),
    });
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ user: { id: "u-1" }, access_token: "at" });
    expect(cookie(response, "agenticos_access_token")).toMatch(
      /^agenticos_access_token=at;.*HttpOnly/,
    );
    expect(cookie(response, "agenticos_refresh_token")).toMatch(
      /^agenticos_refresh_token=rt;.*HttpOnly/,
    );
  });

  it("returns the platform's refusal with the platform's own words", async () => {
    vi.mocked(backendFetch).mockRejectedValue(
      new BackendApiError(401, "Unauthorized", {
        error: { code: "AUTHENTICATION_ERROR", message: "密码错误，您还可以尝试4次" },
      }),
    );

    const response = await loginHop(post("login", { username: "zhangsan", password: "wrong" }));

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      error: { code: "AUTHENTICATION_ERROR", message: "密码错误，您还可以尝试4次" },
    });
    expect(cookie(response, "agenticos_access_token")).toBeUndefined();
  });

  it("spends a ticket the same way", async () => {
    vi.mocked(backendFetch)
      .mockResolvedValueOnce({ access_token: "at", refresh_token: "rt" })
      .mockResolvedValueOnce({ id: "u-1" });

    const response = await onceHop(post("once", { accessToken: "tk-abc" }));

    expect(backendFetch).toHaveBeenNthCalledWith(1, "/api/v1/auth/unione/once", {
      method: "POST",
      headers: {},
      body: JSON.stringify({ accessToken: "tk-abc" }),
    });
    expect(cookie(response, "agenticos_access_token")).toBeDefined();
  });
});

describe("the hops that carry no session", () => {
  it("answers the login page's questions without caching them", async () => {
    vi.mocked(backendFetch).mockResolvedValue({
      enabled: true,
      captcha_enabled: true,
      local_login_visible: false,
      // Passed through untouched, as every field of this answer is: this hop has no
      // opinion about what the login page needs.
      sm2_public_key: "04aa",
    });

    const response = await configHop(
      new NextRequest("http://localhost:3000/api/auth/unione/config"),
    );

    expect(backendFetch).toHaveBeenCalledWith("/api/v1/auth/unione/config", {
      method: "GET",
      headers: {},
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({
      enabled: true,
      captcha_enabled: true,
      local_login_visible: false,
      sm2_public_key: "04aa",
    });
  });
});
