"""测试只在插件目录中创建隔离数据，不触碰真实宿主配置。"""

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[2]))
os.environ["ASTRBOT_ROOT"] = str(ROOT / ".pytest-tmp-runtime")
package = types.ModuleType("astrbot_plugin_jev")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
