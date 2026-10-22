"""第三方插件生态：运行时加载、安装管理、插件目录、独立存储和说明资料。"""

# 这两个名字是跨版本、跨仓库的导入入口，不能删：升级器的导入检查脚本在候选版本上
# 执行 ``from tenko.host.plugins import PluginRuntime``（rc23 及更早的升级器同样写死），
# tenko-registry 的 probe 和插件模板的测试脚手架也从这里导入。
from .runtime import PluginRuntime, api_incompatibility

__all__ = ["PluginRuntime", "api_incompatibility"]
