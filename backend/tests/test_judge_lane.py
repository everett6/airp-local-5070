import asyncio
import time

import pytest

from app.sandbox.judge_lane import Lane, budget, run_overlapped


class FakeJudge:
    def __init__(self, s):
        self.s, self.active, self.peak = s, 0, 0

    async def __call__(self, system, user):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.s)
        self.active -= 1
        return "{}"


def company(lane, model_calls=2, lookup_s=0.06):
    async def work(_i, _row):
        for _ in range(model_calls):
            async with budget(1.0):
                await lane("s", "u")
        await asyncio.sleep(lookup_s)  # web lookup: the GPU is idle
    return work


def wall(width):
    fake = FakeJudge(0.03)
    lane = Lane(fake)
    t = time.monotonic()
    asyncio.run(run_overlapped(list(range(4)), company(lane), width))
    return time.monotonic() - t, fake.peak


def test_two_at_once_overlaps_lookups_but_never_two_model_calls():
    alone, peak1 = wall(1)
    both, peak2 = wall(2)
    assert peak1 == peak2 == 1
    assert alone / both > 1.3  # 4 x (2 x 0.03 + 0.06) = 0.48 s alone; lookups hidden when two run


def test_waiting_for_the_lane_does_not_use_up_the_budget():
    async def main():
        lane = Lane(FakeJudge(0.3))

        async def call():
            async with budget(0.4):  # each call alone takes 0.3 s; the second waits 0.3 s first
                return await lane("s", "u")
        return await asyncio.gather(call(), call(), call())
    assert asyncio.run(main()) == ["{}"] * 3


def test_a_slow_call_still_times_out():
    async def main():
        async with budget(0.05):
            await Lane(FakeJudge(0.3))("s", "u")
    with pytest.raises(TimeoutError):
        asyncio.run(main())


def test_every_row_finishes_before_an_error_is_raised():
    done = []

    async def work(i, _row):
        await asyncio.sleep(0.01 * i)
        if i == 0:
            raise ValueError("boom")
        done.append(i)
    with pytest.raises(ValueError):
        asyncio.run(run_overlapped([0, 1, 2], work, 2))
    assert done == [1, 2]
