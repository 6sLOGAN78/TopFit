import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_worker_heartbeats_and_exits_cleanly(sig: signal.Signals) -> None:
    env = {
        **os.environ,
        "WORKER_HEARTBEAT_SECONDS": "0.2",
        "DATABASE_URL": "postgresql+psycopg://x:y@127.0.0.1:1/none",
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "ranker.worker"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    seen: list[str] = []
    deadline = time.monotonic() + 3
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            seen.append(line)
            if "worker_heartbeat" in line:
                break
        assert any("worker_started" in ln for ln in seen), seen
        assert any("worker_heartbeat" in ln for ln in seen), seen

        proc.send_signal(sig)
        rest, _ = proc.communicate(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    assert proc.returncode == 0
    assert "worker_stopped" in rest
