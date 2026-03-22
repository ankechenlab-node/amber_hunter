# Amber-Hunter Skill

> Huper琥珀本地感知引擎
> 版本：0.8.4 | 2026-03-22

---

## 功能定位

Amber-Hunter 是琥珀的**入口层**，GitHub 公开免费。让用户零门槛感受"即时冻结当下"的 AI 协作记忆力。

- 免费、公开、零门槛
- 核心价值：即时冻结当下——让 AI 协作的记忆力可被唤醒
- 轻量到让人愿意每天用几次

---

## 核心功能

- **Session 读取**：读取 OpenClaw 实时对话历史，作为冻结内容摘要
- **文件监控**：监控 workspace 最近修改的文件列表
- **本地加密存储**：AES-256-GCM 加密，master_password 存 macOS Keychain
- **云端同步**：加密后上传 huper.org，可选功能

---

## API 端点（v0.8.4）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/status` | GET | 无 | 服务状态 |
| `/token` | GET | localhost | 获取本地 API key |
| `/session/summary` | GET | 无 | OpenClaw session 摘要 |
| `/session/files` | GET | 无 | Workspace 最近文件 |
| `/freeze` | GET/POST | Bearer token 或 ?token= | 触发 freeze |
| `/capsules` | GET | Bearer token | 本地胶囊列表 |
| `/capsules` | POST | Bearer token | 创建胶囊 |
| `/capsules/{id}` | GET | Bearer token | 读取单个胶囊 |
| `/capsules/{id}` | DELETE | Bearer token | 删除胶囊 |
| `/sync` | GET | Bearer token 或 ?token= | 云端同步 |
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

### 前置条件
- macOS
- Python 3.10+
- huper.org 账号

### 快速安装
```bash
bash ~/.openclaw/skills/amber-hunter/install.sh
```

### 手动安装
```bash
# 1. 安装依赖
pip install -r ~/.openclaw/skills/amber-hunter/requirements.txt

# 2. 配置（API Key 从 https://huper.org/dashboard 生成）
cat > ~/.amber-hunter/config.json << 'EOF'
{
  "api_key": "你的huper.org API Key",
  "huper_url": "https://huper.org/api"
}
EOF

# 3. 启动服务
python3 ~/.openclaw/skills/amber-hunter/amber_hunter.py &

# 4. 设置 master_password（存 macOS Keychain）
#    打开 https://huper.org/dashboard →「加密」标签设置
```

---

## 开机自启

```bash
# 使用 install.sh 会自动配置 LaunchAgent
# 或手动：
cp ~/.openclaw/skills/amber-hunter/com.huper.amber-hunter.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.huper.amber-hunter.plist
```

---

## 配置说明

- `~/.amber-hunter/config.json`：API Key 和 Huper URL
- `~/.amber-hunter/hunter.db`：本地胶囊 SQLite 数据库
- `~/.amber-hunter/amber-hunter.log`：运行日志
- **macOS Keychain**：`master_password`（不落在磁盘）

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

### master_password 未设置
1. 打开 https://huper.org/dashboard
2. 点「加密」标签
3. 输入密码（不少于 8 位）
4. 点「保存」

### 云端同步失败
1. 确认已设置 master_password
2. 确认 API Key 有效（https://huper.org/dashboard → API Key）
3. 检查网络连接

---

## 版本历史

- **v0.8.4**（2026-03-22）：/sync 云端同步、/master-password 设置、/token 端点、CORS 修复
- **v0.8.3**（2026-03-22）：初始版本

---

*Built with 🔒 by [Anke Chen](https://github.com/ankechenlab-node) for the [Huper琥珀](https://huper.org) ecosystem.*
