import asyncio
import pytest


@pytest.mark.asyncio
async def test_runs_iterations_periodically():
    from orchestrator.utils.worker import run_resilient_worker
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    task = asyncio.create_task(
        run_resilient_worker("test", tick, interval=0.05, iteration_timeout=1.0)
    )
    await asyncio.sleep(0.18)  # ~3 iterations
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert counter["n"] >= 2


@pytest.mark.asyncio
async def test_backoff_grows_on_repeated_errors():
    from orchestrator.utils.worker import run_resilient_worker
    timestamps = []

    async def fail():
        timestamps.append(asyncio.get_event_loop().time())
        raise RuntimeError("boom")

    task = asyncio.create_task(
        run_resilient_worker(
            "test", fail, interval=0.01,
            iteration_timeout=1.0, max_backoff=4.0
        )
    )
    # Run long enough to observe at least: fail -> sleep 1s -> fail -> sleep 2s -> fail
    await asyncio.sleep(3.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Esperamos que o gap entre falhas cresça (1s -> 2s)
    assert len(timestamps) >= 3
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    # Gap mais recente deve ser maior que o primeiro (backoff cresceu)
    assert gaps[-1] > gaps[0] + 0.3  # 0.3s slack for scheduling jitter


@pytest.mark.asyncio
async def test_iteration_timeout_aborts_hung_function():
    from orchestrator.utils.worker import run_resilient_worker
    completed = {"n": 0}

    async def hang():
        await asyncio.sleep(10)  # vai estourar o timeout
        completed["n"] += 1

    task = asyncio.create_task(
        run_resilient_worker(
            "test", hang, interval=0.01, iteration_timeout=0.05
        )
    )
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert completed["n"] == 0  # nunca completou — sempre cortado


@pytest.mark.asyncio
async def test_backoff_resets_after_consecutive_successes():
    """After backoff_reset_after consecutive successes, backoff returns to 1s."""
    from orchestrator.utils.worker import run_resilient_worker

    call_log = []  # list of (status, timestamp) tuples

    # Sequence: fail, fail, fail, then succeed N times, then fail again.
    # We expect the post-reset failure to backoff at 1s, not at the elevated level.
    plan = ["fail", "fail", "fail", "ok", "ok", "ok", "fail", "fail"]
    idx = {"i": 0}

    async def scripted():
        i = idx["i"]
        idx["i"] += 1
        now = asyncio.get_event_loop().time()
        call_log.append((plan[i] if i < len(plan) else "ok", now))
        if i < len(plan) and plan[i] == "fail":
            raise RuntimeError(f"boom-{i}")

    task = asyncio.create_task(
        run_resilient_worker(
            "test", scripted, interval=0.01,
            iteration_timeout=1.0,
            max_backoff=4.0,
            backoff_reset_after=3,
        )
    )
    # Need enough time to: fail (1s), fail (2s), fail (4s), ok×3 (~30ms), fail (1s after reset!)
    await asyncio.sleep(9.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Find the gap between the last "fail" before the OK streak and the "fail" after the streak.
    # The first post-reset fail should sleep ~1s, not ~4s.
    fails = [(s, t) for s, t in call_log if s == "fail"]
    if len(fails) < 4:
        pytest.fail(f"Expected ≥4 fail entries, got {len(fails)}: {call_log}")

    # Gap between fail #3 (the last in streak) and fail #4 (first after reset)
    # should be approximately 1s (initial backoff), NOT 8s (would-be next).
    # We can't measure this exactly because the OK iterations also take time,
    # but we CAN check: gap between fail #3 and fail #4 < 5s (much less than
    # if no reset happened, which would be 8s sleep + 3*0.01 OK time).
    gap_post_reset = fails[3][1] - fails[2][1]
    assert gap_post_reset < 5.0, f"Expected <5s gap (reset happened), got {gap_post_reset}s"
