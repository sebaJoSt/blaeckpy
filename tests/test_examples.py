"""Every example runs: started on a free TCP port, a host connects, asks for the catalogs and starts
data and entities, and gets valid frames back while the example keeps running."""

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from blaeckpy.protocol import envelope

EXAMPLES = Path(__file__).parent.parent / "examples"
SCRIPTS = sorted(EXAMPLES.glob("*.py"))

# Runs an example with its fixed TCP port swapped for a free one, which it prints.
LAUNCHER = """
import runpy, socket, sys
create_server = socket.create_server
def on_free_port(address, *args, **kwargs):
    server = create_server((address[0], 0), *args, **kwargs)
    print("TCP port", server.getsockname()[1], flush=True)
    return server
socket.create_server = on_free_port
runpy.run_path(sys.argv[1], run_name="__main__")
"""


def frames_in(data: bytes) -> list:
    out = []
    for chunk in data.split(envelope.END)[:-1]:
        start = chunk.find(envelope.START)
        if start >= 0:
            out.append(envelope.open_frame(chunk[start:] + envelope.END))
    return out


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_example_runs_and_answers_a_host(script):
    process = subprocess.Popen([sys.executable, "-u", "-c", LAUNCHER, str(script)], cwd=EXAMPLES,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        port = int(re.search(r"TCP port (\d+)", line).group(1))
        host = socket.create_connection(("127.0.0.1", port), timeout=3)
        host.sendall(b"<BLAECK.GET_DEVICES>")
        time.sleep(0.3)
        for command in (b"<BLAECK.WRITE_ENTITIES>", b"<BLAECK.DATA_START>",
                        b"<BLAECK.ENTITIES_START>", b"<BLAECK.INTERVAL_START,100>"):
            host.sendall(command)
            time.sleep(0.3)
        time.sleep(0.5)
        # Data keeps coming while the interval runs, so read for a fixed time, not until quiet.
        host.settimeout(0.1)
        data, end = b"", time.monotonic() + 0.5
        while time.monotonic() < end:
            try:
                chunk = host.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                break
            data += chunk
        host.close()
        assert process.poll() is None, process.stderr.read()
        keys = [f.key for f in frames_in(data)]
        assert keys.count(0xA5) == 5, keys
        assert 0xB7 in keys and 0x90 in keys
        # Data frames come only from an example that logs signals.
        assert (0xD3 in keys) == ("add_signal(" in script.read_text(encoding="utf-8")), keys
    finally:
        process.kill()
        process.wait()
