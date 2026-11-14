/**
 * Swarm Mix Sidecar Backend (MIT License)
 *
 * Read-only telemetry & live board server for Antigravity native Swarms,
 * plus cross-host multi-agent ledger inspection (Antigravity / Claude Code / Codex).
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync, watchFile } from 'node:fs';
import { createServer } from 'node:http';
import { homedir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = join(__dirname, '..', '..');
const SWARM_TOKENS_PY = join(PLUGIN_ROOT, 'skills', 'swarm-mix', 'scripts', 'swarm_tokens.py');
const ID_RE = /^[0-9a-zA-Z_-]{4,64}$/;

let SidecarApp;
let Response;
try {
  if (!process.env.ANTIGRAVITY_SIDECAR_WEB_PORT) throw new Error('standalone');
  ({ SidecarApp, Response } = await import('sidecar_sdk'));
} catch {
  // Standalone local fallback for CLI smoke testing outside Antigravity LS
  Response = class StandaloneResponse {
    constructor(body, opts = {}) {
      this.body = body;
      this.status = opts.status || 200;
      this.contentType = opts.contentType || 'text/plain; charset=utf-8';
      this.headers = opts.headers || {};
    }
  };
  SidecarApp = class StandaloneSidecarApp {
    constructor() {
      this.config = { conversationId: process.env.ANTIGRAVITY_CONVERSATION_ID || '' };
      this._pages = new Map();
      this._apis = new Map();
    }
    page(route, handler) {
      this._pages.set(route, handler);
    }
    api(route, handler) {
      this._apis.set(route, handler);
    }
    run() {
      const port = Number(process.env.PORT || process.env.ANTIGRAVITY_SIDECAR_WEB_PORT || 0);
      const srv = createServer(async (req, res) => {
        const u = new URL(req.url || '/', 'http://127.0.0.1');
        if (u.pathname === '/preload.js') {
          res.writeHead(200, { 'Content-Type': 'application/javascript' });
          res.end('');
          return;
        }
        if (this._pages.has(u.pathname)) {
          const html = await this._pages.get(u.pathname)(Object.fromEntries(u.searchParams));
          res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
          res.end(html);
          return;
        }
        if (this._apis.has(u.pathname)) {
          const out = await this._apis.get(u.pathname)(Object.fromEntries(u.searchParams));
          if (out instanceof Response) {
            res.writeHead(out.status, { 'Content-Type': out.contentType, ...out.headers });
            res.end(out.body);
          } else {
            res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
            res.end(JSON.stringify(out));
          }
          return;
        }
        res.writeHead(404);
        res.end('Not found');
      });
      srv.listen(port, '127.0.0.1', () => {
        const addr = srv.address();
        console.log(`[swarm_mix_board] standalone listening on 127.0.0.1:${addr?.port}`);
      });
    }
  };
}

let SqliteDatabaseSync = null;
try {
  ({ DatabaseSync: SqliteDatabaseSync } = await import('node:sqlite'));
} catch {
  SqliteDatabaseSync = null;
}

// ---------------------------------------------------------------------------
// App Data & File Caching
// ---------------------------------------------------------------------------

function findAppDataDir() {
  const execDir = process.env.ANTIGRAVITY_EXECUTABLE_DATA_DIR;
  if (execDir) {
    for (let d = execDir; d !== dirname(d); d = dirname(d)) {
      if (basename(d) === 'sidecar_data') {
        const root = dirname(d);
        if (existsSync(join(root, 'brain'))) return root;
      }
    }
  }
  const envDir = process.env.ANTIGRAVITY_DATA_DIR;
  if (envDir && existsSync(join(envDir, 'brain'))) return envDir;
  const fallback = join(homedir(), '.gemini', 'antigravity');
  return fallback;
}

const fileCache = new Map(); // path -> { mtimeMs, size, parsed }
function readCached(filePath, parser) {
  let st;
  try {
    st = statSync(filePath);
  } catch {
    fileCache.delete(filePath);
    return null;
  }
  const prev = fileCache.get(filePath);
  if (prev && prev.mtimeMs === st.mtimeMs && prev.size === st.size) {
    return prev.parsed;
  }
  let text = '';
  try {
    text = readFileSync(filePath, 'utf-8');
  } catch {
    return null;
  }
  const parsed = parser(text, st);
  fileCache.set(filePath, { mtimeMs: st.mtimeMs, size: st.size, parsed });
  return parsed;
}

function safeInt(v) {
  if (v === null || v === undefined || typeof v === 'boolean') return 0;
  if (typeof v === 'number') return Number.isFinite(v) ? Math.trunc(v) : 0;
  const n = Number.parseInt(String(v), 10);
  return Number.isFinite(n) && String(n) === String(v).trim() ? n : 0;
}

function stripQuotes(v) {
  if (v === null || v === undefined) return '';
  const s = String(v).trim();
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    try {
      return String(JSON.parse(s));
    } catch {
      return s.slice(1, -1);
    }
  }
  return s;
}

function classifyTier(model, role, name) {
  const m = String(model || '').toLowerCase().replace(/\*$/, '');
  const r = String(role || '').toLowerCase();
  const n = String(name || '').toLowerCase();
  if (n.includes('verifier') || r.includes('verifier')) return 'verify';
  if (/(?:^|[-_\s])(hydrogen|flash|mini|nano|haiku|lite|low)(?:$|[-_\s\d)])/.test(m)) {
    return 'l3';
  }
  if (/(?:^|[-_\s])(argon|sonnet|luna)(?:$|[-_\s\d)])/.test(m)) return 'l2';
  if (/(?:^|[-_\s])(carbon|opus|pro|astra|o3)(?:$|[-_\s\d)])/.test(m) || /^gpt-[56](?!.*-mini)/.test(m)) return 'l1';
  return 'l2';
}

// ---------------------------------------------------------------------------
// Transcript & Swarm Parsing
// ---------------------------------------------------------------------------

function parseTranscriptFile(filePath) {
  return readCached(filePath, (text, st) => {
    const plannerSteps = [];
    let firstSwarmStep = null;
    let lastSwarmToolStep = null;
    let firstUserAfterSwarm = null;
    let lastUserStep = null;
    let lastStepType = '';
    let lastWaitDone = false;
    let latestActivity = '';
    const subagentModels = [];
    const subagents = [];
    const codexBridges = [];

    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      let d;
      try {
        d = JSON.parse(line);
      } catch {
        continue;
      }
      if (!d || typeof d !== 'object' || Array.isArray(d)) continue;

      const stepIdx = typeof d.step_index === 'number' ? d.step_index : null;
      const dtype = String(d.type || '');
      lastStepType = dtype;

      if (dtype === 'USER_INPUT' && stepIdx !== null) {
        lastUserStep = stepIdx;
        if (lastSwarmToolStep !== null && firstUserAfterSwarm === null && stepIdx > lastSwarmToolStep) {
          firstUserAfterSwarm = stepIdx;
        }
      }

      const tcalls = Array.isArray(d.tool_calls) ? d.tool_calls : [];
      if (dtype === 'PLANNER_RESPONSE') {
        plannerSteps.push({
          stepIdx,
          inTok: safeInt(d.input_tokens),
          cacheTok: safeInt(d.cache_read_tokens),
          outTok: safeInt(d.output_tokens),
        });
        lastWaitDone = false;

        // Current turn activity label
        if (tcalls.length > 0) {
          const lastTc = tcalls[tcalls.length - 1];
          const args = lastTc?.args && typeof lastTc.args === 'object' ? lastTc.args : {};
          const summary = stripQuotes(args.toolSummary) || lastTc?.name || '';
          if (summary) latestActivity = summary;
        } else if (typeof d.thinking === 'string' && d.thinking.trim()) {
          const firstLine = d.thinking.trim().split('\n')[0].replace(/^#+\s*/, '').slice(0, 64);
          if (firstLine) latestActivity = firstLine;
        } else if (typeof d.content === 'string' && d.content.trim()) {
          latestActivity = '';
        }
      }

      for (const tc of tcalls) {
        if (!tc || typeof tc !== 'object') continue;
        const tname = String(tc.name || '');
        const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
        if (tname === 'swarm') {
          const action = stripQuotes(args.action);
          if ((action === 'add' || action === 'title') && stepIdx !== null && firstSwarmStep === null) {
            firstSwarmStep = lastUserStep !== null ? lastUserStep : stepIdx;
          }
          if (firstSwarmStep !== null && stepIdx !== null) {
            lastSwarmToolStep = stepIdx;
            firstUserAfterSwarm = null;
          }
        } else if (tname === 'read_board' || tname === 'post_message' || tname === 'swarm_wait') {
          if (firstSwarmStep !== null && stepIdx !== null) {
            lastSwarmToolStep = stepIdx;
            firstUserAfterSwarm = null;
          }
        } else if (tname === 'invoke_subagent') {
          const stype = stripQuotes(args.TypeName) || 'subagent';
          const smodel = stripQuotes(args.Model) || 'inherit';
          subagentModels.push({ stype, smodel });
        }
      }

      if (dtype === 'GENERIC' && typeof d.content === 'string') {
        if (d.content.includes('swarm_wait') || (tcalls.length === 0 && d.status === 'DONE' && d.content.includes('"since"'))) {
          lastWaitDone = true;
        }
        if (d.content.includes('[CODEX_BRIDGE_USAGE]')) {
          for (const rawLine of d.content.split('\n')) {
            const s = rawLine.trim();
            if (s.startsWith('[CODEX_BRIDGE_USAGE] {') && s.endsWith('}')) {
              try {
                const cb = JSON.parse(s.slice('[CODEX_BRIDGE_USAGE] '.length));
                if (cb && typeof cb === 'object' && typeof cb.model === 'string' && cb.model.trim()) {
                  const t = safeInt(cb.turns);
                  const i = safeInt(cb.input_tokens);
                  const c = safeInt(cb.cache_read_tokens);
                  const o = safeInt(cb.output_tokens);
                  if (t > 0 || i > 0 || o > 0) {
                    codexBridges.push({ stepIdx, model: cb.model.trim(), turns: t, inTok: i, cacheTok: c, outTok: o });
                  }
                }
              } catch {
                // ignore malformed telemetry
              }
            }
          }
        }
        if (d.content.includes('"conversationId"') && subagentModels.length > 0) {
          for (const rawLine of d.content.split('\n')) {
            const idx = rawLine.indexOf('{');
            if (idx !== -1 && rawLine.includes('"conversationId"')) {
              try {
                const obj = JSON.parse(rawLine.slice(idx));
                if (obj && typeof obj === 'object' && obj.conversationId && subagentModels.length > 0) {
                  const meta = subagentModels.shift();
                  subagents.push({
                    cid: String(obj.conversationId),
                    stype: meta.stype,
                    smodel: meta.smodel,
                  });
                }
              } catch {
                // ignore
              }
            }
          }
        }
      }
    }

    // Determine whether the agent's latest turn has parked
    let inferredRunning = false;
    if (plannerSteps.length > 0) {
      inferredRunning = lastStepType !== 'PLANNER_RESPONSE' ? !lastWaitDone : false;
    }

    return {
      mtimeMs: st.mtimeMs,
      plannerSteps,
      firstSwarmStep,
      endSwarmStep: firstUserAfterSwarm !== null ? firstUserAfterSwarm - 1 : null,
      latestActivity,
      inferredRunning,
      subagents,
      codexBridges,
    };
  });
}

