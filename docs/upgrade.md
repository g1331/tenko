# 升级与部署

Tenko 可以自己检查新版本、下载校验、切换并在失败时回滚，配置和数据不受影响。
Master 可以用聊天命令操作，也可以在 WebUI 的「版本与升级」页完成同样的流程。

## 标准启动器

生产部署可使用仓库内的标准启动器：

    ./scripts/launcher.sh

启动器把脚本所在仓库目录作为稳定根目录，固定以该目录为 cwd，并通过
`uv run --project <stable_root> --no-sync` 复用项目环境。没有 `active.json` 时运行
稳定根目录中的代码；存在有效的 `active.json` 时只把对应 `versions/` 子目录作为
代码源。启动器不消费 `handoff.json`，handoff 由 `tenko.__main__` 在正常启动时一次性
应用，应用成功后会在 fresh Python 进程中重执行 active 版本。升级命令 arm 的 detached
watcher 会等待当前进程退出，再调用这个启动器；升级切换时还会观察新进程的启动存活，
因此 Ctrl+C 仍是正常的优雅退出路径。

## 升级命令

升级命令只允许配置的超级用户执行：

    /检查更新
    /升级 [停用不兼容插件]
    /回滚
    /重启

`/重启` 复用升级系统的重启接管，未启用升级系统时需要手动重启。

## 版本源与更新通道

配置模板默认使用 `github_release`：从 GitHub Release 读取版本，下载发布流程附上的
源码包 `tenko-<tag>.tar.gz`，按 GitHub 为附件提供的 SHA-256 校验后安装，不需要 Git，
git clone 和下载压缩包两种部署都能用：

    [upgrade]
    source = "github_release"
    github_repository = "g1331/tenko"
    channel = "stable"

源码包由 `.github/workflows/release-asset.yml` 在 Release 发布后上传；还没有附件的
Release 不算候选版本，上传完成后才会出现在检查结果里。附件上传失败时，可在 Actions
页手动运行该 workflow 并填写 tag 补传。GitHub API 未登录时每小时限 60 次请求，
频繁检查或共享出口 IP 时可填写只读的 `github_token`。

也可以改用 `git_tag`，这需要安装 Git。当 `source = "git_tag"` 时，`repository` 是用于
`git ls-remote --tags` 和 `git clone` 的仓库地址。需要从远端发现新版本的部署必须
填写完整 Git URL，例如：

    [upgrade]
    source = "git_tag"
    repository = "https://github.com/g1331/tenko.git"
    tag_prefix = "v"
    channel = "stable"

不要把 `repository = "."` 当作远端地址：它指向当前本地工作树，`git_tag` 只会看到
本地已有的 refs，不会联网发现远端 tag。当前实现兼容已有 Git 工作树中的 named
remote（例如 `origin`），会在加载配置时用 `git remote get-url` 将其解析为 URL；但
直接填写完整 URL 不依赖部署目录中的 remote 配置，也能保证检查和下载使用同一个远端。

`tag_prefix` 默认是 `v`，因此 `v4.0.0` 会被解析为版本 `4.0.0`。通道决定预发布
版本是否进入候选集合，并且候选版本仍必须高于当前版本：

- `stable` 只选择正式版本，适合生产实例；`4.0.0-rc.1` 等预发布 tag 会被排除。
- `prerelease` 同时接受正式版和预发布版，适合验证实例或需要跟进预发布版本的部署。

执行策略的含义是：`policy = "check"` 只发现并记录候选版本，`policy = "download"`
自动准备制品，`policy = "install"` 生成外部安装接管记录。进程切换仍由稳定的外部
启动器完成。

## `/升级`：下载、接管与重启

`/检查更新` 只查询当前通道中高于当前版本的最高候选版本，返回当前版本、候选版本、
标签和来源，不下载制品。

`/升级` 不会在当前进程中热替换代码，完整流程如下：

