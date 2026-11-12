# Creates proj/blinky.xpr from the sources in src/ (the repository ships no .xpr):
#   vivado -mode batch -source create_project.tcl [-tclargs <part>]
# The sources are added by path relative to the project ($PPRDIR/../src/...), which is what
# `vsweep run --parallel` needs to copy the project.
set here [file dirname [file normalize [info script]]]
set part [expr {[llength $argv] ? [lindex $argv 0] : "xc7a100tcsg324-1"}]
create_project blinky $here/proj -part $part -force
add_files -norecurse [list $here/src/top.v $here/src/config.vh]
set_property file_type {Verilog Header} [get_files config.vh]
add_files -fileset constrs_1 -norecurse $here/src/arty.xdc
set_property top top [current_fileset]
close_project
puts "created $here/proj/blinky.xpr"
