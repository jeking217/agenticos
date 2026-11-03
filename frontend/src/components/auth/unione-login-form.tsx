"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { ArrowRight, RefreshCw } from "lucide-react";

import { Button, Input, Label } from "@/components/ui";
import { useAuth } from "@/hooks";
import { ApiError } from "@/lib/api-client";
import { getErrorMessage, isTimeout } from "@/lib/api-error";
import { loadUnioneCaptcha, sendUnioneSmsCaptcha } from "@/lib/unione";
import { encryptSm2 } from "@/lib/sm2";
import { cn } from "@/lib/utils";

const LABEL = "text-foreground text-sm font-medium";
const INPUT = "h-11 rounded-lg text-[15px]";

/** Mainland mobile numbers, as the platform's own form checks them. */
const PHONE_RE = /^1[3-9]\d{9}$/;

type Tab = "account" | "phone";

/**
 * Signing in with a Unione platform account (#2101).
 *
 * Two tabs, as the platform's own login page has them: the account and password,
 * optionally with the graphical captcha the platform may insist on, and the phone
 * it can text a code to. Neither credential is checked here - both are forwarded
 * to the platform, which is the only party that knows whether they are right - and
 * a refusal is shown in the platform's own words, because it is the only party
 * that knows if it was the password, the code, or a lockout.
 *
 * The password is SM2-encrypted here, against the key `/auth/unione/config`
 * publishes, before it goes anywhere: the platform only accepts a ciphertext, and
 * making one in this browser is what keeps the plaintext out of our own logs, our
 * own backend and every hop in between. There is no unencrypted fallback - a form
 * that could send either would be a form whose traffic is sometimes readable.
 *
 * The captcha is fetched through this deployment rather than linked from the
 * platform: the answer is filed against an id the platform returns in a header,
 * and this browser never sees that header. It is spent when the platform checks
 * it, so a failed attempt draws a new one, exactly as the platform's form does.
 */
