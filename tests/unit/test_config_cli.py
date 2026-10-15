import json

import pytest
from pydantic import ValidationError

from mtunifi.config import Settings
from mtunifi.main import main


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@host/inform",
        "file:///inform",
        "http://host/api",
        "http://host/inform?key=secret",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(ValidationError):
        Settings(inform_url=url)


def test_diagnostics_and_read_only_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MTUNIFI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["mtunifi", "health"])
    assert main() == 2
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr("sys.argv", ["mtunifi", "init"])
    assert main() == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["mtunifi", "diagnose"])
    assert main() == 0
    public = json.loads(capsys.readouterr().out)
    assert public["synthetic_ports"] == 8
    assert not public["controller_adoption_verified"]
    assert "authkey" not in public
