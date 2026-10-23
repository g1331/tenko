"""稳定插件 API：只读账号信息。"""

from satori.client import Account

from tenko.host.accounts import account_key, account_registry
from tenko.host.perm import permission_store

__all__ = ["Account", "accounts_for_group", "online_accounts"]


def online_accounts() -> tuple[Account, ...]:
    """返回当前在线（已连接且未被停用）的全部账号。"""

    return tuple(
        account
        for account in account_registry.accounts.values()
        if account_registry.is_available(account)
    )


def accounts_for_group(group_id: str | int) -> tuple[Account, ...]:
    """返回在该群可响应的账号：在线、未停用、未被该群禁言，且群不在黑名单。"""

    return tuple(
        account
        for account in account_registry.accounts_for_group(group_id)
        if not permission_store.blacklisted(
            "group", account_key(account)[0], str(group_id)
        )
    )
