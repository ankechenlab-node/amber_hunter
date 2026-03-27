#!/usr/bin/env node
/**
 * amber-proactive V3: 简化版，只把对话文本写入待处理队列
 * LLM 提取由 agent 在 heartbeat 时执行（使用 agent 自己的模型）
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const HOME = os.homedir();
const SESSIONS_DIR = path.join(HOME, '.openclaw', 'agents', 'main', 'sessions');
const PENDING_FILE = path.join(HOME, '.amber-hunter', 'pending_extract.jsonl');
const LOG_PATH = path.join(HOME, '.amber-hunter', 'amber-proactive.log');

function log(msg) {
  const ts = new Date().toISOString().slice(11, 19);
  fs.appendFileSync(LOG_PATH, `[${ts}] ${msg}\n`);
}

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

function extractTextFromContent(msg) {
  if (!msg) return '';
  let parsed = msg;
  if (typeof msg === 'string') {
    try { parsed = JSON.parse(msg); } catch { return ''; }
  }
  if (!parsed || typeof parsed !== 'object') return '';
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
      try { var d = JSON.parse(line); } catch { continue; }
      if (d.type === 'message') {
        const raw = d.message;
        if (!raw) continue;
        const text = extractTextFromContent(raw);
        if (text && text.trim().length > 10) {
          let role = '';
          if (typeof raw === 'object' && raw.role) role = raw.role;
          messages.push({ role, text: text.trim() });
        }
      }
    }
    return messages;
  } catch { return []; }
}

function buildConversationText(messages) {
  const recent = messages.slice(-20);
  return recent.map(m => `[${m.role}]: ${m.text}`).join('\n');
}

function alreadyQueued(sessionId) {
  try {
    if (!fs.existsSync(PENDING_FILE)) return false;
    const lines = fs.readFileSync(PENDING_FILE, 'utf8').split('\n');
    return lines.some(l => {
      try {
        const item = JSON.parse(l.trim());
        return item.session_id === sessionId;
      } catch { return false; }
    });
  } catch { return false; }
}

async function main() {
  const sessionPath = getLatestSession();
  if (!sessionPath) {
    log('No session file found');
    return;
  }

  const sessionId = path.basename(sessionPath, '.jsonl');

  // 每个 session 只入队一次
  if (alreadyQueued(sessionId)) {
    log(`Session ${sessionId} already queued, skipping`);
    return;
  }

  const messages = extractMessages(sessionPath);
  const MIN_MESSAGES = 20;
  if (messages.length < MIN_MESSAGES) {
    log(`Skipping: only ${messages.length} messages (need ${MIN_MESSAGES} to trigger auto-extract)`);
    return;
  }

  const conversation = buildConversationText(messages);
  const item = {
    session_id: sessionId,
    conversation,
    message_count: messages.length,
    created_at: Date.now(),
  };

  // 追加到 pending_extract.jsonl
  fs.appendFileSync(PENDING_FILE, JSON.stringify(item) + '\n');
  log(`Queued ${messages.length} messages from session ${sessionId} for extraction`);
}

main().catch(e => log('Error: ' + e.message));
