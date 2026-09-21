#!/usr/bin/env python3
"""Fig. 1: the YARROW loop as a CHIA graph, colour-coded by CHIA construct.
Orthogonal arrows, one line of text per box, no overlaps."""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
plt.rcParams.update({"font.family": "DejaVu Sans"})
INK, MUTED, WHITE, LINE = "#22313f", "#5c6770", "#ffffff", "#3d4a56"
C = {"orch": "#e9c46a", "llm": "#264653", "sim": "#2a9d8f", "actor": "#6d597a", "tool": "#e0e1dd", "oracle": "#e76f51", "check": "#f8cdbf"}
LEG = [("orch", "CHIA orchestration"), ("llm", "CHIA model function"), ("sim", "CHIA simulation"),
       ("tool", "Sandbox tools"), ("actor", "Ray budget actor"), ("check", "Diagnosis check"), ("oracle", "Reference label")]
fig, ax = plt.subplots(figsize=(7.2, 2.75), dpi=220); ax.set_xlim(0, 117); ax.set_ylim(6, 48.6); ax.axis("off")

def box(x, y, w, h, kind, title, sub=None, fs=7.2, sfs=6.0):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.6", lw=0.8, ec=WHITE, fc=C[kind], zorder=2))
    tc = INK if kind in ("orch", "tool", "check") else WHITE
    if sub:
        ax.text(x + w / 2, y + h * 0.64, title, ha="center", va="center", fontsize=fs, color=tc, fontweight="bold", zorder=3)
        ax.text(x + w / 2, y + h * 0.30, sub, ha="center", va="center", fontsize=sfs, color=tc, zorder=3)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs, color=tc, fontweight="bold", zorder=3)

def path(pts, color=LINE, ls="-", lw=0.9):
    for a, b in zip(pts[:-2], pts[1:-1]):
        ax.plot([a[0], b[0]], [a[1], b[1]], color=color, lw=lw, ls=ls, zorder=4, solid_capstyle="round")
    ax.add_patch(FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>", mutation_scale=7, lw=lw, color=color, ls=ls, zorder=4, joinstyle="round"))

def note(x, y, s, color=MUTED):
    ax.text(x, y, s, fontsize=5.8, color=color, ha="center", va="center", zorder=5, style="italic")

Y, H, W = 22, 10, 16
xs = [2, 22, 42, 62, 90]
box(xs[0], Y, W, H, "orch", "Fresh sandbox", "original RTL, restarts")
box(xs[1], Y, W, H, "llm", "Agent turn", "edit, test, claim")
box(xs[2], Y, W, H, "sim", "Verify", "rebuild + full suite")
box(xs[3], Y, W, H, "sim", "Self-replay", "original vs patch")
box(xs[4], Y, W, H, "check", "Diagnosis check", "signal in label set?")
for a, b in zip(xs[:-2], xs[1:-1]):
    path([(a + W, Y + H / 2), (b, Y + H / 2)])
path([(xs[3] + W, Y + H / 2), (79.5, Y + H / 2)])      # self-replay -> report diagnosis
path([(87, Y + H / 2), (xs[4], Y + H / 2)])            # report diagnosis -> diagnosis check
note(60, Y + H / 2 + 2.2, "pass", C["sim"]); box(79.5, Y + 1.5, 7.5, H - 3, "llm", "Report", "diagnosis", fs=5.6, sfs=5.0)
ax.text(xs[4] + W + 1.2, Y + H / 2, "Tests pass;\nsignal matches\nreference label", fontsize=6.0, color=INK, ha="left", va="center", linespacing=1.25)

# fail loop (below): verify -> agent
path([(50, Y), (50, 17.5), (36, 17.5), (36, Y)]); note(43, 15.8, "fail: test report to agent", LINE)
# restart loop (above): agent -> sandbox
path([(26, Y + H), (26, 36), (10, 36), (10, Y + H)]); note(18, 37.6, "attempt budget exhausted; up to 3 attempts", LINE)
# claim feedback (dashed, above): claim check -> agent
path([(98, Y + H), (98, 40.5), (83.25, 40.5), (83.25, Y + H - 1.5)], color=C["oracle"], ls="--"); note(90, 42.1, "nonmatching: revise report,\nat most two rounds", C["oracle"])
# budget actor bar
box(22, 44.2, 56, 4.0, "actor", "Per-bug budget (Ray): model calls, simulations, time, cost", fs=6.2)
# tools under the agent
box(22, 7, 7.5, 6, "tool", "./edit", "lint, rollback", fs=6.0, sfs=5.2); path([(25.75, 13), (25.75, Y)], color=MUTED, lw=0.8)
box(30.5, 7, 7.5, 6, "tool", "./reset.sh", "original files", fs=6.0, sfs=5.2); path([(33, 13), (33, Y)], color=MUTED, lw=0.8)
# audit label under the claim check
box(90, 7, 16, 6, "oracle", "Reference label", "reference vs faulty", fs=6.2, sfs=5.4); path([(98, 13), (98, Y)], color=C["oracle"], lw=0.9)

handles = [plt.Rectangle((0, 0), 1, 1, color=C[k]) for k, _ in LEG]
handles = [plt.Rectangle((0, 0), 1, 1, fc=C[k], ec=WHITE, lw=0.6) for k, _ in LEG]
fig.legend(handles, [l for _, l in LEG], loc="lower center", ncol=4, frameon=False, fontsize=7.0, labelcolor=INK,
           handlelength=1.0, columnspacing=1.0, handletextpad=0.4, bbox_to_anchor=(0.5, -0.005))
fig.subplots_adjust(left=0.01, right=0.99, top=1.0, bottom=0.135)
fig.savefig("figures/fig_workflow.pdf", bbox_inches="tight", pad_inches=0.03)
fig.savefig("figures/fig_workflow.png", bbox_inches="tight", pad_inches=0.03)
print("wrote fig_workflow.{pdf,png}")