1. 检查当前通道并选择候选版本。
2. 获取候选制品；`github_release` 源下载源码包并校验 SHA-256，`git_tag` 源会按 tag
   做浅克隆并校验 commit SHA。随后升级器
   检查配置兼容性和切换前健康状态，并用候选版本的宿主锁重新解析插件锁（环境不动）：
   有插件不兼容时停止升级并列出原因，可改发 `/升级 停用不兼容插件`，先停用它们再升级。
   停用会把包移出环境，安装记录、启用状态、数据和配置都保留；之后用
   `/更新插件 <插件ID>` 或面板待处理页装上兼容版本即可恢复原样。面板升级还可以给每个
   插件选一个能装在新版本上的目录版本，进入新版本后的首次启动会自动装回，装不上的保持
   停用并记下原因。成功后把制品提升到
   `.tenko/upgrades/versions/`，写入 `pending.json`。
3. 写入 `handoff.json`，请求外部重启时执行 `activate`。命令会尝试 arm 一次性
   detached watcher；watcher 等待当前进程退出后，在新制品启动宽限期内观察进程存活，
   连续两次失败会写入自动回滚 handoff。
4. 收到成功回复后使用 `Ctrl+C` 让当前 Tenko 进程优雅退出。watcher 检测到旧进程
   退出后启动标准启动器 `scripts/launcher.sh`，并在启动宽限期内观察新进程。
5. 启动器固定以部署根目录为工作目录，并使用共享的 uv 环境启动 Tenko。启动早期
   会消费 `handoff.json`，原子更新 `active.json`，按候选版本的 `uv.lock` 同步依赖，
   执行切换后的健康检查，然后在 fresh Python 进程中重新执行 active 版本。配置、
   SQLite 数据库和 `.tenko/` 下的运行状态仍使用稳定根目录中的原有路径。

依赖同步等价于 `uv sync --frozen --inexact --no-editable --project <版本目录>`，
目标是当前运行的虚拟环境。扩展插件与宿主共用这个环境，所以用 `--inexact` 保留锁文件
之外的插件依赖（默认的精确同步会把它们卸载）；`tenko-api` 以普通包装入，
环境里登记的版本始终与宿主一致。需要 `uv` 可执行文件（`PATH` 或环境变量 `UV`）；没有 `uv.lock` 的旧版本目录
跳过同步。插件自己需要的包版本与锁文件冲突时以锁文件为准，导入检查会暴露由此造成的
插件加载失败。

默认健康检查（`health_command` 为空时）在切换前只做编译冒烟；依赖同步后再用候选代码
启动子进程，导入宿主并通过 Entari 加载全部内置插件和已启用的扩展插件，任何一个导入
失败都视为健康检查失败。


如果 watcher 未能 arm，`/升级` 的回复会提示手动接管；此时退出当前进程后运行
`./scripts/launcher.sh`。如果依赖同步或切换后的健康检查失败，升级器会恢复原 active
指针、把依赖重新同步回原版本的锁文件，并清理本次接管记录，避免失败 handoff 在后续
启动中反复执行；失败原因写入日志、审计和 `recovery-notice.json`，运行版本不变。启动器拉起后续进程仍在
宽限期内退出时，watcher 会把 `active.json` 切回 `previous.json` 指向的制品并重新拉起；
回滚成功后，新进程启动通知会播报“已自动回滚至 X 版本”。如果回滚制品也无法存活，
watcher 停止尝试、隔离失败 handoff 并保留审计记录，等待人工处理，不会继续循环拉起。

`/回滚` 请求回到 `.tenko/upgrades/previous.json` 指向的上一可用版本：

1. 命令检查是否存在上一版本和当前 `active` 指针。
2. 写入 `action = "rollback"` 的 `handoff.json`，并按与 `/升级` 相同的方式尝试
   arm watcher。
3. 使用 `Ctrl+C` 退出当前进程；下一次由标准启动器启动时，启动早期消费回滚 handoff，
   将 `active.json` 切换到上一版本，按上一版本的 `uv.lock` 恢复依赖，再执行健康检查。

