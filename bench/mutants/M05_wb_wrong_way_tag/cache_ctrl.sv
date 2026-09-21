// YARROW benchmark DUT: 2-way set-associative write-back, write-allocate
// cache controller with whole-cache flush.
//
// Geometry (fixed for the benchmark):
//   32-bit words, 4 words/line, 8 sets, 2 ways  => 256 B of cache data
//   16-bit byte addresses: [1:0] byte, [3:2] word, [6:4] index, [15:7] tag
//
// CPU side: single-outstanding valid/ready request, one-cycle resp pulse.
// Mem side: word-granularity valid/ready request channel; read data returns
// on a valid-only resp channel, in order, after arbitrary latency.
module cache_ctrl #(
    parameter ADDR_W = 16,
    parameter DATA_W = 32
) (
    input  logic              clk,
    input  logic              rst,

    // CPU request channel
    input  logic              req_valid,
    output logic              req_ready,
    input  logic [ADDR_W-1:0] req_addr,
    input  logic              req_we,
    input  logic [DATA_W-1:0] req_wdata,
    input  logic [3:0]        req_wstrb,

    // CPU response (pulses once per accepted request)
    output logic              resp_valid,
    output logic [DATA_W-1:0] resp_rdata,

    // Flush request: pulse flush_req while idle; flush_done pulses when all
    // dirty lines have been written back.
    input  logic              flush_req,
    output logic              flush_done,

    // Memory request channel (word granularity)
    output logic              mem_req_valid,
    input  logic              mem_req_ready,
    output logic              mem_req_we,
    output logic [ADDR_W-1:0] mem_req_addr,
    output logic [DATA_W-1:0] mem_req_wdata,

    // Memory read response channel
    input  logic              mem_resp_valid,
    input  logic [DATA_W-1:0] mem_resp_rdata
);

  localparam int WORDS   = 4;                 // words per line
  localparam int SETS    = 8;
  localparam int WAYS    = 2;
  localparam int OFF_W   = 2;                 // word-offset bits
  localparam int IDX_W   = 3;
  localparam int TAG_W   = ADDR_W - IDX_W - OFF_W - 2;  // 9

  typedef enum logic [2:0] {
    S_IDLE, S_CMP, S_WB, S_RF, S_RESP, S_FLUSH, S_FLUSH_WB
  } state_e;

  state_e state;

  // Cache arrays
  logic [TAG_W-1:0]  tag_arr   [SETS][WAYS];
  logic              valid_arr [SETS][WAYS];
  logic              dirty_arr [SETS][WAYS];
  logic              lru_arr   [SETS];        // way that is least recently used
  logic [DATA_W-1:0] data_arr  [SETS][WAYS][WORDS];

  // Latched request
  logic [ADDR_W-1:0] r_addr;
  logic              r_we;
  logic [DATA_W-1:0] r_wdata;
  logic [3:0]        r_wstrb;

  wire [OFF_W-1:0] r_off = r_addr[3:2];
  wire [IDX_W-1:0] r_idx = r_addr[6:4];
  wire [TAG_W-1:0] r_tag = r_addr[15:7];

  // Hit detection
  logic hit0, hit1, hit;
  logic hit_way;
  always_comb begin
    hit0    = valid_arr[r_idx][0] && (tag_arr[r_idx][0] == r_tag);
    hit1    = valid_arr[r_idx][1] && (tag_arr[r_idx][1] == r_tag);
    hit     = hit0 || hit1;
    hit_way = hit1;
  end

  // Victim selection (LRU)
  logic victim_way;
  always_comb victim_way = lru_arr[r_idx];

  // Beat counters / flush scan pointers
  logic [2:0] beat;        // 0..WORDS, counts issued beats
  logic [2:0] rf_got;      // refill beats received
  logic [IDX_W-1:0] fl_idx;
  logic             fl_way;

  // Writeback source registers (line captured at WB entry)
  logic [TAG_W-1:0] wb_tag;
  logic [IDX_W-1:0] wb_idx;
  logic             wb_way;

  logic [DATA_W-1:0] r_resp_data;
  assign resp_rdata = r_resp_data;

  // Memory request outputs
  always_comb begin
    mem_req_valid = 1'b0;
    mem_req_we    = 1'b0;
    mem_req_addr  = '0;
    mem_req_wdata = '0;
    case (state)
      S_WB, S_FLUSH_WB: begin
        mem_req_valid = (beat < WORDS[2:0]);
        mem_req_we    = 1'b1;
        mem_req_addr  = {wb_tag, wb_idx, beat[OFF_W-1:0], 2'b00};
        mem_req_wdata = data_arr[wb_idx][wb_way][beat[OFF_W-1:0]];
      end
      S_RF: begin
        mem_req_valid = (beat < WORDS[2:0]);
        mem_req_we    = 1'b0;
        mem_req_addr  = {r_tag, r_idx, beat[OFF_W-1:0], 2'b00};
      end
      default: ;
    endcase
  end

  assign req_ready = (state == S_IDLE) && !flush_req;

  integer s, w, b;
  always_ff @(posedge clk) begin
    if (rst) begin
      state      <= S_IDLE;
      resp_valid <= 1'b0;
      flush_done <= 1'b0;
      beat       <= '0;
      rf_got     <= '0;
      for (s = 0; s < SETS; s = s + 1) begin
        lru_arr[s] <= 1'b0;
        for (w = 0; w < WAYS; w = w + 1) begin
          valid_arr[s][w] <= 1'b0;
          dirty_arr[s][w] <= 1'b0;
        end
      end
    end else begin
      resp_valid <= 1'b0;
      flush_done <= 1'b0;

      case (state)
        S_IDLE: begin
          if (flush_req) begin
            fl_idx <= '0;
            fl_way <= 1'b0;
            state  <= S_FLUSH;
          end else if (req_valid) begin
            r_addr  <= req_addr;
            r_we    <= req_we;
            r_wdata <= req_wdata;
            r_wstrb <= req_wstrb;
            state   <= S_CMP;
          end
        end

        S_CMP: begin
          if (hit) begin
            if (r_we) begin
              for (b = 0; b < 4; b = b + 1) begin
                if (r_wstrb[b])
                  data_arr[r_idx][hit_way][r_off][8*b +: 8] <= r_wdata[8*b +: 8];
              end
              dirty_arr[r_idx][hit_way] <= 1'b1;
            end
            r_resp_data       <= data_arr[r_idx][hit_way][r_off];
            lru_arr[r_idx]    <= ~hit_way;   // other way becomes LRU
            state             <= S_RESP;
          end else begin
            // Miss: evict victim if it holds a valid dirty line.
            wb_tag <= tag_arr[r_idx][~victim_way];
            wb_idx <= r_idx;
            wb_way <= victim_way;
            beat   <= '0;
            rf_got <= '0;
            if (valid_arr[r_idx][victim_way] && dirty_arr[r_idx][victim_way])
              state <= S_WB;
            else
              state <= S_RF;
          end
        end

        S_WB: begin
          if (mem_req_valid && mem_req_ready) begin
            beat <= beat + 3'd1;
            if (beat == WORDS[2:0] - 3'd1) begin
              dirty_arr[wb_idx][wb_way] <= 1'b0;
              beat  <= '0;
              state <= S_RF;
            end
          end
        end

        S_RF: begin
          if (mem_req_valid && mem_req_ready)
            beat <= beat + 3'd1;
          if (mem_resp_valid) begin
            data_arr[r_idx][wb_way][rf_got[OFF_W-1:0]] <= mem_resp_rdata;
            rf_got <= rf_got + 3'd1;
            if (rf_got == WORDS[2:0] - 3'd1) begin
              // Line complete: install tag and retry the lookup.
              tag_arr[r_idx][wb_way]   <= r_tag;
              valid_arr[r_idx][wb_way] <= 1'b1;
              dirty_arr[r_idx][wb_way] <= 1'b0;
              state <= S_CMP;
            end
          end
        end

        S_RESP: begin
          resp_valid <= 1'b1;
          state      <= S_IDLE;
        end

        S_FLUSH: begin
          if (valid_arr[fl_idx][fl_way] && dirty_arr[fl_idx][fl_way]) begin
            wb_tag <= tag_arr[fl_idx][fl_way];
            wb_idx <= fl_idx;
            wb_way <= fl_way;
            beat   <= '0;
            state  <= S_FLUSH_WB;
          end else begin
            if (fl_way == 1'b1) begin
              if (fl_idx == IDX_W'(SETS - 1)) begin
                flush_done <= 1'b1;
                state      <= S_IDLE;
              end
              fl_idx <= fl_idx + 1'b1;
            end
            fl_way <= ~fl_way;
          end
        end

        S_FLUSH_WB: begin
          if (mem_req_valid && mem_req_ready) begin
            beat <= beat + 3'd1;
            if (beat == WORDS[2:0] - 3'd1) begin
              dirty_arr[wb_idx][wb_way] <= 1'b0;
              state <= S_FLUSH;   // rescan same slot; now clean, scan advances
            end
          end
        end

        default: state <= S_IDLE;
      endcase
    end
  end

endmodule
