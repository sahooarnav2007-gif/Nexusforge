"""Engineering report assembly (Markdown) + minimal PDF export.

The report is assembled from REAL persisted run data: steps, changes, test
runs, security & review findings, budgets. Nothing is fabricated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from core.config import get_settings
from core.models import (
    CodeChange,
    ReviewFinding,
    SecurityFinding,
    TaskRun,
    TestRun,
)


async def build_markdown_report(ctx) -> str:
    db = ctx.db
    run = ctx.run
    spec = ctx.get_data("spec", {}) or {}
    plan = ctx.get_data("plan", {}) or {}
    security = ctx.get_data("security", {}) or {}
    review = ctx.get_data("review", {}) or {}
    last_test = ctx.get_data("last_test", {}) or {}

    changes = (
        (await db.execute(select(CodeChange).where(CodeChange.run_id == run.id)
                          .order_by(CodeChange.path))).scalars().all()
    )
    test_runs = (
        (await db.execute(select(TestRun).where(TestRun.run_id == run.id)
                          .order_by(TestRun.iteration))).scalars().all()
    )
    sec = (
        (await db.execute(select(SecurityFinding).where(SecurityFinding.run_id == run.id)
                          .order_by(SecurityFinding.severity))).scalars().all()
    )
    rev = (
        (await db.execute(select(ReviewFinding).where(ReviewFinding.run_id == run.id))).scalars().all()
    )

    L: list[str] = []
    A = L.append
    A(f"# NexusForge Engineering Report")
    A("")
    A(f"- **Project:** {ctx.project.name}")
    A(f"- **Repository:** {ctx.repo.name} (head `{(ctx.repo.head_commit or '')[:12]}`)")
    A(f"- **Task:** {ctx.task.title}")
    A(f"- **Run:** `{run.id[:12]}`")
    A(f"- **Final state:** {run.state}")
    A(f"- **Generated:** {datetime.now(timezone.utc).isoformat()}")
    A(f"- **Iterations:** {run.iteration} | **LLM calls:** {run.llm_calls} | "
      f"**Tokens:** {run.tokens_used} | **Wall time:** "
      f"{(run.finished_at - run.started_at).total_seconds():.1f}s"
      if run.started_at and run.finished_at else
      f"- **Iterations:** {run.iteration} | **LLM calls:** {run.llm_calls} | "
      f"**Tokens:** {run.tokens_used}")
    A("")

    A("## 1. Requirement")
    A("")
    A(ctx.task.requirement)
    A("")
    if spec:
        A("### Parsed targets")
        A("")
        for t in spec.get("targets", []):
            if isinstance(t, dict):
                kind = t.get("kind", "target")
                desc = t.get("description") or t.get("name") or t.get("path") or ""
            else:
                kind = "target"
                desc = str(t)
            A(f"- `{kind}`: {desc}")
        A("")
        A("### Acceptance criteria")
        A("")
        for a in spec.get("acceptance", []):
            A(f"- {a}")
        A("")

    A("## 2. Repository analysis")
    A("")
    rs = ctx.get_data("repo_summary", {}) or {}
    A(f"- Files indexed: {rs.get('file_count', ctx.repo.file_count)}")
    A(f"- Languages: {', '.join(f'{k} ({v})' for k, v in (rs.get('languages') or {}).items()) or 'n/a'}")
    A(f"- Test files: {len(rs.get('test_files', []))}")
    A("")

    A("## 3. Implementation plan")
    A("")
    for st in plan.get("steps", []):
        A(f"{st.get('id')}. {st.get('description')} — _{st.get('acceptance', '')}_")
    A("")

    A("## 4. Files changed")
    A("")
    if changes:
        A("| File | Action | Reasoning |")
        A("|------|--------|-----------|")
        for c in changes:
            A(f"| `{c.path}` | {c.action} | {c.reasoning[:120]} |")
    else:
        A("_No files were changed._")
    A("")

    A("## 5. Tests")
    A("")
    if test_runs:
        A("| Iteration | Command | Passed | Failed | Errors | Duration |")
        A("|-----------|---------|--------|--------|--------|----------|")
        for t in test_runs:
            A(f"| {t.iteration} | `{t.command}` | {t.passed} | {t.failed} | {t.errors} | "
              f"{t.duration_ms / 1000:.1f}s |")
        A("")
        last = test_runs[-1]
        if last.failed_tests:
            A("### Last failing tests (if any)")
            A("")
            for ft in last.failed_tests[:10]:
                A(f"- `{ft}`")
            A("")
    else:
        A("_No test runs recorded._")
    A("")

    A("## 6. Debugging history")
    A("")
    if run.iteration > 0:
        for s in run.steps:
            if s.agent in ("failure_analyzer", "repair_agent"):
                A(f"- **{s.agent}** (iter {s.seq}): {s.summary}")
                if s.error:
                    A(f"  - error: {s.error[:200]}")
    else:
        A("_No repair iterations were needed._")
    A("")

    A("## 7. Security findings")
    A("")
    if sec:
        for f in sec:
            loc = f"{f.path}:{f.line}" if f.line else f.path
            A(f"- **[{f.severity.upper()}]** `{f.rule_id}` — {loc}: {f.message} _({f.source})_")
    else:
        A("_No security findings._")
    A("")

    A("## 8. Code review findings")
    A("")
    if rev:
        for f in rev:
            A(f"- **[{f.severity.upper()}]** {f.category} — {f.title}: {f.detail[:160]}")
        A("")
        A(f"Verdict: **{review.get('verdict', 'n/a')}** — {review.get('summary', '')}")
    else:
        A("_No review findings._")
    A("")

    A("## 9. Remaining risks & notes")
    A("")
    risks = []
    if run.state == "FAILED":
        risks.append(f"Run failed: {run.error}")
    if run.state == "SECURITY_BLOCKED":
        risks.append("Run was blocked by security findings; changes may be uncommitted")
    if not test_runs or (test_runs and test_runs[-1].failed):
        risks.append("Test suite is not fully green")
    if not risks:
        risks.append("None identified by the automated pipeline. Manual review of the "
                     "diff is still recommended before merging.")
    for r in risks:
        A(f"- {r}")
    A("")
    A("---")
    A(f"_Generated by NexusForge run `{run.id[:12]}` — all data above is taken from "
      f"persisted run state._")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Minimal PDF writer (dependency-free, text-only)
# ---------------------------------------------------------------------------


def _pdf_escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def markdown_to_pdf_lines(md: str) -> list[str]:
    """Very small markdown -> PDF text-line converter (headings, bullets,
    tables flattened, code kept verbatim)."""
    lines: list[str] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", " ", ":"} for c in cells):
                continue
            lines.append("  ".join(c for c in cells if c))
            continue
        if line.startswith("# "):
            lines.append(line[2:].upper())
            continue
        if line.startswith("## "):
            lines.append(line[3:])
            continue
        if line.startswith("### "):
            lines.append("  " + line[4:])
            continue
        if line.startswith("- "):
            lines.append("  • " + line[2:])
            continue
        if line.startswith("_") and line.endswith("_") and len(line) > 2:
            lines.append(line[1:-1])
            continue
        if line.startswith("---"):
            lines.append("")
            continue
        # strip inline markdown
        text = line.replace("**", "").replace("`", "")
        lines.append(text)
    return lines


def build_pdf(text_lines: list[str], title: str = "NexusForge Report") -> bytes:
    """Build a valid single/multi-page PDF from text lines (Helvetica)."""
    page_w, page_h = 612, 792  # US Letter
    margin = 54
    font_size = 10
    leading = 13
    max_chars = 92

    def wrap(s: str) -> list[str]:
        out = []
        for para in s.split("\n"):
            while len(para) > max_chars:
                cut = para.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                out.append(para[:cut])
                para = para[cut:].lstrip()
            out.append(para)
        return out

    all_lines: list[str] = []
    for ln in text_lines:
        all_lines.extend(wrap(ln) or [""])

    # paginate
    lines_per_page = int((page_h - 2 * margin) / leading)
    pages: list[list[str]] = []
    for i in range(0, len(all_lines), lines_per_page):
        pages.append(all_lines[i:i + lines_per_page])
    if not pages:
        pages = [[""]]

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    # 1: catalog, 2: pages, 3: font
    catalog_n = 1
    pages_n = 2
    font_n = 3
    # content streams + page objects
    page_obj_ns = []
    content_obj_ns = []
    for _ in pages:
        page_obj_ns.append(len(objects) + 1 + len(pages) * 2)  # placeholder
    # simpler: build in order
    objects = []
    add(b"")  # 1 catalog
    add(b"")  # 2 pages
    add(b"")  # 3 font
    page_ns = []
    for p in pages:
        # content stream
        stream = _content_stream(p, margin, page_h - margin, font_size, leading)
        objects.append(stream)
        content_ns = len(objects)
        # page object
        objects.append(b"")  # placeholder for page
        page_ns.append((len(objects), content_ns))

    kids = " ".join(f"{n} 0 R" for n, _ in page_ns)
    objects[0] = (b"<< /Type /Catalog /Pages 2 0 R >>")
    objects[1] = (f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ns)} >>").encode()
    objects[2] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                  b"/Encoding /WinAnsiEncoding >>")
    for n, content_ns in page_ns:
        objects[n - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_ns} 0 R >>"
        ).encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)


def _content_stream(lines: list[str], x: int, y_top: int,
                    font_size: int, leading: int) -> bytes:
    parts = [b"BT", b"/F1", str(font_size).encode(), b" Tf",
             str(leading).encode(), b" TL"]
    first = True
    for ln in lines:
        safe = _pdf_escape(ln)[:120]
        if first:
            parts.append(f"1 0 0 1 {x} {y_top} Tm".encode())
            first = False
        parts.append(f"({safe}) Tj T*".encode())
    parts.append(b"ET")
    stream = b"\n".join(parts)
    return (f"<< /Length {len(stream)} >>\nstream\n").encode() + stream + b"\nendstream"


async def build_report_pdf(run_id: str, markdown: str) -> str:
    lines = markdown_to_pdf_lines(markdown)
    pdf = build_pdf(lines)
    s = get_settings()
    out_dir = Path(s.workspace_root).parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report-{run_id[:12]}.pdf"
    path.write_bytes(pdf)
    return str(path)