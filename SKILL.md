# Amber-Hunter Skill

> Huper琥珀本地感知引擎
> 版本：0.8.3 | 2026-03-22

---

## 功能

Amber-Hunter 是琥珀协议的**本地感知层**，安装在用户 Mac 上，为琥珀提供 AI 协同上下文捕获能力。

### 核心功能

- **Session 读取**：读取 OpenClaw 实时对话历史，作为冻结内容摘要
- **文件监控**：监控 workspace 最近修改的文件列表
- **本地存储**：胶囊加密存储在本地 SQLite
- **云端同步**：可选，API Key 配置后加密上传 huper.org

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/status` | GET | 服务状态，是否已配置 master_password / API Key |
| `/session/summary` | GET | OpenClaw 最近 session 对话摘要 |
| `/session/files` | GET | Workspace 最近修改文件（top 10）|
| `/freeze` | POST | 触发 freeze：返回预填数据 |
| `/capsules` | GET | 本地胶囊列表 |
| `/capsules` | POST | 创建胶囊 |

---

## 安装

### 前置条件

- macOS
- Python 3.10+
- huper.org 账号

### 安装步骤

```bash
# 1. 安装依赖
pip install -r ~/.openclaw/skills/amber-hunter/requirements.txt

# 2. 配置（编辑 config.json）
cat > ~/.amber-hunter/config.json << 'EOF'
{
  "api_key": "你的huper.org API Key",
  "master_password": "你的本地加密密码"
}
EOF

# 3. 启动服务
python3 ~/.openclaw/skills/amber-hunter/amber_hunter.py &

# 4. 设置开机自启（LaunchAgent）
# 见 README.md
```

### 配置 API Key

1. 打开 https://huper.org/dashboard
2. 登录 → API Key 页 → 生成新 Key
3. 复制 Key，填入 `~/.amber-hunter/config.json`

---

## 使用

### 触发 freeze

**方式一：浏览器直接访问**
```
http://localhost:18998/freeze
```
返回预填数据，前端读取后填入琥珀弹窗。

**方式二：Raycast**
```
amber freeze
```

### API 示例

```bash
# 查看服务状态
curl http://localhost:18998/status

# 获取对话摘要
curl http://localhost:18998/session/summary

# 获取最近文件
curl http://localhost:18998/session/files

# 触发 freeze
curl -X POST http://localhost:18998/freeze

# 创建本地胶囊
curl -X POST http://localhost:18998/capsules \
  -H "Content-Type: application/json" \
  -d '{"memo":"测试胶囊","content":"内容","tags":"test"}'
```

---

## 安全说明

- `master_password` 仅存储在本地，不上传
- 胶囊内容 AES-256-GCM 加密后存储
- API Key 只用于云端身份认证，不参与加密
