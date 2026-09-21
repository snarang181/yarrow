// Record/replay harness for causal labels.  Record mode regenerates the
// tb_main stimulus and saves every DUT input before its active edge.  Replay
// mode applies that stream open-loop, deliberately ignoring DUT outputs.
//
// +record=FILE +trace=FILE plus the normal tb_main stimulus plusargs records.
// +replay=FILE +trace=FILE replays a previously recorded stream.

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <random>
#include <string>
#include <vector>

#include "Vcache_ctrl.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static const int ADDR_W = 16;
static const size_t MEM_WORDS = 1u << (ADDR_W - 2);
struct PendingRead { uint32_t data; int delay; };
struct Inputs {
  unsigned rst, req_valid, req_addr, req_we, req_wdata, req_wstrb;
  unsigned flush_req, mem_req_ready, mem_resp_valid, mem_resp_rdata;
};

static uint32_t init_word(uint32_t widx, uint32_t seed) {
  uint32_t x = widx * 2654435761u ^ seed * 97531u ^ 0xdeadbeefu;
  x ^= x >> 15; x *= 2246822519u; x ^= x >> 13;
  return x;
}

static void drive(Vcache_ctrl* d, const Inputs& in) {
  d->rst = in.rst; d->req_valid = in.req_valid; d->req_addr = in.req_addr;
  d->req_we = in.req_we; d->req_wdata = in.req_wdata; d->req_wstrb = in.req_wstrb;
  d->flush_req = in.flush_req; d->mem_req_ready = in.mem_req_ready;
  d->mem_resp_valid = in.mem_resp_valid; d->mem_resp_rdata = in.mem_resp_rdata;
}

static void write_input(FILE* f, const Inputs& in) {
  fprintf(f, "%u %u %x %u %x %x %u %u %u %x\n", in.rst, in.req_valid,
          in.req_addr, in.req_we, in.req_wdata, in.req_wstrb, in.flush_req,
          in.mem_req_ready, in.mem_resp_valid, in.mem_resp_rdata);
}

