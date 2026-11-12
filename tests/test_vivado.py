import pytest

from vivado_sweeper import vivado as v


def test_windows_detection():
    assert v.is_windows_vivado('/mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat')
    assert v.is_windows_vivado('/mnt/d/tools/vivado')
    assert not v.is_windows_vivado('/tools/Xilinx/Vivado/2024.2/bin/vivado')
    assert not v.is_windows_vivado('vivado')


def test_path_conversion():
    assert v.to_windows('/mnt/c/Users/me/proj/a.xpr') == 'C:/Users/me/proj/a.xpr'
    assert v.to_windows('/mnt/d') == 'D:/'
    assert v.from_windows('C:\\Xilinx\\2025.1\\Vivado\\bin\\vivado.bat') == '/mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat'
    assert v.from_windows('D:/x/y') == '/mnt/d/x/y'
    assert v.from_windows('/home/me') == '/home/me'


def test_linux_side_path_goes_through_wslpath_or_distro(tmp_path, monkeypatch):
    monkeypatch.setattr(v.shutil, 'which', lambda name: None)          # no wslpath: use the distro name
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    assert v.to_windows(str(tmp_path / 'not' / 'yet')) == '//wsl.localhost/Ubuntu%s/not/yet' % tmp_path


def test_command_lines():
    lin = v.Vivado('/tools/Xilinx/Vivado/2024.2/bin/vivado')
    assert lin.command('/r/b.tcl', '/r/v.log') == ['/tools/Xilinx/Vivado/2024.2/bin/vivado', '-mode', 'batch',
                                                    '-notrace', '-nojournal', '-log', '/r/v.log', '-source', '/r/b.tcl']
    win = v.Vivado('/mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat')
    cmd = win.command('/mnt/c/p/b.tcl', '/mnt/c/p/v.log')
    assert cmd[:3] == ['cmd.exe', '/c', 'C:\\Xilinx\\2025.1\\Vivado\\bin\\vivado.bat']
    assert cmd[-3:] == ['C:/p/v.log', '-source', 'C:/p/b.tcl']


def test_windows_vivado_refuses_a_linux_cwd():
    win = v.Vivado('/mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat')
    win.check_cwd('/mnt/c/work')
    with pytest.raises(v.VivadoError, match='Windows drive'):
        win.check_cwd('/home/me/proj')
    v.Vivado('/usr/bin/vivado').check_cwd('/home/me/proj')