export function UnioneLoginForm({
  captchaRequired,
  sm2PublicKey,
  initialError = "",
}: {
  captchaRequired: boolean;
  sm2PublicKey: string;
  initialError?: string;
}) {
  const t = useTranslations("auth");
  const tErrors = useTranslations("errors");
  const { loginWithUnione, loginWithUnioneSms } = useAuth();
  const [tab, setTab] = useState<Tab>("account");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneTouched, setPhoneTouched] = useState(false);
  const [smsCode, setSmsCode] = useState("");
  const [captcha, setCaptcha] = useState<{ id: string; image: string } | null>(null);
  const [captchaAnswer, setCaptchaAnswer] = useState("");
  const [error, setError] = useState(initialError);
  const [isLoading, setIsLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  const phoneValid = PHONE_RE.test(phone);

  const refreshCaptcha = useCallback(async () => {
    try {
      const next = await loadUnioneCaptcha();
      setCaptcha({ id: next.captcha_id, image: next.image });
      setCaptchaAnswer("");
    } catch {
      // A captcha that will not load is not a reason to hide the form: the
      // platform's own refusal will say what is missing.
      setCaptcha(null);
    }
  }, []);

  useEffect(() => {
    if (!captchaRequired) return;
    // Fetched through a promise rather than by calling `refreshCaptcha`: the
    // captcha arrives after a round trip, and a rule that refuses state written
    // synchronously from an effect cannot see past the call.
    let cancelled = false;
    loadUnioneCaptcha()
      .then((next) => {
        if (cancelled) return;
        setCaptcha({ id: next.captcha_id, image: next.image });
      })
      .catch(() => {
        if (!cancelled) setCaptcha(null);
      });
    return () => {
      cancelled = true;
    };
  }, [captchaRequired]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((remaining) => remaining - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const showError = (err: unknown) => {
    const message =
      err instanceof ApiError
        ? getErrorMessage(err, tErrors)
        : isTimeout(err)
          ? t("loginTimedOut")
          : t("loginFailedPleaseTry");
    setError(message);
    toast.error(message);
  };

  const returnTo = () => new URLSearchParams(window.location.search).get("returnTo");

  const handleAccountSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    // Encrypted before anything else happens: the plaintext is never written to the
    // request, the logs, or this deployment's backend.
    let ciphertext: string;
    try {
      ciphertext = encryptSm2(password, sm2PublicKey);
    } catch {
      const message = t("unioneEncryptFailed");
      setError(message);
      toast.error(message);
      setIsLoading(false);
      return;
    }

    try {
      await loginWithUnione(
        {
          username,
          password: ciphertext,
          ...(captchaRequired && captcha ? { captcha: captchaAnswer, captcha_id: captcha.id } : {}),
        },
        returnTo(),
      );
      toast.success(t("loginSuccess"));
    } catch (err) {
      showError(err);
      setIsLoading(false);
      // The platform spends a captcha answer when it checks one, so the next
      // attempt has to be against a new image - as its own form does.
      if (captchaRequired) void refreshCaptcha();
    }
  };

  const handleSendCode = async () => {
    if (!phoneValid) {
      setPhoneTouched(true);
      return;
    }
    setIsSending(true);
    setError("");
    try {
      await sendUnioneSmsCaptcha(phone);
      setCooldown(60);
      toast.success(t("unioneCodeSent"));
    } catch (err) {
      showError(err);
    } finally {
      setIsSending(false);
    }
  };

  const handlePhoneSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phoneValid) {
      setPhoneTouched(true);
      return;
    }
    setIsLoading(true);
    setError("");
    try {
      await loginWithUnioneSms({ phone, captcha: smsCode }, returnTo());
      toast.success(t("loginSuccess"));
    } catch (err) {
      showError(err);
      setIsLoading(false);
    }
  };

  const switchTab = (next: Tab) => {
    setTab(next);
    // A refusal belongs to the credential that was tried, not to the other tab.
    setError("");
  };

  return (
    <div className="space-y-5">
      <div role="tablist" className="border-border flex gap-6 border-b">
        {(["account", "phone"] as const).map((value) => {
          const active = tab === value;
          return (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => switchTab(value)}
              className={cn(
                "-mb-px border-b-2 pb-2 text-sm font-medium transition-colors",
                active
                  ? "border-foreground text-foreground"
                  : "text-foreground/60 hover:text-foreground border-transparent",
              )}
            >
              {value === "account" ? t("unioneAccountTab") : t("unionePhoneTab")}
            </button>
          );
        })}
      </div>

      {tab === "account" ? (
        <form onSubmit={handleAccountSubmit} className="space-y-5">
          <div className="space-y-1.5">
            <Label htmlFor="unione-username" className={LABEL}>
              {t("unioneUsername")}
            </Label>
            <Input
              id="unione-username"
              type="text"
              placeholder={t("unioneUsernamePlaceholder")}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={isLoading}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              className={INPUT}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="unione-password" className={LABEL}>
              {t("password")}
            </Label>
            <Input
              id="unione-password"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={isLoading}
              autoComplete="current-password"
              className={INPUT}
            />
          </div>

          {captchaRequired && (
            <div className="space-y-1.5">
              <Label htmlFor="unione-captcha" className={LABEL}>
                {t("unioneCaptcha")}
              </Label>
              <div className="flex items-center gap-3">
                <Input
                  id="unione-captcha"
                  type="text"
                  inputMode="text"
                  placeholder={t("unioneCaptchaPlaceholder")}
                  value={captchaAnswer}
                  onChange={(e) => setCaptchaAnswer(e.target.value)}
                  required
                  disabled={isLoading}
                  autoComplete="off"
                  className={INPUT}
                />
                <button
                  type="button"
                  onClick={() => void refreshCaptcha()}
                  title={t("unioneRefreshCaptcha")}
                  aria-label={t("unioneRefreshCaptcha")}
                  // The platform draws 143x50; at the field's height that is ~126 wide.
                  // `object-cover` cropped the right-hand characters off, which is the
                  // half of a captcha nobody can guess.
                  className="border-border hover:border-foreground/40 flex h-11 w-[126px] shrink-0 items-center justify-center overflow-hidden rounded-lg border"
                >
                  {captcha ? (
                    // eslint-disable-next-line @next/next/no-img-element -- a data: URL, which next/image cannot optimize
                    <img
                      src={captcha.image}
                      alt={t("unioneCaptcha")}
                      className="h-full w-full object-contain"
                    />
                  ) : (
                    <RefreshCw className="text-foreground/50 h-4 w-4" aria-hidden />
                  )}
                </button>
              </div>
            </div>
          )}

          {error && <ErrorLine message={error} />}

          <Button type="submit" disabled={isLoading} className="h-11 w-full text-[15px]">
            {isLoading ? (
              t("loggingIn")
            ) : (
              <>
                {t("login")}
                <ArrowRight className="ml-2 h-4 w-4" />
              </>
            )}
          </Button>
        </form>
      ) : (
        <form onSubmit={handlePhoneSubmit} className="space-y-5">
          <div className="space-y-1.5">
            <Label htmlFor="unione-phone" className={LABEL}>
              {t("unionePhone")}
            </Label>
            {/* The code button sits inside the field, as it does on the
                platform's own page: it is about this number, and a separate
                button beside it reads as a second submit. */}
            <div className="relative">
              <Input
                id="unione-phone"
                type="tel"
                inputMode="numeric"
                maxLength={11}
                placeholder={t("unionePhonePlaceholder")}
                value={phone}
                onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 11))}
                onBlur={() => setPhoneTouched(true)}
                aria-invalid={phoneTouched && phone !== "" && !phoneValid}
                required
                disabled={isLoading}
                autoComplete="tel"
                className={cn(INPUT, "pr-32 tabular-nums")}
              />
              <button
                type="button"
                onClick={() => void handleSendCode()}
                disabled={isSending || cooldown > 0 || !phoneValid}
                className="text-foreground/70 hover:text-foreground absolute top-1/2 right-1 -translate-y-1/2 rounded-md px-3 py-1.5 text-[13px] font-medium tabular-nums transition-colors disabled:opacity-50"
              >
                {cooldown > 0
                  ? t("unioneResendIn", { seconds: cooldown })
                  : isSending
                    ? t("unioneSending")
                    : t("unioneSendCode")}
              </button>
            </div>
            {phoneTouched && phone !== "" && !phoneValid && (
              <p className="text-destructive text-xs">{t("unionePhoneInvalid")}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="unione-sms-code" className={LABEL}>
              {t("unioneSmsCode")}
            </Label>
            <Input
              id="unione-sms-code"
              type="text"
              inputMode="numeric"
              maxLength={8}
              placeholder={t("unioneSmsCodePlaceholder")}
              value={smsCode}
              onChange={(e) => setSmsCode(e.target.value.replace(/\D/g, ""))}
              required
              disabled={isLoading}
              autoComplete="one-time-code"
              className={cn(INPUT, "tracking-[0.2em]")}
            />
          </div>

          {error && <ErrorLine message={error} />}

          <Button type="submit" disabled={isLoading} className="h-11 w-full text-[15px]">
            {isLoading ? (
              t("loggingIn")
            ) : (
              <>
                {t("login")}
                <ArrowRight className="ml-2 h-4 w-4" />
              </>
            )}
          </Button>
        </form>
      )}
    </div>
  );
}