function sumCodexBridges(parsedTp, minStep = null, maxStep = null) {
  const byModel = new Map();
  if (!parsedTp?.codexBridges?.length) return byModel;
  for (const cb of parsedTp.codexBridges) {
    if (cb.stepIdx !== null) {
      if (minStep !== null && cb.stepIdx < minStep) continue;
      if (maxStep !== null && cb.stepIdx > maxStep) continue;
    }
    const prev = byModel.get(cb.model) || { turns: 0, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0 };
    prev.turns += cb.turns;
    prev.input_tokens += cb.inTok;
    prev.cache_read_tokens += cb.cacheTok;
    prev.output_tokens += cb.outTok;
    byModel.set(cb.model, prev);
  }
  return byModel;
}

function sumSteps(parsedTp, minStep = null, maxStep = null) {
  if (!parsedTp) return { turns: 0, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0 };
  let turns = 0;
  let inTok = 0;
  let cacheTok = 0;
  let outTok = 0;
  for (const s of parsedTp.plannerSteps) {
    if (s.stepIdx !== null) {
      if (minStep !== null && s.stepIdx < minStep) continue;
      if (maxStep !== null && s.stepIdx > maxStep) continue;
    }
    turns += 1;
    inTok += s.inTok;
    cacheTok += s.cacheTok;
    outTok += s.outTok;
  }
  return { turns, input_tokens: inTok, cache_read_tokens: cacheTok, output_tokens: outTok };
}

