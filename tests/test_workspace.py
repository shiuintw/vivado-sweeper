from vivado_sweeper import config, workspace

XPR = '''<?xml version="1.0" encoding="UTF-8"?>
<Project Path="{root}/hw/p.xpr">
  <FileSets><FileSet Name="sources_1">
    <File Path="{vh}"/>
    <File Path="$PPRDIR/../src/top.v"/>
    {extra}
    <Config><Option Name="IncludeDirs" Val="$PPRDIR/../src"/></Config>
  </FileSet></FileSets>
</Project>
'''


def cfg_for(root, vh_ref, extra=''):
    (root / 'hw').mkdir(parents=True, exist_ok=True)
    (root / 'src').mkdir(exist_ok=True)
    (root / 'src' / 'c.vh').write_text('`define A 1\n')
    (root / 'hw' / 'p.xpr').write_text(XPR.format(root=root, vh=vh_ref, extra=extra))
    return config.Config(path=root / 'vsweep.toml', vivado='vivado', jobs=1, root=root,
                         xpr=root / 'hw' / 'p.xpr', vh=[root / 'src' / 'c.vh'], output_dir=root / 'vsweep_runs')


def test_relative_project_is_clonable(tmp_path):
    assert workspace.check_clonable(cfg_for(tmp_path, '$PPRDIR/../src/c.vh')) == ([], [])


def test_absolute_vh_reference_is_refused(tmp_path):
    errors, _ = workspace.check_clonable(cfg_for(tmp_path, str(tmp_path / 'src' / 'c.vh')))
    assert len(errors) == 1 and 'absolute path' in errors[0]


def test_absolute_read_only_source_is_a_warning(tmp_path):
    errors, warnings = workspace.check_clonable(
        cfg_for(tmp_path, '$PPRDIR/../src/c.vh', '<File Path="%s/src/top.v"/>' % tmp_path))
    assert errors == [] and len(warnings) == 1


def test_relative_path_leaving_root_is_refused(tmp_path):
    errors, _ = workspace.check_clonable(cfg_for(tmp_path, '$PPRDIR/../src/c.vh', '<File Path="$PPRDIR/../../lib.v"/>'))
    assert len(errors) == 1 and 'outside project root' in errors[0]


def test_clone_skips_runs_caches_and_output(tmp_path):
    cfg = cfg_for(tmp_path, '$PPRDIR/../src/c.vh')
    for d in ('hw/p.runs/synth_1', 'hw/p.runs/impl_1', 'hw/p.runs/fifo_synth_1', 'hw/p.cache/ip', 'vsweep_runs/x',
              '.git', 'hw/p.gen/ip'):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / 'f').write_text('x')
    ws = workspace.clone(cfg, 0)
    have = {str(p.relative_to(ws.root)) for p in ws.root.rglob('f')}
    assert have == {'hw/p.runs/fifo_synth_1/f', 'hw/p.gen/ip/f'}
    assert ws.map(cfg.vh[0]) == ws.root / 'src' / 'c.vh' and ws.xpr.exists()


def test_absolute_include_dir_holding_the_vh_is_refused(tmp_path):
    cfg = cfg_for(tmp_path, '$PPRDIR/../src/c.vh',
                  '<Config><Option Name="IncludeDirs" Val="%s/src"/></Config>' % tmp_path)
    errors, _ = workspace.check_clonable(cfg)
    assert len(errors) == 1 and 'include' in errors[0]
