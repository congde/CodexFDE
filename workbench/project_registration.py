"""Attach local repositories or clone a remote without executing project code."""
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlsplit


class ProjectRegistration:
    def __init__(self, projects):
        self.projects = projects
        self.lock = threading.Lock()

    @staticmethod
    def git(args, cwd=None, timeout=120):
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
        # Only ordinary network protocols; no helpers, submodules or checkout hooks.
        command = ['git', '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
                   '-c', 'protocol.ssh.allow=always', '-c', 'core.hooksPath=', *args]
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout,
                env=env, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        except subprocess.TimeoutExpired as error:
            raise ValueError('Git 操作超时，已停止等待；目标目录保留，请检查网络和目录后重试') from error
        except OSError as error:
            raise ValueError('无法运行 Git，请先在本机安装并配置 Git') from error
        if result.returncode:
            raise ValueError('Git 操作失败，请检查仓库地址、访问权限和本机 Git 登录；已有文件已保留')
        return result.stdout.decode('utf-8', errors='replace').strip()

    @staticmethod
    def remote(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            raise ValueError('请填写 Git 仓库链接')
        value = value.strip()
        if any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError('Git 链接不能包含空白或控制字符')
        parsed = urlsplit(value)
        if parsed.hostname and not re.fullmatch(r'[A-Za-z0-9.:-]+', parsed.hostname):
            raise ValueError('Git 链接的主机名无效')
        https = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment and parsed.path not in {'', '/'})
        ssh = (parsed.scheme == 'ssh' and parsed.hostname and parsed.username == 'git'
               and not parsed.password and not parsed.query and not parsed.fragment and parsed.path not in {'', '/'})
        scp = re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', value)
        if not (https or ssh or scp):
            raise ValueError('请使用 HTTPS 或 git 用户的 SSH 仓库链接；不要在链接里填写密码或访问令牌')
        return value

    @staticmethod
    def command(value):
        if value is None or value == []:
            return []
        if not isinstance(value, list) or any(not isinstance(p, str) or not p.strip() for p in value):
            raise ValueError('质量检查命令须为非空参数组成的 JSON 数组')
        if not Path(value[0]).is_absolute() or not Path(value[0]).is_file():
            raise ValueError('质量检查命令首项必须是本机可执行程序的绝对路径')
        return value

    def add(self, body):
        kind = body.get('source_type', 'local')
        if kind not in {'local', 'git'}:
            raise ValueError('请选择本地目录或 Git 链接')
        name, location = body.get('name'), body.get('root_path')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('请填写项目名称（最多 120 字）')
        if not isinstance(location, str) or not location.strip() or not Path(location).is_absolute():
            raise ValueError('请填写本地目录的绝对路径')
        root = Path(location.strip()).resolve()
        if root == root.parent:
            raise ValueError('请使用具体项目目录，不能将磁盘根目录作为项目')
        command = self.command(body.get('eval_command'))
        remote = self.remote(body.get('git_url')) if kind == 'git' else None
        # Serialize directory claims and registration; never overwrite a destination.
        with self.lock:
            if any(Path(p['root_path']) == root for p in self.projects.list()):
                raise ValueError('该目录已添加，请选择已有项目')
            if kind == 'git':
                if root.exists():
                    raise ValueError('克隆目标目录已存在，请选择一个尚不存在的新目录；不会覆盖已有文件')
                root.parent.mkdir(parents=True, exist_ok=True)
                self.git(['clone', '--', remote, str(root)])
            else:
                if not root.is_dir():
                    raise ValueError('本地项目目录不存在')
                if not (root / '.git').exists():
                    # A subdirectory is not an independent project checkout.
                    try:
                        enclosing = self.git(['rev-parse', '--show-toplevel'], cwd=root, timeout=10)
                    except ValueError:
                        enclosing = ''
                    if enclosing:
                        raise ValueError('该目录属于另一个 Git 仓库，请添加仓库根目录：' + enclosing)
                    if body.get('initialize_git') is not True:
                        raise ValueError('该目录尚未使用 Git，请勾选初始化版本管理后添加')
                    self.git(['init', '--quiet', str(root)], timeout=15)
            top = Path(self.git(['rev-parse', '--show-toplevel'], cwd=root, timeout=10)).resolve()
            if top != root:
                raise ValueError('请使用独立 Git 仓库的根目录')
            project = self.projects.create(name, root, command, allow_pending_eval=True)
            if body.get('make_default') is True:
                self.projects.set_default(project['id'])
            return project

    def configure(self, project_id, body):
        command = self.command(body.get('eval_command'))
        self.projects.configure(project_id, command)
        if body.get('make_default') is True:
            self.projects.set_default(project_id)
        return self.projects.get(project_id)
