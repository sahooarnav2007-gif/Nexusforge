"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { authApi, setTokens } from "@/lib/api";
import { ErrorNote, Spinner } from "../components/ui";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Already signed in? Straight to the dashboard.
    if (localStorage.getItem("nexusforge.access_token")) router.replace(next);
  }, [router, next]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const tokens = await authApi.login(email, password);
      setTokens(tokens);
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-600 font-mono text-lg font-bold text-white">
            {"</>"}
          </span>
          <h1 className="text-lg font-bold tracking-tight text-ink">
            NEXUS<span className="text-brand-600">FORGE</span>
          </h1>
          <p className="text-sm text-ink-muted">Sign in to your workspace</p>
        </div>

        <form onSubmit={submit} className="card space-y-4 p-5">
          <ErrorNote message={error} onDismiss={() => setError("")} />
          <div>
            <label className="label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              className="input"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              className="input"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy && <Spinner />}
            Sign in
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-ink-muted">
          No account?{" "}
          <Link href="/register" className="font-semibold text-brand-700 hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}