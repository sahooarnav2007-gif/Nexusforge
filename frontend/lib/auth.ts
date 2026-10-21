// Client-side auth helpers (token presence + redirect).
// The real guard is <RequireAuth> in app/components/RequireAuth.tsx.

import { useRouter } from "next/navigation";
import { clearTokens, isLoggedIn } from "./api";

export function useAuthRedirect() {
  const router = useRouter();
  return {
    /** Redirect to /login, remembering where the user came from. */
    login: (next?: string) => {
      const target = next ?? (typeof window !== "undefined" ? window.location.pathname : "/");
      const q = target !== "/" ? `?next=${encodeURIComponent(target)}` : "";
      router.replace(`/login${q}`);
    },
    /** Clear tokens and go to /login. */
    logout: () => {
      clearTokens();
      router.replace("/login");
    },
    hasToken: () => isLoggedIn(),
  };
}