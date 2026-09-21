#!/usr/bin/env python3
"""Fig. 2 of the paper: joint success on the field tier, baseline vs full workflow."""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
INK, MUTED, GRID = "#1a1a1a", "#6e6e6e", "#e6e6e6"
BASE, FULL = "#c9ccd1", "#1f5f8b"
models = ["Gemini 2.5 Flash", "Gemini 2.5 Pro", "Gemini 3.1 Pro"]
base = [(7, 28), (8, 14), (18, 28)]
full = [(18, 28), (19, 28), (24, 28)]
fig, ax = plt.subplots(figsize=(4.6, 2.6), dpi=200)
w = 0.34
for i, (b, f) in enumerate(zip(base, full)):
    pb, pf = 100 * b[0] / b[1], 100 * f[0] / f[1]
    ax.bar(i - w / 2 - 0.02, pb, w, color=BASE, edgecolor="none", zorder=2, label="Baseline" if i == 0 else None)
    ax.bar(i + w / 2 + 0.02, pf, w, color=FULL, edgecolor="none", zorder=2, label="YARROW" if i == 0 else None)
    ax.text(i - w / 2 - 0.02, pb + 2.5, f"{pb:.0f}%", ha="center", va="bottom", fontsize=9, color=INK, fontweight="bold")
    ax.text(i - w / 2 - 0.02, pb - 3, f"{b[0]}/{b[1]}", ha="center", va="top", fontsize=7.5, color=INK)
    ax.text(i + w / 2 + 0.02, pf + 2.5, f"{pf:.0f}%", ha="center", va="bottom", fontsize=9, color=INK, fontweight="bold")
    ax.text(i + w / 2 + 0.02, pf - 3, f"{f[0]}/{f[1]}", ha="center", va="top", fontsize=7.5, color="white")
    ax.annotate(f"+{pf - pb:.0f} pp", xy=(i, max(pb, pf) + 11), ha="center", va="bottom", fontsize=8, color=FULL)
ax.set_xticks(range(3)); ax.set_xticklabels(models, fontsize=9, color=INK)
ax.set_ylim(0, 110); ax.set_yticks([0, 25, 50, 75, 100]); ax.set_yticklabels(["0", "25", "50", "75", "100%"], color=MUTED)
ax.set_ylabel("Joint success (%)", color=INK)
for s in ("top", "right", "left"): ax.spines[s].set_visible(False)
ax.spines["bottom"].set_color(GRID)
ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0); ax.tick_params(length=0)
ax.legend(frameon=False, fontsize=8.5, loc="upper left", bbox_to_anchor=(0.16, 1.02), ncol=2, handlelength=1.2, columnspacing=1.4)
fig.tight_layout(pad=0.2)
fig.savefig("figures/fig_joint_success.png", bbox_inches="tight", pad_inches=0.02); fig.savefig("figures/fig_joint_success.pdf", bbox_inches="tight", pad_inches=0.02)
print("wrote fig_joint_success.{png,pdf}")
