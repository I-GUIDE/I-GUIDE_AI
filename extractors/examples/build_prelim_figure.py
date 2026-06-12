"""Build the preliminary-example figure — designed: full-width bands, numbered rail,
soft-shadow cards, restrained palette, real heat map embedded in its own panel.

Run:  python -m extractors.examples.build_prelim_figure
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import matplotlib.image as mpimg

HERE = Path(__file__).resolve().parent
HEATMAP = HERE / "output" / "violent_crime_heatmap.png"
OUT = HERE / "prelim_example_figure.png"
OUT_SVG = HERE / "prelim_example_figure.svg"  # editable vector (Illustrator / Inkscape / Figma)

INK = "#0f172a"; MUTE = "#64748b"; LINE = "#cbd5e1"
# kind: (fill, edge, accent)
K = {
    "notebook": ("#eff6ff", "#bfdbfe", "#2563eb"),
    "infra":    ("#eef2ff", "#c7d2fe", "#4f46e5"),
    "kb":       ("#faf5ff", "#e9d5ff", "#7c3aed"),
    "io":       ("#f8fafc", "#e2e8f0", "#475569"),
    "tool":     ("#ecfdf5", "#a7f3d0", "#059669"),
    "code":     ("#fffbeb", "#fde68a", "#d97706"),
    "file":     ("#eff6ff", "#bfdbfe", "#2563eb"),
}
BAND = "#f8fafc"


def shadow(ax, x, y, w, h, rs=0.08):
    ax.add_patch(FancyBboxPatch((x + 0.05, y - 0.07), w, h,
                 boxstyle=f"round,pad=0.04,rounding_size={rs}", fc="#0f172a", ec="none", alpha=0.06, zorder=1))


def card(ax, x, y, w, h, kind, title, sub="", glyph="", tag="", title_fs=11.5):
    fc, ec, ac = K[kind]
    shadow(ax, x, y, w, h)
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.09",
                 fc=fc, ec=ec, lw=1.4, zorder=2))
    tx = x + 0.30
    if glyph:
        ax.add_patch(Circle((x + 0.42, y + h - 0.40), 0.23, fc="white", ec=ac, lw=1.6, zorder=4))
        ax.text(x + 0.42, y + h - 0.40, glyph, ha="center", va="center", fontsize=11, color=ac, fontweight="bold", zorder=5)
        tx = x + 0.82
    ax.text(tx, y + h - 0.34, title, fontsize=title_fs, fontweight="bold", color=INK, va="top", zorder=5)
    if sub:
        ax.text(tx, y + h - 0.34 - 0.40, sub, fontsize=title_fs - 2.7, color=MUTE, va="top", zorder=5)
    if tag:
        ax.text(x + w - 0.20, y + 0.18, tag, fontsize=7.5, color=ac, ha="right", va="bottom", fontweight="bold", zorder=5)


def chip(ax, x, y, w, kind, text, glyph=""):
    fc, ec, ac = K[kind]
    ax.add_patch(FancyBboxPatch((x, y), w, 0.62, boxstyle="round,pad=0.03,rounding_size=0.16",
                 fc=fc, ec=ec, lw=1.2, zorder=3))
    t = x + 0.22
    if glyph:
        ax.text(x + 0.28, y + 0.31, glyph, ha="center", va="center", fontsize=10, color=ac, fontweight="bold", zorder=4)
        t = x + 0.55
    ax.text(t, y + 0.31, text, fontsize=8.4, color=INK, va="center", zorder=4, family="monospace")


def arrow(ax, x1, y1, x2, y2, color="#94a3b8", dashed=False, lw=1.7):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15, lw=lw,
                 color=color, zorder=1, linestyle="--" if dashed else "-", shrinkA=2, shrinkB=2))


def rail(ax, yc, num, short, color):
    ax.add_patch(Circle((0.62, yc), 0.27, fc=color, ec="none", zorder=4))
    ax.text(0.62, yc, str(num), ha="center", va="center", color="white", fontsize=12.5, fontweight="bold", zorder=5)
    ax.text(0.62, yc - 0.5, short, ha="center", fontsize=9.5, fontweight="bold", color=color, va="top")


def main() -> int:
    XC = 2.5  # cards start here; rail occupies x < 2.3
    fig, ax = plt.subplots(figsize=(15.5, 10.5))
    ax.set_xlim(0, 16); ax.set_ylim(0, 11); ax.axis("off")

    # band backgrounds (subtle), behind the card region only
    for (y0, y1) in [(8.4, 9.95), (6.55, 7.8), (4.3, 6.2)]:
        ax.add_patch(FancyBboxPatch((2.1, y0), 13.7, y1 - y0, boxstyle="round,pad=0.02,rounding_size=0.05",
                     fc=BAND, ec="none", zorder=0))

    ax.text(0.2, 10.55, "Notebook  →  agent KB  →  grounded execution",
            fontsize=18, fontweight="bold", color=INK)
    # legend (top-right)
    lg = [("notebook", "notebook", "▤"), ("tool", "reused tool", "ƒ"), ("code", "code-gen", "</>"),
          ("kb", "skill / KB", "★"), ("file", "file_id", "▭")]
    lx = 9.05
    for kind, lab, g in lg:
        fc, ec, ac = K[kind]
        ax.add_patch(FancyBboxPatch((lx, 10.42), 0.30, 0.30, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fc, ec=ec, lw=1.1))
        ax.text(lx + 0.15, 10.57, g, ha="center", va="center", fontsize=7.5, color=ac, fontweight="bold")
        ax.text(lx + 0.46, 10.57, lab, fontsize=8.4, color="#334155", va="center")
        lx += 0.82 + len(lab) * 0.125

    # ── ① EXTRACT ──
    rail(ax, 9.18, 1, "Extract", "#2563eb")
    card(ax, XC, 8.55, 3.2, 1.2, "notebook", "notebook.ipynb", "→ element  ke_crimeagent", glyph="▤")
    arrow(ax, XC + 3.2, 9.15, XC + 3.8, 9.15)
    card(ax, XC + 3.8, 8.6, 2.3, 1.1, "infra", "extractor", "IPython · AST", glyph="⚙")
    arrow(ax, XC + 6.1, 9.15, XC + 6.7, 9.15)
    # KB card (taller) with two tidy chip rows
    kbx = XC + 6.7
    card(ax, kbx, 8.45, 15.7 - kbx, 1.5, "kb", "Agent KB  ·  agent-only index", glyph="◆", title_fs=11)
    chip(ax, kbx + 0.4, 8.95, 4.2, "tool", "load · join · filter · plot", glyph="ƒ")
    chip(ax, kbx + 4.75, 8.95, 1.5, "code", "cells", glyph="</>")
    chip(ax, kbx + 0.4, 8.52, 6.0, "kb", "skill · workflow (mcp_run_…) · deps", glyph="★")

    # ── ② RETRIEVE ──
    rail(ax, 7.15, 2, "Retrieve", "#0e7490")
    card(ax, XC, 6.65, 3.5, 1.05, "notebook", "user query", '“heat map of violent crime”', glyph="?")
    arrow(ax, XC + 3.5, 7.17, XC + 4.1, 7.17)
    card(ax, XC + 4.1, 6.65, 4.4, 1.05, "io", "search · agent_kb_search", "blocks → evidence (cite element)", glyph="○")
    arrow(ax, XC + 8.5, 7.17, XC + 9.1, 7.17)
    card(ax, XC + 9.1, 6.65, 4.1, 1.05, "io", "get_kb_block", "full source → verbatim reuse", glyph="▦")

    # ── ③ EXECUTE ──  (absolute x; titles sized to fit each card, tags at card foot)
    rail(ax, 5.3, 3, "Execute", "#b45309")
    yy = 4.85
    card(ax, 2.50, yy, 2.45, 1.2, "tool", "A · load", "extracted tool", glyph="ƒ", tag="TOOL", title_fs=10.5)
    arrow(ax, 4.95, yy + 0.6, 5.35, yy + 0.6, color="#2563eb")
    chip(ax, 5.35, yy + 0.3, 1.75, "file", "crime · 50k", glyph="▭")
    arrow(ax, 7.10, yy + 0.6, 7.50, yy + 0.6, color="#2563eb")
    card(ax, 7.50, yy, 2.60, 1.2, "code", "B · filter", "execute_code · input A", glyph="</>", tag="CODE", title_fs=10.5)
    arrow(ax, 10.10, yy + 0.6, 10.50, yy + 0.6, color="#2563eb")
    chip(ax, 10.50, yy + 0.3, 2.00, "file", "violent · 15.3k", glyph="▭")
    arrow(ax, 12.50, yy + 0.6, 12.90, yy + 0.6, color="#2563eb")
    card(ax, 12.90, yy, 2.55, 1.2, "tool", "C · heatmap", "hexbin", glyph="ƒ", tag="TOOL", title_fs=10.5)
    ax.text(2.50, 4.45, "↳ also  spatial_join_and_count(areas, violent) [tool]  →  Austin 922 · South Shore 672 · Near West 549",
            fontsize=8.2, color="#047857", va="top")

    # ── ④ SYNTHESIZE (left)  +  OUTPUT panel (right) ──
    rail(ax, 2.95, 4, "Synthesize", "#059669")
    card(ax, XC, 2.35, 5.7, 1.2, "io", "compose + grounding audit",
         "cites ke_crimeagent · returns heatmap.png", glyph="✓")
    ax.text(XC + 0.05, 1.7, "lineage:  crime[A·tool] → violent[B·code] → heatmap[C·tool]  ·  cite ke_crimeagent",
            fontsize=8.4, family="monospace", color=MUTE)
    ax.text(XC + 0.05, 1.2, "Reusable GIS = tools  ·  bespoke transforms = code-gen  ·  both pass files (file_id).",
            fontsize=9, color=MUTE)

    px, py, pw, ph = 8.7, 0.55, 7.0, 3.55
    shadow(ax, px, py, pw, ph)
    ax.add_patch(FancyBboxPatch((px, py), pw, ph, boxstyle="round,pad=0.04,rounding_size=0.06",
                 fc="white", ec=K["file"][1], lw=1.6, zorder=2))
    ax.text(px + 0.3, py + ph - 0.28, "Produced output  ·  heatmap.png", fontsize=10, fontweight="bold",
            color="#0b2545", va="top", zorder=5)
    if HEATMAP.exists():
        img = mpimg.imread(HEATMAP)
        ax.add_artist(AnnotationBbox(OffsetImage(img, zoom=0.162), (px + pw / 2, py + ph / 2 - 0.22),
                      frameon=False, zorder=4))
    ax.text(px + pw / 2, py + 0.22, "actual hexbin density — validated vs live Chicago data",
            fontsize=8, color=MUTE, ha="center", zorder=5)
    arrow(ax, XC + 11.35 + 0.9, yy, 12.0, 4.1, color="#2563eb", dashed=True)

    fig.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white", pad_inches=0.3)
    fig.savefig(OUT_SVG, bbox_inches="tight", facecolor="white", pad_inches=0.3)
    fig.savefig(OUT.with_suffix(".pdf"), bbox_inches="tight", facecolor="white", pad_inches=0.3)
    print("saved:", OUT)
    print("saved:", OUT_SVG, "(editable vector)")
    print("saved:", OUT.with_suffix(".pdf"), "(vector for LaTeX)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
