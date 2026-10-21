import Dashboard from "./components/Dashboard";
import Header from "./components/Header";
import RequireAuth from "./components/RequireAuth";

export const dynamic = "force-dynamic";

export default function Home() {
  return (
    <RequireAuth>
      <div className="min-h-screen">
        <Header />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <Dashboard />
        </main>
      </div>
    </RequireAuth>
  );
}