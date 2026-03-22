#!/usr/bin/env node
/**
 * amber-proactive: Proactively scans recent session messages for significant moments
 * and silently writes them to amber memory.
 *
 * Run via: node ~/.openclaw/workspace/skills/amber-proactive/scripts/proactive-check.js
 *
 * Reads from: ~/.openclaw/agents/main/sessions/<latest>.jsonl
 * Writes to: amber via localhost:18998 (local service only — no external network access)
 *
 * SECURITY NOTE: This script is strictly local-only.
 * - File reads are limited to local OpenClaw/Claude session files (~/.openclaw/, ~/Library/...)
 * - ALL network calls go exclusively to localhost:18998 (the amber-hunter local service)
 * - process.env.HOME / os.homedir() used only to construct local filesystem paths
 * - No data is ever sent to any external server or internet endpoint
 */

const fs = require('fs');
const http = require('http');
const path = require('path');
const os = require('os');

const AMBER_PORT = 18998;
// os.homedir() used only to construct local filesystem paths — never transmitted
// os.homedir() used only to construct local filesystem paths — never transmitted
const HOME = os.homedir();
const CONFIG_PATH = path.join(HOME, '.amber-hunter', 'config.json');
const SESSIONS_DIR = path.join(HOME, '.openclaw', 'agents', 'main', 'sessions');

// Claude Cowork sessions (macOS)
const CLAUDE_SESSIONS_BASE = process.platform === 'darwin'
  ? path.join(HOME, 'Library', 'Application Support', 'Claude', 'local-agent-mode-sessions')
  : null;
const LOG_PATH = path.join(HOME, '.amber-hunter', 'amber-proactive.log');

