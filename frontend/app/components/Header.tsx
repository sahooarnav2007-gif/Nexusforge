"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { authApi, clearTokens } from "@/lib/api";
import type { UserOut } from "@/lib/types";

export default function Header() {
  const router = useRouter();
  const [user, setUser] = useState<UserOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    authApi
      .me()
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .catch(() => {
        // 401 that the client couldn't recover from: force a login round-trip.
        if (!cancelled) {
          clearTokens();
          router.replace("/login");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  const logout = async () => {
    try {
      await authApi.logout();
    } catch {
      // best effort; clear locally regardless
    }
    clearTokens();
    router.replace("/login");
  };

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 font-mono text-sm font-bold text-white">
            {"</>"}
          </span>
          <span className="text-[15px] font-bold tracking-tight text-ink">
            NEXUS<span className="text-brand-600">FORGE</span>
          </span>
          <span className="hidden rounded-full bg-surface-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-muted sm:inline">
            AI engineering
          </span>
        </Link>

        <div className="flex items-center gap-3">
          {user && (
            <span className="hidden text-xs text-ink-muted sm:inline">
              {user.username}
              <span className="mx-1.5 text-slate-300">·</span>
              {user.email}
            </span>
          )}
          <button type="button" onClick={logout} className="btn-secondary !px-3 !py-1.5 text-xs">
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}