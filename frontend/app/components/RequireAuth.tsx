"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { getToken } from "@/lib/api";
import { Spinner } from "./ui";

/**
 * Client-side auth guard: renders children only when a token exists,
 * otherwise redirects to /login (preserving the current path as ?next=).
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      const next = window.location.pathname + window.location.search;
      const q = next && next !== "/" ? `?next=${encodeURIComponent(next)}` : "";
      router.replace(`/login${q}`);
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Spinner className="h-6 w-6 text-brand-600" />
      </div>
    );
  }

  return <>{children}</>;
}