const MENTION_RE = /(?:^|[^a-zA-Z0-9_-])@([a-zA-Z0-9][a-zA-Z0-9_-]*)/g;
function parseBoardFile(filePath) {
  return readCached(filePath, (text, st) => {
    const messages = [];
    let head = 0;
    for (const raw of text.split('\n')) {
      const line = raw.trim();
      if (!line) continue;
      try {
        const m = JSON.parse(line);
        if (!m || typeof m !== 'object') continue;
        const seq = Number(m.seq) || messages.length + 1;
        if (seq > head) head = seq;
        const body = String(m.text ?? '');
        const mentions = [];
        let isAll = false;
        for (const hit of body.matchAll(MENTION_RE)) {
          const target = hit[1];
          if (target.toLowerCase() === 'all') {
            isAll = true;
          } else if (!mentions.includes(target)) {
            mentions.push(target);
          }
        }
        messages.push({
          seq,
          agent: String(m.agent || 'unknown'),
          text: body,
          time: String(m.time || ''),
          channel: String(m.channel || 'main'),
          mentions,
          all: isAll,
        });
      } catch {
        // skip malformed line
      }
    }
    return { mtimeMs: st.mtimeMs, head, messages };
  });
}

// ---------------------------------------------------------------------------
// Resolving Conversation -> Swarm
// ---------------------------------------------------------------------------

