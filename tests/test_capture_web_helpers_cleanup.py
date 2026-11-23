"""Chrome user-data temp dir cleanup for capture_viewport."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from scripts import capture_web_helpers as cap


def test_capture_viewport_cleans_temp_user_data_on_chrome_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    created: list[Path] = []

    def fake_mkdtemp(*_args, **_kwargs):
        p = tmp_path / f"ud-{len(created)}"
        p.mkdir()
        created.append(p)
        return str(p)

    monkeypatch.setattr(cap.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(cap, "_pick_free_port", lambda: 9222)
    stopped: list[bool] = []

    popen_kwargs: list[dict] = []

    def fake_popen(*_a, **k):
        popen_kwargs.append(k)
        return mock.Mock(poll=lambda: None, pid=4242)

    monkeypatch.setattr(cap.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cap,
        "stop_chrome_process_group",
        lambda proc, **_kw: stopped.append(True),
    )
    monkeypatch.setattr(cap.urllib.request, "urlopen", mock.Mock(side_effect=OSError("no chrome")))

    with pytest.raises(RuntimeError, match="Chrome DevTools not ready"):
        cap.capture_viewport("/bin/false", "http://127.0.0.1:9/", None, 800, 600, settle_s=0.01)

    assert created
    assert stopped
    assert popen_kwargs and popen_kwargs[0].get("start_new_session") is True
    assert not created[0].exists()


def test_capture_viewport_keeps_caller_user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ud = tmp_path / "keep-me"
    ud.mkdir()
    marker = ud / "profile"
    marker.write_text("stay", encoding="utf-8")

    monkeypatch.setattr(cap, "_pick_free_port", lambda: 9222)
    stopped: list[bool] = []

    def fake_popen(*_a, **_k):
        m = mock.Mock(poll=lambda: None, pid=4242)
        return m

    monkeypatch.setattr(cap.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cap,
        "stop_chrome_process_group",
        lambda proc, **_kw: stopped.append(True),
    )
    removed: list[Path] = []
    monkeypatch.setattr(cap, "remove_chrome_user_data_dir", lambda p: removed.append(p))
    monkeypatch.setattr(cap.urllib.request, "urlopen", mock.Mock(side_effect=OSError("no chrome")))

    with pytest.raises(RuntimeError):
        cap.capture_viewport("/bin/false", "http://127.0.0.1:9/", None, 800, 600, settle_s=0.01, user_data=ud)

    assert stopped
    assert not removed
    assert marker.is_file()