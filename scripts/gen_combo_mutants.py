#!/usr/bin/env python3
"""Generate the hard benchmark tier: mutants composed of 2-3 interacting
single mutations (bench/mutants_hard/). Mutations are applied sequentially;
each find-string must still occur exactly once at its turn, otherwise the
combo is rejected loudly.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "rtl" / "cache_ctrl.sv"
MANIFEST = ROOT / "bench" / "mutations.json"
OUT = ROOT / "bench" / "mutants_hard"

# Pairs/triples chosen for interacting, cross-class evidence: each combo's
# bugs corrupt overlapping mechanisms so single-hypothesis debugging is
# misleading.
COMBOS = [
    ("H01_dirty_x_flushrange",   ["M01_whit_no_dirty", "M21_flush_done_after_set0"]),
    ("H02_wbinv_x_earlyinstall", ["M02_wb_cond_inverted", "M16_refill_installs_early"]),
    ("H03_wbtag_x_hitway",       ["M05_wb_wrong_way_tag", "M09_hit_way_inverted"]),
    ("H04_wbready_x_rfbeat",     ["M11_wb_ignores_ready", "M27_refill_indexed_by_beat"]),
    ("H05_flushdirty_x_wbtrunc", ["M03_flush_checks_way0_dirty", "M15_wb_truncated"]),
    ("H06_tagslice_x_wstrb",     ["M06_tag_slice_off_by_one", "M24_wstrb_ignored"]),
    ("H07_idxslice_x_flushbp",   ["M07_index_slice_off_by_one", "M13_flushwb_ignores_ready"]),
    ("H08_hit1tag_x_skipwb",     ["M08_hit1_compares_way0_tag", "M14_miss_skips_writeback"]),
    ("H09_rstvalid_x_word0",     ["M17_reset_valid_high", "M26_resp_word0"]),
    ("H10_rfaddr_x_rstflush",    ["M10_refill_addr_uses_victim_tag", "M18_reset_flush_done_high"]),
    ("H11_dirty_wbtrunc_flush",  ["M01_whit_no_dirty", "M15_wb_truncated", "M21_flush_done_after_set0"]),
    ("H12_wbinv_hitway_rfbeat",  ["M02_wb_cond_inverted", "M09_hit_way_inverted", "M27_refill_indexed_by_beat"]),
    ("H13_tag_flushbp_wstrb",    ["M06_tag_slice_off_by_one", "M13_flushwb_ignores_ready", "M24_wstrb_ignored"]),
    ("H14_wbtag_early_rstflush", ["M05_wb_wrong_way_tag", "M16_refill_installs_early", "M18_reset_flush_done_high"]),
    ("H15_flushd_wbready_word0", ["M03_flush_checks_way0_dirty", "M11_wb_ignores_ready", "M26_resp_word0"]),
]


def main() -> int:
    golden = GOLDEN.read_text()
    muts = {m["id"]: m for m in json.loads(MANIFEST.read_text())["mutations"]}
    errors = 0
    for combo_id, parts in COMBOS:
        text = golden
        ok = True
        for pid in parts:
            m = muts[pid]
            if text.count(m["find"]) != 1:
                print(f"ERROR {combo_id}: '{pid}' find-string count "
                      f"{text.count(m['find'])} != 1 after prior edits")
                ok = False
                break
            text = text.replace(m["find"], m["replace"], 1)
        if not ok:
            errors += 1
            continue
        d = OUT / combo_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "cache_ctrl.sv").write_text(text)
        (d / "bug.json").write_text(json.dumps({
            "id": combo_id, "class": "composed",
            "parts": parts,
            "desc": " + ".join(muts[p]["desc"] for p in parts),
        }, indent=2) + "\n")
        print(f"ok    {combo_id} ({len(parts)} bugs)")
    if errors:
        return 1
    print(f"\ngenerated {len(COMBOS)} hard mutants in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
