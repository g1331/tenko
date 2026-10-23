from __future__ import annotations

from collections.abc import Iterable

import pytest
from arclet.alconna import Alconna, command_manager

from tenko.commands import configure_command_prefix


_POSITIVE_CASES: dict[str, dict[str, tuple[str, ...]]] = {
    "清除插件数据": {
        "positive": ("/清除插件数据 example",),
        "boundary": ("/清除插件数据-1", "/ 清除插件数据"),
    },
    "帮助": {
        "positive": ("/帮助", "/帮助 1"),
        "boundary": ("/帮助-1", "/ 帮助"),
    },
    "群设置": {
        "positive": ("/群设置", "/群设置 --group 40001"),
        "boundary": ("/群设置-1", "/ 群设置"),
    },
    "频率限制": {
        "positive": ("/频率限制", "/频率限制 开启", "/频率限制 关闭"),
        "boundary": ("/频率限制-1", "/ 频率限制"),
    },
    "禁言": {
        "positive": ("/禁言 20002 5", "/禁言 -t 5 20002"),
        "boundary": ("/禁言-1", "/ 禁言"),
    },
    "解禁": {
        "positive": ("/解禁 20002", "/解禁"),
        "boundary": ("/解禁-1", "/ 解禁"),
    },
    "解禁自己": {
        "positive": ("/解禁自己",),
        "boundary": ("/解禁自己-1", "/ 解禁自己"),
    },
    "开启": {
        "positive": ("/开启 demo",),
        "boundary": ("/开启-1", "/ 开启"),
    },
    "关闭": {
        "positive": ("/关闭 demo",),
        "boundary": ("/关闭-1", "/ 关闭"),
    },
    "全体禁言": {
        "positive": ("/全体禁言",),
        "boundary": ("/全体禁言-1", "/ 全体禁言"),
    },
    "全体解禁": {
        "positive": ("/全体解禁",),
        "boundary": ("/全体解禁-1", "/ 全体解禁"),
    },
    "撤回": {
        "positive": ("/撤回",),
        "boundary": ("/撤回-1", "/ 撤回"),
    },
    "加精": {
        "positive": ("/加精", "/加精 60001", "/设精"),
        "boundary": ("/加精-1", "/ 加精"),
    },
    "踢出": {
        "positive": ("/踢出 20002", "/踢出"),
        "boundary": ("/踢出-1", "/ 踢出"),
    },
    "同意邀请": {
        "positive": ("/同意邀请 request-1",),
        "boundary": ("/同意邀请", "/ 同意邀请"),
    },
    "拒绝邀请": {
        "positive": ("/拒绝邀请 request-1", "/拒绝邀请 request-1 理由"),
        "boundary": ("/拒绝邀请", "/ 拒绝邀请"),
    },
    "待审邀请": {
        "positive": ("/待审邀请",),
        "boundary": ("/待审邀请-1", "/ 待审邀请"),
    },
    "退群": {
        "positive": ("/退群 40001 10001", "/quit 40001 10001"),
        "boundary": ("/退群-1", "/ 退群"),
    },
    "重置能力": {
        "positive": ("/重置能力", "/重置能力 10001"),
        "boundary": ("/重置能力-1", "/ 重置能力"),
    },
    "公告": {
        "positive": ("/公告 帮助系统 维护通知", "/公告 帮助系统 维护 通知 -t 2"),
        "boundary": ("/公告-1", "/ 公告"),
    },
    "BOT列表": {
        "positive": ("/BOT列表",),
        "boundary": ("/BOT列表-1", "/ BOT列表"),
    },
    "BOT群列表": {
        "positive": ("/BOT群列表", "/BOT群列表 10001"),
        "boundary": ("/BOT群列表-1", "/ BOT群列表"),
    },
    "在线BOT": {
        "positive": ("/在线BOT",),
        "boundary": ("/在线BOT-1", "/ 在线BOT"),
    },
    "授权": {
        "positive": ("/授权 群管理 30001", "/授权 群管理 30001 30002 -g 40001"),
        "boundary": ("/授权-1", "/ 授权"),
    },
    "取消授权": {
        "positive": ("/取消授权 30001", "/取消授权 30001 --group 40001"),
        "boundary": ("/取消授权-1", "/ 取消授权"),
    },
    "权限列表": {
        "positive": ("/权限列表", "/权限列表 --group 40001"),
        "boundary": ("/权限列表-1", "/ 权限列表"),
    },
    "拉黑": {
        "positive": ("/拉黑 30001", "/拉黑 30001 30002 -r 刷屏"),
        "boundary": ("/拉黑-1", "/ 拉黑"),
    },
    "解除拉黑": {
        "positive": ("/解除拉黑 30001", "/解除拉黑 30001 30002"),
        "boundary": ("/解除拉黑-1", "/ 解除拉黑"),
    },
    "黑名单": {
        "positive": ("/黑名单", "/黑名单 "),
        "boundary": ("/黑名单-1", "/ 黑名单"),
    },
    "拉黑群": {
        "positive": ("/拉黑群", "/拉黑群 40001 -r 广告"),
        "boundary": ("/拉黑群-1", "/ 拉黑群"),
    },
    "解除拉黑群": {
        "positive": ("/解除拉黑群 40001",),
        "boundary": ("/解除拉黑群-1", "/ 解除拉黑群"),
    },
    "群黑名单": {
        "positive": ("/群黑名单", "/群黑名单 "),
        "boundary": ("/群黑名单-1", "/ 群黑名单"),
    },
    "BOT管理": {
        "positive": ("/BOT管理 添加 30001", "/BOT管理 删除 30001"),
        "boundary": ("/BOT管理-1", "/ BOT管理"),
    },
    "BOT管理列表": {
        "positive": ("/BOT管理列表", "/BOT管理列表 "),
        "boundary": ("/BOT管理列表-1", "/ BOT管理列表"),
    },
    "状态": {
        "positive": ("/状态", "/状态 -t"),
        "boundary": ("/状态-1", "/ -bot", "/-bot"),
    },
    "设定响应": {
        "positive": ("/设定响应", "/设定响应 random", "/设定响应 deterministic"),
        "boundary": ("/设定响应-1", "/ 设定响应"),
    },
    "指定BOT": {
        "positive": ("/指定BOT 10001", "/指定BOT 清除"),
        "boundary": ("/指定BOT-1", "/ 指定BOT"),
    },
    "安装插件": {
        "positive": (
            "/安装插件 tenko-plugin-example",
            "/安装插件 https://github.com/o/r@v1",
        ),
        "boundary": ("/安装插件-1", "/ 安装插件"),
    },
    "更新插件": {
        "positive": ("/更新插件", "/更新插件 example"),
        "boundary": ("/更新插件-1", "/ 更新插件"),
    },
    "卸载插件": {
        "positive": ("/卸载插件 example", "/卸载插件 example 清除数据"),
        "boundary": ("/卸载插件-1", "/ 卸载插件"),
    },
    "回滚插件": {
        "positive": ("/回滚插件",),
        "boundary": ("/回滚插件-1", "/ 回滚插件"),
    },
    "同步插件环境": {
        "positive": ("/同步插件环境",),
        "boundary": ("/同步插件环境-1", "/ 同步插件环境"),
    },
    "插件列表": {
        "positive": ("/插件列表",),
        "boundary": ("/插件列表-1", "/ 插件列表"),
    },
    "插件市场": {
        "positive": ("/插件市场", "/插件市场 工具"),
        "boundary": ("/插件市场-1", "/ 插件市场"),
    },
    "插件详情": {
        "positive": ("/插件详情 example",),
        "boundary": ("/插件详情-1", "/ 插件详情"),
    },
    "启用插件": {
        "positive": ("/启用插件 example",),
        "boundary": ("/启用插件-1", "/ 启用插件"),
    },
    "停用插件": {
        "positive": ("/停用插件 example",),
        "boundary": ("/停用插件-1", "/ 停用插件"),
    },
}


