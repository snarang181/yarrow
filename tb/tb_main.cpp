// YARROW benchmark testbench for cache_ctrl.
//
// Drives randomized CPU traffic (constrained to a small tag pool so lines
// conflict and evict), models a word-granular backing memory with random
// request backpressure and random read latency, and checks every read
// response against a golden memory model. At the end of the run it flushes
// the cache and verifies the backing memory matches the golden model.
//
// Clocking discipline: inputs are driven while clk=0, combinational outputs
// settle on eval(), handshakes and registered one-cycle pulses are sampled
// pre-edge, then the posedge commits state.
//
// Plusargs:
//   +seed=N       RNG seed                       (default 1)
//   +ops=N        number of CPU operations       (default 2000)
//   +wprob=N      write probability, percent     (default 50)
//   +tags=N       size of tag pool (conflict pressure; smaller = hotter)
//                                                (default 4)
//   +readyprob=N  mem_req_ready probability, %   (default 70)
//   +latmax=N     max extra read latency cycles  (default 8)
//   +verbose      per-op logging
//
// Exit code 0 on PASS, 1 on FAIL (first mismatch reported), 2 on timeout.

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <random>
#include <vector>

#include "Vcache_ctrl.h"
#include "verilated.h"

static const int ADDR_W = 16;
static const size_t MEM_WORDS = 1u << (ADDR_W - 2);

struct PendingRead {
  uint32_t data;
  int delay;
};

