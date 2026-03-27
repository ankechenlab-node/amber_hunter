---
name: amber-proactive
version: 0.4.0
description: "Amber proactive memory capture. Fully self-contained Node.js script — no agent dependency, cron-triggered LLM extraction. Supports bilingual triggers (Chinese + English)."
---

# Amber-Proactive Skill

> 让琥珀主动记忆，无需开口。

---

## 工作原理

```
cron（每15分钟）
  → proactive-check.js V4（完全自包含）
    → 检查 session 消息数 ≥ 20 条
    → 调用 MiniMax API 提取关键事实
    → 直接写胶囊到 amber-hunter
```

**完全自包含**：脚本内部完成所有步骤，不需要 agent 介入，不需要 heartbeat。

---

## 触发方式

### 自动触发（cron，每15分钟）

阈值：session 消息数 ≥ 20 条。

### 手动触发（agent）

| 中文 | English |
|------|---------|
| 保存、记住、冻结、留住 | save, remember, freeze, capture |

手动不受消息数量限制，任意对话量都能触发。

---

## 使用方式

```bash
# 自动（cron 触发）
node ~/.openclaw/workspace/skills/amber-proactive/scripts/proactive-check.js

# 手动强制触发
node ~/.openclaw/workspace/skills/amber-proactive/scripts/proactive-check.js --manual
```

---

## 日志

```bash
tail -f ~/.amber-hunter/amber-proactive.log
```

---

## 文件结构

```
amber-hunter/
└── proactive/
    ├── README.md
    └── scripts/
        └── proactive-check.js   # V4，完全自包含
```

---

## 版本历史

- **v0.4.0**：完全自包含，脚本内部完成 LLM 提取+写胶囊，cron 直接触发，不需要 agent
- **v0.3.0**：LLM extraction via agent model（实验版）
- **v0.2.0**：Signal-based capture（已废弃）
- **v0.1.0**：Initial

---

*Built for the [Huper琥珀](https://huper.org) ecosystem.*
