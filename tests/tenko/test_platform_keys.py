"""#234：群状态按“平台 + 群 ID”区分，裸群号沿用 QQ。"""

from __future__ import annotations

from types import SimpleNamespace

from tenko.host.accounts import AccountRegistry
from tenko.host.features import FeatureService
from tenko.host.ratelimit import RateLimitService


def test_same_group_id_on_two_platforms_is_independent() -> None:
    features = FeatureService()
    features.disable("demo", ("discord", "40001"))
    assert features.is_enabled("demo", "40001")
    assert not features.is_enabled("demo", ("discord", "40001"))
    assert features.state["demo"]["groups"] == {'["discord", "40001"]': False}
    # 引用字符串能还原成同一个键，面板回传时不丢平台。
    assert not features.is_enabled("demo", '["discord", "40001"]')

    limiter = RateLimitService(max_weight=2, cooldown_seconds=10)
    limiter.set_group_limited(("onebot", "40001"), False)
    assert not limiter.is_group_limited("40001")
    assert limiter.is_group_limited(("discord", "40001"))
    assert not limiter.check(("discord", "40001"), "1", weight=2).allowed
    assert limiter.check("40001", "1", weight=2).allowed

    registry = AccountRegistry()
    qq = SimpleNamespace(platform="qq-client", self_id="10001")
    bot = SimpleNamespace(platform="discord", self_id="10001")
    registry.register(qq, available=True, groups=["40001"])
    registry.register(bot, available=True, groups=[("discord", "40001")])
    assert registry.accounts_for_group("40001") == (qq,)
    assert registry.accounts_for_group(("discord", "40001")) == (bot,)
    assert registry.groups_for_account(bot) == ('["discord", "40001"]',)
