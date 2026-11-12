# vivado-sweeper build script. vsweep prepends the `set vs(...)` lines:
#   vs(xpr) vs(out) vs(synth_run) vs(impl_run) vs(stop_after) vs(jobs) vs(hier) vs(keep) vs(extra_tcl)
# Everything Python needs to know comes back as VSWEEP_<KIND> lines on stdout.

puts "VSWEEP_PID [pid]"
flush stdout

proc vs_step {msg} { puts "VSWEEP_STEP $msg"; flush stdout }
proc vs_warn {msg} { puts "VSWEEP_WARN [string map {"\n" " "} $msg]"; flush stdout }
proc vs_fail {msg} {
    puts "VSWEEP_FAIL [string map {"\n" " "} $msg]"
    flush stdout
    catch {close_project}
    exit 1
}
# Run script in the caller's scope; a failure is a warning, not the end of the build.
proc vs_try {what script} {
    if {[catch {uplevel 1 $script} err]} { vs_warn "$what: $err" }
}
proc vs_copy {pattern dir} {
    foreach f [glob -nocomplain $pattern] { file copy -force $f $dir }
}

vs_step "open_project"
if {[catch {open_project $vs(xpr)} err]} { vs_fail "open_project: $err" }
set synth [get_runs -quiet $vs(synth_run)]
if {[llength $synth] != 1} { vs_fail "the project has no run named $vs(synth_run)" }
set impl {}
if {$vs(stop_after) ne "synth"} {
    set impl [get_runs -quiet $vs(impl_run)]
    if {[llength $impl] != 1} { vs_fail "the project has no run named $vs(impl_run)" }
}
set sdir [get_property DIRECTORY $synth]
puts "VSWEEP_DIR synth $sdir"

reset_run $synth
set t0 [clock seconds]
if {$impl eq ""} {
    vs_step "synthesis"
    launch_runs $synth -jobs $vs(jobs)
} else {
    set to_step [expr {$vs(stop_after) eq "bitstream" ? "write_bitstream" : "route_design"}]
    vs_step "synthesis"
    launch_runs $impl -to_step $to_step -jobs $vs(jobs)
}
wait_on_run $synth
set s [get_property STATUS $synth]
puts "VSWEEP_STATUS synth $s"
vs_copy $sdir/*.vds $vs(out)
if {[file exists $sdir/runme.log]} { file copy -force $sdir/runme.log $vs(out)/synth_runme.log }
if {![string match "*Complete*" $s]} { vs_fail "synthesis did not complete: $s (see synth_runme.log)" }
puts "VSWEEP_TIME synth [expr {[clock seconds] - $t0}]"

if {$impl ne ""} {
    vs_step "implementation (to $to_step)"
    set t1 [clock seconds]
    wait_on_run $impl
    set i [get_property STATUS $impl]
    set idir [get_property DIRECTORY $impl]
    puts "VSWEEP_STATUS impl $i"
    puts "VSWEEP_DIR impl $idir"
    if {[file exists $idir/runme.log]} { file copy -force $idir/runme.log $vs(out)/impl_runme.log }
    if {![string match "$to_step Complete*" $i]} { vs_fail "implementation did not complete: $i (see impl_runme.log)" }
    puts "VSWEEP_TIME impl [expr {[clock seconds] - $t1}]"
    if {[lsearch -exact $vs(keep) bit] >= 0} { vs_copy $idir/*.bit $vs(out) }
    if {[lsearch -exact $vs(keep) ltx] >= 0} { vs_copy $idir/*.ltx $vs(out) }
    if {[lsearch -exact $vs(keep) dcp] >= 0} { vs_copy $idir/*_routed.dcp $vs(out) }
} elseif {[lsearch -exact $vs(keep) dcp] >= 0} {
    vs_copy $sdir/*.dcp $vs(out)
}

vs_step "reports"
if {[catch {open_run [expr {$impl eq "" ? $synth : $impl}]} err]} { vs_fail "open_run: $err" }
vs_try timing { report_timing_summary -delay_type min_max -max_paths 10 -file $vs(out)/timing_summary.rpt }
vs_try utilization { report_utilization -file $vs(out)/utilization.rpt }
if {[llength $vs(hier)]} {
    # By default report_utilization -hierarchical leaves out small instances; the ones asked
    # for must be there whatever their size. (Older Vivado lacks the option: plain fallback.)
    if {[catch {report_utilization -hierarchical -hierarchical_min_primitive_count 0 \
                    -file $vs(out)/utilization_hier.rpt}]} {
        vs_try utilization_hier { report_utilization -hierarchical -file $vs(out)/utilization_hier.rpt }
    }
    vs_try power { report_power -hierarchical_depth 20 -file $vs(out)/power.rpt }
} else {
    vs_try power { report_power -file $vs(out)/power.rpt }
}
if {$vs(extra_tcl) ne ""} {
    set run_dir $vs(out)
    vs_try extra_tcl { source $vs(extra_tcl) }
}
close_project
puts "VSWEEP_DONE"
exit 0
