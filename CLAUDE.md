# CLAUDE.md — amber-hunter Skill Directory

## 安全规范

### ⚠️ 绝对禁止
- 禁止在目录内任何文件里硬编码 API keys、tokens 或密码
- 禁止提交 `.env`、`.config.json` 等含真实凭证的文件
- 禁止在 git remote URL 里包含 token（即使临时）

### ✅ 正确做法
- 所有凭证通过环境变量或 OS keychain 获取
- `api_key` → 从 `~/.amber-hunter/config.json` 读取（用户配置）
- `huper.org` token → 通过浏览器交互获取，不写在文件里
- GitHub token → 用 `ghp_` token 临时授权后，立即改为 HTTPS URL

### 📋 发布前检查清单
```
1. git remote -v → 确认不含 token
2. grep -r "ghp_\|sk-\|api_key.*=\|YOUR_KEY" . → 确认无泄露
3. ls *.bak* *.new → 确认无备份文件
4. clawhub inspect <skill> → 确认发布的文件列表
```

### 🔑 密钥管理
- `master_password` → OS Keychain（macOS: security, Linux: secret-tool, Windows: cmdkey）
- `api_token` → `~/.amber-hunter/config.json`（用户配置）
- 所有加密在本地完成，密钥不上传