回滚也不会在当前进程中立即替换代码。回滚时的依赖同步或健康检查失败会恢复原 active
指针和原版本的依赖，并隔离失败的 handoff；如果不存在上一可用版本，命令会返回“没有可回滚的上一可用版本”。

`[upgrade]` 配置在 Tenko 启动时读取并构造升级管理器，运行期间不会自动重新读取配置文件。
在 WebUI 宿主配置里修改 `channel`、`policy` 和 `check_interval_hours` 会立即生效；
修改 `enabled`、`source`、`repository` 等其他升级配置，或直接编辑配置文件后，必须重启
Tenko 才会生效；若配置修改本身需要先停止服务，应按部署方式退出当前进程，再使用
标准启动器（或原有的 `uv run python -m tenko` 启动命令）启动。

启用周期检查时，check_interval_hours 控制定时检查间隔。升级目录、配置目录和
数据目录彼此分离，升级过程不会覆盖用户配置或运行数据；`data_dir` 不会改变
Tenko 自身 `.tenko` 应用数据的落点。


## 配置兼容性清单

仓库根目录的 `upgrade-manifest.json` 是发布制品中的 canonical 配置兼容性清单，
最小内容为：

    {
      "min_config_version": "1.0.0"
    }

清单中的 `min_config_version` 是独立于项目版本的配置协议版本。它表示候选代码
能够读取的最低用户配置版本：普通代码修复或功能增加不需要提升它；只有配置格式
发生破坏性变化、旧配置无法继续被新代码读取时才提升它。项目版本（例如 `4.0.1`）
和配置协议版本（例如 `1.0.0`）不能互相推导。

发布源数据写在 `pyproject.toml` 的 `[tool.tenko.upgrade]` 段：

    [tool.tenko.upgrade]
    min_config_version = "1.0.0"

运行版本脚本时，`scripts.bump` 会在修改版本前校验该字段，并在版本、锁文件和
可选 changelog 步骤成功后生成根目录清单：

    uv run python -m scripts.bump pre_n --no-commit
    uv run python -m scripts.bump patch --commit

字段缺失或不是合法 SemVer 会使发布流程失败，不会从项目版本猜测默认值。使用
`--commit` 时清单会和 `pyproject.toml`、`uv.lock` 一起加入提交；不使用该选项时
仍由发布者自行检查并提交生成的清单。

旧制品可能没有清单。升级器会将这类制品按“未声明最低配置版本”的 legacy 兼容
行为处理并写入审计；新制品应只生成根目录这一份 canonical 清单。为兼容更早的
制品，升级器仍会按固定顺序检查 `tenko/upgrade-manifest.json`，再检查根目录清单。

## 代码、配置、数据与升级状态目录

标准启动器以部署根目录为稳定 `cwd`。候选版本目录只提供代码，不携带配置、虚拟
环境或应用数据；表中的相对路径都相对于稳定 `cwd` 解析：

| 目录或文件 | 用途 | 解析位置 |
| --- | --- | --- |
| 部署根目录 | 稳定启动根、共享依赖和当前工作树 | 启动器确定的稳定根目录 |
| `config/tenko.toml` | 用户配置 | 稳定 `cwd` |
| `.tenko/tenko.db` | Tenko 运行期状态和既有业务表 | 稳定 `cwd` |
| `.tenko/exceptions/` | 异常取证文件 | 稳定 `cwd` |
| `.tenko/upgrades/` | `active.json`、`previous.json`、`pending.json`、`handoff.json`、`recovery-notice.json`、`audit.jsonl` 和版本目录 | 稳定 `cwd` |
| `.tenko/upgrades/versions/<version>/` | 候选代码 | 升级状态目录下，作为代码源 |

`[upgrade].data_dir` 是只传给自定义健康检查命令的外部目录，不是 Tenko
自身应用数据根目录。升级切换后，数据库、JSON 状态、异常取证和配置仍指向稳定
根目录下的原有物理路径；候选版本不会在自己的目录中生成新的 `.tenko/` 或
`config/`。