let rosterScanCache = { at: 0, byWorker: new Map(), swarms: [] };
function scanLocalSwarms(appData) {
  const now = Date.now();
  if (now - rosterScanCache.at < 2500) return rosterScanCache;
  const brainDir = join(appData, 'brain');
  const byWorker = new Map();
  const swarms = [];
  let entries = [];
  try {
    entries = readdirSync(brainDir);
  } catch {
    entries = [];
  }
  for (const cid of entries) {
    if (!ID_RE.test(cid)) continue;
    const mPath = join(brainDir, cid, '.system_generated', 'swarm', 'members.json');
    const roster = readCached(mPath, (txt, st) => {
      try {
        const obj = JSON.parse(txt);
        return obj && typeof obj === 'object' ? { ...obj, _mtimeMs: st.mtimeMs } : null;
      } catch {
        return null;
      }
    });
    if (!roster || !roster.members || typeof roster.members !== 'object') continue;
    swarms.push({ cid, title: roster.title || cid.slice(0, 8), mtimeMs: roster._mtimeMs || 0 });
    for (const [mName, mInfo] of Object.entries(roster.members)) {
      if (mInfo && typeof mInfo.conversation_id === 'string') {
        byWorker.set(mInfo.conversation_id, { swarmCid: cid, memberName: mName });
      }
    }
  }
  swarms.sort((a, b) => b.mtimeMs - a.mtimeMs);
  rosterScanCache = { at: now, byWorker, swarms };
  return rosterScanCache;
}

function querySubagentsFromDb(appData, rootCid) {
  if (!SqliteDatabaseSync) return null;
  const dbPath = join(appData, 'conversation_summaries.db');
  if (!existsSync(dbPath)) return null;
  let db;
  try {
    const uri = `${pathToFileURL(dbPath).href}?mode=ro`;
    db = new SqliteDatabaseSync(uri, { open: true });
    const stmt = db.prepare(`
      SELECT conversation_id, agent_name, status, killed
      FROM conversation_summaries
      WHERE parent_conversation_id = ?
    `);
    return stmt.all(rootCid);
  } catch {
    return null;
  } finally {
    try {
      db?.close();
    } catch {
      // ignore
    }
  }
}

