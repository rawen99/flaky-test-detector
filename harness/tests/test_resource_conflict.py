"""
Ground truth category: RESOURCE_CONFLICT

Contends for a fixed port. In real suites this appears as "address already in
use" or file-lock errors that have nothing to do with the code under test.
"""
import random
import socket

from flake_config import rate


def test_resource_conflict():
    port = 8765
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if random.random() < rate("resource"):
            raise OSError(f"[simulated] Address already in use: {port}")
        sock.bind(("localhost", port))
    finally:
        sock.close()
