#!/bin/bash
# amber-hunter 安装脚本
# 用法: bash install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUNTER_DIR="$HOME/.openclaw/skills/amber-hunter"
PLIST_PATH="$HOME/Library/LaunchAgents/com.huper.amber-hunter.plist"

echo "🌙 Amber-Hunter 安装脚本"
echo "━━━━━━━━━━━━━━━━━━━━━━"

# 1. 检查依赖
echo "[1/4] 检查 Python 依赖..."
pip install -q -r "$HUNTER_DIR/requirements.txt" 2>/dev/null || \
pip3 install -q -r "$HUNTER_DIR/requirements.txt" 2>/dev/null

# 2. 初始化配置
echo "[2/4] 初始化配置..."
mkdir -p "$HOME/.amber-hunter"
if [ ! -f "$HOME/.amber-hunter/config.json" ]; then
    echo '{"api_key": "", "huper_url": "https://huper.org/api"}' > "$HOME/.amber-hunter/config.json"
    echo "  → 已创建 $HOME/.amber-hunter/config.json"
    echo "  → 请编辑填入 api_key，或在 huper.org/dashboard 获取"
fi

# 3. 生成本地 API token
echo "[3/4] 生成本地 API token..."
LOCAL_TOKEN=$(python3 -c "
import secrets, json, os
path = os.path.expanduser('$HOME/.amber-hunter/config.json')
cfg = {}
try:
    with open(path) as f: cfg = json.load(f)
except: pass
token = cfg.get('api_token') or secrets.token_urlsafe(32)
cfg['api_token'] = token
with open(path, 'w') as f: json.dump(cfg, f, indent=2)
print(token)
")
echo "  → 本地 API token 已生成"

# 4. 安装 LaunchAgent
echo "[4/4] 安装 LaunchAgent..."
mkdir -p "$HOME/Library/LaunchAgents"
HOME_ESC=$(eval echo ~)
sed "s|/Users/leo|$HOME_ESC|g" "$HUNTER_DIR/com.huper.amber-hunter.plist" > "$PLIST_PATH"
chmod 644 "$PLIST_PATH"
launchctl load "$PLIST_PATH" 2>/dev/null || true

echo "━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 安装完成！"
echo ""
echo "请完成以下配置："
echo "1. 打开 https://huper.org/dashboard → API Key 页生成 Key"
echo "2. 编辑 $HOME/.amber-hunter/config.json"
echo "   填入 api_key 和 master_password"
echo ""
echo "服务管理命令："
echo "  启动: launchctl load $PLIST_PATH"
echo "  停止: launchctl unload $PLIST_PATH"
echo "  日志: tail -f \$HOME/Library/Logs/amber-hunter.log"
