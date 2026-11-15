import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run tests that hit Yahoo/BSE/NSE")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live test: run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Isolated data dir so tests never touch the real cache/db/snapshots."""
    monkeypatch.setenv("FINN_DATA_DIR", str(tmp_path))
    return tmp_path
