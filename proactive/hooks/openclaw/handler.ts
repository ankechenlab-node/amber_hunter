#!/usr/bin/env node
/**
 * amber-proactive hook handler
 * Runs on agent:response events — silently captures significant moments to amber
 *
 * Signals detected:
 * - User corrections ("不对", "actually", "错了")
 * - Error fixes (exec failure → solution found)
 * - Key decisions (architecture, approach confirmed)
 * - User preferences ("I prefer...", "I usually...")
 * - First successes
 * - Important discoveries
 */

import * as fs from 'fs';
import * as path from 'path';
import * as http from 'http';

// ── Config ────────────────────────────────────────────────────
const AMBER_PORT = 18998;
// process.env.HOME used only to locate local config/log paths — never transmitted
const CONFIG_PATH = path.join(process.env.HOME || '', '.amber-hunter', 'config.json');
const LOG_PATH    = path.join(process.env.HOME || '', '.amber-hunter', 'amber-proactive.log');

// ── Signal Patterns ───────────────────────────────────────────
const SIGNALS = {
  correction: [
    /不对|不是这样|错了|actually|no,?\s*(?:it's|you|that)|wait,?\s*(?:that's|not)|you('re| are) wrong|incorrect/i,
    /not quite|that('s| is) not right|i meant|i('d| would) say/i,
    /let me (?:correct|fix) that|my mistake/i,
  ],
  error_fix: [
    /(?:error|failed|exception|traceback|crash)(?:ed|ing)?.*(?:found|got|discovered|solved)/i,
    /(?:found|got|discovered) the (?:issue|problem|cause|bug)/i,
    /(?:solution|fix|workaround):?\s*[—\-]?\s*["']?(\S+)/i,
    /(?:try this|use this|here('s| is) (?:a|how))/i,
  ],
  decision: [
    /(?:let'?s|lets|we('ll| will)|i('ll| will)|shall we)\s+(?:go with|use|try|build|implement|go ahead)/i,
    /(?:decided|decision|chose|choosing|agreed):\s*(.+)/i,
    /(?:architecture|approach|strategy|tech stack|stack):\s*(.+)/i,
    /(?:settled on|going with|we('re| are) using)\s+(.+)/i,
  ],
  preference: [
    /i (?:usually|prefer|tend|always|never|like to|don't like)/i,
    /my (?:preferred|preference|prefer|default|usual|style)/i,
    /(?:for me|i('d| would) prefer|i('m| am) (?:more|less) comfortable)/i,
    /(?:don'?t|dont) (?:usually|like|want|need) to\s+(\S+)/i,
  ],
  discovery: [
    /(?:first (?:time|first)|just (?:discovered|found|got))/i,
    /(?:discovered|found out|learned that)/i,
    /(?:game.?changer|breakthrough|novel|unexpected)/i,
    /(?:never knew|didn'?t know (?:that|you|it)|surprisingly)/i,
  ],
};

// ── Utilities ───────────────────────────────────────────────
function log(msg: string) {
  const ts = new Date().toISOString().slice(11, 19);
  fs.appendFileSync(LOG_PATH, `[${ts}] ${msg}\n`);
}

function readConfig(): { api_key?: string; apiToken?: string } {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch {
    return {};
  }
}

async function httpPost(path: string, body: object, token: string): Promise<boolean> {
  return new Promise((resolve) => {
    const opts = {
      hostname: 'localhost', // localhost only — data never leaves your machine // localhost only — no external network
      port: AMBER_PORT,
      path,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    };
    const req = http.request(opts, (res) => {
      res.resume();
      resolve(res.statusCode === 200 || res.statusCode === 201);
    });
    req.on('error', () => resolve(false));
    req.write(JSON.stringify(body));
    req.end();
  });
}

function detectSignals(text: string): Array<{type: string; matched: string; snippet: string}> {
  const results = [];
  const lower = text.toLowerCase();
  for (const [type, patterns] of Object.entries(SIGNALS)) {
    for (const pattern of patterns) {
      const m = lower.match(pattern);
      if (m) {
        const snippet = text.slice(Math.max(0, m.index! - 40), m.index! + 80).trim();
        results.push({ type, matched: m[0], snippet });
        break; // one signal per type max
      }
    }
  }
  return results;
}

// ── Main ─────────────────────────────────────────────────────
async function main() {
  // Read event payload from stdin
  let event: { response?: string; userMessage?: string; sessionKey?: string };
  try {
    event = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
  } catch {
    log('Failed to parse stdin event');
    process.exit(0);
  }

  const { response = '', userMessage = '' } = event;
  const combined = `${userMessage}\n${response}`.trim();
  if (combined.length < 20) { process.exit(0); }

  const signals = detectSignals(combined);
  if (signals.length === 0) { process.exit(0); }

  const cfg = readConfig();
  const token = cfg.api_key || cfg.apiToken;
  if (!token) { log('No api_key in config, skipping'); process.exit(0); }

  // Build capsule payload
  const types = [...new Set(signals.map(s => s.type))];
  const snippet = signals.map(s => s.snippet).join(' | ');
  const capsule = {
    memo: `[Proactive] ${types.join(' + ')}: ${signals[0].matched}`,
    content: snippet.slice(0, 1000),
    tags: types.join(','),
    session_id: event.sessionKey || null,
  };

  const ok = await httpPost('/capsules', capsule, token);
  if (ok) {
    log(`Captured: ${types.join('+')} for session ${event.sessionKey}`);
  } else {
    log(`Failed to capture: ${types.join('+')}`);
  }

  process.exit(0);
}

main();