function buildAgySwarmSnapshot(appData, swarmCid, meName, fullSession) {
  const swarmFolder = join(appData, 'brain', swarmCid, '.system_generated', 'swarm');
  const membersPath = join(swarmFolder, 'members.json');
  const boardPath = join(swarmFolder, 'board.jsonl');

  const roster = readCached(membersPath, (txt, st) => {
    try {
      const o = JSON.parse(txt);
      return o && typeof o === 'object' ? { ...o, _mtimeMs: st.mtimeMs } : null;
    } catch {
      return null;
    }
  });
  if (!roster || !roster.members) return null;

  const board = parseBoardFile(boardPath) || { mtimeMs: 0, head: 0, messages: [] };
  const postCounts = new Map();
  const lastPostByAgent = new Map();
  for (const msg of board.messages) {
    postCounts.set(msg.agent, (postCounts.get(msg.agent) || 0) + 1);
    lastPostByAgent.set(msg.agent, { seq: msg.seq, time: msg.time });
  }

  const parentTpPath = join(appData, 'brain', swarmCid, '.system_generated', 'logs', 'transcript.jsonl');
  const parentTp = parseTranscriptFile(parentTpPath);

  const memberEntries = Object.entries(roster.members);
  const workerEntries = memberEntries.filter(([k, v]) => (v?.role || k) !== 'coordinator');
  const allWorkersKilled = workerEntries.length > 0 && workerEntries.every(([, v]) => Boolean(v?.killed));

  const minCoordStep = fullSession ? null : (parentTp?.firstSwarmStep ?? null);
  const maxCoordStep = !fullSession && allWorkersKilled ? (parentTp?.endSwarmStep ?? null) : null;

  let latestMtime = Math.max(roster._mtimeMs || 0, board.mtimeMs || 0, parentTp?.mtimeMs || 0);
  const seenCids = new Set();
  const agents = [];

  let coordModel = '-';
  for (const [mName, mInfo] of memberEntries) {
    if (!mInfo || typeof mInfo !== 'object') continue;
    const cid = String(mInfo.conversation_id || (mName === 'coordinator' ? swarmCid : ''));
    if (cid) seenCids.add(cid);
    const isCoord = mInfo.role === 'coordinator' || mName === 'coordinator';
    const model = String(mInfo.applied_model || mInfo.model || mInfo.desired_model || '-');
    if (isCoord && model !== '-') coordModel = model;

    const tpPath = cid ? join(appData, 'brain', cid, '.system_generated', 'logs', 'transcript.jsonl') : '';
    const tp = isCoord ? parentTp : (tpPath ? parseTranscriptFile(tpPath) : null);
    if (tp?.mtimeMs) latestMtime = Math.max(latestMtime, tp.mtimeMs);

    const sums = isCoord ? sumSteps(tp, minCoordStep, maxCoordStep) : sumSteps(tp);
    const totCtx = sums.input_tokens + sums.cache_read_tokens;
    const cachePct = totCtx > 0 ? (sums.cache_read_tokens * 100) / totCtx : 0;

    let state = 'waiting';
    if (mInfo.killed || mInfo.phase === 'KILLED') {
      state = 'killed';
    } else if (mInfo.wake_mode === 'working' && tp?.inferredRunning) {
      state = 'working';
    } else if (isCoord) {
      const recentlyActive = tp?.mtimeMs && Date.now() - tp.mtimeMs < 12000;
      if (tp?.inferredRunning || (mInfo.wake_mode === 'working' && recentlyActive)) {
        state = 'working';
      } else {
        state = allWorkersKilled ? 'done' : 'waiting';
      }
    }

    const lastRead = Number(mInfo.last_read_seq) || 0;
    const unread = Math.max(0, board.head - lastRead);
    const lp = lastPostByAgent.get(mName);
    const role = isCoord ? 'coordinator' : (/^(narrator|monitor)(?:-\d+)?$/.test(mName) ? mName.split('-')[0] : String(mInfo.role || 'worker'));

    agents.push({
      name: mName,
      role,
      model,
      tier: classifyTier(model, role, mName),
      state,
      turns: sums.turns,
      input_tokens: sums.input_tokens,
      cache_read_tokens: sums.cache_read_tokens,
      output_tokens: sums.output_tokens,
      cache_hit_pct: Math.round(cachePct * 10) / 10,
      conversation_id: cid,
      posts: postCounts.get(mName) || 0,
      unread,
      last_post_seq: lp?.seq || 0,
      last_post_time: lp?.time || '',
      activity: state === 'working' ? (tp?.latestActivity || 'Thinking…') : '',
      inherited: false,
    });

    const bridged = isCoord ? sumCodexBridges(tp, minCoordStep, maxCoordStep) : sumCodexBridges(tp);
    for (const [cbModel, cbSums] of bridged.entries()) {
      const cbCtx = cbSums.input_tokens + cbSums.cache_read_tokens;
      const cbPct = cbCtx > 0 ? (cbSums.cache_read_tokens * 100) / cbCtx : 0;
      const cbName = `${mName}[codex]`;
      agents.push({
        name: cbName,
        role: 'codex-bridge',
        model: cbModel,
        tier: classifyTier(cbModel, role, cbName),
        state,
        turns: cbSums.turns,
        input_tokens: cbSums.input_tokens,
        cache_read_tokens: cbSums.cache_read_tokens,
        output_tokens: cbSums.output_tokens,
        cache_hit_pct: Math.round(cbPct * 10) / 10,
        conversation_id: cid,
        posts: 0,
        unread: 0,
        last_post_seq: 0,
        last_post_time: '',
        activity: '',
        inherited: false,
      });
    }
  }

  // Include direct non-Swarm subagents spawned in the same parent session
  const subModelByCid = new Map();
  for (const s of parentTp?.subagents || []) {
    subModelByCid.set(s.cid, s);
  }
  const dbRows = querySubagentsFromDb(appData, swarmCid);
  const extraSubs = dbRows
    ? dbRows.filter((r) => r.conversation_id && !seenCids.has(r.conversation_id))
    : (parentTp?.subagents || [])
        .filter((s) => !seenCids.has(s.cid))
        .map((s) => ({ conversation_id: s.cid, agent_name: s.stype, status: 'done', killed: 1 }));

  for (const row of extraSubs) {
    const cid = String(row.conversation_id);
    seenCids.add(cid);
    const tpPath = join(appData, 'brain', cid, '.system_generated', 'logs', 'transcript.jsonl');
    const tp = parseTranscriptFile(tpPath);
    if (tp?.mtimeMs) latestMtime = Math.max(latestMtime, tp.mtimeMs);
    const sums = sumSteps(tp);
    const totCtx = sums.input_tokens + sums.cache_read_tokens;
    const cachePct = totCtx > 0 ? (sums.cache_read_tokens * 100) / totCtx : 0;

    const meta = subModelByCid.get(cid);
    const rawModel = meta?.smodel || 'inherit';
    const inherited = rawModel === 'inherit';
    const model = inherited && coordModel !== '-' ? `${coordModel}*` : (rawModel === 'flash' ? 'gemini-flash' : rawModel);
    const aname = String(row.agent_name || meta?.stype || 'subagent');
    const displayName = `${aname}-${cid.slice(0, 4)}`;
    const state = row.killed ? 'killed' : String(row.status || 'done').replace(/^cascade_(?:run_)?status_/i, '').toLowerCase();

    agents.push({
      name: displayName,
      role: 'subagent',
      model,
      tier: classifyTier(model, 'subagent', displayName),
      state,
      turns: sums.turns,
      input_tokens: sums.input_tokens,
      cache_read_tokens: sums.cache_read_tokens,
      output_tokens: sums.output_tokens,
      cache_hit_pct: Math.round(cachePct * 10) / 10,
      conversation_id: cid,
      posts: 0,
      unread: 0,
      last_post_seq: 0,
      last_post_time: '',
      activity: '',
      inherited,
    });
  }

  return formatUnifiedPayload({
    host: 'agy',
    sessionId: swarmCid,
    title: String(roster.title || swarmCid.slice(0, 8)),
    me: meName || '',
    updatedAtMs: latestMtime,
    fullSession,
    agents,
    messages: board.messages,
    head: board.head,
  });
}

