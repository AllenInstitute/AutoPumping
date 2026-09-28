from pathlib import Path
from pytest import mark
from auto_pumping.config import Config


@mark.parametrize(
    "config_file", list((Path(__file__).parent.parent / "config").glob("*.yaml"))
)
def test_load_config(config_file):
    Config.load_file(config_file)