def _parse(command: Alconna, text: str):
    try:
        return command.parse(text)
    except Exception as exc:  # pragma: no cover - 将解析器回归转换为断言输出
        pytest.fail(f"解析 {text!r} 时不应抛出 {exc!r}")


def _commands_for_loaded_plugin() -> Iterable[Alconna]:
    return command_manager.get_commands()


@pytest.mark.parametrize(
    "loaded_plugin",
    [
        "helper",
        "group_manager",
        "perm_manager",
        "status",
        "announcement",
        "feature_manager",
        "response_manager",
        "plugin_manager",
    ],
    indirect=True,
)
def test_every_required_command_has_positive_negative_and_boundary_matrix(
    loaded_plugin,
) -> None:
    commands = list(_commands_for_loaded_plugin())
    assert commands

    for command in commands:
        case = _POSITIVE_CASES[command.command]
        assert command.prefixes == ["/"]

        for text in case["positive"]:
            assert _parse(command, text).matched, (command.command, text)

        negative = (
            case["positive"][0].removeprefix("/"),
            "HELP",
            f"/{command.command}x",
            f"请{command.command}我",
        )
        if command.command in {"同意邀请", "拒绝邀请"}:
            # request_id 是不透明的平台标记，可以合法地紧邻命令头；不要将该
            # token 当作命令边界断言。
            negative = tuple(
                text for text in negative if text != f"/{command.command}x"
            )
        for text in negative:
            assert not _parse(command, text).matched, (command.command, text)

        for text in case["boundary"]:
            result = _parse(command, text)
            if command.command == "帮助" and text == "/帮助-1":
                assert result.matched
                assert loaded_plugin.build_help(result.main_args["index"]) == (
                    "编号不在范围内~"
                )
            else:
                assert not result.matched, (command.command, text)


@pytest.mark.parametrize("loaded_plugin", ["helper"], indirect=True)
def test_help_list_and_out_of_range_detail_use_native_prefix(loaded_plugin) -> None:
    # 该 fixture 只加载 helper，因此其原生注册表是确定的。
    assert "/帮助" in loaded_plugin.build_help()
    assert loaded_plugin.build_help(0) == "编号不在范围内~"
    assert loaded_plugin.build_help(99999999999999999999) == "编号不在范围内~"
    assert not _parse(loaded_plugin.help_command, "/帮助 abc").matched
    assert _parse(loaded_plugin.help_command, "/帮助 0").matched
    assert _parse(loaded_plugin.help_command, "/帮助 99999999999999999999").matched


def test_command_prefix_configuration_is_applied_before_alconna_construction() -> None:
    configure_command_prefix(["!"])
    command = Alconna("临时前缀命令")
    try:
        assert command.prefixes == ["!"]
        assert command.parse("!临时前缀命令").matched
        assert not command.parse("临时前缀命令").matched
    finally:
        command_manager.delete(command)
        configure_command_prefix()