function formatUnifiedPayload({ host, sessionId, title, me, updatedAtMs, fullSession, agents, messages, head }) {
  let totalTurns = 0;
  let totalInput = 0;
  let totalCache = 0;
  let totalOutput = 0;

  for (const a of agents) {
    totalTurns += a.turns;
    totalInput += a.input_tokens;
    totalCache += a.cache_read_tokens;
    totalOutput += a.output_tokens;
  }
  const totalCtx = totalInput + totalCache;
  const cacheHitPct = totalCtx > 0 ? Math.round((totalCache * 1000) / totalCtx) / 10 : 0;

  // Rollup by clean model name
  const modelMap = new Map();
  const safeTotIn = Math.max(1, totalInput);
  const safeTotOut = Math.max(1, totalOutput);
  for (const a of agents) {
    if (a.turns === 0 && a.input_tokens === 0 && a.output_tokens === 0) continue;
    const cleanModel = String(a.model || '-').replace(/\*$/, '');
    let b = modelMap.get(cleanModel);
    if (!b) {
      b = {
        model: cleanModel,
        tier: a.tier === 'verify' ? 'verify' : classifyTier(cleanModel, '', ''),
        _allVerify: a.tier === 'verify',
        agents: 0,
        turns: 0,
        input_tokens: 0,
        cache_read_tokens: 0,
        output_tokens: 0,
      };
      modelMap.set(cleanModel, b);
    } else if (a.tier !== 'verify' && b._allVerify) {
      b._allVerify = false;
      b.tier = classifyTier(cleanModel, '', '');
    }
    b.agents += 1;
    b.turns += a.turns;
    b.input_tokens += a.input_tokens;
    b.cache_read_tokens += a.cache_read_tokens;
    b.output_tokens += a.output_tokens;
  }

  const byModel = [...modelMap.values()]
    .sort((a, b) => b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens))
    .map((r) => {
      const ctx = r.input_tokens + r.cache_read_tokens;
      return {
        ...r,
        cache_hit_pct: ctx > 0 ? Math.round((r.cache_read_tokens * 1000) / ctx) / 10 : 0,
        input_share_pct: Math.round((r.input_tokens * 1000) / safeTotIn) / 10,
        output_share_pct: Math.round((r.output_tokens * 1000) / safeTotOut) / 10,
      };
    });

  // Anomaly & optimization insights
  const anomalies = [];
  if (totalInput > 0) {
    for (const a of agents) {
      const base = a.name.split('-')[0].toLowerCase();
      if (base === 'monitor' || base === 'narrator') {
        const share = (a.input_tokens * 100) / totalInput;
        if (share >= 25 || a.turns >= 80) {
          anomalies.push({
            kind: 'daemon',
            text: `Daemon "${a.name}" ran ${a.turns} turns (${fmtCompact(a.input_tokens)} input, ${share.toFixed(1)}%). Kill idle swarms when finished.`,
          });
        }
      }
    }
    const leakedSubs = agents.filter((a) => a.role === 'subagent' && String(a.model).endsWith('*') && a.tier === 'l1');
    const leakedIn = leakedSubs.reduce((acc, a) => acc + a.input_tokens, 0);
    if (leakedSubs.length > 0 && leakedIn >= 100_000) {
      anomalies.push({
        kind: 'subagent',
        text: `${leakedSubs.length} direct subagent(s) inherited L1 "${leakedSubs[0].model}" (${fmtCompact(leakedIn)} input). Pass Model="flash" for browser/research tasks.`,
      });
    }
  }

  const etag = `${host}|${sessionId}|${fullSession ? 1 : 0}|${updatedAtMs}|${totalTurns}|${totalInput}|${totalOutput}|${head}`;
  return {
    etag,
    host,
    session_id: sessionId,
    title,
    me,
    full_session: Boolean(fullSession),
    updated_at_ms: updatedAtMs,
    head,
    totals: {
      agents: agents.length,
      active_agents: agents.filter((a) => a.state === 'working').length,
      waiting_agents: agents.filter((a) => a.state === 'waiting').length,
      killed_agents: agents.filter((a) => a.state === 'killed').length,
      turns: totalTurns,
      input_tokens: totalInput,
      cache_read_tokens: totalCache,
      output_tokens: totalOutput,
      cache_hit_pct: cacheHitPct,
    },
    by_model: byModel,
    anomalies,
    agents,
    messages,
  };
}

