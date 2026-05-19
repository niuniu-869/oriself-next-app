"""OriSelf Next (v2.0) · 产品即 skill 的对话式人格测试框架。"""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # 单一事实源 = server/pyproject.toml 的 [project].version。
    # pip install -e . 之后由包元数据读出;升级版本只改 pyproject.toml 一处。
    __version__ = _pkg_version("oriself-server")
except PackageNotFoundError:  # 未经 pip install 直接跑源码的极少数情况
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
