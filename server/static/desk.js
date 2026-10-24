/* Nightshift desk. No dependencies. */

const $ = (id) => document.getElementById(id);

function esc(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function shortId(id) {
  if (!id) return "–";
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { hour12: false });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    const err = new Error(`${res.status}: ${text.slice(0, 200)}`);
    err.status = res.status;
    throw err;
  }
  if (!res.ok) {
    const detail = body.detail;
    const message =
      typeof detail === "string" ? detail : body.error || `HTTP ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return body;
}

function chipClass(status) {
  if (status === "done" || status === "ready" || status === "started") return "ok";
  if (status === "failed" || status === "down" || status === "collision") return "bad";
  if (status === "running" || status === "queued") return "warn";
  return "";
}

function renderMirror(snapshot) {
  const aside = $("mirror-aside");
  const panel = $("mirror");
  if (!snapshot) {
    aside.textContent = "Could not reach the editor bridge.";
    panel.innerHTML = `<p class="empty">No status yet.</p>`;
    return;
  }
  aside.textContent = snapshot.detail || snapshot.status;
  const typing = snapshot.typing
    ? "File edits type at human pace in the editor."
    : "Typing replay is off until the editor bridge is up.";
  const beats =
    snapshot.heartbeats === true
      ? "Heartbeats are on."
      : snapshot.telemetry_enabled === false
        ? "Heartbeats are off. Run hackaboom --track-telemetry on this box if you want them."
        : "Heartbeats: unknown (no telemetry flag in ~/.hackaboom.json).";
  const panelLink = snapshot.panel_url
    ? `<p><a href="${esc(snapshot.panel_url)}" target="_blank" rel="noreferrer">Open editor control panel</a></p>`
    : "";
  const plugin = snapshot.plugin_dir
    ? `<dt>Plugin</dt><dd><code>${esc(snapshot.plugin_dir)}</code></dd>`
    : `<dt>Plugin</dt><dd>not found — resume still runs, but the editor will not see the keystrokes</dd>`;
  panel.innerHTML = `
    <dl class="dl">
      <dt>Bridge</dt>
      <dd><span class="chip ${chipClass(snapshot.status)}">${esc(snapshot.status)}</span>
        on port ${esc(snapshot.port)}${snapshot.version ? ` (${esc(snapshot.version)})` : ""}</dd>
      ${plugin}
    </dl>
    <p>${esc(typing)}</p>
    <p>${esc(beats)}</p>
    ${panelLink}
  `;
}

function sessionLabel(session) {
  const project = session.project || "session";
  const id = shortId(session.session_id);
  return `${project} · ${id}`;
}

function renderSessions(sessions, selected) {
  const select = $("session");
  if (!sessions.length) {
    select.innerHTML = `<option value="">No sessions ingested yet</option>`;
    $("session-meta").textContent =
      "Ship a bundle with the collector first, then refresh.";
    return;
  }
  select.innerHTML = sessions
    .map((s) => {
      const sel = s.session_id === selected ? " selected" : "";
      return `<option value="${esc(s.session_id)}"${sel}>${esc(sessionLabel(s))}</option>`;
    })
    .join("");
  updateSessionMeta(sessions, select.value);
}

function updateSessionMeta(sessions, id) {
  const found = sessions.find((s) => s.session_id === id);
  const el = $("session-meta");
  if (!found) {
    el.textContent = "";
    return;
  }
  const bits = [found.cwd, found.last_ts ? `last ${when(found.last_ts)}` : ""]
    .filter(Boolean);
  el.textContent = bits.join(" · ");
}

function renderRuns(runs) {
  const panel = $("runs");
  if (!runs.length) {
    panel.innerHTML = `<p class="empty">No runs yet.</p>`;
    return;
  }
  panel.innerHTML = runs
    .map((run) => {
      const summary = run.result_summary
        ? `<p>${esc(run.result_summary)}</p>`
        : run.error
          ? `<p class="err">${esc(run.error)}</p>`
          : "";
      const mirror = run.mirror_status
        ? ` · editor ${esc(run.mirror_status)}`
        : "";
      const panelLink =
        run.mirror_panel_url && (run.status === "running" || run.status === "queued")
          ? ` · <a href="${esc(run.mirror_panel_url)}" target="_blank" rel="noreferrer">editor panel</a>`
          : "";
      return `<article class="run">
        <p><span class="chip ${chipClass(run.status)}">${esc(run.status)}</span>
          <span class="id">${esc(shortId(run.id))}</span>
          · ${esc(shortId(run.session_id))}${mirror}${panelLink}</p>
        <p>${esc(run.instruction)}</p>
        ${summary}
        <p class="hint">${esc(when(run.started_at || run.created_at))}</p>
      </article>`;
    })
    .join("");
}

let sessionsCache = [];
let pollTimer = 0;

async function refresh() {
  let healthOk = false;
  try {
    const health = await api("/health");
    healthOk = health.status === "ok";
    $("svc-stamp").className = "stamp ok";
    $("svc-stamp").innerHTML = `<span class="dot"></span> ${esc(health.service)} ${esc(health.version)}`;
  } catch {
    $("svc-stamp").className = "stamp bad";
    $("svc-stamp").innerHTML = `<span class="dot"></span> unreachable`;
  }

  try {
    renderMirror(await api("/mirror"));
  } catch {
    renderMirror(null);
  }

  const selected = $("session").value;
  try {
    const body = await api("/sessions?limit=100");
    sessionsCache = body.sessions || [];
    renderSessions(sessionsCache, selected);
  } catch (err) {
    $("session-meta").textContent = err.message;
  }

  try {
    const body = await api("/runs");
    const runs = body.runs || [];
    renderRuns(runs);
    const busy = runs.some((r) => r.status === "queued" || r.status === "running");
    if (busy) {
      schedulePoll(2000);
    }
  } catch (err) {
    $("runs").innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
  return healthOk;
}

function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    void refresh();
  }, ms);
}

$("session").addEventListener("change", () => {
  updateSessionMeta(sessionsCache, $("session").value);
  $("btn-resume").textContent = "Resume";
});

$("resume-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const sessionId = $("session").value;
  const instruction = $("instruction").value.trim();
  const status = $("form-status");
  const btn = $("btn-resume");
  if (!sessionId || !instruction) {
    status.textContent = "Pick a session and write an instruction.";
    return;
  }
  btn.disabled = true;
  status.textContent = "Queuing…";
  try {
    const run = await api("/runs", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, instruction }),
    });
    status.textContent = `Queued ${shortId(run.id)}. Send another instruction on the same session when you want a follow-up.`;
    btn.textContent = "Send follow-up";
    $("instruction").value = "";
    $("instruction").placeholder = "Follow-up for this session";
    await refresh();
    schedulePoll(1500);
  } catch (err) {
    status.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

void refresh();
schedulePoll(8000);
