from __future__ import annotations

import pytest
from tenko.db.errors import (
    DatabaseIdentifierError,
    DatabaseUnavailableError,
    InvalidGroupSettingError,
)


@pytest.mark.asyncio
async def test_group_setting_repository_supports_defaults_partial_updates_and_validation(
    repositories,
) -> None:
    setting = repositories["setting"]

    assert await setting.get(50001) is None
    await setting.set(50001, frequency_limitation=False, response_type="deterministic")
    row = await setting.get("50001")
    assert row is not None
    assert (row.frequency_limitation, row.response_type) == (False, "deterministic")

    await setting.set_response_type(50001, "random")
    assert await setting.frequency_disabled_groups() == (50001,)
    await setting.set_frequency_limitation(50001, True)
    assert await setting.frequency_disabled_groups() == ()
    row = await setting.get(50001)
    assert row is not None
    assert (row.frequency_limitation, row.response_type) == (True, "random")

    with pytest.raises(InvalidGroupSettingError):
        await setting.set_response_type(50001, "fixed")
    with pytest.raises(TypeError, match="frequency_limitation"):
        await setting.set_frequency_limitation(50001, 1)
    with pytest.raises(DatabaseIdentifierError, match="纯数字"):
        await setting.get("not-a-group")


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
        ),
        (AccountResponseRecord("40001", "deterministic", "10002"),),
    )

    assert await repository.load_state() == AccountStateSnapshot(
        routes=(
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
