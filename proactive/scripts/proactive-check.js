#!/usr/bin/env node
/**
 * amber-proactive V4: Fully self-contained extraction
 * 
 * 三步全部在脚本内完成，cron 直接触发，不需要 agent 介入。
 * 
 * 触发方式：
 * - 自动: cron 每15分钟运行此脚本 → 检查阈值 → LLM提取 → 写胶囊
 * - 手动: agent 调用此脚本（任何对话量都触发）
 * 
 * 触发阈值：
 * - 自动模式: session 消息数 ≥ 20 条
 * - 手动模式: 无限制（任意对话量）
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const https = require('https');
const http = require('http');

const HOME = os.homedir();
const SESSIONS_DIR = path.join(HOME, '.openclaw', 'agents', 'main', 'sessions');
const PENDING_FILE = path.join(HOME, '.amber-hunter', 'pending_extract.jsonl');
const CONFIG_PATH   = path.join(HOME, '.amber-hunter', 'config.json');
const OPENCLAW_CONFIG = path.join(HOME, '.openclaw', 'openclaw.json');
const LOG_PATH = path.join(HOME, '.amber-hunter', 'amber-proactive.log');

const AMBER_PORT = 18998;
const MIN_MESSAGES_THRESHOLD = 20;

// ── Logging ────────────────────────────────────────────────────────────

function log(msg) {
  const ts = new Date().toISOString().slice(11, 19);
  fs.appendFileSync(LOG_PATH, `[${ts}] ${msg}\n`);
  console.log(`[${ts}] ${msg}`);
}

// ── Config ────────────────────────────────────────────────────────────

function getConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch { return {}; }
}

function getApiKey() {
  // Read from OpenClaw config: models.providers['minimax-cn'].apiKey
  try {
    const openclawConfig = JSON.parse(fs.readFileSync(OPENCLAW_CONFIG, 'utf8'));
    return openclawConfig?.models?.providers?.['minimax-cn']?.apiKey || '';
  } catch { return ''; }
}

// ── MiniMax LLM Call ─────────────────────────────────────────────────

function callMinimaxLLM(prompt, apiKey) {
  return new Promise((resolve, reject) => {
    const bodyStr = JSON.stringify({
      model: 'minimax-cn/MiniMax-M2.1-flash',
      max_tokens: 1024,
      messages: [{ role: 'user', content: prompt }],
    });

    const url = new URL('https://api.minimaxi.com/anthropic/v1/messages');
    const opts = {
      hostname: url.hostname,
      port: url.port || 443,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01',
        'Content-Length': Buffer.byteLength(bodyStr),
      },
    };

    const req = https.request(opts, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          const items = parsed.content || [];
          let text = '';
          for (const item of items) {
            if (item.type === 'text') { text = item.text; break; }
          }
          // 去掉可能的markdown包裹
          text = text.replace(/^```json\s*/i, '').replace(/```\s*$/i, '').trim();
          const result = JSON.parse(text);
          resolve(result.facts || []);
        } catch (e) {
          log(`[llm] Parse error: ${e.message}, raw: ${data.slice(0, 100)}`);
          resolve([]); // 失败不阻断
        }
      });
    });
    req.on('error', e => { log(`[llm] API error: ${e.message}`); resolve([]); });
    req.write(bodyStr);
    req.end();
  });
}

// ── Session Reading ────────────────────────────────────────────────────

function getLatestSession() {
  try {
    const files = fs.readdirSync(SESSIONS_DIR)
      .filter(f => f.endsWith('.jsonl'))
      .map(f => ({
        name: f,
        mtime: fs.statSync(path.join(SESSIONS_DIR, f)).mtime.getTime()
      }))
      .sort((a, b) => b.mtime - a.mtime);
    return files[0] ? path.join(SESSIONS_DIR, files[0].name) : null;
  } catch { return null; }
}

function extractMessages(sessionPath) {
  try {
    const content = fs.readFileSync(sessionPath, 'utf8');
    const lines = content.split('\n').filter(l => l.trim());
    const messages = [];
    for (const line of lines) {
      let d;
      try { d = JSON.parse(line); } catch { continue; }
      if (d.type !== 'message') continue;
      const raw = d.message;
      if (!raw || typeof raw !== 'object') continue;
      const parts = raw.content || [];
      let text = '';
      if (Array.isArray(parts)) {
        text = parts.map(p =>
          typeof p === 'string' ? p : (p && p.type === 'text' ? p.text : '')
        ).join('\n');
      } else if (typeof parts === 'string') {
        text = parts;
      }
      text = text.trim();
      if (text && text.length > 10) {
        messages.push({ role: raw.role || '?', text });
      }
    }
    return messages;
  } catch { return []; }
}

function buildConversationText(messages, maxChars = 8000) {
  const recent = messages.slice(-50);
  const text = recent.map(m => `[${m.role}]: ${m.text}`).join('\n');
  return text.length > maxChars ? text.slice(-maxChars) : text;
}

// ── Amber API ────────────────────────────────────────────────────────

function getAmberToken() {
  return new Promise(resolve => {
    http.get({ hostname: 'localhost', port: AMBER_PORT, path: '/token' }, res => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        try { resolve(JSON.parse(data).api_key); } catch { resolve(''); }
      });
    }).on('error', () => resolve(''));
  });
}

function writeCapsule(token, memo, content, tags) {
  return new Promise(resolve => {
    // memo 最多80字符，整句不断词
    const memoText = memo.length > 80 ? memo.slice(0, 77) + '…' : memo;
    const capsule = { memo: memoText, content, tags };
    const bodyStr = JSON.stringify(capsule);
    const opts = {
      hostname: 'localhost', port: AMBER_PORT, path: '/capsules',
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr),
      },
    };
    const req = http.request(opts, res => {
      res.resume();
      resolve(res.statusCode === 200 || res.statusCode === 201);
    });
    req.on('error', () => resolve(false));
    req.write(bodyStr);
    req.end();
  });
}

// ── Main ─────────────────────────────────────────────────────────────

async function main() {
  const isManual = process.argv.includes('--manual');
  const apiKey = getApiKey();

  if (!apiKey) {
    log('[main] No API key found in config, skipping');
    return;
  }

  const sessionPath = getLatestSession();
  if (!sessionPath) {
    log('[main] No session file found');
    return;
  }

  const sessionId = path.basename(sessionPath, '.jsonl');
  const messages = extractMessages(sessionPath);

  log(`[main] Session ${sessionId}: ${messages.length} messages`);

  // 检查阈值
  if (!isManual && messages.length < MIN_MESSAGES_THRESHOLD) {
    log(`[main] Skipping: ${messages.length} messages (need ≥ ${MIN_MESSAGES_THRESHOLD} for auto)`);
    return;
  }

  // 去重：当前 session 已在 pending_extract.jsonl 且消息数未增加则跳过
  if (fs.existsSync(PENDING_FILE)) {
    const lines = fs.readFileSync(PENDING_FILE, 'utf8').split('\n').filter(l => l.trim());
    for (const line of lines) {
      try {
        const item = JSON.parse(line);
        if (item.session_id === sessionId && item.message_count === messages.length) {
          log(`[main] Session ${sessionId} already queued with same message count, skipping`);
          return;
        }
      } catch {}
    }
  }

  const conversation = buildConversationText(messages);

  // 构建提取 prompt
  const prompt = `从以下对话中提取关键事实。只返回纯JSON，不要markdown，不要思考过程。

对话：
${conversation}

输出格式：
{"facts": [{"fact": "具体描述这个事实", "worth": true}]}`;

  log(`[llm] Calling MiniMax API...`);
  const facts = await callMinimaxLLM(prompt, apiKey);

  if (!facts || facts.length === 0) {
    log('[llm] No facts extracted, skipping');
    return;
  }

  const worthIt = facts.filter(f => f.worth);
  log(`[llm] Extracted ${facts.length} facts, ${worthIt.length} worth saving`);

  // 获取 amber-hunter 自己的 token（不是 MiniMax key）
  const amberToken = await getAmberToken();
  let written = 0;

  for (const { fact, worth } of facts) {
    if (!worth) continue;
    const ok = await writeCapsule(amberToken, fact, fact, 'auto-extract');
    if (ok) written++;
    log(`[capsule] ${ok ? '✅' : '❌'} ${fact.slice(0, 50)}`);
  }

  log(`[done] Wrote ${written}/${worthIt.length} capsules from ${messages.length} messages`);
}

main().catch(e => log('[error] ' + e.message));
