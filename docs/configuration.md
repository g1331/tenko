# 配置参考

配置读取自 tenko/config.py。未写出的配置项使用代码中的默认值；配置表中的
路径相对于启动目录解析。WebUI 的 `[webui]` 配置见 [WebUI 管理面板](webui.md)，
插件目录的 `[registry]` 见 [扩展插件](plugins.md)。

## 最小配置

config/tenko.toml 可以从 `config/tenko.toml.example` 复制后按需补充。下面的配置覆盖连接、权限、
数据库、渲染和升级的基本入口；所有敏感字段和账号标识都是占位符：

    [basic]
    prefix = ["/"]
    ignore_self_message = true
    skip_req_missing = false
    superusers = { qq-client = ["<QQ_ID>"] }

    [basic.log]
    level = "INFO"
    # save = { rotation = "00:00", compression = "gz", colorize = false }

    [onebot]
    listen_host = "127.0.0.1"
    listen_port = 8080
    reverse_ws_prefix = "/"
    reverse_ws_path = "onebot/v11"
    reverse_ws_endpoint = "ws"
    access_token = "<ONEBOT_ACCESS_TOKEN>"
    api_timeout = 60

    [debug]
    enabled = false

    [database]
    url = "sqlite+aiosqlite:///./.tenko/tenko.db"
    echo = false
    create_table_at = "preparing"

    [render]
    timeout = 10.0
    width = 800
    quality = 85
    device_scale_factor = 2

    [upgrade]
    enabled = true
    source = "git_tag"
    repository = "https://github.com/g1331/tenko.git"
    tag_prefix = "v"
    channel = "stable"
    policy = "check"

    [notify]
    # 通知群：启动通知、异常报告等通知的默认目标；留空表示私聊 Master。
    group = ""

不要把真实 token、平台用户标识、GitHub 凭据或其他密钥写入版本库。

## 通知

启动通知、自动回滚通知、异常报告、好友申请、入群邀请、群管理降级提示、插件更新提醒，
以及账号掉线、被 QQ 下线和恢复在线的提醒，都按同一套通知设置发送：

- **发往**：私聊全部 Master（`[basic].superusers`），或发到通知群。
- **发送账号**：指定一个 bot 账号负责发通知；它不在线时改用其他在线账号。不指定时任选在线账号。

这两项在面板“设置 > 通知”或由 Master 私聊 `/通知设置` 修改，保存后立即生效；
`/通知设置 测试` 会按当前设置发一条测试通知。私聊时 Master 需要是发送账号的好友，
发往通知群时发送账号需要在群里。

账号断开连接超过 1 分钟仍未恢复时提醒一次，恢复后再告知离线时长；协议端报告 QQ 被下线
（NapCat 的 `bot_offline`）时立即提醒。在面板里停用账号引起的断开不提醒。

| `[notify]` 字段 | 说明 |
| --- | --- |
| group | 面板或命令保存通知设置之前的默认通知群；留空或省略时默认私聊 Master |

`superusers` 和 `[notify] group` 至少要配置一个，否则 Tenko 拒绝启动（`--dry-run` 同样检查）。
旧版写在顶层的 `notify_group` 已移除，留在配置里会直接报错，请改写到 `[notify]` 节。

## [onebot]

| 字段 | 说明 |
| --- | --- |
| listen_host | 反向 WebSocket 监听地址，默认 127.0.0.1 |
| listen_port | 监听端口，默认 8080 |
| reverse_ws_prefix | 反向 WebSocket 路径前缀，默认 / |
| reverse_ws_path | OneBot 11 路径，默认 onebot/v11 |
| reverse_ws_endpoint | WebSocket 端点名，默认 ws |
| access_token | 可选的 OneBot 访问 token；不设置时可留空 |
| api_timeout | OneBot action 超时时间，默认 60 秒 |

旧版的 `satori_host`、`satori_path`、`satori_token` 已不再使用：OneBot 事件在进程内直接交给
Entari，内部接口的令牌每次启动随机生成。配置里留着这几项也能启动，只会打印一条弃用提示。

旧版的 `[onebot.capability_overrides]` 已移除，按账号覆盖能力改在面板“账户”页设置。

## [basic] 与 [debug]

