from click.testing import CliRunner

from openhexa.companion.cli import main


def test_cli_runs():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0, result.output
