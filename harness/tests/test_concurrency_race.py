"""
Ground truth category: CONCURRENCY_RACE

An unsynchronised shared counter updated by several threads. The
read-modify-write is not atomic, so an increment is occasionally lost when two
threads interleave. Only a fraction of threads pause between the read and the
write, so the loss is intermittent -- the defining property of a race.
"""
import random
import threading
import time

from flake_config import rate

_counter = {"value": 0}
THREADS = 8


def test_concurrency_race():
    _counter["value"] = 0
    pause_probability = rate("concurrency") * 0.35

    def increment():
        current = _counter["value"]
        if random.random() < pause_probability:
            time.sleep(0.001)       # occasionally lose the race
        _counter["value"] = current + 1

    threads = [threading.Thread(target=increment) for _ in range(THREADS)]
    for t in threads:
        t.start()
        time.sleep(0.0005)          # stagger starts so collisions are occasional
    for t in threads:
        t.join()

    assert _counter["value"] == THREADS
