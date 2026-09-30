from pytest import fixture
from pathlib import Path
from auto_pumping.config import Config

config_dir = Path(__file__).parent.parent / "config"


@fixture(scope="session")
def config():
    return Config.load_file(config_dir / "config.yaml")
