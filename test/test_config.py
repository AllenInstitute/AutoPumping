from pathlib import Path
from pytest import mark, raises
from auto_pumping.config import Config


@mark.parametrize(
    "config_file", list((Path(__file__).parent.parent / "config").glob("*.yaml"))
)
def test_load_config(config_file):
    Config.load_file(config_file)


def test_actuation_timeout_defaults_to_30_seconds(config):
    assert config.actuation_timeout == 30


def test_actuation_timeout_must_be_positive(config):
    with raises(ValueError):
        Config.model_validate(config.model_dump() | {"actuation_timeout": 0})
