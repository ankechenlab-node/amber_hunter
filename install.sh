#!/bin/bash
# amber-hunter 安装脚本
# 用法: bash install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUNTER_DIR="$HOME/.openclaw/skills/amber-hunter"
PLIST_PATH="$HOME/Library/LaunchAgents/com.huper.amber-hunter.plist"

echo "🌙 Amber-Hunter 安装脚本"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. 安装依赖
echo "[1/4] 安装 Python 依赖..."
pip install -q -r "$HUNTER_DIR/requirements.txt" 2>/dev/null || \
pip3 install -q -r "$HUNTER_DIR/requirements.txt" 2>/dev/null
echo "  ✅ 依赖安装完成"

# 2. 初始化配置目录
echo "[2/4] 初始化配置..."
mkdir -p "$HOME/.amber-hunter"
CONFIG="$HOME/.amber-hunter/config.json"
if [ ! -f "$CONFIG" ]; then
    echo "{}" > "$CONFIG"
    echo "  → 已创建 $CONFIG"
fi

# 3. 生成或验证 api_key（存 config.json，给 huper.org 认证用）
API_KEY=$(python3 -c "
import json, os, secrets
path = os.path.expanduser('$CONFIG')
cfg = json.load(open(path)) if os.path.exists(path) else {}
key = cfg.get('api_key') or (secrets.token_urlsafe(32) if 'api_key' not in cfg else '')
if not cfg.get('api_key'):
    cfg['api_key'] = key
    json.dump(cfg, open(path,'w'), indent=2)
print(key)
" 2>/dev/null)

echo "  → API Key 已配置（用于连接 huper.org 云端）"

# 4. 安装 LaunchAgent（开机自启）
echo "[3/4] 配置开机自启..."
mkdir -p "$HOME/Library/LaunchAgents"
HOME_EXPANDED=$(eval echo ~)
sed "s|/Users/leo|$HOME_EXPANDED|g" "$HUNTER_DIR/com.huper.amber-hunter.plist" > "$PLIST_PATH"
chmod 644 "$PLIST_PATH"
echo "  → LaunchAgent 已安装"

# 5. 启动服务
echo "[4/4] 启动服务..."
if curl -s --max-time 1 http://localhost:18998/status > /dev/null 2>&1; then
    echo "  ✅ amber-hunter 已在运行"
else
    nohup python3 "$HUNTER_DIR/amber_hunter.py" >> "$HOME/.amber-hunter/amber-hunter.log" 2>&1 &
    sleep 2
    if curl -s --max-time 2 http://localhost:18998/status > /dev/null 2>&1; then
        echo "  ✅ amber-hunter 已启动"
    else
        echo "  ⚠️ 启动失败，请查看 $HOME/.amber-hunter/amber-hunter.log"
    fi
fi

# 6. 注册 Raycast 快捷命令（可选）
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 安装完成！"
echo ""
echo "📋 下一步："
echo "1. 打开 https://huper.org/dashboard →「加密」标签"
echo "2. 设置 master_password（本地加密密钥，存 macOS Keychain）"
echo "3. 点「同步到云端」测试连接"
echo ""
echo "🔧 服务管理："
echo "   状态: curl http://localhost:18998/status"
echo "   日志: tail -f $HOME/.amber-hunter/amber-hunter.log"
echo "   启动: launchctl load $PLIST_PATH"
echo "   停止: launchctl unload $PLIST_PATH"
echo ""
echo "🔗 GitHub: https://github.com/ankechenlab-node/amber_hunter"