static uint32_t init_word(uint32_t widx, uint32_t seed) {
  uint32_t x = widx * 2654435761u ^ seed * 97531u ^ 0xdeadbeefu;
  x ^= x >> 15; x *= 2246822519u; x ^= x >> 13;
  return x;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);

  auto plusarg = [&](const char* name, uint64_t dflt) -> uint64_t {
    const char* v = Verilated::commandArgsPlusMatch(name);
    if (!v || !*v) return dflt;
    const char* eq = strchr(v, '=');
    return eq ? strtoull(eq + 1, nullptr, 0) : dflt;
  };

  const uint32_t seed      = (uint32_t)plusarg("seed", 1);
  const uint64_t num_ops   = plusarg("ops", 2000);
  const int      wprob     = (int)plusarg("wprob", 50);
  const int      tag_pool  = (int)plusarg("tags", 4);
  const int      readyprob = (int)plusarg("readyprob", 70);
  const int      latmax    = (int)plusarg("latmax", 8);
  const bool     verbose   = Verilated::commandArgsPlusMatch("verbose")[0] != 0;

  std::mt19937 rng(seed);
  auto pct = [&](int p) { return (int)(rng() % 100) < p; };

  Vcache_ctrl* dut = new Vcache_ctrl;

  // Backing memory and golden model start identical.
  std::vector<uint32_t> mem(MEM_WORDS), golden(MEM_WORDS);
  for (size_t i = 0; i < MEM_WORDS; i++) mem[i] = golden[i] = init_word(i, seed);

  std::deque<PendingRead> rd_q;

  // CPU-side op state
  uint64_t ops_done = 0, ops_issued = 0;
  bool req_inflight = false;
  bool cur_we = false;
  uint32_t cur_addr = 0, cur_wdata = 0, exp_rdata = 0;
  uint8_t cur_wstrb = 0;
  int idle_gap = 0;

  enum Phase { RUN, FLUSH_GO, FLUSH_WAIT, DONE_OK, DONE_FAIL } phase = RUN;
  uint64_t cycle = 0;
  const uint64_t MAX_CYCLES = 400 * num_ops + 100000;
  int rc = 1;

  // Reset
  dut->rst = 1; dut->clk = 0;
  dut->req_valid = 0; dut->flush_req = 0;
  dut->mem_req_ready = 0; dut->mem_resp_valid = 0;
  for (int i = 0; i < 4; i++) { dut->clk = 1; dut->eval(); dut->clk = 0; dut->eval(); }
  dut->rst = 0;

  while (phase != DONE_OK && phase != DONE_FAIL && cycle < MAX_CYCLES) {
    cycle++;

    // ---- clk=0: drive this cycle's inputs ----

    dut->mem_req_ready = pct(readyprob);

    dut->mem_resp_valid = 0;
    if (!rd_q.empty()) {
      if (rd_q.front().delay > 0) rd_q.front().delay--;
      if (rd_q.front().delay == 0) {
        dut->mem_resp_valid = 1;
        dut->mem_resp_rdata = rd_q.front().data;
      }
    }

    bool flush_fired = false;
    if (phase == RUN) {
      if (!req_inflight && idle_gap == 0 && ops_issued < num_ops &&
          !dut->req_valid) {
        uint32_t tag = rng() % tag_pool;
        uint32_t idx = rng() % 8;
        uint32_t off = rng() % 4;
        cur_addr = (tag << 7) | (idx << 4) | (off << 2);
        cur_we = pct(wprob);
        cur_wdata = rng();
        cur_wstrb = (uint8_t)(1 + rng() % 15);  // nonzero
        dut->req_valid = 1;
        dut->req_addr = cur_addr;
        dut->req_we = cur_we;
        dut->req_wdata = cur_wdata;
        dut->req_wstrb = cur_wstrb;
      }
      if (idle_gap > 0) idle_gap--;
    } else if (phase == FLUSH_GO) {
      // FSM is guaranteed idle here (last resp seen, no new reqs issued).
      dut->flush_req = 1;
      flush_fired = true;
    }

    dut->eval();  // settle combinational outputs against driven inputs

    // ---- pre-edge sampling: this is what the posedge will commit ----
    bool cpu_acc = dut->req_valid && dut->req_ready;
    bool mem_acc = dut->mem_req_valid && dut->mem_req_ready;
    bool mem_rsp = dut->mem_resp_valid != 0;
    bool rv      = dut->resp_valid != 0;    // registered one-cycle pulse
    uint32_t rdata = dut->resp_rdata;
    bool fd      = dut->flush_done != 0;    // registered one-cycle pulse
    uint32_t m_addr  = dut->mem_req_addr;
    bool     m_we    = dut->mem_req_we != 0;
    uint32_t m_wdata = dut->mem_req_wdata;

    // ---- posedge ----
    dut->clk = 1; dut->eval();
    dut->clk = 0; dut->eval();

    // ---- react to sampled events ----

    if (cpu_acc) {
      ops_issued++;
      req_inflight = true;
      dut->req_valid = 0;
      uint32_t widx = cur_addr >> 2;
      if (cur_we) {
        uint32_t g = golden[widx];
        for (int b = 0; b < 4; b++)
          if (cur_wstrb & (1 << b)) {
            g &= ~(0xffu << (8 * b));
            g |= (cur_wdata & (0xffu << (8 * b)));
          }
        golden[widx] = g;
      } else {
        exp_rdata = golden[widx];
      }
      if (verbose)
        printf("[%8llu] issue %s addr=%04x wdata=%08x wstrb=%x\n",
               (unsigned long long)cycle, cur_we ? "W" : "R", cur_addr,
               cur_wdata, cur_wstrb);
    }

    if (mem_acc) {
      uint32_t widx = (m_addr & 0xffff) >> 2;
      if (m_we) {
        mem[widx] = m_wdata;
      } else {
        rd_q.push_back({mem[widx], 1 + (int)(rng() % (uint32_t)(latmax + 1))});
      }
    }

    if (mem_rsp) rd_q.pop_front();

    if (rv) {
      if (!req_inflight) {
        printf("FAIL @%llu: spurious resp_valid with no request in flight\n",
               (unsigned long long)cycle);
        phase = DONE_FAIL;
      } else {
        if (!cur_we && rdata != exp_rdata) {
          printf("FAIL @%llu: read addr=%04x expected=%08x got=%08x (op %llu)\n",
                 (unsigned long long)cycle, cur_addr, exp_rdata, rdata,
                 (unsigned long long)ops_done);
          phase = DONE_FAIL;
        }
        req_inflight = false;
        ops_done++;
        idle_gap = pct(30) ? (int)(rng() % 4) : 0;
        if (phase == RUN && ops_done == num_ops) phase = FLUSH_GO;
      }
    }

    if (flush_fired) {
      dut->flush_req = 0;
      phase = FLUSH_WAIT;
    }

    if (fd) {
      if (phase != FLUSH_WAIT) {
        printf("FAIL @%llu: spurious flush_done\n", (unsigned long long)cycle);
        phase = DONE_FAIL;
      } else {
        bool ok = true;
        for (size_t i = 0; i < MEM_WORDS; i++) {
          if (mem[i] != golden[i]) {
            printf("FAIL @%llu: post-flush mem[%04zx]=%08x expected %08x\n",
                   (unsigned long long)cycle, i << 2, mem[i], golden[i]);
            ok = false;
            break;
          }
        }
        phase = ok ? DONE_OK : DONE_FAIL;
      }
    }
  }

  if (phase == DONE_OK) {
    printf("PASS: seed=%u ops=%llu cycles=%llu\n", seed,
           (unsigned long long)ops_done, (unsigned long long)cycle);
    rc = 0;
  } else if (phase != DONE_FAIL) {
    printf("FAIL: TIMEOUT after %llu cycles (ops_done=%llu/%llu, phase=%d)\n",
           (unsigned long long)cycle, (unsigned long long)ops_done,
           (unsigned long long)num_ops, (int)phase);
    rc = 2;
  }

  dut->final();
  delete dut;
  return rc;
}