| 配置 | 说明 |
| --- | --- |
| basic.prefix | Entari 官方命令前缀列表；默认兼容 Tenko 的 `/`，可以配置多个前缀 |
| basic.ignore_self_message | 是否忽略机器人自己发送的消息，默认开启 |
| basic.skip_req_missing | 是否跳过缺少依赖的事件监听器，默认关闭 |
| basic.superusers | 超级用户唯一权威来源，格式为“平台名到用户 ID 列表”的映射 |
| basic.log.level | 日志级别，默认 INFO |
| basic.log.ignores | 要忽略的日志记录器名称列表，支持通配符 |
| basic.log.save | 日志保存与轮转；默认 `None`，不落盘 |
| basic.log.save.rotation | 轮转时间或文件大小，例如 `00:00` 或 `10 MB` |
| basic.log.save.compression | 压缩格式，例如 `gz`；留空表示不压缩 |
| basic.log.save.colorize | 是否在文件中保留颜色，默认由官方模型开启 |
| debug.enabled | 是否只处理 debug.masters 中用户产生的事件，默认关闭 |
| debug.masters | 调试白名单；省略时继承 basic.superusers，显式空列表表示不放行用户 |

QQ 号的平台名是 `qq-client`，与 satori 1.3.10 起的命名一致，OneBot、Milky 等协议端共用；
4.0.0-rc30 之前的 `onebot` 仍按别名读取。例如，超级用户和日志轮转配置为：

    [basic]
    superusers = { qq-client = ["<QQ_ID>"] }

    [basic.log]
    save = { rotation = "00:00", compression = "gz", colorize = false }

`basic.network` 也按 Entari 官方模型解析并保留，但 Tenko 的 OneBot 连接仍由
`[onebot]` 节组装；本批次不会用 `basic.network` 驱动 OneBot 连接。旧配置中的
`[runtime].log_level`、`[runtime].command_prefix` 和 `[entari].superusers` 会在读取时
映射到 `basic`，新配置应直接使用官方节。

## [database]、[features] 与 [exception]

| 配置 | 说明 |
| --- | --- |
| database.url | SQLAlchemy 数据库 URL，默认 sqlite+aiosqlite:///./.tenko/tenko.db |
| database.echo | 是否输出 SQL，默认关闭 |
| database.create_table_at | 建表时机，可选 preparing、prepared 或 blocking；Tenko 运行期状态接入时为保证状态读取晚于建表会使用 preparing |
| accounts / response state | 多账号路由和响应策略保存在 database.url 指定的数据库中 |
| features | 群功能开关保存在 database.url 指定的数据库中 |
| features.default_enabled | 新群或未记录功能的默认开关，默认开启 |
| exception.message_buffer_size | 异常报告保留的最近消息数量，默认 10 |
| exception.evidence_dir | 报告无法投递时的本地证据目录，默认 .tenko/exceptions |

升级相关的 schema 变更遵循只前进纪律：禁止直接 `DROP` 或改型既有列；废弃列先保留并
标记 `deprecated`，至少跨两个版本后再清理。使用文件型 SQLite 时，`/升级` 在写入
安装接管记录前会用 SQLite online backup API 生成
`.tenko/tenko.db.bak-<version>-<timestamp>`，最近保留 3 份；备份失败会写入升级审计
并记录 ERROR，但不会阻塞升级。

Tenko 的 `/开启`、`/关闭` 使用 `database.url` 指定的数据库保存“群 × 插件”的功能状态，
多账号路由、响应策略、命令频控和启动耗时历史也使用同一数据库。它与 Entari builtin
control 使用的 `local_data` 状态文件（包括全局插件/函数控制）是两套独立边界，不会
互相读写；实际执行需要插件仍被 Entari 注册且没有被全局控制停用，同时通过 Tenko 当前
群的开关检查。Entari 全局控制因此优先于 Tenko 群级开关，而 Tenko 群级开关在命令进入
插件分发前生效。

## [chat_log]

面板“聊天”页的消息记录。开启后，每个账号收到和发出的群聊、私聊消息都会写入
`database.url` 指定数据库的 `TenkoChatMessage` 表，同一条群消息在每个账号下各存一份。

