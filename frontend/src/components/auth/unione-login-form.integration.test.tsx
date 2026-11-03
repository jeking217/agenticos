import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./login-form";
import { PublicConfigProvider } from "@/components/public-config/public-config-provider";
import { apiClient, ApiError } from "@/lib/api-client";
import { DEFAULT_PUBLIC_CONFIG } from "@/lib/public-config";
import type { UnioneConfig } from "@/types";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api-client")>("@/lib/api-client");
  return { ...actual, apiClient: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const query = vi.hoisted(() => ({ current: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => query.current,
  usePathname: () => "/login",
  useParams: () => ({}),
  redirect: vi.fn(),
  permanentRedirect: vi.fn(),
}));

/** The platform's public key, as `/auth/unione/config` publishes it. */
const PUBLIC_KEY =
  "04a684d7140aebfc83515a2312dac7ab836f3adc484627971b6936ad7d5575048c" +
  "aecf9c7eb07ed4dcee9d04cb3a7b1c514477b646b1ebe8245de215733baa5133";

const BRIDGED: UnioneConfig = {
  enabled: true,
  captcha_enabled: false,
  local_login_visible: false,
  sm2_public_key: PUBLIC_KEY,
};

const SIGNED_IN = {
  user: { id: "u-1", email: "zhangsan@unione.local", is_active: true, created_at: "" },
  access_token: "t",
};

/** The deployment's answers: the bridge's config, then a captcha if one is asked for. */
function answerConfig(config: UnioneConfig) {
  vi.mocked(apiClient.get).mockImplementation(async (endpoint: string) => {
    if (endpoint === "/auth/unione/config") return config;
    if (endpoint.startsWith("/auth/unione/captcha")) {
      return { captcha_id: "cid-1", image: "data:image/jpeg;base64,AAAA" };
    }
    throw new ApiError(401, "Not authenticated");
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PublicConfigProvider config={DEFAULT_PUBLIC_CONFIG}>
        <LoginForm />
      </PublicConfigProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/login");
  query.current = new URLSearchParams();
  // Off by default, so a test that is not about the bridge sees the form it always did.
  answerConfig({
    enabled: false,
    captcha_enabled: false,
    local_login_visible: true,
    sm2_public_key: "",
  });
  vi.mocked(apiClient.post).mockResolvedValue(SIGNED_IN);
});

describe("a deployment that bridges to Unione", () => {
  it("offers the platform's form and retires the local one", async () => {
    answerConfig(BRIDGED);
    mount();

    expect(await screen.findByRole("tab", { name: "Account" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Phone" })).toBeInTheDocument();
    // No local form: one account, one way in.
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    // And no "create an account", which would mint a second account beside it.
    expect(screen.queryByRole("link", { name: "Register" })).not.toBeInTheDocument();
  });

  it("leaves the local form reachable when the deployment asked for a break-glass way in", async () => {
    answerConfig({ ...BRIDGED, local_login_visible: true });
    mount();

    await userEvent.click(await screen.findByRole("button", { name: "Sign in with email instead" }));

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to Unione sign-in" })).toBeInTheDocument();
  });

  it("keeps the local form when no bridge is configured", async () => {
    mount();

    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Account" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Register" })).toBeInTheDocument();
  });

  it("asks for a captcha only when the platform does", async () => {
    answerConfig(BRIDGED);
    mount();

    await screen.findByRole("tab", { name: "Account" });
    expect(screen.queryByLabelText("Verification code")).not.toBeInTheDocument();
  });

  it("draws the captcha the platform filed the answer under", async () => {
    answerConfig({ ...BRIDGED, captcha_enabled: true });
    mount();

    expect(await screen.findByLabelText("Verification code")).toBeInTheDocument();
    expect(await screen.findByAltText("Verification code")).toHaveAttribute(
      "src",
      expect.stringContaining("data:image/jpeg;base64,"),
    );
  });

  it("encrypts the password before it leaves the browser", async () => {
    answerConfig({ ...BRIDGED, captcha_enabled: true });
    mount();

    await userEvent.type(await screen.findByLabelText("Account"), "zhangsan");
    await userEvent.type(screen.getByLabelText("Password"), "Uni@12345");
    await userEvent.type(screen.getByLabelText("Verification code"), "8J2K");
    await userEvent.click(screen.getByRole("button", { name: "Login" }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        "/auth/unione/login",
        expect.anything(),
        { signal: expect.any(AbortSignal) },
      ),
    );
    const body = vi.mocked(apiClient.post).mock.calls[0]![1] as Record<string, string>;
    expect(body).toMatchObject({ username: "zhangsan", captcha: "8J2K", captcha_id: "cid-1" });
    // `04` then C1, C3 and C2 - what the platform decrypts - and not the password.
    expect(body.password).toMatch(/^04[0-9a-f]{194,}$/);
    expect(JSON.stringify(body)).not.toContain("Uni@12345");
  });

  it("refuses to sign in rather than send a password in the clear", async () => {
    // A deployment that publishes no key cannot be encrypted to. The form says so
    // and stops: it never posts the password it could not wrap.
    answerConfig({ ...BRIDGED, sm2_public_key: "" });
    mount();

    await userEvent.type(await screen.findByLabelText("Account"), "zhangsan");
    await userEvent.type(screen.getByLabelText("Password"), "Uni@12345");
    await userEvent.click(screen.getByRole("button", { name: "Login" }));

    expect(await screen.findByText(/Could not encrypt the password/)).toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("shows the platform's own refusal in the form", async () => {
    answerConfig(BRIDGED);
    vi.mocked(apiClient.post).mockRejectedValue(
      new ApiError(401, "密码错误，您还可以尝试4次", undefined),
    );
    mount();

    await userEvent.type(await screen.findByLabelText("Account"), "zhangsan");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Login" }));

    expect(await screen.findByText("密码错误，您还可以尝试4次")).toBeInTheDocument();
  });

  it("texts a code to the number in the phone tab and signs in with it", async () => {
    answerConfig(BRIDGED);
    mount();

    await userEvent.click(await screen.findByRole("tab", { name: "Phone" }));
    await userEvent.type(screen.getByLabelText("Phone number"), "13800138000");
    await userEvent.click(screen.getByRole("button", { name: "Send code" }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith("/auth/unione/captcha/sms", {
        phone: "13800138000",
      }),
    );

    await userEvent.type(screen.getByLabelText("SMS code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Login" }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        "/auth/unione/login-sms",
        { phone: "13800138000", captcha: "123456" },
        { signal: expect.any(AbortSignal) },
      ),
    );
  });

  it("refuses to text a code to something that is not a mobile number", async () => {
    answerConfig(BRIDGED);
    mount();

    await userEvent.click(await screen.findByRole("tab", { name: "Phone" }));
    const phone = screen.getByLabelText("Phone number");
    await userEvent.type(phone, "12345");
    // The message is about the field, so it arrives when the field is left -
    // and the button is disabled until there is a number to send to.
    await userEvent.tab();

    expect(await screen.findByText("Enter a valid mobile number")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send code" })).toBeDisabled();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("drops a refusal when the other tab is opened", async () => {
    answerConfig(BRIDGED);
    vi.mocked(apiClient.post).mockRejectedValue(new ApiError(401, "密码错误", undefined));
    mount();

    await userEvent.type(await screen.findByLabelText("Account"), "zhangsan");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Login" }));
    expect(await screen.findByText("密码错误")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Phone" }));

    expect(screen.queryByText("密码错误")).not.toBeInTheDocument();
  });
});

describe("arriving with a one-time ticket", () => {
  it("spends the ticket and takes it out of the address bar", async () => {
    answerConfig(BRIDGED);
    query.current = new URLSearchParams("accessToken=tk-abc123");
    window.history.replaceState(null, "", "/login?accessToken=tk-abc123");
    mount();

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        "/auth/unione/once",
        { accessToken: "tk-abc123" },
        { signal: expect.any(AbortSignal) },
      ),
    );
    // While it is in the URL it is a credential in the place credentials leak.
    expect(window.location.search).toBe("");
  });

  it("falls back to the form when the ticket is already spent", async () => {
    answerConfig(BRIDGED);
    query.current = new URLSearchParams("accessToken=tk-spent");
    window.history.replaceState(null, "", "/login?accessToken=tk-spent");
    vi.mocked(apiClient.post).mockRejectedValue(
      new ApiError(401, "票据无效或已使用", undefined),
    );
    mount();

    expect(await screen.findByText("票据无效或已使用")).toBeInTheDocument();
    expect(await screen.findByLabelText("Account")).toBeInTheDocument();
  });
});
