---
name: amber-proactive
version: 0.1.0
description: "Amber proactive memory capture skill. Silently watches AI collaboration sessions and automatically writes significant moments to amber storage. Activate when: you want AI to naturally remember important decisions, corrections, preferences, and discoveries without being asked. This skill makes amber truly intelligent — it captures memories proactively, not just on demand."
---

# Amber-Proactive Skill

> 让琥珀主动记忆，无需开口。

---

## 核心理念

**琥珀应该是外挂大脑，不是人工打卡机。**

现有的 freeze 需要人主动想起"这条值得存"。真正的记忆层应该在 AI 协作自然发生的过程中，由 AI 自己判断"这条值得记住"，然后静默写入。

---

## 工作原理

```
AI 回复
   ↓
amber-proactive hook 拦截（agent:response）
   ↓
分析：这段对话有什么值得记住的？
   ↓
值得记住 → 静默写入 amber（无用户感知）
   ↓
下次类似上下文出现 → AI 自动查找琥珀 → 预填相关记忆
```

---

## 触发条件（自动判断）

以下情况会自动触发静默记忆：

| 信号类型 | 触发词/场景 | 写入内容 |
|---------|------------|---------|
| **用户纠正** | "不对" / "actually" / "错了" / "不是这样" | 用户的正确做法 + 原始错误 |
| **错误修复** | exec 失败 → 找到正确方法 | 问题 + 解决方案 |
| **关键决策** | 用户确定了一个方向/方案 | 决策内容 + 理由 |
| **用户偏好** | "我喜欢..." / "我一般会..." / "不要..." | 偏好内容 |
| **第一次做到** | 某件事第一次成功完成 | 成果 + 上下文 |
| **重要发现** | 找到了更好的方法/工具/流程 | 发现内容 + 原方案对比 |
| **安全/隐私决定** | 用户明确了数据边界 | 边界定义 |

---

## 写入格式

每个主动记忆包含：
- `type`: `correction` | `decision` | `preference` | `discovery` | `error_fix`
- `trigger`: 触发这段记忆的原始对话片段
- `content`: 记忆内容（AI 总结）
- `context`: 相关项目/技术栈
- `tags`: 自动打标签

---

## 静默原则

- **用户零感知**：写入过程完全后台，不打断协作流
- **无额外提示**：不告诉用户"已存入琥珀"
- **失败不报错**：写入失败静默跳过，不影响主流程

---

## API 调用

通过 amber-hunter 的本地 API 写入：
```
POST http://localhost:18998/capsules
Authorization: Bearer <api_key>
```

---

## 依赖

- amber-hunter 服务（localhost:18998）必须在运行
- 读取 `~/.amber-hunter/config.json` 获取 api_key
- 如果 amber-hunter 未运行，skill 静默跳过

---

## 文件结构

```
amber-proactive/
├── SKILL.md              # 本文件
├── hooks/openclaw/
│   ├── HOOK.md          # OpenClaw hook 定义
│   └── handler.ts       # Hook 执行脚本
└── scripts/
    └── capture.ts       # 主动捕获逻辑
```

---

## 与其他 Skill 的关系

- **amber-hunter**（琥珀入口）：提供 `/freeze` 端点，被动捕获
- **amber-proactive**（主动记忆）：主动判断+写入，主动补充
- **self-improving-agent**：专注错误记录，amber-proactive 扩展为通用记忆

---

*Built for the [Huper琥珀](https://huper.org) ecosystem.*
