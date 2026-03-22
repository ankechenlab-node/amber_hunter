#!/usr/bin/env node
/**
 * amber-proactive hook handler (JavaScript version)
 * Runs on agent:response events — silently captures significant moments to amber
 *
 * SECURITY NOTE: This script is strictly local-only.
 * - process.env.HOME is used ONLY to build local filesystem paths (~/.amber-hunter/)
 * - ALL network calls go exclusively to localhost:18998 (the amber-hunter local service)
 * - No data is ever sent to any external server or internet endpoint
 * - Optional cloud sync is a separate user-initiated action handled by amber-hunter itself
 */
const fs = require('fs');
const http = require('http');
const path = require('path');

const AMBER_PORT = 18998;
// process.env.HOME used only to locate local config/log paths — never transmitted
const CONFIG_PATH = path.join(process.env.HOME || '', '.amber-hunter', 'config.json');
const LOG_PATH    = path.join(process.env.HOME || '', '.amber-hunter', 'amber-proactive.log');

const SIGNALS = {
  // 用户纠正了 AI 的错误
  correction: [
    /不对|不是这样|错了|错了啦|no,?\s*(?:that|it's|you)|actually|not quite/i,
    /that('s| is) (?:not |no )?(?:right|correct)|you('re| are) wrong/i,
    /let me (?:correct|fix) that|my mistake|i meant/i,
    /\bwait\b.*(?:actually|not|no)/i,
  ],
  // 错误被解决：发现了方法/方案
  error_fix: [
    /错误|失败|exception|traceback|bug|崩溃/i,
    /找到了|发现.{0,10}(?:方法|方案|原因|解决)/i,
    /(?:solution|fix|workaround):?\s*(.+)/i,
    /(?:试试|用这个|这个可以|这样就行)/i,
  ],
  // 关键决策：确定了架构/方案/方向
  decision: [
    /决定|决定了|decision|choosing|agreed|architecture/i,
    /(?:用|用\w+|采用)(?:FastAPI|Flask|React|Vue|Python|JS|TS|Go|Rust)/i,
    /(?:go with|going with|settled on|using)\s+(\S+)/i,
    /(?:tech stack|技术栈|架构)/i,
  ],
  // 用户偏好：明确表达个人喜好
  preference: [
    /我喜欢|我一般|我通常|我比较|我不喜欢|我不怎么|我宁愿/i,
    /(?:usually|prefer|tend|always|never|like to|don't like|don't usually)/i,
    /my (?:preferred|preference|prefer|default|usual|style|approach)/i,
    /(?:for me|i('d| would) prefer|i('m| am) more comfortable)/i,
  ],
  // 重要发现/第一次做到
  discovery: [
    /第一次|首次|第一次做|头一次/i,
    /(?:discovered|found out|learned that|just found)/i,
    /(?:game.?changer|breakthrough|novel|没想到|居然|竟然)/i,
    /原来.{0,15}(?:可以|要|是|需要)/i,
  ],
};

function log(msg) {
  const ts = new Date().toISOString().slice(11, 19);
  fs.appendFileSync(LOG_PATH, `[${ts}] ${msg}\n`);
}

function readConfig() {
  try { return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8')); }
  catch { return {}; }
}

function httpPost(apiPath, body, token) {
  return new Promise(resolve => {
    const bodyStr = JSON.stringify(body);
    const opts = {
      hostname: 'localhost', port: AMBER_PORT, path: apiPath, // localhost only — data never leaves your machine
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

function detectSignals(text) {
  const results = [];
  const lower = text.toLowerCase();
  for (const [type, patterns] of Object.entries(SIGNALS)) {
    for (const pattern of patterns) {
      const m = lower.match(pattern);
      if (m) {
        const idx = m.index || 0;
        const snippet = text.slice(Math.max(0, idx - 40), idx + 80).trim();
        results.push({ type, matched: m[0], snippet });
        break;
      }
    }
  }
  return results;
}

async function main() {
  let event = {};
  try {
    const raw = fs.readFileSync('/dev/stdin', 'utf8');
    event = JSON.parse(raw);
  } catch {
    process.exit(0);
  }

  const { response = '', userMessage = '' } = event;
  const combined = `${userMessage}\n${response}`.trim();
  if (combined.length < 20) { process.exit(0); }

  const signals = detectSignals(combined);
  if (signals.length === 0) { process.exit(0); }

  const cfg = readConfig();
  const token = cfg.api_key || cfg.apiToken;
  if (!token) { log('No api_key, skip'); process.exit(0); }

  const types = [...new Set(signals.map(s => s.type))];
  const capsule = {
    memo: `[Proactive] ${types.join(' + ')}: ${signals[0].matched.slice(0, 60)}`,
    content: signals.map(s => s.snippet).join('\n---\n').slice(0, 1000),
    tags: types.join(','),
    session_id: event.sessionKey || null,
  };

  const ok = await httpPost('/capsules', capsule, token);
  log(`amber-proactive: ${ok ? 'captured' : 'failed'} — ${types.join('+')}`);
  process.exit(0);
}

main();
