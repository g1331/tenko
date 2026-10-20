from __future__ import annotations

import pytest

from tenko.host.ratelimit import RateLimitService


@pytest.mark.asyncio
async def test_rate_limit_blocks_during_cooldown_and_recovers_after_window(
    repositories,
) -> None:
    now = [100.0]
    service = RateLimitService(
        repositories["rate"],
        window_seconds=5,
        max_weight=3,
        cooldown_seconds=2,
        blacklist_seconds=0,
        clock=lambda: now[0],
    )
    await service.initialize()

    assert (await service.check_and_persist("40001", "20001")).allowed
    assert (await service.check_and_persist("40001", "20001")).allowed
    limited = await service.check_and_persist("40001", "20001")
    assert not limited.allowed
    assert limited.retry_after == 2

    now[0] += 1
    assert not (await service.check_and_persist("40001", "20001")).allowed
    now[0] += 5
    assert (await service.check_and_persist("40001", "20001")).allowed


@pytest.mark.asyncio
async def test_rate_limit_blacklist_survives_service_recreation(repositories) -> None:
    now = [100.0]
    repository = repositories["rate"]
    service = RateLimitService(
        repository,
        max_weight=2,
        cooldown_seconds=0,
        blacklist_seconds=30,
        clock=lambda: now[0],
    )
    await service.initialize()

    assert (await service.check_and_persist("40001", "20001")).allowed
    assert not (await service.check_and_persist("40001", "20001")).allowed
    assert service.is_blacklisted("40001", "20001")

    restored = RateLimitService(
        repository,
        max_weight=2,
        cooldown_seconds=0,
        blacklist_seconds=30,
        clock=lambda: now[0],
    )
    await restored.initialize()
    assert not (await restored.check_and_persist("40001", "20001")).allowed

    now[0] += 31
    assert not restored.is_blacklisted("40001", "20001")
    restored.clear("40001", "20001")
    await restored.persist_state()
    assert (await restored.check_and_persist("40001", "20001")).allowed


@pytest.mark.asyncio
async def test_rate_limit_database_failure_intercepts_commands() -> None:
    class FailingRepository:
        async def load_state(self):
            raise RuntimeError("database offline")

    service = RateLimitService(FailingRepository())

    with pytest.raises(RuntimeError, match="database offline"):
        await service.initialize()

    assert not service.ready
    decision = await service.check_and_persist("40001", "20001")
    assert not decision.allowed
    assert "暂不可用" in (decision.message or "")
    assert service.is_blacklisted("40001", "20001")


@pytest.mark.asyncio
async def test_rate_limit_write_failure_keeps_memory_state_and_retries() -> None:
    from tenko.db.repositories import RateLimitStateSnapshot

    class FlakyRepository:
        def __init__(self) -> None:
            self.fail = True
            self.writes = 0

        async def load_state(self):
            return RateLimitStateSnapshot(events=(), subjects=())

        async def replace_state(self, events, subjects):
            del events, subjects
            if self.fail:
                raise RuntimeError("database write failed")
            self.writes += 1

    repository = FlakyRepository()
    service = RateLimitService(
        repository,
        max_weight=2,
        cooldown_seconds=0,
        blacklist_seconds=30,
        clock=lambda: 100.0,
    )
    await service.initialize()

    assert (await service.check_and_persist("40001", "20001")).allowed
    limited = await service.check_and_persist("40001", "20001")

    # 写失败不让服务整体失效，黑名单仍在内存中生效。
    assert service.ready
    assert not limited.allowed
    assert service.is_blacklisted("40001", "20001")

    repository.fail = False
    await service.check_and_persist("40001", "20001")
    assert repository.writes == 1
    await service.check_and_persist("40001", "20001")
    assert repository.writes == 1


@pytest.mark.asyncio
async def test_rate_limit_prunes_idle_users_and_skips_plain_command_writes() -> None:
    from tenko.db.repositories import RateLimitStateSnapshot

    class CountingRepository:
        def __init__(self) -> None:
            self.writes = 0

        async def load_state(self):
            return RateLimitStateSnapshot(events=(), subjects=())

        async def replace_state(self, events, subjects):
            del events, subjects
            self.writes += 1

    now = [100.0]
    repository = CountingRepository()
    service = RateLimitService(
        repository, window_seconds=5, max_weight=10, clock=lambda: now[0]
    )
    await service.initialize()

    await service.check_and_persist("40001", "idle")
    now[0] += 6
    await service.check_and_persist("40001", "active")

    assert ("40001", "idle") not in service._events
    assert repository.writes == 0


@pytest.mark.asyncio
async def test_rate_limit_unavailable_does_not_silently_accept_writes() -> None:
    from tenko.db.errors import DatabaseUnavailableError

    service = RateLimitService()
    service.mark_unavailable()

    with pytest.raises(DatabaseUnavailableError, match="暂不可用"):
        await service.persist_state()
