// A chain of accumulator stages whose size follows config.vh, so a sweep has something to
// move: WIDTH and STAGES change the LUT/FF count and the critical path, USE_MULT pulls in DSPs.
`include "config.vh"

module top (
    input  wire       clk,
    input  wire [3:0] sw,
    output wire [3:0] led
);
    wire [`WIDTH-1:0] chain [0:`STAGES];
    assign chain[0] = {{(`WIDTH-4){1'b0}}, sw};

    genvar i;
    generate
        for (i = 0; i < `STAGES; i = i + 1) begin : g
            stage #(.WIDTH(`WIDTH)) u_stage (.clk(clk), .d(chain[i]), .q(chain[i+1]));
        end
    endgenerate

    assign led = chain[`STAGES][`WIDTH-1 -: 4];
endmodule

// keep_hierarchy: every stage stays an instance of its own after synthesis, so
// [reports] hierarchical can give it utilization and power columns.
(* keep_hierarchy = "yes" *)
module stage #(
    parameter WIDTH = 8
) (
    input  wire             clk,
    input  wire [WIDTH-1:0] d,
    output reg  [WIDTH-1:0] q
);
    reg [WIDTH-1:0] acc = 0;
    always @(posedge clk) begin
`ifdef USE_MULT
        acc <= acc * d + d;
`else
        acc <= acc + d;
`endif
        q <= acc;
    end
endmodule
