"""BOT 账号的展示信息：头像（嵌成 data URI）、昵称和在线/禁言状态。"""

from __future__ import annotations

import asyncio
import base64
import time

import httpx

from tenko.context import QQ_PLATFORM
from tenko.host.accounts import AccountRegistry, account_key, account_reference

_AVATAR_TTL = 3600
_AVATAR_BYTES = 512 * 1024
# 头像地址 -> (取到的时间, data URI 或 None)；失败也缓存，避免每次都等超时。
_avatar_cache: dict[str, tuple[float, str | None]] = {}
# 与面板 platformText 一致。
_PLATFORMS = {"qq-client": "QQ", "onebot": "OneBot 11", "satori": "Satori"}


def bot_user(account: object) -> object | None:
    return getattr(getattr(account, "self_info", None), "user", None)


def avatar_url(account: object) -> str | None:
    url = getattr(bot_user(account), "avatar", None)
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url
    platform, account_id = account_key(account)
    if platform == QQ_PLATFORM and account_id.isdigit():
        return f"https://q1.qlogo.cn/g?b=qq&nk={account_id}&s=100"
    return None


async def fetch_avatar(url: str) -> str | None:
    """下载头像嵌进图片：渲染页面不联网，远程图片加载慢会拖到渲染超时。"""

    now = time.monotonic()
    if (hit := _avatar_cache.get(url)) and now - hit[0] < _AVATAR_TTL:
        return hit[1]
    value = None
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=True) as client:
            response = await client.get(url)
        kind = response.headers.get("content-type", "").split(";")[0].strip()
        if (
            response.is_success
            and kind.startswith("image/")
            and len(response.content) <= _AVATAR_BYTES
        ):
            encoded = base64.b64encode(response.content).decode()
            value = f"data:{kind};base64,{encoded}"
    except httpx.HTTPError:
        pass
    _avatar_cache[url] = (now, value)
    return value


async def bot_avatars(accounts) -> dict[str, str | None]:
    urls = {url for account in accounts if (url := avatar_url(account))}
    found = await asyncio.gather(*(fetch_avatar(url) for url in urls))
    return dict(zip(urls, found, strict=True))


def bot_card(
    registry: AccountRegistry,
    account: object,
    avatars: dict[str, str | None],
    group_id: str | None = None,
) -> dict[str, object]:
    """一个 BOT 的展示数据。

    给出 group_id 时禁言状态按该群计算（带到期时间）；不给时汇总为“禁言 群数/总群数”，
    多个群的到期时间各不相同，不再列出。
    """

    platform, account_id = account_key(account)
    ref = account_reference((platform, account_id))
    name = str(getattr(bot_user(account), "name", None) or ref)
    groups = registry.groups_for_account(account)
    muted = sum(registry.is_muted(account, group) for group in groups)
    if not registry.is_available(account):
        tone, state, note = "off", "离线", ""
    elif group_id is None and muted:
        tone, state, note = "warn", f"禁言 {muted}/{len(groups)}", ""
    elif group_id is not None and registry.is_muted(account, group_id):
        until = registry.mute_until(account, group_id)
        tone, state = "warn", "禁言中"
        note = "永久" if until is None else f"至 {until.astimezone():%m-%d %H:%M}"
    else:
        tone, state, note = "ok", "在线", ""
    return {
        "ref": ref,
        "id": account_id,
        "platform": _PLATFORMS.get(platform.lower(), platform or "unknown"),
        "groups": len(groups),
        "name": name,
        "initial": name[:1],
        "avatar": avatars.get(avatar_url(account) or ""),
        "tone": tone,
        "state": state,
        "state_note": note,
        "chips": [],
    }