function fmtCompact(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// ---------------------------------------------------------------------------
// Cross-Host CLI Bridge (`claude` / `codex` / Session List)
// ---------------------------------------------------------------------------

let sessionsCache = { at: 0, list: [] };
function listRecentSessions(appData) {
  const now = Date.now();
  if (now - sessionsCache.at < 4000) return sessionsCache.list;

  const outList = [];
  const seen = new Set();

  if (existsSync(SWARM_TOKENS_PY)) {
    try {
      const raw = execFileSync('python3', [SWARM_TOKENS_PY, '--list', '-n', '15', '--json'], {
        encoding: 'utf-8',
        timeout: 3500,
        env: { ...process.env, ANTIGRAVITY_DATA_DIR: appData },
      });
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          const key = `${item.host}:${item.session_id}`;
          seen.add(key);
          outList.push(item);
        }
      }
    } catch {
      // fallback to local scan below
    }
  }

  // Ensure any local Swarm directories are always discoverable
  const scan = scanLocalSwarms(appData);
  for (const s of scan.swarms) {
    const key = `agy:${s.cid}`;
    if (!seen.has(key)) {
      outList.push({
        host: 'agy',
        session_id: s.cid,
        title: s.title,
        updated_at: new Date(s.mtimeMs).toISOString().slice(0, 16).replace('T', ' '),
      });
    }
  }

  sessionsCache = { at: now, list: outList };
  return outList;
}

