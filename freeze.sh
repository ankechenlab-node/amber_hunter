#!/bin/bash
# Amber-Hunter Freeze Trigger
# 用法：amber freeze (Raycast)

curl -s -X POST http://localhost:18998/freeze | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('🌙 Amber-Hunter Freeze')
print('='*40)
print(f'Session: {d.get(\"session_key\", \"N/A\")}')
print(f'摘要：{d.get(\"summary\", \"N/A\")[:100]}')
print()
print('最近文件:')
for f in d.get('files', [])[:3]:
    print(f'  - {f[\"path\"]} ({f[\"size_kb\"]}KB)')
print()
print('✅ 数据已发送至琥珀冻结弹窗')
"