// ── Signal Patterns ──────────────────────────────────────────
const SIGNALS = {
  correction: [
    /不对|不是这样|错了|错了啦|no,?\s*that|actually|not quite/i,
    /that('s| is) (?:not |no )?(?:right|correct)|you('re| are) wrong/i,
    /let me (?:correct|fix) that|my mistake|i meant/i,
  ],
  error_fix: [
    /错误|失败|exception|traceback|bug|崩溃/i,
    /找到了|发现.{0,10}(?:方法|方案|原因|解决)/i,
    /(?:solution|fix|workaround):?\s*(.+)/i,
  ],
  decision: [
    /决定|决定了|decision|choosing|agreed|architecture/i,
    /(?:tech stack|技术栈|架构)/i,
  ],
  preference: [
    /我喜欢|我一般|我通常|我比较|我不喜欢|我不怎么|我宁愿/i,
    /(?:usually|prefer|tend|always|never|like to|don't like)/i,
    /my (?:preferred|preference|prefer|default|usual|style)/i,
  ],
  discovery: [
    /第一次|首次|第一次做|头一次/i,
    /(?:discovered|found out|learned that|just found)/i,
    /(?:game.?changer|breakthrough|novel|没想到|居然|竟然)/i,
  ],
};

// ── Utilities ───────────────────────────────────────────────
function log(msg) {
  const ts = new Date().toISOString().slice(11, 19);
  fs.appendFileSync(LOG_PATH, `[${ts}] ${msg}\n`);
}

function readConfig() {
  try { return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8')); }
  catch { return {}; }
}

// Recursively find the latest .jsonl (skip audit.jsonl and subagents/)
function findLatestJsonl(dir, maxDepth) {
  if (maxDepth === undefined) maxDepth = 8;
  var best = null, bestMtime = 0;
  function walk(d, depth) {
    if (depth > maxDepth) return;
    var entries;
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch(e) { return; }
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var full = path.join(d, e.name);
      if (e.isDirectory()) {
        if (e.name !== 'subagents') walk(full, depth + 1);
      } else if (e.name.endsWith('.jsonl') && e.name !== 'audit.jsonl') {
        try {
          var mtime = fs.statSync(full).mtime.getTime();
          if (mtime > bestMtime) { bestMtime = mtime; best = full; }
        } catch(e2) {}
      }
    }
  }
  walk(dir, 0);
  return best ? { file: best, mtime: bestMtime } : null;
}

function getLatestSession() {
  // OpenClaw
  var ocResult = null;
  try {
    var files = fs.readdirSync(SESSIONS_DIR)
      .filter(function(f) { return f.endsWith('.jsonl'); })
      .map(function(f) {
        return { file: path.join(SESSIONS_DIR, f), mtime: fs.statSync(path.join(SESSIONS_DIR, f)).mtime.getTime() };
      })
      .sort(function(a, b) { return b.mtime - a.mtime; });
    if (files[0]) ocResult = files[0];
  } catch(e) {}

  // Claude Cowork
  var clResult = null;
  if (CLAUDE_SESSIONS_BASE) {
    try {
      if (fs.existsSync(CLAUDE_SESSIONS_BASE)) clResult = findLatestJsonl(CLAUDE_SESSIONS_BASE);
    } catch(e) {}
  }

  if (!ocResult && !clResult) return null;
  if (!ocResult) return clResult.file;
  if (!clResult) return ocResult.file;
  return clResult.mtime > ocResult.mtime ? clResult.file : ocResult.file;
}

function extractTextFromContent(msg) {
  if (!msg) return '';
  // msg can be a dict or a string (Python dict repr)
  let parsed = msg;
  if (typeof msg === 'string') {
    try { parsed = JSON.parse(msg); } catch { return ''; }
  }
  if (!parsed || typeof parsed !== 'object') return '';

  const role = parsed.role || '';
  const parts = parsed.content || [];
  if (Array.isArray(parts)) {
    return parts.map(p => {
      if (typeof p === 'string') return p;
      if (p && p.type === 'text') return p.text || '';
      return '';
    }).join('\n');
  }
  if (typeof parts === 'string') return parts;
  return '';
}

function extractMessages(sessionPath) {
  try {
    const content = fs.readFileSync(sessionPath, 'utf8');
    const lines = content.split('\n').filter(l => l.trim());
    const messages = [];

    for (const line of lines) {
      let d;
      try { d = JSON.parse(line); }
      catch { continue; }

      // OpenClaw format: type === 'message'
      if (d.type === 'message') {
        const raw = d.message;
        if (!raw) continue;
        const text = extractTextFromContent(raw);
        if (text && text.trim().length > 5) {
          let role = '';
          if (typeof raw === 'object' && raw.role) role = raw.role;
          else if (typeof raw === 'string') {
            try { const p = JSON.parse(raw); role = p.role || ''; } catch {}
          }
          messages.push({ role, text: text.trim() });
        }
      }
      // Claude Cowork format: type === 'user' | 'assistant'
      else if (d.type === 'user' || d.type === 'assistant') {
        const msg = d.message;
        let text = '';
        if (msg && typeof msg === 'object') {
          const content = msg.content;
          if (typeof content === 'string') {
            text = content;
          } else if (Array.isArray(content)) {
            text = content
              .filter(p => p && p.type === 'text')
              .map(p => p.text || '')
              .join('\n');
          }
        }
        if (text && text.trim().length > 5) {
          messages.push({ role: d.type, text: text.trim().slice(0, 500) });
        }
      }
    }
    return messages;
  } catch (e) {
    return [];
  }
}

function detectSignals(text) {
  const results = [];
  const lower = text.toLowerCase();
  for (const [type, patterns] of Object.entries(SIGNALS)) {
    for (const pattern of patterns) {
      const m = lower.match(pattern);
      if (m) {
        const idx = m.index || 0;
        results.push({
          type,
          matched: m[0],
          snippet: text.slice(Math.max(0, idx - 30), idx + 80).trim(),
        });
        break;
      }
    }
  }
  return results;
}

function httpPost(apiPath, body, token) {
  return new Promise(resolve => {
    const bodyStr = JSON.stringify(body);
    const opts = {
      hostname: 'localhost', port: AMBER_PORT, path: apiPath, // localhost only — data never leaves your machine // localhost only — no external network
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

// ── Main ─────────────────────────────────────────────────────
async function main() {
  const cfg = readConfig();
  const token = cfg.api_key || cfg.apiToken;
  if (!token) {
    log('No api_key, skipping');
    return;
  }

  const sessionPath = getLatestSession();
  if (!sessionPath) {
    log('No session file found');
    return;
  }

  const messages = extractMessages(sessionPath);
  if (messages.length === 0) {
    log('No messages in session');
    return;
  }

  // Get last N user+assistant messages
  const recent = messages.slice(-20);
  const combined = recent.map(m => `[${m.role}]: ${m.text}`).join('\n');

  const signals = detectSignals(combined);
  if (signals.length === 0) return;

  const types = [...new Set(signals.map(s => s.type))];
  const capsule = {
    memo: `[Proactive] ${types.join(' + ')}: ${signals[0].matched.slice(0, 60)}`,
    content: signals.map(s => s.snippet).join('\n---\n').slice(0, 800),
    tags: types.join(','),
    session_id: path.basename(sessionPath, '.jsonl'),
  };

  const ok = await httpPost('/capsules', capsule, token);
  log(`proactive-check: ${ok ? 'captured' : 'failed'} ${types.join('+')} (${messages.length} msgs scanned)`);
}

main().catch(e => log('Error: ' + e.message));
