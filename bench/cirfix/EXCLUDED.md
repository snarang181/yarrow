# Excluded CirFix entries (not repair tasks under our criterion)

- decoder_3_to_8-kgoliya_buggy1 — buggy variant does not compile
  (`assign ... <=`); a compile error is not a behavioral failure.
- mux_4_1-wadden_buggy2 — buggy variant does not compile under Verilator
  (`2'h10`: too many digits for a 2-bit literal).
- reed_solomon_decoder-original — RTL-Repair baseline entry; buggy file is
  byte-identical to golden (empty bug.diff).
- sdram_controller-* (7) — only Verilog oracle testbenches (VCS/iverilog),
  no CSV table; no open-loop stimulus available.
- lshift_reg-kgoliya_buggy1 — compiles, but the only available table
  (orig_tb) does not distinguish it from golden (buggy passes); no
  detectable failure under our stimulus.
- reed_solomon_decoder-* (6) — golden does NOT reproduce its recorded table
  under our replay (first output byte at row 3094 never appears; neither
  pre- nor post-edge sampling nor --x-initial unique changes this; the
  build is warning-free). Not resolved within the time box; excluded
  rather than guessed at.
- first_counter_overflow-kgoliya_buggy1, fsm_full-ssscrazy_buggy1,
  fsm_full-wadden_buggy2 — compile, but no available table distinguishes
  them from golden (buggy passes); undetectable under our stimulus.
- pairing-* (5) — golden does NOT reproduce its 74k-row recorded table
  (expected values of 2^64-1 appear where our golden outputs 0/2; likely
  X/undefined in the recorder printed as -1). Not resolved in the time box;
  excluded rather than guessed at.