| 字段 | 说明 |
| --- | --- |
| enabled | 是否记录消息，默认开启；关闭后聊天页仍可发送，但不显示历史 |
| retention_days | 保留天数，默认 7 |
| max_messages | 最多保留的消息条数，默认 50000 |

记录只保存文字、@、表情、回复摘要和图片的网络地址，不保存图片本身；内联图片、语音、
视频、文件等显示为占位。旧消息每 10 分钟按天数和条数清理一次。记录包含群友的聊天内容，
公开部署面板前请确认这符合群内的约定，不需要时关闭即可。

## [ratelimit]

| 字段 | 说明 |
| --- | --- |
| enabled | 是否启用命令限流，默认开启 |
| window_seconds | 滚动窗口长度，默认 15.0 秒 |
| max_weight | 窗口允许的最大权重，默认 10 |
| default_weight | 未单独指定命令的默认权重，默认 1 |
| cooldown_seconds | 同一来源冷却时间，默认 5.0 秒 |
| blacklist_seconds | 触发后持续限流的时间，默认 300.0 秒 |
| override_permission | 可豁免限流的最低权限：1 群管理、2 群主、3 BOT 管理员、4 Master，默认 1 |

按默认值，同一用户在同一群里 15 秒内的第 10 条命令会触发限流。越线的那一次
bot 回复“请求太频繁，已进入限流 5 分钟”，限流期间的命令**不再回复**，只在调试日志里
记录群、用户和插件，避免刷屏的人借提示继续刷屏。群管理员可以用 `/频率限制 关闭` 让某个群不限流
（`/频率限制` 查询当前状态，默认开启）；该开关保存在 `GroupSetting.frequency_limitation`。

每条命令默认记权重 1；插件可以用 `declare(rate_weight=...)` 设置整个插件、用 `@rate_limit(weight)`
设置单条命令，`0` 表示不限流。

## [render]

| 字段 | 说明 |
| --- | --- |
| timeout | 单次渲染超时时间，默认 10.0 秒 |
| width | 图片 viewport 宽度，默认 800 |
| quality | JPEG 质量，范围为 0 到 100，默认 85 |
| device_scale_factor | 浏览器设备像素比，默认 2 |

## [upgrade]

升级配置控制版本发现、制品准备和外部重启接管。默认策略只检查，不会自动下载
或安装。

| 字段 | 说明 |
| --- | --- |
| enabled | 是否启用升级功能，默认开启 |
| source | 版本源：git_tag、github_release 或 manifest |
| repository | `git_tag` 源使用的 Git 仓库地址；默认值 `.` 只适合本地 refs，远端部署必须填写完整 URL，例如 `https://github.com/g1331/tenko.git` |
| github_repository | GitHub 版本源的仓库标识 |
| manifest_url | manifest 版本源的地址 |
| github_token | 可选的 GitHub 访问 token |
| asset_name | 可选的发布制品名称 |
| tag_prefix | Git tag 前缀，默认 v |
| channel | 更新通道；stable 只接受正式版本，prerelease 同时接受正式和预发布版本 |
| policy | 执行策略；常用值为 check、download、install |
| current_version | 当前版本；留空时从项目版本读取 |
| config_version | 配置兼容版本，默认 1.0.0 |
| install_root | 版本和升级状态目录，默认 .tenko/upgrades |
| config_path | 外部配置路径，默认 config/tenko.toml |
| data_dir | 仅传给自定义健康检查命令的外部目录，默认 data；Tenko 自身持久化数据仍在 .tenko |
| health_command | 可选健康检查命令参数列表；设置后取代默认检查（含导入检查） |
| health_timeout | 健康检查超时时间，默认 30 秒 |
| restart_watch_timeout | 等待旧进程退出的最长时间，默认 300 秒；超时只留痕，不会强制拉起 |
| check_interval_hours | 定时检查间隔，默认 24 小时 |
| superuser_ids | 可执行升级命令的用户 ID；省略时继承 basic.superusers |

`current_version`、`config_version`、`asset_name`、`install_root`、`config_path`、`data_dir`、
`health_command`、`health_timeout`、`restart_watch_timeout` 是高级项，用于测试或特殊部署，一般保持默认。
