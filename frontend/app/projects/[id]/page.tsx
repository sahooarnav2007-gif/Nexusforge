import ProjectDetail from "../../components/ProjectDetail";
import Header from "../../components/Header";
import RequireAuth from "../../components/RequireAuth";

export const dynamic = "force-dynamic";

export default function ProjectPage() {
  return (
    <RequireAuth>
      <div className="min-h-screen">
        <Header />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <ProjectDetail />
        </main>
      </div>
    </RequireAuth>
  );
}