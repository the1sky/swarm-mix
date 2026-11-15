/**
 * Swarm Mix Sidecar Frontend (MIT License)
 *
 * Polls /api/state & /api/sessions to render live per-agent & per-model token
 * telemetry, heterogeneous roster status, and native Swarm board messages.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);

  const uiState = {
    target: params.get('target') || 'auto',
    fullSession: params.get('fullSession') === '1',
    tab: 'tokens',
    barMetric: 'input', // 'input' | 'output'
    memberSort: 'burn', // 'burn' | 'status'
    showKilled: true,
    boardQuery: '',
    boardSender: '',
    boardFollow: true,
    etag: '',
    assetVersion: '',
    snap: null,
    sessionsLoadedAt: 0,
    capHost: 'agy',
    capLoadedAt: 0,
    capabilities: null,
  };

  // ---------- Host Theme Sync (`preload.js` + `prefers-color-scheme`) ----------
  const colorCanvas = document.createElement('canvas');
  colorCanvas.width = 1;
  colorCanvas.height = 1;
  const colorCtx = colorCanvas.getContext('2d', { willReadFrequently: true });

  function parseCssLuminance(cssColor) {
    if (!colorCtx || !cssColor || cssColor === 'transparent' || cssColor === 'rgba(0, 0, 0, 0)') {
      return null;
    }
    try {
      colorCtx.clearRect(0, 0, 1, 1);
      colorCtx.fillStyle = '#000000';
      colorCtx.fillStyle = cssColor;
      colorCtx.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = colorCtx.getImageData(0, 0, 1, 1).data;
      if (a === 0) return null;
      const toLin = (c) => {
        const s = c / 255;
        return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * toLin(r) + 0.7152 * toLin(g) + 0.0722 * toLin(b);
    } catch {
      return null;
    }
  }

  let syncingTheme = false;
  function syncHostTheme() {
    if (syncingTheme) return;
    syncingTheme = true;
    try {
      const probeEl = document.body || document.documentElement;
      const resolvedBg = getComputedStyle(probeEl).backgroundColor;
      const rawVarBg = getComputedStyle(document.documentElement).getPropertyValue('--background').trim();
      let lum = parseCssLuminance(rawVarBg);
      if (lum === null) lum = parseCssLuminance(resolvedBg);
      let mode;
      if (lum !== null) {
        mode = lum < 0.35 ? 'dark' : 'light';
      } else {
        mode = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      }
      if (document.documentElement.dataset.theme !== mode) {
        document.documentElement.dataset.theme = mode;
      }
    } finally {
      syncingTheme = false;
    }
  }

  syncHostTheme();
  window.addEventListener('message', (ev) => {
    if (ev.data?.type === 'theme-change') {
      requestAnimationFrame(syncHostTheme);
    }
  });
  new MutationObserver(syncHostTheme).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['style'],
  });
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener('change', syncHostTheme);

  // ---------- Helpers ----------
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  function fmtNum(n) {
    const v = Number(n) || 0;
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
    if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
    return String(v);
  }

  function resolveTier(model, role, name) {
    const m = String(model || '').toLowerCase().replace(/\*$/, '');
    const r = String(role || '').toLowerCase();
    const n = String(name || '').toLowerCase();
    if (n.includes('verifier') || r.includes('verifier')) return 'verify';
    if (/(?:^|[-_\s])(hydrogen|flash|mini|nano|haiku|lite|low)(?:$|[-_\s\d)])/.test(m)) return 'l3';
    if (/(?:^|[-_\s])(argon|sonnet|luna)(?:$|[-_\s\d)])/.test(m)) return 'l2';
    if (/(?:^|[-_\s])(carbon|opus|pro|astra|o3)(?:$|[-_\s\d)])/.test(m) || /^gpt-[56](?!.*-mini)/.test(m)) return 'l1';
    return 'l2';
  }

  function tierLabel(t) {
    if (t === 'l1') return 'L1';
    if (t === 'l2') return 'L2';
    if (t === 'verify') return 'Verify';
    return 'L3';
  }

  function normalizeSnapshot(snap) {
    const agents = (snap.agents || []).map((a) => ({
      ...a,
      tier: resolveTier(a.model, a.role, a.name),
    }));
    const workers = agents.filter((a) => a.role !== 'coordinator');
    const swarmEnded = workers.length > 0 && workers.every((w) => w.state === 'killed' || w.state === 'done');
    const stale = snap.updated_at_ms && Date.now() - snap.updated_at_ms > 20000;
    if (swarmEnded && stale) {
      for (const a of agents) {
        if (a.role === 'coordinator' && a.state === 'working') {
          a.state = 'done';
          a.activity = '';
        }
      }
    }
    const verifyOnlyModels = new Map();
    for (const a of agents) {
      if (a.turns === 0 && a.input_tokens === 0 && a.output_tokens === 0) continue;
      const cm = String(a.model || '-').replace(/\*$/, '');
      const prev = verifyOnlyModels.get(cm);
      verifyOnlyModels.set(cm, prev === undefined ? a.tier === 'verify' : prev && a.tier === 'verify');
    }
    const byModel = (snap.by_model || []).map((r) => {
      const cm = String(r.model || '-').replace(/\*$/, '');
      return {
        ...r,
        tier: verifyOnlyModels.get(cm) ? 'verify' : resolveTier(cm, '', ''),
      };
    });
    const activeCount = agents.filter((a) => a.state === 'working').length;
    return {
      ...snap,
      agents,
      by_model: byModel,
      totals: {
        ...(snap.totals || {}),
        active_agents: activeCount,
      },
    };
  }

  const currentConvId = () => window.sidecar?.conversationId || params.get('conversationId') || '';

  async function apiGet(path) {
    const fn = window.sidecar?.fetch ? window.sidecar.fetch.bind(window.sidecar) : fetch;
    const res = await fn(path, { method: 'GET' });
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  }

  // ---------- Accessible Tabs (`selection follows focus` + `hidden="until-found"`) ----------
  const tabButtons = [...document.querySelectorAll('[role="tab"][data-tab]')];
  const tabPanels = {
    tokens: $('panel-tokens'),
    members: $('panel-members'),
    board: $('panel-board'),
    models: $('panel-models'),
  };

  function syncEmptyStateVisibility() {
    const hasSnap = Boolean(uiState.snap);
    const showGrid = hasSnap || uiState.tab === 'models';
    $('empty-state').hidden = showGrid;
    document.querySelector('.panels-grid').hidden = !showGrid;
  }

  function selectTab(name, focusTab = false) {
    if (!tabPanels[name]) return;
    uiState.tab = name;
    for (const btn of tabButtons) {
      const active = btn.dataset.tab === name;
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
      btn.tabIndex = active ? 0 : -1;
      if (active && focusTab) {
        btn.focus();
        btn.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
      }
    }
    for (const [key, panel] of Object.entries(tabPanels)) {
      if (key === name) {
        panel.removeAttribute('hidden');
        panel.tabIndex = 0;
      } else {
        panel.setAttribute('hidden', 'until-found');
        panel.removeAttribute('tabindex');
      }
    }
    syncEmptyStateVisibility();
    if (name === 'models' && !uiState.capabilities) {
      refreshCapabilities(true);
    }
  }

  for (const btn of tabButtons) {
    btn.addEventListener('click', () => selectTab(btn.dataset.tab, false));
    btn.addEventListener('keydown', (e) => {
      const idx = tabButtons.indexOf(btn);
      if (idx < 0) return;
      let nextIdx = -1;
      if (e.key === 'ArrowRight') nextIdx = (idx + 1) % tabButtons.length;
      else if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + tabButtons.length) % tabButtons.length;
      else if (e.key === 'Home') nextIdx = 0;
      else if (e.key === 'End') nextIdx = tabButtons.length - 1;
      if (nextIdx >= 0) {
        e.preventDefault();
        selectTab(tabButtons[nextIdx].dataset.tab, true);
      }
    });
  }

  for (const [key, panel] of Object.entries(tabPanels)) {
    panel.addEventListener('beforematch', () => selectTab(key, false));
  }

  // ---------- Interactive Controls ----------
  $('session-select').addEventListener('change', (e) => {
    uiState.target = e.target.value || 'auto';
    uiState.etag = '';
    pollNow();
  });

  $('scope-btn').addEventListener('click', () => {
    uiState.fullSession = !uiState.fullSession;
    $('scope-btn').setAttribute('aria-pressed', uiState.fullSession ? 'true' : 'false');
    $('scope-btn').textContent = uiState.fullSession ? 'Full Session' : 'Swarm Window';
    uiState.etag = '';
    pollNow();
  });

  $('bar-mode-in').addEventListener('click', () => {
    uiState.barMetric = 'input';
    $('bar-mode-in').setAttribute('aria-pressed', 'true');
    $('bar-mode-out').setAttribute('aria-pressed', 'false');
    if (uiState.snap) renderAllocation(uiState.snap);
  });

  $('bar-mode-out').addEventListener('click', () => {
    uiState.barMetric = 'output';
    $('bar-mode-in').setAttribute('aria-pressed', 'false');
    $('bar-mode-out').setAttribute('aria-pressed', 'true');
    if (uiState.snap) renderAllocation(uiState.snap);
  });

  $('sort-burn').addEventListener('click', () => {
    uiState.memberSort = 'burn';
    $('sort-burn').setAttribute('aria-pressed', 'true');
    $('sort-status').setAttribute('aria-pressed', 'false');
    if (uiState.snap) renderMembers(uiState.snap);
  });

  $('sort-status').addEventListener('click', () => {
    uiState.memberSort = 'status';
    $('sort-burn').setAttribute('aria-pressed', 'false');
    $('sort-status').setAttribute('aria-pressed', 'true');
    if (uiState.snap) renderMembers(uiState.snap);
  });

  $('show-killed').addEventListener('change', (e) => {
    uiState.showKilled = Boolean(e.target.checked);
    if (uiState.snap) renderMembers(uiState.snap);
  });

  $('board-search').addEventListener('input', (e) => {
    uiState.boardQuery = e.target.value.trim().toLowerCase();
    if (uiState.snap) renderBoard(uiState.snap, false);
  });

  $('board-author').addEventListener('change', (e) => {
    uiState.boardSender = e.target.value;
    if (uiState.snap) renderBoard(uiState.snap, false);
  });

  $('board-follow').addEventListener('change', (e) => {
    uiState.boardFollow = Boolean(e.target.checked);
  });

  for (const hbtn of document.querySelectorAll('.cap-host-btn')) {
    hbtn.addEventListener('click', () => {
      const h = hbtn.getAttribute('data-cap-host') || 'agy';
      uiState.capHost = h;
      for (const b of document.querySelectorAll('.cap-host-btn')) {
        b.setAttribute('aria-pressed', b.getAttribute('data-cap-host') === h ? 'true' : 'false');
      }
      refreshCapabilities(true);
    });
  }

  // Delegate member chat open & board #seq jump clicks
  document.addEventListener('click', (e) => {
    const chatBtn = e.target.closest('[data-open-conv]');
    if (chatBtn) {
      const cid = chatBtn.getAttribute('data-open-conv');
      if (cid && window.sidecar?.ui?.toggleConversation) {
        window.sidecar.ui.toggleConversation(cid);
      }
      return;
    }
    const seqBtn = e.target.closest('[data-jump-seq]');
    if (seqBtn) {
      const seq = seqBtn.getAttribute('data-jump-seq');
      const card = document.getElementById(`post-seq-${seq}`);
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.add('flash-target');
        setTimeout(() => card.classList.remove('flash-target'), 1400);
      }
    }
  });

  // ---------- Renderers ----------
  function renderSnapshot(snap) {
    $('empty-state').hidden = true;
    document.querySelector('.panels-grid').hidden = false;

    $('swarm-title').textContent = snap.title || 'Swarm Mix';
    const tot = snap.totals || {};
    const activeCount = tot.active_agents || 0;
    $('live-pill').dataset.active = activeCount > 0 ? 'true' : 'false';
    $('live-label').textContent = activeCount > 0 ? `${activeCount} Active` : 'Idle';

    const hostTag = String(snap.host || 'agy').toUpperCase();
    const shortId = String(snap.session_id || '').slice(0, 8);
    const updatedStr = snap.updated_at_ms
      ? new Date(snap.updated_at_ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      : '';
    $('meta-line').textContent = `[${hostTag}:${shortId}] ${tot.active_agents || 0} active · ${tot.waiting_agents || 0} waiting · ${tot.killed_agents || 0} ended${updatedStr ? ` · ${updatedStr}` : ''}`;

    $('badge-tokens').textContent = fmtNum(tot.input_tokens || 0);
    $('badge-members').textContent = String(tot.agents || 0);
    $('badge-board').textContent = String((snap.messages || []).length);

    // Fallback notice when current conversation has no Swarm yet
    const fb = $('fallback-banner');
    if (snap.is_fallback_latest && uiState.target === 'auto') {
      fb.hidden = false;
      fb.textContent = 'Current chat has no active Swarm — showing most recent Swarm ledger. Will auto-switch when a Swarm starts.';
    } else {
      fb.hidden = true;
    }

    // Anomalies / Hotspot insights
    const anomBox = $('anomalies-box');
    const anomalies = snap.anomalies || [];
    if (anomalies.length > 0) {
      anomBox.hidden = false;
      anomBox.innerHTML = anomalies.map((a) => `<div class="anomaly-item">⚠ ${esc(a.text)}</div>`).join('');
    } else {
      anomBox.hidden = true;
      anomBox.innerHTML = '';
    }

    // KPIs
    $('kpi-input').textContent = fmtNum(tot.input_tokens || 0);
    $('kpi-input-sub').textContent = snap.full_session ? 'Full parent + workers' : 'Swarm window input';
    $('kpi-cache').textContent = fmtNum(tot.cache_read_tokens || 0);
    $('kpi-cache-pct').textContent = `${(tot.cache_hit_pct || 0).toFixed(1)}%`;
    $('kpi-output').textContent = fmtNum(tot.output_tokens || 0);
    $('kpi-turns').textContent = `${tot.turns || 0} turns`;
    $('kpi-agents-sub').textContent = `${tot.agents || 0} total nodes`;

    renderAllocation(snap);
    renderTierTable(snap);
    renderMembers(snap);
    renderBoard(snap, true);
  }

  function renderAllocation(snap) {
    const rows = snap.by_model || [];
    const bar = $('tier-stack-bar');
    const legend = $('tier-legend');
    if (!rows.length) {
      bar.innerHTML = '';
      legend.innerHTML = '<span class="kpi-sub">No token usage recorded yet.</span>';
      return;
    }
    const isOut = uiState.barMetric === 'output';
    bar.innerHTML = rows
      .map((r) => {
        const pct = isOut ? r.output_share_pct : r.input_share_pct;
        if (pct <= 0) return '';
        return `<div class="stack-seg" data-tier="${esc(r.tier)}" style="width:${Math.max(1.5, pct)}%" title="${esc(r.model)}: ${pct}% (${fmtNum(isOut ? r.output_tokens : r.input_tokens)})"></div>`;
      })
      .join('');

    legend.innerHTML = rows
      .map((r) => {
        const pct = isOut ? r.output_share_pct : r.input_share_pct;
        return `<span class="legend-chip"><span class="tier-dot" data-tier="${esc(r.tier)}"></span><span>${esc(r.model)}</span><strong>${pct.toFixed(1)}%</strong></span>`;
      })
      .join('');
  }

  function renderTierTable(snap) {
    const rows = snap.by_model || [];
    const tbody = $('tier-tbody');
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="kpi-sub">Waiting for first planner turn…</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(
        (r) => `<tr>
          <th scope="row"><span class="tier-dot" data-tier="${esc(r.tier)}" style="display:inline-block;margin-right:6px"></span>${esc(r.model)}</th>
          <td class="num">${r.agents}</td>
          <td class="num">${r.turns}</td>
          <td class="num">${fmtNum(r.input_tokens)}</td>
          <td class="num">${fmtNum(r.cache_read_tokens)} (${r.cache_hit_pct.toFixed(0)}%)</td>
          <td class="num">${fmtNum(r.output_tokens)}</td>
          <td class="num">${r.input_share_pct.toFixed(1)}%</td>
        </tr>`
      )
      .join('');
  }

  function renderMembers(snap) {
    const allAgents = snap.agents || [];
    const killedCount = allAgents.filter((a) => a.state === 'killed').length;
    $('show-killed-text').textContent = killedCount > 0 ? `Show killed (${killedCount})` : 'Show killed';

    const filtered = uiState.showKilled ? [...allAgents] : allAgents.filter((a) => a.state !== 'killed');
    const statusRank = { working: 0, waiting: 1, idle: 2, done: 2, killed: 3 };

    if (uiState.memberSort === 'burn') {
      filtered.sort((a, b) => b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens));
    } else {
      filtered.sort((a, b) => {
        if (a.role === 'coordinator' && b.role !== 'coordinator') return -1;
        if (b.role === 'coordinator' && a.role !== 'coordinator') return 1;
        const ra = statusRank[a.state] ?? 2;
        const rb = statusRank[b.state] ?? 2;
        return ra !== rb ? ra - rb : a.name.localeCompare(b.name);
      });
    }

    const maxBurn = Math.max(1, ...allAgents.map((a) => a.input_tokens + a.output_tokens));
    const canOpenChat = Boolean(window.sidecar?.ui?.toggleConversation);

    const listEl = $('members-list');
    if (!filtered.length) {
      listEl.innerHTML = '<div class="info-banner">No matching members visible. Enable "Show killed" to inspect finished workers.</div>';
      return;
    }

    listEl.innerHTML = filtered
      .map((a) => {
        const burnPct = Math.min(100, Math.max(2, ((a.input_tokens + a.output_tokens) * 100) / maxBurn));
        const youBadge = snap.me && snap.me === a.name ? '<span class="badge-role">you</span>' : '';
        const openBtn =
          a.conversation_id && canOpenChat
            ? `<button type="button" class="chat-btn" data-open-conv="${esc(a.conversation_id)}" title="Open member conversation">Chat ↗</button>`
            : '';
        const actLine = a.activity ? `<div class="member-activity">⚡ ${esc(a.activity)}</div>` : '';
        const postMeta = a.posts > 0 ? `<span>Posts <span class="metric-strong">${a.posts}</span></span>` : '';
        const shortShell = a.shell_model ? String(a.shell_model).replace(/^gemini-4-/, '').replace(/^claude-/, '') : '';
        const shellBadge = shortShell
          ? `<span class="badge-shell" title="Swarm board shell: ${esc(a.shell_model)}">shell: ${esc(shortShell)}</span>`
          : '';
        const bd = a.bridge_detail;
        const bridgeLine = bd
          ? bd.codex_turns > 0
            ? `<div class="member-bridge-split">Codex <span class="metric-strong">${bd.codex_turns}t</span> · ${fmtNum(bd.codex_input)} in · ${fmtNum(bd.codex_output)} out <span class="split-sep">│</span> Shell <span class="metric-strong">${bd.shell_turns}t</span> · ${fmtNum(bd.shell_input)} in</div>`
            : `<div class="member-bridge-split">Codex bridged (awaiting completion) <span class="split-sep">│</span> Shell <span class="metric-strong">${bd.shell_turns}t</span> · ${fmtNum(bd.shell_input)} in</div>`
          : '';

        return `<article class="member-card" role="listitem" data-state="${esc(a.state)}">
          <div class="member-top">
            <span class="status-dot" data-state="${esc(a.state)}" title="${esc(a.state)}"></span>
            <span class="member-name">${esc(a.name)}</span>
            ${youBadge}
            <span class="badge-role">${esc(a.role)}</span>
            <span class="badge-model" data-tier="${esc(a.tier)}">${tierLabel(a.tier)} · ${esc(a.model)}</span>
            ${shellBadge}
            ${openBtn}
          </div>
          ${actLine}
          <div class="member-metrics">
            <span>Turns <span class="metric-strong">${a.turns}</span></span>
            <span>In <span class="metric-strong">${fmtNum(a.input_tokens)}</span></span>
            <span>Cache <span class="metric-strong">${fmtNum(a.cache_read_tokens)}</span> (${a.cache_hit_pct.toFixed(0)}%)</span>
            <span>Out <span class="metric-strong">${fmtNum(a.output_tokens)}</span></span>
            ${postMeta}
          </div>
          ${bridgeLine}
          <div class="burn-track" aria-hidden="true">
            <div class="burn-fill" data-tier="${esc(a.tier)}" style="width:${burnPct.toFixed(1)}%"></div>
          </div>
        </article>`;
      })
      .join('');
  }

  function formatPostHtml(raw) {
    let html = esc(raw);
    html = html.replace(/(^|[^a-zA-Z0-9_-])@([a-zA-Z0-9][a-zA-Z0-9_-]*)/g, '$1<span class="mention-pill">@$2</span>');
    html = html.replace(/(^|[^a-zA-Z0-9&#])#(\d{1,5})\b/g, '$1<button type="button" class="seq-jump" data-jump-seq="$2">#$2</button>');
    return html;
  }

  function renderBoard(snap, fromPoll) {
    const msgs = snap.messages || [];
    const authorSel = $('board-author');
    const senders = [...new Set(msgs.map((m) => m.agent))].sort();
    const prevVal = uiState.boardSender;
    authorSel.innerHTML =
      '<option value="">All senders</option>' +
      senders.map((s) => `<option value="${esc(s)}"${s === prevVal ? ' selected' : ''}>${esc(s)}</option>`).join('');

    const feed = $('board-feed');
    if (!msgs.length) {
      feed.innerHTML =
        snap.host === 'agy'
          ? '<div class="info-banner">No board messages recorded in this session.</div>'
          : '<div class="info-banner">Claude Code / Codex subagent sessions do not use a shared Antigravity board.jsonl feed — inspect per-agent token metrics in the Tokens and Members tabs.</div>';
      return;
    }

    const filtered = msgs.filter((m) => {
      if (uiState.boardSender && m.agent !== uiState.boardSender) return false;
      if (uiState.boardQuery) {
        const hay = `${m.agent} ${m.text}`.toLowerCase();
        if (!hay.includes(uiState.boardQuery)) return false;
      }
      return true;
    });

    if (!filtered.length) {
      feed.innerHTML = '<div class="info-banner">No posts match current filter.</div>';
      return;
    }

    feed.innerHTML = filtered
      .map(
        (m) => `<article class="post-card" id="post-seq-${m.seq}">
          <div class="post-head">
            <span class="post-seq">#${m.seq}</span>
            <span class="post-agent">${esc(m.agent)}</span>
            <span class="post-time">${esc(m.time)}</span>
          </div>
          <div class="post-body">${formatPostHtml(m.text)}</div>
        </article>`
      )
      .join('');

    if (fromPoll && uiState.boardFollow && uiState.tab === 'board') {
      const last = feed.lastElementChild;
      last?.scrollIntoView({ block: 'end' });
    }
  }

  function fmtCtx(tokens) {
    const n = Number(tokens) || 0;
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M ctx`;
    if (n >= 1_000) return `${Math.round(n / 1_000)}K ctx`;
    return `${n} ctx`;
  }

  function renderCapabilities(cap) {
    if (!cap || !cap.ok) return;
    const models = cap.models || [];
    $('badge-models').textContent = String(models.length);

    const poolsEl = $('cap-pools');
    const pools = cap.pools || {};
    poolsEl.innerHTML = Object.entries(pools)
      .map(([k, v]) => {
        const ready = !String(v).includes('not_') && v !== 'unavailable';
        return `<span class="pool-pill" data-ready="${ready}">${esc(k)}: ${esc(v)}</span>`;
      })
      .join('');

    const rgrid = $('routing-grid');
    const recs = Object.values(cap.recommendations || {});
    rgrid.innerHTML = recs
      .map((r) => {
        const isBridge = r.channel !== 'native';
        return `<article class="route-card">
          <div class="route-head">
            <span class="route-task">${esc(r.label || r.task)}</span>
            <span class="route-score">${Number(r.match_score || 0).toFixed(2)}</span>
          </div>
          <div class="route-model">${esc(r.recommended_model)}</div>
          <div class="route-meta">
            <span class="chan-badge" data-bridge="${isBridge}">${esc(r.channel)}</span>
            <span>${esc(r.rationale)}</span>
            ${r.fallback_model ? `<span>· alt: ${esc(r.fallback_model)}</span>` : ''}
          </div>
        </article>`;
      })
      .join('');

    const mlist = $('models-list');
    const dimDefs = [
      ['Arch', 'architecture_refactor'],
      ['Algo', 'algorithm_state'],
      ['UI/MM', 'frontend_multimodal'],
      ['Bug', 'bug_hunting'],
      ['Speed', 'throughput_efficiency'],
    ];
    mlist.innerHTML = models
      .map((m) => {
        const t = m.tier || 'l2';
        const isBridge = m.channel !== 'native';
        const qBadge =
          m.quota_pct !== null && m.quota_pct !== undefined
            ? `<span class="chan-badge">${Math.round(Number(m.quota_pct) * 100)}% quota</span>`
            : '';
        const emp = m.empirical || {};
        const hasEmp = (emp.author_tasks || 0) > 0 || (emp.verifier_reviews || 0) > 0;
        const shiftVal = (emp.author_shift || 0) + (emp.verify_shift || 0);
        const shiftStr =
          shiftVal !== 0 ? `<span class="emp-shift">Bayesian Δ ${shiftVal > 0 ? '+' : ''}${shiftVal.toFixed(3)}</span>` : '';
        return `<article class="member-card" role="listitem" data-tier="${esc(t)}">
          <div class="member-top">
            <span class="tier-dot" data-tier="${esc(t)}"></span>
            <strong class="member-name">${esc(m.id)}</strong>
            <span class="tier-chip" data-tier="${esc(t)}">${esc(String(m.vendor || '').toUpperCase())} · ${esc(tierLabel(t))}</span>
            <span class="chan-badge" data-bridge="${isBridge}">${esc(m.channel)}</span>
            ${qBadge}
            <span class="spacer"></span>
            <span class="member-model">${esc(fmtCtx(m.context_window))}</span>
          </div>
          <div class="dim-grid">
            ${dimDefs
              .map(([lbl, key]) => {
                const v = Number(m.scores?.[key] || 0);
                const pct = Math.max(4, Math.min(100, Math.round(v * 100)));
                return `<div class="dim-cell">
                  <div class="dim-top"><span>${lbl}</span><strong>${pct}</strong></div>
                  <div class="dim-track"><div class="dim-fill" style="width:${pct}%"></div></div>
                </div>`;
              })
              .join('')}
          </div>
          ${
            hasEmp
              ? `<div class="emp-row">
                  <span>L3 Empirical:</span>
                  <span>Author pass ${emp.author_first_pass || 0}/${emp.author_tasks || 0}</span>
                  <span>·</span>
                  <span>Verify catches ${emp.verifier_bug_catches || 0}/${emp.verifier_reviews || 0}</span>
                  ${shiftStr}
                </div>`
              : ''
          }
        </article>`;
      })
      .join('');
  }

  async function refreshCapabilities(force = false) {
    const now = Date.now();
    if (!force && now - uiState.capLoadedAt < 20000) return;
    uiState.capLoadedAt = now;
    try {
      const cap = await apiGet(`/api/capabilities?host=${encodeURIComponent(uiState.capHost)}${force ? '&refresh=1' : ''}`);
      if (cap && cap.ok) {
        uiState.capabilities = cap;
        renderCapabilities(cap);
      }
    } catch {
      // ignore transient error
    }
  }

  // ---------- Polling Loop ----------
  async function refreshSessionDropdown() {
    const now = Date.now();
    if (now - uiState.sessionsLoadedAt < 8000) return;
    uiState.sessionsLoadedAt = now;
    try {
      const data = await apiGet('/api/sessions');
      const list = data.sessions || [];
      const sel = $('session-select');
      const cur = uiState.target;
      const opts = ['<option value="auto">Current Conversation (Auto)</option>'];
      for (const s of list) {
        const val = `${s.host}:${s.session_id}`;
        const label = `[${String(s.host).toUpperCase()}] ${s.title || s.session_id.slice(0, 8)} (${s.session_id.slice(0, 6)})`;
        opts.push(`<option value="${esc(val)}"${val === cur ? ' selected' : ''}>${esc(label)}</option>`);
      }
      sel.innerHTML = opts.join('');
    } catch {
      // ignore transient error
    }
  }

  let pollTimer = null;
  async function pollNow() {
    if (pollTimer) clearTimeout(pollTimer);
    let nextDelay = 2000;
    try {
      refreshSessionDropdown();
      refreshCapabilities(false);
      const q = new URLSearchParams({
        conversationId: currentConvId(),
        target: uiState.target,
        fullSession: uiState.fullSession ? '1' : '0',
        etag: uiState.etag,
      });
      const res = await apiGet(`/api/state?${q.toString()}`);
      if (res.asset_version) {
        if (uiState.assetVersion && uiState.assetVersion !== res.asset_version) {
          location.reload();
          return;
        }
        uiState.assetVersion = res.asset_version;
      }
      if (!res.found) {
        uiState.snap = null;
        uiState.etag = '';
        syncEmptyStateVisibility();
        $('meta-line').textContent = 'No active Swarm detected yet';
        nextDelay = 3000;
      } else if (!res.unchanged) {
        const norm = normalizeSnapshot(res);
        uiState.etag = norm.etag || '';
        uiState.snap = norm;
        renderSnapshot(norm);
        nextDelay = (norm.totals?.active_agents || 0) > 0 ? 1500 : 2500;
      }
    } catch {
      nextDelay = 3500;
    }
    pollTimer = setTimeout(pollNow, nextDelay);
  }

  pollNow();
})();