static bool read_input(FILE* f, Inputs* in) {
  return fscanf(f, "%u %u %x %u %x %x %u %u %u %x", &in->rst, &in->req_valid,
                &in->req_addr, &in->req_we, &in->req_wdata, &in->req_wstrb,
                &in->flush_req, &in->mem_req_ready, &in->mem_resp_valid,
                &in->mem_resp_rdata) == 10;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  auto arg = [](const char* name) -> std::string {
    const char* v = Verilated::commandArgsPlusMatch(name);
    const char* eq = (v && *v) ? strchr(v, '=') : nullptr;
    return eq ? std::string(eq + 1) : std::string();
  };
  auto plusarg = [&](const char* name, uint64_t dflt) -> uint64_t {
    std::string v = arg(name); return v.empty() ? dflt : strtoull(v.c_str(), nullptr, 0);
  };
  const std::string record_name = arg("record");
  const std::string replay_name = arg("replay");
  const std::string trace_name = arg("trace");
  if ((record_name.empty() && replay_name.empty()) ||
      (!record_name.empty() && !replay_name.empty()) || trace_name.empty()) {
    fprintf(stderr, "need exactly one of +record=FILE/+replay=FILE and +trace=FILE\n"); return 2;
  }
  const char* stream_name = (record_name.empty() ? replay_name : record_name).c_str();
  FILE* stream = fopen(stream_name, record_name.empty() ? "r" : "w");
  if (!stream) { fprintf(stderr, "input stream %s: ", stream_name); perror(""); return 2; }

  Verilated::traceEverOn(true);
  Vcache_ctrl* dut = new Vcache_ctrl;
  VerilatedVcdC* trace = new VerilatedVcdC;
  dut->trace(trace, 99); trace->open(trace_name.c_str());
  uint64_t tick = 0;
  auto edge = [&](const Inputs& in) {
    drive(dut, in); dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval(); trace->dump(tick++);
    dut->clk = 0; dut->eval();
  };

  if (!replay_name.empty()) {
    Inputs in;
    while (read_input(stream, &in)) edge(in);
  } else {
    const uint32_t seed = (uint32_t)plusarg("seed", 1);
    const uint64_t num_ops = plusarg("ops", 2000);
    const int wprob = (int)plusarg("wprob", 50), tag_pool = (int)plusarg("tags", 4);
    const int readyprob = (int)plusarg("readyprob", 70), latmax = (int)plusarg("latmax", 8);
    std::mt19937 rng(seed); auto pct = [&](int p) { return (int)(rng() % 100) < p; };
    std::vector<uint32_t> mem(MEM_WORDS), golden(MEM_WORDS);
    for (size_t i = 0; i < MEM_WORDS; ++i) mem[i] = golden[i] = init_word(i, seed);
    std::deque<PendingRead> rd_q;
    uint64_t ops_done = 0, ops_issued = 0, cycle = 0;
    bool req_inflight = false, cur_we = false;
    uint32_t cur_addr = 0, cur_wdata = 0, exp_rdata = 0; uint8_t cur_wstrb = 0;
    int idle_gap = 0; enum Phase { RUN, FLUSH_GO, FLUSH_WAIT, DONE_OK, DONE_FAIL } phase = RUN;
    Inputs in{}; in.rst = 1;
    for (int i = 0; i < 4; ++i) { write_input(stream, in); edge(in); }
    in.rst = 0;
    const uint64_t max_cycles = 400 * num_ops + 100000;
    while (phase != DONE_OK && phase != DONE_FAIL && cycle < max_cycles) {
      ++cycle; in.mem_req_ready = pct(readyprob); in.mem_resp_valid = 0;
      if (!rd_q.empty()) { if (rd_q.front().delay > 0) --rd_q.front().delay;
        if (!rd_q.front().delay) { in.mem_resp_valid = 1; in.mem_resp_rdata = rd_q.front().data; } }
      bool flush_fired = false;
      if (phase == RUN) {
        if (!req_inflight && !idle_gap && ops_issued < num_ops && !in.req_valid) {
          uint32_t tag = rng() % tag_pool, idx = rng() % 8, off = rng() % 4;
          cur_addr = (tag << 7) | (idx << 4) | (off << 2); cur_we = pct(wprob);
          cur_wdata = rng(); cur_wstrb = (uint8_t)(1 + rng() % 15);
          in.req_valid = 1; in.req_addr = cur_addr; in.req_we = cur_we;
          in.req_wdata = cur_wdata; in.req_wstrb = cur_wstrb;
        }
        if (idle_gap > 0) --idle_gap;
      } else if (phase == FLUSH_GO) { in.flush_req = 1; flush_fired = true; }
      drive(dut, in); dut->clk = 0; dut->eval();
      bool cpu_acc = dut->req_valid && dut->req_ready, mem_acc = dut->mem_req_valid && dut->mem_req_ready;
      bool mem_rsp = dut->mem_resp_valid, rv = dut->resp_valid, fd = dut->flush_done;
      uint32_t rdata = dut->resp_rdata, m_addr = dut->mem_req_addr, m_wdata = dut->mem_req_wdata;
      bool m_we = dut->mem_req_we;
      write_input(stream, in); dut->clk = 1; dut->eval(); trace->dump(tick++); dut->clk = 0; dut->eval();
      if (cpu_acc) { ++ops_issued; req_inflight = true; in.req_valid = 0; uint32_t widx = cur_addr >> 2;
        if (cur_we) { uint32_t g = golden[widx]; for (int b = 0; b < 4; ++b) if (cur_wstrb & (1 << b)) { g &= ~(0xffu << (8*b)); g |= cur_wdata & (0xffu << (8*b)); } golden[widx] = g; }
        else exp_rdata = golden[widx]; }
      if (mem_acc) { uint32_t widx = (m_addr & 0xffff) >> 2; if (m_we) mem[widx] = m_wdata; else rd_q.push_back({mem[widx], 1 + (int)(rng() % (uint32_t)(latmax + 1))}); }
      if (mem_rsp) rd_q.pop_front();
      if (rv) { if (!req_inflight || (!cur_we && rdata != exp_rdata)) phase = DONE_FAIL; else { req_inflight = false; ++ops_done; idle_gap = pct(30) ? (int)(rng() % 4) : 0; if (ops_done == num_ops) phase = FLUSH_GO; } }
      if (flush_fired) { in.flush_req = 0; phase = FLUSH_WAIT; }
      if (fd) { bool ok = phase == FLUSH_WAIT; for (size_t i = 0; ok && i < MEM_WORDS; ++i) ok = mem[i] == golden[i]; phase = ok ? DONE_OK : DONE_FAIL; }
    }
    fprintf(stderr, "recorded: seed=%u cycles=%llu status=%d\n", seed, (unsigned long long)cycle, (int)phase);
  }
  trace->close(); dut->final(); delete trace; delete dut; fclose(stream); return 0;
}
