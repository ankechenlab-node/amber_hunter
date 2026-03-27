---
name: amber-proactive
version: 0.3.0
description: "Amber proactive memory capture. Watches AI collaboration sessions, extracts key facts via LLM when threshold is met or user triggers, writes to amber storage. Supports bilingual triggers (Chinese + English)."
---

# Amber-Proactive Skill

> 让琥珀主动记忆，无需开口。

---

## 核心理念

**琥珀应该是外挂大脑，不是人工打卡机。**

amber-proactive 在对话过程中自动判断"这条值得记住"，然后静默写入。用户可以完全无感，也可以随时手动触发。

---

## 触发方式（混合）

### 手动触发（优先，随时可用）

| 中文触发词 | English trigger | 效果 |
|-----------|----------------|------|
| 保存、记住、冻结、留住、存下来 | save, remember, freeze, capture | 当前 session 最近消息提取关键事实，写胶囊 |
| 保存关于 X 的内容 | save about X, remember X | 提取与 X 相关的内容片段 |

手动触发不受消息数量限制，任意对话量都能触发。

### 自动触发（阈值兜底）

当满足以下全部条件时自动入队：
- session 消息数 ≥ 20 条
- proactive-check.js 被调用（idle 超时触发）

入队后由 agent 在下次响应时读取并处理，无需等待。

---

## 工作原理

```
对话积累（proactive-check.js）
   ↓
达到阈值（≥20条）或手动触发
   ↓
Agent 用自己的模型提取关键事实
   ↓
LLM 认为 worth=true 的事实 → 写琥珀胶囊
```

**不依赖任何具体 LLM provider** — 由 agent 调用自己配置的模型，无需 skill 配置 API。

---

## 写入格式

每个主动胶囊包含：
- `memo`: 事实摘要（前60字）
- `content`: 完整事实描述
- `tags`: `auto-extract`
- `session_id`: 来源 session

---

## 静默原则

- **用户零感知**：写入过程完全后台，不打断协作流
- **无额外提示**：不告诉用户"已存入琥珀"
- **失败不报错**：写入失败静默跳过，不影响主流程
- **去重**：同一 session 只入队一次

---

## 文件结构

```
amber-hunter/
├── proactive/
│   ├── README.md           # 本文件
│   ├── scripts/
│   │   └── proactive-check.js  # 入队脚本（阈值触发）
│   └── hooks/openclaw/
│       └── handler.js      # OpenClaw hook 配置
├── amber_hunter.py        # Flask 服务（含 /capsules API）
└── core/
    └── db.py             # 数据库操作（含 list_capsules）
```

---

## API

通过 amber-hunter 本地 API 写入：
```
POST http://localhost:18998/capsules
Authorization: Bearer <api_key>
```

---

## 依赖

- amber-hunter 服务（localhost:18998）必须在运行
- 读取 `~/.amber-hunter/config.json` 获取 api_key

---

## 与其他模块的关系

- **amber-hunter**（琥珀入口）：提供 `/freeze` 端点，被动捕获
- **amber-proactive**（主动记忆）：主动判断+写入，主动补充
- **HEARTBEAT.md**：agent 在 heartbeat 时处理 `pending_extract.jsonl` 队列

---

*Built for the [Huper琥珀](https://huper.org) ecosystem.*