const cliInspectCache = new Map();
function inspectViaPython(appData, host, targetId, fullSession) {
  const cacheKey = `${host}|${targetId}|${fullSession ? 1 : 0}`;
  const now = Date.now();
  const hit = cliInspectCache.get(cacheKey);
  if (hit && now - hit.at < 2500) return hit.data;

  if (!existsSync(SWARM_TOKENS_PY)) return null;
  try {
    const args = [SWARM_TOKENS_PY, '--host', host, '--id', targetId, '--json'];
    if (fullSession) args.push('--full-session');
    const raw = execFileSync('python3', args, {
      encoding: 'utf-8',
      timeout: 4000,
      env: { ...process.env, ANTIGRAVITY_DATA_DIR: appData },
    });
    const d = JSON.parse(raw);
    const agents = (d.agents || []).map((a) => {
      const totCtx = (a.input_tokens || 0) + (a.cache_read_tokens || 0);
      return {
        name: a.name,
        role: a.role,
        model: a.model,
        tier: classifyTier(a.model, a.role, a.name),
        state: a.state,
        turns: a.turns || 0,
        input_tokens: a.input_tokens || 0,
        cache_read_tokens: a.cache_read_tokens || 0,
        output_tokens: a.output_tokens || 0,
        cache_hit_pct: totCtx > 0 ? Math.round((a.cache_read_tokens * 1000) / totCtx) / 10 : 0,
        conversation_id: '',
        posts: 0,
        unread: 0,
        last_post_seq: 0,
        last_post_time: '',
        activity: '',
        inherited: String(a.model || '').endsWith('*'),
      };
    });
    const payload = formatUnifiedPayload({
      host: d.host || host,
      sessionId: d.session_id || targetId,
      title: d.title || targetId.slice(0, 8),
      me: '',
      updatedAtMs: now,
      fullSession,
      agents,
      messages: [],
      head: 0,
    });
    cliInspectCache.set(cacheKey, { at: now, data: payload });
    return payload;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// HTTP Routes
// ---------------------------------------------------------------------------

const app = new SidecarApp();

const STATIC_FILES = {
  '/styles.css': ['styles.css', 'text/css; charset=utf-8'],
  '/app.js': ['app.js', 'application/javascript; charset=utf-8'],
};

const NO_CACHE_HEADERS = {
  'Cache-Control': 'no-store, no-cache, must-revalidate',
  Pragma: 'no-cache',
};

function currentAssetVersion() {
  try {
    const c = Math.trunc(statSync(join(__dirname, 'styles.css')).mtimeMs);
    const j = Math.trunc(statSync(join(__dirname, 'app.js')).mtimeMs);
    const h = Math.trunc(statSync(join(__dirname, 'index.html')).mtimeMs);
    return `${h}-${c}-${j}`;
  } catch {
    return '1';
  }
}

app.page('/', () => {
  const ver = encodeURIComponent(currentAssetVersion());
  const html = readFileSync(join(__dirname, 'index.html'), 'utf-8')
    .replace('href="/styles.css"', `href="/styles.css?v=${ver}"`)
    .replace('src="/app.js"', `src="/app.js?v=${ver}"`);
  return new Response(html, { contentType: 'text/html; charset=utf-8', headers: NO_CACHE_HEADERS });
});

for (const [route, [fname, ctype]] of Object.entries(STATIC_FILES)) {
  app.api(
    route,
    () => new Response(readFileSync(join(__dirname, fname), 'utf-8'), { contentType: ctype, headers: NO_CACHE_HEADERS }),
    'GET'
  );
}

app.api('/api/sessions', () => {
  const appData = findAppDataDir();
  return { sessions: listRecentSessions(appData) };
}, 'GET');

app.api('/api/state', (data = {}) => {
  const appData = findAppDataDir();
  const fullSession = data.fullSession === '1' || data.fullSession === 'true';
  const reqTarget = typeof data.target === 'string' ? data.target.trim() : '';
  const paneConv = typeof data.conversationId === 'string' && data.conversationId
    ? data.conversationId
    : (app.config?.conversationId || '');

  let snap = null;

  if (reqTarget && reqTarget !== 'auto') {
    const [tHost, tId] = reqTarget.includes(':') ? reqTarget.split(':', 2) : ['agy', reqTarget];
    if (ID_RE.test(tId)) {
      if (tHost === 'agy') {
        snap = buildAgySwarmSnapshot(appData, tId, paneConv === tId ? 'coordinator' : '', fullSession);
      }
      if (!snap && (tHost === 'agy' || tHost === 'claude' || tHost === 'codex')) {
        snap = inspectViaPython(appData, tHost, tId, fullSession);
      }
    }
  } else {
    // Auto mode: resolve current conversation's Swarm first; if none, fall back to latest Swarm
    if (paneConv && ID_RE.test(paneConv)) {
      const directMembers = join(appData, 'brain', paneConv, '.system_generated', 'swarm', 'members.json');
      if (existsSync(directMembers)) {
        snap = buildAgySwarmSnapshot(appData, paneConv, 'coordinator', fullSession);
      } else {
        const scan = scanLocalSwarms(appData);
        const owner = scan.byWorker.get(paneConv);
        if (owner) {
          snap = buildAgySwarmSnapshot(appData, owner.swarmCid, owner.memberName, fullSession);
        }
      }
    }
    // If current conversation has no Swarm yet, show most recent local Swarm with a fallback flag
    if (!snap) {
      const scan = scanLocalSwarms(appData);
      if (scan.swarms.length > 0) {
        snap = buildAgySwarmSnapshot(appData, scan.swarms[0].cid, '', fullSession);
        if (snap) snap = { ...snap, is_fallback_latest: true };
      }
    }
  }

  const assetVer = currentAssetVersion();
  if (!snap) {
    return { conversationId: paneConv || null, found: false, asset_version: assetVer };
  }
  if (data.etag && data.etag === snap.etag) {
    return { conversationId: paneConv || null, found: true, unchanged: true, etag: snap.etag, asset_version: assetVer };
  }
  return { conversationId: paneConv || null, found: true, asset_version: assetVer, ...snap };
}, 'GET');

const SELF_PATH = fileURLToPath(import.meta.url);
watchFile(SELF_PATH, { interval: 2000 }, (curr, prev) => {
  if (curr.mtimeMs !== prev.mtimeMs) process.exit(0);
});

app.run();
