# Amber-Hunter Skill

> Huper琥珀本地感知引擎
> 版本：0.8.7 | 2026-03-22

---

> amber-hunter 运行在用户的本地机器上（Mac / Linux / Windows），agent 通过 `localhost:18998` 与它通信。


## 功能定位

Amber-Hunter 是琥珀的**入口层**，GitHub 公开免费。让用户零门槛感受"即时冻结当下"的 AI 协作记忆力。

- **免费、公开、零门槛** — 无需账号即可立即使用
- **核心价值**：即时冻结当下——让 AI 协作的记忆力可被唤醒
- **可选升级**：注册 huper.org 账号解锁跨设备云端同步

---

## 核心功能

- **Session 读取**：读取 OpenClaw / Claude 实时对话历史，作为冻结内容摘要
- **文件监控**：监控 workspace 最近修改的文件列表
- **本地加密存储**：AES-256-GCM 加密，master_password 存系统密钥链
- **云端同步**：加密后上传 huper.org（可选，需注册账号）

---

## API 端点（v0.8.4）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/status` | GET | 无 | 服务状态 |
| `/memories` | GET | **无需认证** | 本地记忆快照（仅 localhost） |
| `/token` | GET | localhost | 获取本地 API key |
| `/session/summary` | GET | 无 | OpenClaw/Claude session 摘要 |
| `/session/files` | GET | 无 | Workspace 最近文件 |
| `/freeze` | GET/POST | Bearer token 或 ?token= | 触发 freeze |
| `/capsules` | GET | Bearer token | 本地胶囊列表 |
| `/capsules` | POST | Bearer token | 创建胶囊 |
| `/capsules/{id}` | GET | Bearer token | 读取单个胶囊 |
| `/capsules/{id}` | DELETE | Bearer token | 删除胶囊 |
| `/sync` | GET | Bearer token 或 ?token= | 云端同步（需注册） |
| `/config` | GET/POST | Bearer token 或 ?token= | 读取/设置配置 |
| `/recall` | GET | Bearer token 或 ?token= | 主动检索相关记忆 |
| `/master-password` | POST | localhost | 设置 master_password |

---

## 认证方式

### 方式一：Bearer Header（服务器间调用）
```
Authorization: Bearer <api_key>
```

### 方式二：Query Parameter（浏览器跨域兼容）
```
GET /freeze?token=<api_key>
GET /sync?token=<api_key>
```

> ⚠️ 浏览器从 HTTPS 页面发请求到 HTTP localhost 时，Authorization header 会被拦截。因此前端使用 query parameter 方式。

---

## 安装

### 平台支持

| 平台 | 开机自启方式 | 密钥链 |
|------|------------|--------|
| **macOS** | LaunchAgent (launchctl) | macOS Keychain |
| **Linux** | systemd 用户服务 | GNOME Keyring (secret-tool) |
| **Windows** | 任务计划程序 (schtasks) | Windows 凭据管理器 (cmdkey) |

### 前置条件

- macOS / Linux / Windows（用户的本地电脑）
- Python 3.10+

### 快速安装（在用户本地机器上执行）

```bash
bash ~/.openclaw/skills/amber-hunter/install.sh
```

### 手动安装

```bash
# 1. 安装依赖
pip install -r ~/.openclaw/skills/amber-hunter/requirements.txt

# 2. 启动服务（无需账号）
python3 ~/.openclaw/skills/amber-hunter/amber_hunter.py &

# 3. 验证安装
curl http://localhost:18998/status
curl http://localhost:18998/memories   # 查看本地记忆（无需认证）
```

---

## 开机自启

**install.sh 会自动配置，无需手动操作。**

| 平台 | 命令 |
|------|------|
| macOS | `launchctl load ~/Library/LaunchAgents/com.huper.amber-hunter.plist` |
| Linux | `systemctl --user start amber-hunter` |
| Windows | 任务计划已自动创建（登录时启动） |

---

## 配置说明

- `~/.amber-hunter/config.json`：API Key 和 Huper URL
- `~/.amber-hunter/hunter.db`：本地胶囊 SQLite 数据库
- `~/.amber-hunter/amber-hunter.log`：运行日志
- **系统密钥链**（macOS Keychain / Linux GNOME Keyring / Windows 凭据管理器）：存储 `master_password`，不落磁盘

---

## 使用流程

### 无账号（立即可用）

安装完成后直接使用，无需注册：

```bash
# 查看本地记忆
curl http://localhost:18998/memories

# 获取 API token（用于 OpenClaw/Claude 集成）
curl http://localhost:18998/token
```

### 可选：注册 huper.org 账号解锁云同步

1. 打开 https://huper.org 注册账号
2. 在 dashboard 获取 API Key，填入 `~/.amber-hunter/config.json`
3. 设置 master_password（本地加密密钥，不会上传到服务器）
4. 启用云端同步后，可在多设备之间访问记忆

---

## 故障排除

### amber-hunter 无法连接
```bash
# 检查状态
curl http://localhost:18998/status

# 重启服务
python3 ~/.openclaw/skills/amber-hunter/amber_hunter.py &

# 查看日志
tail -f ~/.amber-hunter/amber-hunter.log
```

### Linux：secret-tool 未安装
```bash
# Ubuntu/Debian
sudo apt install libsecret-tools
# Fedora
sudo dnf install libsecret
# Arch
sudo pacman -S libsecret
```

---

## 版本历史

- **v0.8.4**（2026-03-22）：跨平台支持（macOS/Linux/Windows）、E2E 加密、/memories 无账号本地访问、Claude Cowork session 支持
- **v0.8.3**（2026-03-22）：初始版本

---

*Built with 🔒 by [Anke Chen](https://github.com/ankechenlab-node) for the [Huper琥珀](https://huper.org) ecosystem.*
