"""Prompts owned by the supervisor-over-peers path.

``SYNTHESIS_PROMPT`` is the supervisor's own final-answer composer. It deliberately does
NOT reuse the legacy ``ANALYSIS_AGENT_PROMPT`` (a *tool-calling* AnalysisAgent persona whose
rule 7 — "call ``code_agent_answer``" — is contradictory here: the synthesize/compose step is
tool-free and the code peer already ran upstream). Keeping a separate constant lets the
supervisor composer evolve independently of the legacy agent.

The peer prompts (``ANALYSIS_WORKFLOW_PROMPT`` / ``CODE_PEER_PROMPT``) live in
``agent_runtime.supervisor.graph`` next to the peer node fns that use them.
"""

from __future__ import annotations

SYNTHESIS_PROMPT = (
    "You are the answer SYNTHESIZER for the I-GUIDE assistant.\n"
    "Goal: compose the final, user-facing answer from the materials below — the conversation so "
    "far, retrieved evidence, and any analysis/code results. All work has already been done and "
    "its results are provided as text; do NOT call tools.\n"
    "Rules:\n"
    "1. Ground every claim only on the evidence/results provided; do not invent facts.\n"
    "2. Cite only doc_ids that appear in the evidence.\n"
    "3. If the evidence is insufficient, state that clearly rather than guessing.\n"
    "4. Never invent titles, sources, or citation ids.\n"
    "5. If the materials include an image artifact (a PNG/JPG map, plot, or figure with a "
    "download_url, e.g. from plot_vector or execute_code), embed it inline using markdown image "
    "syntax `![short caption](download_url)` with the EXACT download_url provided. Do not invent URLs."
)

__all__ = ["SYNTHESIS_PROMPT"]
