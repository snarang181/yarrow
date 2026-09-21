#!/usr/bin/env python3
"""One benchmark case end to end (axi-lite-s1b): the six artifacts the loop
sees or produces. All text is taken verbatim from the repository / a real run."""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch
OUT = "figures/fig_case_s1b.png"
INK, MUTED, EDGE, FILL = "#0b0b0b", "#6b6b66", "#c3c2b7", "#fafaf8"
RED, GREEN, BLUE = "#b3261e", "#1a7f4b", "#2a5db0"

panels = [
 ("1  Input: faulty design  (bench/real/axi-lite-s1b/rtl_buggy/xlnxdemo.v, 731 lines, 1 file)",
  [("// write-address ready: back-pressure guard is missing", MUTED),
   ("if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID)", RED),
   ("  begin axi_awready <= 1'b1; ...", INK),
   ("// reference:  ... && (!S_AXI_BVALID || S_AXI_BREADY))", MUTED)]),
 ("2  Input: regression table  (tb/tb0.csv, one row per rising clock edge; inputs driven, outputs checked before the edge)",
  [("row  AWVALID WVALID BREADY | AWREADY WREADY BVALID   <- 20 ports, 10 rows", MUTED),
   ("  3     1      1      0    |    1      1      0", INK),
   ("  4     1      1      0    |    0      0      1", INK),
   ("  5     1      1      0    |    0      0      1     FAIL @5: S_AXI_AWREADY got 1 expected 0", RED)]),
 ("3  Hidden audit label  (reference vs faulty replay, bench/causal_labels/real/axi-lite-s1b.json)",
  [("first divergence: xlnxdemo.axi_awready   row 5   kind=state", GREEN),
   ("set at row 5:     {axi_awready, axi_wready, slv_reg_wren}", INK),
   ("never shown to the agent; used only to grade CAUSE_SIGNAL", MUTED)]),
 ("4  Agent output  (gemini-3.1-pro, 1 model call, 6 changed lines; passes independent rebuild + full suite)",
  [("- if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID)", RED),
   ("+ if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && (~axi_bvalid || S_AXI_BREADY))", GREEN),
   ("CAUSE_SIGNAL: axi_awready   CAUSE_MODULE: xlnxdemo   CAUSE_MECHANISM: backpressure", INK),
   ("CAUSE_EVENT: AWVALID and WVALID asserted while axi_bvalid is true and BREADY is false", INK)]),
 ("5  Self-replay  (./firstdiff.sh: original vs the agent's patched design, same table; no reference used)",
  [("First divergence between ORIGINAL and YOUR FIX on this table: table row 5.", INK),
   ("  xlnxdemo.axi_awready   [register]  ORIGINAL=1  YOUR FIX=0", GREEN),
   ("  xlnxdemo.axi_wready    [register]  ORIGINAL=1  YOUR FIX=0", INK),
   ("  xlnxdemo.slv_reg_wren  [net]       ORIGINAL=1  YOUR FIX=0", INK)]),
 ("6  Scoring  (frozen: yarrow/causal.py)",
  [("repair:          PASS  (independent build, all tables)", GREEN),
   ("exact agreement: axi_awready in label set  -> exact", GREEN),
   ("joint success:   right fix AND right reason = 1", GREEN)]),
]

fig = plt.figure(figsize=(11.5, 6.9)); fig.patch.set_facecolor("white")
y = 0.99; gap = 0.02
heights = [0.152, 0.152, 0.125, 0.152, 0.152, 0.125]
for (title, lines), h in zip(panels, heights):
    ax = fig.add_axes([0.03, y - h, 0.94, h]); ax.set_axis_off()
    ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, facecolor=FILL, edgecolor=EDGE, linewidth=0.9))
    ax.text(0.012, 0.86, title, transform=ax.transAxes, fontsize=9.2, color=INK, fontweight="bold", va="center")
    n = len(lines)
    for i, (txt, col) in enumerate(lines):
        ax.text(0.03, 0.66 - i * (0.56 / max(1, n - 1)) if n > 1 else 0.4, txt, transform=ax.transAxes,
                fontsize=8.2, family="monospace", color=col, va="center")
    y -= h + gap
fig.text(0.03, 0.008, "Panels 1-2 are the agent's inputs; 3 is the evaluation oracle; 4-5 are produced inside the loop; 6 is the score. "
         "Case axi-lite-s1b: Xilinx AXI-Lite template, upstream fix = the guard on two lines.", fontsize=8, color=MUTED)
fig.savefig(OUT, dpi=180); print("wrote", OUT)
