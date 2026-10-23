from __future__ import annotations

import pytest
from tenko.db.errors import DatabaseUnavailableError


@pytest.mark.asyncio
async def test_group_setting_repository_keys_groups_by_platform(repositories) -> None:
    setting = repositories["setting"]

    assert await setting.get("50001") is None
    await setting.set_frequency_limitation("50001", False)
    await setting.set_frequency_limitation(("discord", "50001"), False)
    assert await setting.frequency_disabled_groups() == (
        ("discord", "50001"),
        ("qq-client", "50001"),
    )
    await setting.set_frequency_limitation(("onebot", "50001"), True)
    assert await setting.frequency_disabled_groups() == (("discord", "50001"),)
    with pytest.raises(TypeError, match="frequency_limitation"):
        await setting.set_frequency_limitation("50001", 1)


@pytest.mark.asyncio
async def test_repository_without_session_factory_raises_explicit_error() -> None:
    from tenko.db.repositories import PermissionRepository

    repository = PermissionRepository()
    with pytest.raises(DatabaseUnavailableError, match="session 工厂"):
        await repository.load_state()


@pytest.mark.asyncio
async def test_feature_state_repository_round_trip(repositories) -> None:
    from tenko.db.repositories import FeatureStateRecord

    repository = repositories["feature"]

    assert await repository.list_states() == ()
    await repository.replace_states(
        (
            FeatureStateRecord("demo", None, False, True),
            FeatureStateRecord("demo", "40001", False, False),
            FeatureStateRecord("other", "40001", True, False),
        )
    )

    assert await repository.list_states() == (
        FeatureStateRecord("demo", None, False, True),
        FeatureStateRecord("demo", "40001", False, False),
        FeatureStateRecord("other", "40001", True, False),
    )


@pytest.mark.asyncio
async def test_account_state_repository_round_trip(repositories) -> None:
    from tenko.db.repositories import (
        AccountResponseRecord,
        AccountRouteRecord,
        AccountStateSnapshot,
    )

    repository = repositories["account"]

    await repository.replace_state(
        (
            AccountRouteRecord("40001", "10001", 0),
            AccountRouteRecord("40001", "10002", 1),
            AccountRouteRecord("40001", '["discord", "7"]', 0, platform="discord"),
        ),
        (AccountResponseRecord("40001", "deterministic", "10002"),),
    )

    assert await repository.load_state() == AccountStateSnapshot(
        routes=(
            AccountRouteRecord("40001", '["discord", "7"]', 0, platform="discord"),
            AccountRouteRecord("40001", "10001", 0),
            AccountRouteRecord("40001", "10002", 1),
        ),
        responses=(AccountResponseRecord("40001", "deterministic", "10002"),),
    )


@pytest.mark.asyncio
async def test_rate_limit_repository_round_trip(repositories) -> None:
    from tenko.db.repositories import RateLimitEventRecord, RateLimitSubjectRecord

    repository = repositories["rate"]

    await repository.replace_state(
        (RateLimitEventRecord("40001", "20001", 100.0, 2),),
        (RateLimitSubjectRecord("40001", "20001", 105.0, 200.0),),
    )

    snapshot = await repository.load_state()
    assert snapshot.events == (RateLimitEventRecord("40001", "20001", 100.0, 2),)
    assert snapshot.subjects == (
        RateLimitSubjectRecord("40001", "20001", 105.0, 200.0),
    )


@pytest.mark.asyncio
async def test_startup_time_repository_round_trip(repositories) -> None:
    repository = repositories["startup"]

    assert await repository.list_durations() == ()
    await repository.record(2.5)
    await repository.record(1.25)

    assert await repository.list_durations() == (2.5, 1.25)
