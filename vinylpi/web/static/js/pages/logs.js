(() => {
    const stream = document.getElementById('logsStream');
    if (!stream) return;
    const level = document.getElementById('logsLevel');
    const search = document.getElementById('logsSearch');
    const toggle = document.getElementById('logsToggleLive');
    const status = document.getElementById('logsLiveStatus');
    const dot = document.getElementById('logsLiveDot');
    const count = document.getElementById('logsCount');
    const feedback = document.getElementById('logsFeedback');
    const copy = document.getElementById('logsCopy');
    const clear = document.getElementById('logsClear');
    const newBadge = document.getElementById('logsNew');
    const verbose = document.getElementById('logsVerbose');
    const verboseHelp = document.getElementById('logsVerboseHelp');
    const LIMIT = 200;
    const MAX_VISIBLE = 350;
    let entries = [];
    let cursor = 0;
    let live = true;
    let busy = false;
    let generation = 0;
    let timer = null;
    let newBelow = 0;
    let searchTimer = null;

    function setConnection(text, cls = '') {
        status.textContent = text;
        dot.className = `logs-live-dot ${cls}`;
    }
    function displayCount() {
        count.textContent = String(entries.length);
        newBadge.hidden = !newBelow;
        newBadge.textContent = newBelow ? `· ${newBelow} new below` : '';
    }
    const formatDate = epoch => new Date(epoch * 1000).toLocaleString(undefined, {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    });
    function node(tag, className, value) {
        const element = document.createElement(tag);
        element.className = className;
        element.textContent = value; // Messages are untrusted; never inject as HTML.
        return element;
    }
    function entryNode(item) {
        const div = document.createElement('div');
        div.className = 'log-entry';
        div.dataset.level = item.level;
        div.append(
            node('time', 'log-time', formatDate(item.timestamp)),
            node('span', 'log-level', item.level),
            node('span', 'log-source', item.source),
            node('span', 'log-message', item.message),
        );
        div.firstChild.dateTime = new Date(item.timestamp * 1000).toISOString();
        return div;
    }
    function placeholder(message) {
        stream.replaceChildren(node('div', 'logs-empty', message));
    }
    function renderAll() {
        if (!entries.length) {
            placeholder('No messages match this filter.');
        } else {
            const fragment = document.createDocumentFragment();
            entries.forEach(item => fragment.append(entryNode(item)));
            stream.replaceChildren(fragment);
            stream.scrollTop = stream.scrollHeight;
        }
        newBelow = 0;
        displayCount();
    }
    function appendEvents(incoming) {
        if (!incoming.length) return;
        const nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 110;
        if (!entries.length) stream.replaceChildren();
        const fragment = document.createDocumentFragment();
        for (const item of incoming) {
            entries.push(item);
            fragment.append(entryNode(item));
            cursor = Math.max(cursor, item.id);
        }
        stream.append(fragment);
        if (entries.length > MAX_VISIBLE) {
            const overflow = entries.length - MAX_VISIBLE;
            entries.splice(0, overflow);
            for (let i = 0; i < overflow; i++) stream.firstElementChild?.remove();
        }
        if (nearBottom) {
            stream.scrollTop = stream.scrollHeight;
            newBelow = 0;
        } else {
            newBelow += incoming.length;
        }
        displayCount();
    }
    async function query(after) {
        const params = new URLSearchParams({
            level: level.value,
            search: search.value.trim(),
            limit: String(LIMIT),
            after: String(after),
        });
        const response = await fetch(`/api/logs?${params}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const json = await response.json();
        if (!json.ok || !Array.isArray(json.entries)) throw new Error('Unexpected response');
        return json.entries;
    }
    async function loadLatest() {
        const ticket = ++generation;
        if (!live || busy) return;
        busy = true;
        setConnection('Loading…');
        try {
            const fetched = await query(0);
            if (ticket !== generation) return;
            entries = fetched;
            cursor = fetched.length ? fetched[fetched.length - 1].id : 0;
            renderAll();
            setConnection('Live', 'online');
        } catch (e) {
            if (ticket !== generation) return;
            placeholder(`Could not load logs: ${e.message}`);
            setConnection('Connection error', 'offline');
        } finally { busy = false; }
    }
    async function poll() {
        if (!live || busy || document.hidden) return;
        const ticket = generation;
        busy = true;
        try {
            const fetched = await query(cursor);
            if (ticket !== generation) return;
            appendEvents(fetched);
            setConnection('Live', 'online');
            feedback.textContent = '';
        } catch (e) {
            setConnection('Connection error', 'offline');
            feedback.textContent = `Live update failed: ${e.message}`;
        } finally { busy = false; }
    }
    function restart() {
        generation++;
        entries = [];
        cursor = 0;
        if (live) {
            if (busy) { // Previous request is still outstanding; retry shortly.
                setTimeout(() => { if (live && !busy) loadLatest(); }, 400);
            } else { loadLatest(); }
        } else {
            placeholder('Paused. Resume live to load messages.');
            displayCount();
        }
    }
    toggle.addEventListener('click', () => {
        live = !live;
        toggle.setAttribute('aria-pressed', String(live));
        toggle.textContent = live ? 'Pause live' : 'Resume live';
        if (live) {
            setConnection('Connecting…');
            restart();
        } else {
            generation++;
            setConnection('Paused');
        }
    });
    level.addEventListener('change', restart);
    search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(restart, 280);
    });
    clear.addEventListener('click', () => {
        generation++;
        entries = [];
        // Keep position: only messages arriving after clear appear on screen.
        placeholder('View cleared. New messages will appear here.');
        displayCount();
        feedback.textContent = 'Only the view was cleared; saved history remains available.';
        // Preserve the cursor; if no entries were displayed, obtain the latest ID.
        if (!cursor) {
            fetch('/api/logs/summary', { cache: 'no-store' }).then(r => r.json()).then(data => {
                cursor = Math.max(cursor, data.latest_id || 0);
            }).catch(() => {});
        }
    });
    copy.addEventListener('click', async () => {
        if (!entries.length) { feedback.textContent = 'No messages to copy.'; return; }
        const text = entries.map(item => `${formatDate(item.timestamp)} [${item.level}] [${item.source}] ${item.message}`).join('\n');
        try {
            await navigator.clipboard.writeText(text);
            feedback.textContent = `${entries.length} messages copied.`;
        } catch {
            feedback.textContent = 'Clipboard unavailable in this browser.';
        }
    });
    stream.addEventListener('scroll', () => {
        if (stream.scrollHeight - stream.scrollTop - stream.clientHeight < 55) {
            newBelow = 0;
            displayCount();
        }
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && live) poll(); });
    async function initVerbose() {
        try {
            const response = await fetch('/api/config', { cache: 'no-store' });
            if (!response.ok) throw new Error('Settings unavailable');
            const config = await response.json();
            verbose.checked = Boolean(config.debug?.logs);
            verbose.disabled = false;
            verboseHelp.textContent = 'Enable extra recording and recognition messages (also in Settings).';
        } catch {
            verboseHelp.textContent = 'Debug setting unavailable; existing messages remain accessible.';
        }
    }
    verbose.addEventListener('change', async () => {
        const enabled = verbose.checked;
        verbose.disabled = true;
        try {
            const response = await fetch('/api/config', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ debug: { logs: enabled } }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            verboseHelp.textContent = enabled ? 'Detailed messages enabled.' : 'Detailed messages disabled; warnings/errors are still captured.';
            if (enabled && level.value === 'issues') {
                level.value = 'all';
                restart();
            }
        } catch (e) {
            verbose.checked = !enabled;
            feedback.textContent = `Could not update debug setting: ${e.message}`;
        } finally {
            verbose.disabled = false;
        }
    });
    initVerbose();
    loadLatest();
    timer = setInterval(poll, 2200);
})();
