"""Own the trial processes and stop them through a private Unix socket, never a PID file."""

import argparse
import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNTIME = HERE / ".runtime"
CONTROL = RUNTIME / "control.sock"
PYTHON = ROOT / ".venv/bin/python"
TUNNEL_URL = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\b")


class TrialError(Exception):
    pass


def load_environment():
    if not (ROOT / ".env").is_file():
        raise TrialError("缺少项目根目录 .env；请先按照 README 配置本地环境")
    # Match the documented local setup. Capture env privately, never log its contents.
    loaded = subprocess.run(
        [
            "bash",
            "-c",
            'set -ae; source "$1" >/dev/null; env -0',
            "family",
            str(ROOT / ".env"),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if loaded.returncode:
        raise TrialError("加载根目录 .env 失败，请检查配置文件语法")
    env = dict(
        item.decode().split("=", 1) for item in loaded.stdout.split(b"\0") if item
    )
    credential_file = HERE / ".env"
    if not credential_file.exists():
        credentials = {
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
            "FAMILY_TRIAL_USERNAME": "family-trial",
            "FAMILY_TRIAL_PASSWORD": secrets.token_urlsafe(24),
        }
        with credential_file.open("x") as stream:
            os.chmod(credential_file, 0o600)
            stream.write(
                "# Private family trial credentials; do not share or commit this file.\n"
            )
            stream.writelines(f"{key}={value}\n" for key, value in credentials.items())
    os.chmod(credential_file, 0o600)
    credentials = dict(
        line.split("=", 1)
        for line in credential_file.read_text().splitlines()
        if line and not line.startswith("#")
    )
    if (
        len(credentials.get("DJANGO_SECRET_KEY", "")) < 50
        or len(set(credentials.get("DJANGO_SECRET_KEY", ""))) < 5
        or credentials.get("DJANGO_SECRET_KEY", "").startswith("django-insecure-")
        or not credentials.get("FAMILY_TRIAL_USERNAME")
        or len(credentials.get("FAMILY_TRIAL_PASSWORD", "")) < 20
    ):
        raise TrialError("试用 .env 中的密钥或账户凭据不符合要求")
    env.update(credentials)
    env.update(DEBUG="0", DJANGO_SETTINGS_MODULE="config.family_settings")
    env["PYTHONUNBUFFERED"] = "1"
    env["FAMILY_TRIAL_ROOT"] = str(ROOT)
    return env


def stop():
    if not CONTROL.exists():
        print("家庭试用当前未运行。")
        return
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(CONTROL))
            client.sendall(b"stop\n")
            if client.recv(32) != b"stopping\n":
                raise TrialError("未收到试用进程的停止确认")
    except (ConnectionRefusedError, FileNotFoundError):
        raise TrialError("试用控制入口已失效；没有按历史 PID 终止任何进程") from None
    deadline = time.monotonic() + 25
    while CONTROL.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if CONTROL.exists():
        raise TrialError("停止尚未结束，请查看试用终端或日志")
    print("家庭试用已停止；数据库和其他项目的进程保持运行。")


class Supervisor:
    def __init__(self, env):
        self.env = env
        self.children = {}
        self.log_handles = []
        self.stopping = False
        self.control = None
        self.lock = None
        self.url = None
        now = datetime.now(timezone.utc).astimezone()
        self.started = now.isoformat(timespec="seconds")
        self.logs = RUNTIME / "logs" / now.strftime("%Y%m%d-%H%M%S-%f")

    def acquire(self):
        RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(RUNTIME, 0o700)
        self.lock = (RUNTIME / "owner.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise TrialError("家庭试用已经运行，请先执行 stop.sh") from None
        self.logs.mkdir(parents=True, mode=0o700)
        CONTROL.unlink(missing_ok=True)
        (RUNTIME / "public-url.txt").unlink(missing_ok=True)
        self.control = socket.socket(socket.AF_UNIX)
        self.control.bind(str(CONTROL))
        os.chmod(CONTROL, 0o600)
        self.control.listen(2)
        self.control.setblocking(False)
        self.state("preparing")

    def state(self, phase):
        data = {
            "phase": phase,
            "pid": os.getpid(),
            "started": self.started,
            "url": self.url,
            "logs": str(self.logs),
            "children": {name: child.pid for name, child in self.children.items()},
        }
        (RUNTIME / "state.json").write_text(json.dumps(data, indent=2) + "\n")

    def tick(self):
        try:
            client, _ = self.control.accept()
        except BlockingIOError:
            pass
        else:
            with client:
                client.settimeout(1)
                try:
                    if client.recv(32).strip() == b"stop":
                        self.stopping = True
                        client.sendall(b"stopping\n")
                except OSError:
                    pass
        if self.stopping:
            raise TrialError("已请求停止家庭试用")
        for name, child in self.children.items():
            if child.poll() is not None:
                raise TrialError(
                    f"{name} 意外退出；请检查 {self.logs / (name + '.log')}"
                )

    def spawn(self, name, command, *, output=None):
        log = (self.logs / f"{name}.log").open("wb")
        self.log_handles.append(log)
        child = subprocess.Popen(
            [str(part) for part in command],
            cwd=ROOT,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=output if output is not None else log,
            stderr=log,
            start_new_session=True,
        )
        self.children[name] = child
        return child

    def run(self, name, command, *, output=None):
        print(f"正在执行：{name}", flush=True)
        child = self.spawn(name, command, output=output)
        while child.poll() is None:
            # Exclude this finite job from the long-lived component checks.
            del self.children[name]
            try:
                self.tick()
            finally:
                self.children[name] = child
            time.sleep(0.2)
        del self.children[name]
        if child.returncode:
            raise TrialError(f"{name} 失败；请检查 {self.logs / (name + '.log')}")

    def prepare(self):
        for program in ("docker", "npm", "caddy", "cloudflared"):
            if not shutil.which(program, path=self.env.get("PATH")):
                raise TrialError(
                    f"缺少 {program}，安装方法见 deployment/family/README.md"
                )
        for port in (8000, 8080):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    raise TrialError(
                        f"127.0.0.1:{port} 已被占用；不会停止占用进程"
                    ) from None
        database = urlparse(self.env.get("DATABASE_URL", ""))
        if (
            database.hostname not in {"localhost", "127.0.0.1"}
            or (database.port or 5432) != 5432
            or database.path.lstrip("/") != self.env.get("POSTGRES_DB", "baby_study")
            or database.username != self.env.get("POSTGRES_USER", "baby_study")
        ):
            raise TrialError(
                "本脚本仅备份并复用 Compose 本地数据库，请核对 DATABASE_URL"
            )
        self.run("database", ["docker", "compose", "up", "-d", "--wait", "db"])
        backups = RUNTIME / "backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = backups / f"{self.logs.name}.dump"
        try:
            with backup.open("xb") as output:
                self.run(
                    "backup",
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "db",
                        "sh",
                        "-c",
                        (
                            'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump --format=custom '
                            '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
                        ),
                    ],
                    output=output,
                )
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        print(f"数据库备份：{backup}", flush=True)
        self.run("migrate", [PYTHON, "backend/manage.py", "migrate", "--noinput"])
        self.run("account", [PYTHON, "backend/manage.py", "provision_family_trial"])
        self.run("frontend-install", ["npm", "--prefix", "frontend", "ci"])
        self.run("frontend-build", ["npm", "--prefix", "frontend", "run", "build"])
        self.run(
            "collectstatic", [PYTHON, "backend/manage.py", "collectstatic", "--noinput"]
        )

    def launch(self):
        self.state("connecting")
        # --config prevents an unrelated ~/.cloudflared/config.yaml from affecting this trial.
        config = RUNTIME / "cloudflared.yml"
        config.write_text("{}\n")
        self.spawn(
            "tunnel",
            [
                "cloudflared",
                "tunnel",
                "--config",
                config,
                "--no-autoupdate",
                "--url",
                "http://127.0.0.1:8080",
            ],
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            self.tick()
            match = TUNNEL_URL.search(
                (self.logs / "tunnel.log").read_text(errors="replace")
            )
            if match:
                self.url = match.group()
                break
            time.sleep(0.2)
        if self.url is None:
            raise TrialError(
                "90 秒内未获取 Cloudflare 临时地址，请检查 tunnel.log 和网络连接"
            )
        host = urlparse(self.url).hostname
        self.env.update(
            ALLOWED_HOSTS=host,
            CSRF_TRUSTED_ORIGINS=self.url,
            FAMILY_TRIAL_HOST=host,
        )
        self.run(
            "deployment-check",
            [
                PYTHON,
                "backend/manage.py",
                "check",
                "--deploy",
                "--fail-level",
                "WARNING",
            ],
        )
        self.run(
            "caddy-check",
            [
                "caddy",
                "validate",
                "--config",
                HERE / "Caddyfile",
                "--adapter",
                "caddyfile",
            ],
        )
        self.spawn(
            "web",
            [
                PYTHON,
                "-m",
                "gunicorn",
                "--chdir",
                ROOT / "backend",
                "config.wsgi:application",
                "--bind",
                "127.0.0.1:8000",
                "--workers",
                "2",
                "--timeout",
                "180",
                "--access-logfile",
                "-",
                "--error-logfile",
                "-",
            ],
        )
        self.spawn(
            "gateway",
            ["caddy", "run", "--config", HERE / "Caddyfile", "--adapter", "caddyfile"],
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.tick()
            request = urllib.request.Request(
                "http://127.0.0.1:8080/baby/study/health", headers={"Host": host}
            )
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    if json.load(response) == {"status": "ok"}:
                        break
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        else:
            raise TrialError("本机服务未在 30 秒内通过健康检查")
        self.spawn(
            "worker",
            [PYTHON, "backend/manage.py", "process_book_intakes", "--interval", "60"],
        )
        (RUNTIME / "public-url.txt").write_text(self.url + "\n")
        self.state("running")
        print(f"\n家庭试用已启动：{self.url}", flush=True)
        print(
            f"账户：{self.env['FAMILY_TRIAL_USERNAME']}；密码位于 {HERE / '.env'}",
            flush=True,
        )
        print(
            f"日志：{self.logs}\n保持此终端运行；Ctrl+C 或 stop.sh 可停止。", flush=True
        )
        while True:
            self.tick()
            time.sleep(0.2)

    def close(self):
        # Tear down all groups concurrently. No historical PID is trusted or killed.
        for child in reversed(list(self.children.values())):
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 10
        for child in self.children.values():
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        for log in self.log_handles:
            log.close()
        if self.control is not None:
            self.control.close()
            CONTROL.unlink(missing_ok=True)
            (RUNTIME / "public-url.txt").unlink(missing_ok=True)
            self.state("stopped")
        if self.lock is not None:
            self.lock.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="本机家庭试用启动/停止")
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    supervisor = None
    try:
        if args.action == "stop":
            stop()
        elif args.action == "status":
            state = RUNTIME / "state.json"
            print(state.read_text() if state.exists() else "家庭试用尚未启动。")
        else:
            supervisor = Supervisor(load_environment())
            supervisor.acquire()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: setattr(supervisor, "stopping", True))
            supervisor.prepare()
            supervisor.launch()
    except (TrialError, OSError) as error:
        print(f"家庭试用：{error}", file=sys.stderr, flush=True)
        return 0 if supervisor and supervisor.stopping else 1
    finally:
        if supervisor is not None:
            supervisor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
