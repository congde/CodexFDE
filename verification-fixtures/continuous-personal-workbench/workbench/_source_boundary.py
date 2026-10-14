"""Keep borrowed editable environments from supplying unfinished course modules."""
from importlib.machinery import PathFinder
from pathlib import Path
import sys


class _PersonalSourceBoundary:
    def __init__(self):
        self.root = Path(__file__).resolve().parents[1]

    def find_spec(self, fullname, path=None, target=None):
        package = fullname.split('.', 1)[0]
        if package not in {'workbench', 'eval', 'agent', 'harness_web', 'tests'}:
            return None
        spec = PathFinder.find_spec(fullname, path)
        if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(self.root):
            raise ModuleNotFoundError(
                f'个人项目尚未实现 {fullname}；拒绝从参考仓库借入该模块', name=fullname)
        return spec


def install():
    if not any(isinstance(finder, _PersonalSourceBoundary) for finder in sys.meta_path):
        sys.meta_path.insert(0, _PersonalSourceBoundary())
