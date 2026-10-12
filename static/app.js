const POLL_MS = 1000;
const SPARK_POINTS = 90;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n || 0).toLocaleString();

let tpsHistory = [];

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: "Unexpected server response" }));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function busy(button, label) {
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="spinner"></span>${label}`;
  return () => { button.disabled = false; button.innerHTML = original; };
}

// ---- Document input ---------------------------------------------------------
const doc = $("doc");
const updateMeta = () => { $("doc-meta").textContent = `${fmt(doc.value.length)} characters`; };
doc.addEventListener("input", updateMeta);

document.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
  const res = await fetch(`/samples/${b.dataset.sample}`);
  doc.value = await res.text();
  updateMeta();
}));

$("file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  doc.value = await file.text();
  updateMeta();
});

// ---- Analysis ---------------------------------------------------------------
function renderAnalysis(r) {
  $("results").classList.remove("hidden");
  $("qa").classList.remove("hidden");
  $("analysis-meta").textContent = `${r.chunks} part(s) analysed in parallel · ${r.seconds}s on own GPU`;
  $("summary").textContent = r.summary || "No summary returned.";
  $("risks").innerHTML = r.risks.length ? r.risks.map((k) => {
    const sev = String(k.severity || "low").toLowerCase();
    return `<li class="${esc(sev)}"><span class="sev">${esc(sev)}</span><div><b>${esc(k.issue)}</b> — ${esc(k.why)}</div>${k.quote ? `<div class="quote">“${esc(k.quote)}”</div>` : ""}</li>`;
  }).join("") : `<li>No major risks found.</li>`;
  $("facts").innerHTML = r.key_facts.map((f) => `<li>${esc(f)}</li>`).join("") || `<li class="muted">None found.</li>`;
  $("pii").innerHTML = r.pii.length
    ? r.pii.map((p) => `<li><b>${esc(p.type)}</b> ×${p.count} <span class="muted">${p.examples.map(esc).join(", ")}</span></li>`).join("")
    : `<li class="muted">None detected.</li>`;
}

$("analyze").addEventListener("click", async () => {
  if (!doc.value.trim()) { doc.focus(); return; }
  const done = busy($("analyze"), "Analyzing on your GPU…");
  try { renderAnalysis(await api("/api/analyze", { document: doc.value })); }
  catch (err) { alert(err.message); }
  finally { done(); }
});

// ---- Q&A --------------------------------------------------------------------
function addMsg(role, text, meta) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `${esc(text)}${meta ? `<span class="meta">${esc(meta)}</span>` : ""}`;
  $("chat").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return el;
}

$("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = $("question").value.trim();
  if (!question) return;
  $("question").value = "";
  addMsg("user", question);
  const pending = addMsg("bot", "Thinking on your GPU…");
  try {
    const r = await api("/api/ask", { document: doc.value, question });
    pending.remove();
    addMsg("bot", r.answer, `${r.seconds}s · ${r.excerpts_used} excerpt(s) · own GPU`);
  } catch (err) { pending.textContent = err.message; }
});

// ---- Privacy guard + rush hour ---------------------------------------------
$("leak").addEventListener("click", async () => {
  try {
    const r = await api("/api/leak-test", {});
    $("leak-result").innerHTML = `<span class="${r.blocked ? "ok" : "bad"}">${r.blocked ? "🛡️ " : ""}${esc(r.message)}</span>`;
  } catch (err) { $("leak-result").textContent = err.message; }
});

$("rush").addEventListener("click", async () => {
  try { await api("/api/rush", { users: Number($("rush-n").value) }); }
  catch (err) { $("rush-status").textContent = err.message; }
});

// ---- Live metrics -----------------------------------------------------------
function drawSpark() {
  const c = $("spark");
  const w = (c.width = c.clientWidth * devicePixelRatio);
  const h = (c.height = 80 * devicePixelRatio);
  const ctx = c.getContext("2d");
  const max = Math.max(50, ...tpsHistory);
  const css = getComputedStyle(document.documentElement);
  ctx.clearRect(0, 0, w, h);
  ctx.beginPath();
  tpsHistory.forEach((v, i) => {
    const x = (i / (SPARK_POINTS - 1)) * w;
    const y = h - (v / max) * (h - 6) - 3;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = css.getPropertyValue("--accent").trim();
  ctx.lineWidth = 2 * devicePixelRatio;
  ctx.stroke();
  ctx.fillStyle = css.getPropertyValue("--muted").trim();
  ctx.font = `${11 * devicePixelRatio}px sans-serif`;
  ctx.fillText(`tokens/sec · peak ${Math.round(Math.max(0, ...tpsHistory))}`, 4, 12 * devicePixelRatio);
}

async function poll() {
  try {
    const { gpu, privacy, rush } = await api("/api/status");
    $("gpu-dot").className = `dot ${gpu.ok ? "on" : "off"}`;
    $("gpu-error").textContent = gpu.ok ? "" : gpu.error;
    if (gpu.ok) {
      $("badge-model").textContent = `Model: ${gpu.model} · own GPU`;
      $("m-running").textContent = fmt(gpu.running);
      $("m-waiting").textContent = fmt(gpu.waiting);
      $("m-tps").textContent = fmt(Math.round(gpu.tokens_per_s));
      $("m-ttft").textContent = gpu.avg_ttft_s == null ? "–" : `${gpu.avg_ttft_s.toFixed(2)}s`;
      $("m-kv").textContent = `${gpu.kv_cache_pct}%`;
      $("kv-bar").style.width = `${Math.min(100, gpu.kv_cache_pct)}%`;
      tpsHistory = [...tpsHistory, gpu.tokens_per_s].slice(-SPARK_POINTS);
      drawSpark();
    }
    $("p-third").textContent = fmt(privacy.bytes_to_third_parties);
    $("p-blocked").textContent = fmt(privacy.blocked_attempts);
    $("p-tokens").textContent = fmt(privacy.prompt_tokens + privacy.completion_tokens);
    $("p-allow").textContent = privacy.allowlist.join(", ");
    $("badge-egress").textContent = `${fmt(privacy.bytes_to_third_parties)} bytes sent to third-party AI`;
    $("rush-status").textContent = rush.active || rush.done
      ? `${rush.active} in flight · ${rush.done} done${rush.failed ? ` · ${rush.failed} failed` : ""}` : "";
  } catch {
    $("gpu-dot").className = "dot off";
  } finally {
    setTimeout(poll, POLL_MS);
  }
}
poll();
