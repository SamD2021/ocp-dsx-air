import subprocess
import sys


def test_workflow_module_import_has_no_output_or_external_work() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import ocp_dsx_air.core.workflows"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