function ErrorLine({ message }: { message: string }) {
  return (
    <p className="border-destructive/35 bg-destructive/[0.07] text-foreground rounded-lg border px-3 py-2 text-sm">
      {message}
    </p>
  );
}

/**
 * The silent path: the platform's portal sent this browser here with a one-time
 * ticket, so the page's only job is to spend it.
 *
 * The ticket is taken off the URL before the request goes out, and the guard is a
 * ref rather than state because React invokes effects twice in development: a
 * second attempt would be a second attempt at a ticket the platform has already
 * spent, and would report a failure over a sign-in that worked.
 *
 * A failure is handed back to the page rather than shown here, so the form it
 * falls back to is the one that can actually sign the person in.
 */
export function UnioneTicketSignIn({
  ticket,
  onFailed,
}: {
  ticket: string;
  onFailed: (message: string) => void;
}) {
  const t = useTranslations("auth");
  const tErrors = useTranslations("errors");
  const { loginWithUnioneTicket } = useAuth();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    // Out of the address bar first: while it is there it is a credential in a
    // place that gets screenshotted, copied and kept in history.
    const params = new URLSearchParams(window.location.search);
    const returnTo = params.get("returnTo");
    const cleaned = returnTo ? `?returnTo=${encodeURIComponent(returnTo)}` : "";
    window.history.replaceState(null, "", `${window.location.pathname}${cleaned}`);

    void (async () => {
      try {
        await loginWithUnioneTicket(ticket, returnTo);
      } catch (err) {
        onFailed(
          err instanceof ApiError ? getErrorMessage(err, tErrors) : t("unioneOnceFailed"),
        );
      }
    })();
  }, [ticket, loginWithUnioneTicket, onFailed, t, tErrors]);

  return <p className="text-foreground text-sm font-medium">{t("unioneOnceSigningIn")}</p>;
}
