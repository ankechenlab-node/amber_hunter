"""
core/keychain.py — 跨平台密钥管理
支持：macOS / Windows / Linux

存储策略：
- macOS:     security 命令（Keychain）
- Windows:   cmdkey（凭据管理器）
- Linux:     secret-tool（libsecret/GNOME Keyring）
- Fallback:  config.json（仅限 api_token，master_password 必须用系统密钥链）
"""

import os, json, subprocess, sys
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = HOME / ".amber-hunter" / "config.json"
SERVICE_NAME = "com.huper.amber-hunter"


def _detect_os():
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    else:
        return "linux"


OS = _detect_os()


# ── macOS ────────────────────────────────────────────────
def _macos_get(account: str) -> str | None:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", account, "-w"],
            capture_output=True, text=True, timeout=3
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _macos_set(account: str, password: str) -> bool:
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", SERVICE_NAME, "-a", account],
            capture_output=True
        )
        r = subprocess.run(
            ["security", "add-generic-password",
             "-s", SERVICE_NAME, "-a", account, "-w", password, "-U"],
            capture_output=True, text=True
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Windows ─────────────────────────────────────────────
def _windows_get(account: str) -> str | None:
    try:
        r = subprocess.run(
            ["cmdkey", "/list"],
            capture_output=True, text=True, timeout=5, shell=True
        )
        lines = r.stdout.splitlines()
        target = f"amber-hunter:{account}"
        for line in lines:
            if target.lower() in line.lower():
                # Windows 凭据中没有直接获取密码的命令，需要手动注册表读取或用 Python winreg
                # 这里用 powershell 读取
                ps = subprocess.run(
                    ["powershell", "-Command",
                     f"((cmdkey /list:{target} 2>&1) -match 'Password:')"],
                    capture_output=True, text=True, timeout=5
                )
                for l in ps.stdout.splitlines():
                    if "Password:" in l:
                        return l.split("Password:", 1)[1].strip()
        return None
    except Exception:
        return None


def _windows_set(account: str, password: str) -> bool:
    try:
        target = f"amber-hunter:{account}"
        subprocess.run(["cmdkey", "/delete", target], capture_output=True)
        r = subprocess.run(
            ["cmdkey", "/generic", target, "/user", "amber", "/pass", password],
            capture_output=True, timeout=5, shell=True
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Linux ────────────────────────────────────────────────
def _linux_get(account: str) -> str | None:
    try:
        r = subprocess.run(
            ["secret-tool", "lookup", "amber-hunter", account],
            capture_output=True, text=True, timeout=3
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _linux_set(account: str, password: str) -> bool:
    try:
        r1 = subprocess.run(
            ["secret-tool", "store", "--label=f\"Amber Hunter {account}\"",
             "amber-hunter", account],
            input=password, capture_output=True, timeout=3, text=True
        )
        return r1.returncode == 0
    except Exception:
        return False


# ── Cross-platform dispatcher ────────────────────────────
def _credential_get(account: str) -> str | None:
    if OS == "macos":
        return _macos_get(account)
    elif OS == "windows":
        return _windows_get(account)
    elif OS == "linux":
        return _linux_get(account)
    return None


def _credential_set(account: str, password: str) -> bool:
    if OS == "macos":
        return _macos_set(account, password)
    elif OS == "windows":
        return _windows_set(account, password)
    elif OS == "linux":
        return _linux_set(account, password)
    return False


# ── Public API ──────────────────────────────────────────
def get_master_password() -> str | None:
    """
    获取 master_password。
    必须从系统密钥链读取，读不到返回 None（不允许文件 fallback）。
    """
    return _credential_get("master_password")


def set_master_password(password: str) -> bool:
    """设置 master_password 到系统密钥链"""
    return _credential_set("master_password", password)


def get_api_token() -> str | None:
    """
    获取本地 API token。
    优先级：系统密钥链 > 环境变量 AMBER_TOKEN > config.json
    """
    # 1. 系统密钥链
    token = _credential_get("api_token")
    if token:
        return token

    # 2. 环境变量
    token = os.environ.get("AMBER_TOKEN")
    if token:
        return token

    # 3. config.json
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("api_token") or cfg.get("api_key")
        except Exception:
            pass
    return None


def get_huper_url() -> str:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("huper_url", "https://huper.org/api")
        except Exception:
            pass
    return "https://huper.org/api"


def ensure_config_dir():
    Path(".amber-hunter").mkdir(exist_ok=True)


def get_os() -> str:
    """返回当前操作系统名称"""
    return OS
