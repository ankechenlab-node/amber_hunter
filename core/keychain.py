"""
core/keychain.py — macOS Keychain 封装 + 安全配置加载

规则：
- master_password: 优先从 Keychain 读，读不到则报错，不 fallback 到文件
- api_token:   优先从 Keychain 读，读不到则从环境变量或配置文件读
- config.json 仅作为 api_token 的 fallback，不存密码
"""

import os, json
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = HOME / ".amber-hunter" / "config.json"
KEYCHAIN_SVC = "com.huper.amber-hunter"


def _run_security(args: list[str]) -> str | None:
    """调用 security 命令行工具，返回 stdout 或 None"""
    import subprocess
    try:
        r = subprocess.run(
            ["security", "-i"] + args,
            capture_output=True, text=True, timeout=3
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def get_master_password() -> str | None:
    """
    获取 master_password。
    优先从 macOS Keychain 读，读不到直接返回 None（不允许文件 fallback）。
    """
    result = _run_security([
        "find-generic-password", "-s", KEYCHAIN_SVC,
        "-a", "master_password", "-w"
    ])
    return result


def set_master_password(password: str) -> bool:
    """设置 master_password 到 Keychain"""
    import subprocess
    try:
        # 先删旧的
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SVC, "-a", "master_password"],
            capture_output=True
        )
        # 再添加新的
        r = subprocess.run(
            ["security", "add-generic-password",
             "-s", KEYCHAIN_SVC, "-a", "master_password",
             "-w", password, "-U"],
            capture_output=True, text=True
        )
        return r.returncode == 0
    except Exception:
        return False


def get_api_token() -> str | None:
    """
    获取本地 API token。
    优先级：Keychain > 环境变量 AMBER_TOKEN > config.json
    """
    # 1. Keychain
    result = _run_security([
        "find-generic-password", "-s", KEYCHAIN_SVC,
        "-a", "api_token", "-w"
    ])
    if result:
        return result

    # 2. 环境变量
    token = os.environ.get("AMBER_TOKEN")
    if token:
        return token

    # 3. config.json（仅 token，不含密码）
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("api_token") or cfg.get("api_key")
        except Exception:
            pass

    return None


def get_huper_url() -> str:
    """获取 huper.org API 地址"""
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("huper_url", "https://huper.org/api")
        except Exception:
            pass
    return "https://huper.org/api"


def ensure_config_dir():
    HOME.joinpath(".amber-hunter").mkdir(exist_ok=True)
