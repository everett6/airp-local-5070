"""
Renders a `ResearchReport` from a completed `DebateRunState` + verification
report + confidence breakdown. Rendering is template-driven (Jinja2 markdown
template) and is the LAST step — it never invents content; every section
either pulls from `state.findings` (already-claimed, already-verified) or is
explicitly labeled as absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.confidence.engine import ConfidenceBreakdown
from app.debate.orchestrator import DebateRunState
from app.evidence.verifier import VerificationReport

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class ReportRenderContext:
    run_id: str
    ticker: str
    state: DebateRunState
    verification: VerificationReport
    confidence: ConfidenceBreakdown


def render_report(ctx: ReportRenderContext) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(disabled_extensions=("md",)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("report_template.md.j2")
    return template.render(
        run_id=ctx.run_id,
        ticker=ctx.ticker,
        findings=ctx.state.findings,
        errors=ctx.state.errors,
        verification=ctx.verification,
        confidence=ctx.confidence,
        agreement_score=ctx.state.findings.get("agreement_score"),
    )
