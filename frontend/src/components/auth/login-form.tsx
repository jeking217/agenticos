"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { ArrowRight } from "lucide-react";

import { DirectoryLoginForm } from "@/components/auth/directory-login-form";
import { OAuthBlock } from "@/components/auth/oauth-buttons";
import { UnioneLoginForm, UnioneTicketSignIn } from "@/components/auth/unione-login-form";
import { returnToForAttempt } from "@/lib/oauth-return";
import { Button, Input, Label } from "@/components/ui";
import { useAuth } from "@/hooks";
import { ApiError } from "@/lib/api-client";
import { getErrorMessage, isTimeout } from "@/lib/api-error";
import { ROUTES } from "@/lib/constants";
import { registerHref } from "@/lib/invitation-links";
import { loadUnioneConfig, ticketFrom } from "@/lib/unione";
import type { UnioneConfig } from "@/types";
import { EMAIL_RE } from "@/lib/utils";

export function LoginForm() {
  const t = useTranslations("auth");
  const tErrors = useTranslations("errors");
  const search = useSearchParams();
  const { login } = useAuth();
  // Where "create an account" goes. An invitee with no account is bounced here by
  // `AuthGuard` with the invitation in `returnTo`, and a plain link to /register
  // would drop it - leaving an `invite_only` deployment refusing somebody who is
  // holding a valid invitation (#916).
  const signUpHref = registerHref(search.toString());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // A sign-in that failed away from this page - a Kerberos ticket the backend
  // refused, or a browser with none to offer - lands back here with its reason.
  const [error, setError] = useState(search.get("error") ?? "");
  const [isLoading, setIsLoading] = useState(false);
  const [emailTouched, setEmailTouched] = useState(false);
  const [directory, setDirectory] = useState(false);
  // `null` until the deployment has answered whether it bridges to Unione (#2101).
  // The local form is drawn until that answer arrives, because a login page that
  // shows no fields while it waits is a login page that looks broken - and the
  // swap, on a bridged deployment, lands within the same blink as the spinner the
  // page would otherwise have shown.
  const [unione, setUnione] = useState<UnioneConfig | null>(null);
  // Why the silent sign-in failed, if the platform sent this browser here and the
  // ticket was no longer good. Shown above the form that can still sign them in.
  const [unioneError, setUnioneError] = useState("");
  // Whether the local form is being shown on a bridged deployment, at the person's
  // request - the break-glass way in when the platform is down.
  const [localForm, setLocalForm] = useState(false);

  const ticket = ticketFrom(search.toString());

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const config = await loadUnioneConfig();
        if (!cancelled) setUnione(config);
      } catch {
        // A deployment that cannot answer is one that signs in locally: the form
        // below is the one that always works.
        if (!cancelled) {
          setUnione({
            enabled: false,
            captcha_enabled: false,
            local_login_visible: true,
            sm2_public_key: "",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const emailValid = !email || EMAIL_RE.test(email);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      // Read off the URL at submit time: useSearchParams would demand a
      // Suspense boundary around a form that renders statically.
      const returnTo = new URLSearchParams(window.location.search).get("returnTo");
      await login({ email, password }, returnTo);
      toast.success(t("loginSuccess"));
    } catch (err) {
      const message =
        err instanceof ApiError
          ? getErrorMessage(err, tErrors)
          : isTimeout(err)
            ? t("loginTimedOut")
            : t("loginFailedPleaseTry");
      setError(message);
      toast.error(message);
      setIsLoading(false);
    }
  };

  // The platform's ticket is spent on arrival, so this page is that one job while
  // it is in the URL - and once it fails, the form below is the fallback.
  if (ticket && !unioneError) {
    return (
      <div className="space-y-8">
        <UnioneTicketSignIn ticket={ticket} onFailed={setUnioneError} />
      </div>
    );
  }

  const bridged = unione?.enabled === true;
  const showUnione = bridged && !localForm;

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <span className="text-muted-foreground text-sm font-medium">{t("welcomeBack")}</span>
        <h1 className="text-foreground text-[2rem] leading-tight font-semibold tracking-tight text-balance [&_em]:not-italic">
          {t.rich("signInHeading", { em: (chunks) => <em>{chunks}</em> })}
        </h1>
        {bridged ? (
          // No "create an account": on a bridged deployment the accounts are the
          // platform's, and a registration form here would mint a second one.
          <p className="text-foreground/65 text-sm">{t("unioneSignInHint")}</p>
        ) : (
          <p className="text-foreground/65 text-sm">
            {t("noAccount")}{" "}
            <Link
              href={signUpHref}
              className="text-foreground hover:text-foreground/80 font-medium underline-offset-4 hover:underline"
            >
              {t("register")}
            </Link>
          </p>
        )}
      </div>

      {showUnione ? (
        <>
          <UnioneLoginForm
            captchaRequired={unione.captcha_enabled}
            sm2PublicKey={unione.sm2_public_key}
            initialError={unioneError}
          />
          {unione.local_login_visible && (
            <button
              type="button"
              onClick={() => setLocalForm(true)}
              className="text-foreground/65 hover:text-foreground text-sm font-medium underline-offset-4 hover:underline"
            >
              {t("unioneUseEmailInstead")}
            </button>
          )}
        </>
      ) : directory ? (
        <DirectoryLoginForm onBack={() => setDirectory(false)} />
      ) : (
        <>
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="email" className="text-foreground text-sm font-medium">
                {t("email")}
              </Label>
              <Input
                id="email"
                type="email"
                placeholder={t("emailPlaceholder")}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onBlur={() => setEmailTouched(true)}
                required
                disabled={isLoading}
                autoComplete="email"
                className={`h-11 rounded-lg text-[15px] ${emailTouched && email && !emailValid ? "border-destructive" : ""}`}
              />
              {emailTouched && email && !emailValid && (
                <p className="text-destructive text-xs">{t("emailRequired")}</p>
              )}
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password" className="text-foreground text-sm font-medium">
                  {t("password")}
                </Label>
                <Link
                  href={ROUTES.FORGOT_PASSWORD}
                  className="text-muted-foreground hover:text-foreground text-xs font-medium underline-offset-4 hover:underline"
                >
                  {t("forgotShort")}
                </Link>
              </div>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={isLoading}
                autoComplete="current-password"
                className="h-11 rounded-lg text-[15px]"
              />
            </div>

            {error && (
              <p className="border-destructive/35 bg-destructive/[0.07] text-foreground rounded-lg border px-3 py-2 text-sm">
                {error}
              </p>
            )}

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

          <OAuthBlock
            label={t("orSignInWith")}
            returnTo={returnToForAttempt(search)}
            onDirectorySignIn={() => setDirectory(true)}
          />

          {bridged && (
            <button
              type="button"
              onClick={() => setLocalForm(false)}
              className="text-foreground/65 hover:text-foreground text-sm font-medium underline-offset-4 hover:underline"
            >
              {t("unioneBackToPlatform")}
            </button>
          )}
        </>
      )}
    </div>
  );
}
