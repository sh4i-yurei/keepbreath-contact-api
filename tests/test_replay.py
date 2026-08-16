# Tests for the single-use ALTCHA replay registry (replay.py).
import threading

import replay

FAR_FUTURE = 9_999_999_999


def test_fresh_signature_then_replay(fresh_replay):
    assert replay.try_reserve("sig-1", FAR_FUTURE) is True  # first time wins
    assert replay.try_reserve("sig-1", FAR_FUTURE) is False  # a replay is refused


def test_concurrent_reserves_only_one_wins(fresh_replay):
    # Many threads race to claim the SAME signature at once; the database's UNIQUE
    # constraint must let exactly one succeed (this is what stops a replay under load).
    results: list[bool] = []
    start = threading.Barrier(10)

    def claim():
        start.wait()
        results.append(replay.try_reserve("same-sig", FAR_FUTURE))

    threads = [threading.Thread(target=claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 9
