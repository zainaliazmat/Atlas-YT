/* YT-AGENTS Control Room — wires the signed-off prototype to the live JSON API.
   Plain vanilla JS, no build step. Design is preserved verbatim; this file only
   renders real API data into the existing classes. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  function loading(el, label) {
    if (el) el.innerHTML = '<div class="state-msg">' + esc(label || "Loading…") + "</div>";
  }
  function errState(el, msg) {
    if (el) el.innerHTML = '<div class="state-msg err">⚠ ' + esc(msg) + "</div>";
  }
  function emptyState(el, msg) {
    if (el) el.innerHTML = '<div class="state-msg">' + esc(msg) + "</div>";
  }
  function num(v, dflt) { return (v === null || v === undefined || v === "") ? (dflt === undefined ? "—" : dflt) : v; }

  async function getJSON(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error("HTTP " + r.status + " for " + path);
    return r.json();
  }

  async function postJSON(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status + " for " + path));
    return data;
  }

  // module-level "current" deep-nav state (carries slug/name across views)
  var current = { slug: null, agent: null, gate: null };

  // ---------------------------------------------------------------- nav core
  function go(view, rail) {
    document.querySelectorAll(".view").forEach(function (v) {
      v.classList.toggle("active", v.id === view);
    });
    document.querySelectorAll(".rail .ic").forEach(function (i) {
      i.classList.toggle("on", i.dataset.rail === rail);
    });
    window.scrollTo({ top: 0, behavior: "instant" });
    loadView(view);
  }
  window.go = go;

  // delegated click handling for any [data-go] element (rail, cards, rows, links)
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-go]");
    if (!el) return;
    // don't hijack clicks on real form inputs inside a nav container
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    e.preventDefault();
    // carry deep-nav identifiers through data attributes
    if (el.dataset.slug) current.slug = el.dataset.slug;
    if (el.dataset.agent) current.agent = el.dataset.agent;
    if (el.dataset.gate) { current.slug = el.dataset.gate; }
    go(el.dataset.go, el.dataset.rail || "overview");
  });

  // ---------------------------------------------------------------- view dispatch
  function loadView(view) {
    switch (view) {
      case "v-overview": renderOverview(); return renderBelt();
      case "v-projects": return renderProjects();
      case "v-pipeline": return renderPipeline(current.slug);
      case "v-fleet": return renderFleet();
      case "v-agent": return renderAgent(current.agent);
      case "v-quality": return renderQuality();
      case "v-activity": return renderActivity();
      case "v-settings": return renderSettings();
      case "v-gate": return renderGate(current.slug);
    }
  }

  // ================================================================ OVERVIEW
  async function renderOverview() {
    var kpis = $("ov-kpis"), spine = $("ov-spine"), mid = $("ov-mid"), bottom = $("ov-bottom");
    loading(kpis, "Loading mission control…");
    spine.innerHTML = ""; mid.innerHTML = ""; bottom.innerHTML = "";
    var d;
    try { d = await getJSON("/api/overview"); }
    catch (e) { errState(kpis, "Couldn't reach the API. " + e.message); return; }

    var k = d.kpis || {};
    var lq = (k.latest_quality === null || k.latest_quality === undefined)
      ? '<div class="v">—<small>no score</small></div>'
      : '<div class="v ok">' + esc(k.latest_quality) + ' <small>latest</small></div>';
    kpis.innerHTML =
      kpi("In production", '<div class="v">' + num(k.in_production, 0) + "</div>") +
      kpi("Awaiting you", '<div class="v">' + num(k.awaiting_you, 0) + ' <small>gate</small></div>', "alert nav-hint", 'data-go="v-projects" data-rail="projects"') +
      kpi("Fleet", '<div class="v">' + num(k.fleet_total, 0) + " <small>" + num(k.fleet_idle, 0) + ' idle</small></div>', "nav-hint", 'data-go="v-fleet" data-rail="fleet"') +
      kpi("Latest quality", lq, "nav-hint", 'data-go="v-quality" data-rail="quality"') +
      kpi("Projects", '<div class="v">' + num((d.counts || {}).total, 0) + " <small>total</small></div>", "nav-hint", 'data-go="v-projects" data-rail="projects"');

    // production spine
    var ap = d.active_pipeline;
    if (ap && ap.nodes && ap.nodes.length) {
      current.slug = ap.slug; // so clicking opens the right pipeline
      var chain = "";
      var nodes = ap.nodes, forkBuffer = [];
      nodes.forEach(function (n, i) {
        var gate = ap.gates && ap.gates[n.key];
        if (n.group === "parallel") { forkBuffer.push(n); }
        // flush parallel buffer when we leave the group
        var isLastOfGroup = n.group === "parallel" && (i === nodes.length - 1 || nodes[i + 1].group !== "parallel");
        if (n.group !== "parallel") {
          var cls = n.status === "done" ? "done" : (n.status === "blocked" || n.status === "gate" ? "gate" : "pending");
          chain += '<div class="node ' + (n.status === "done" ? "done" : "pending") + '">' +
            '<div class="box"><span class="em">' + esc(n.emoji) + "</span></div>" +
            '<div class="nm">' + esc(n.key) + "</div></div>";
          if (i < nodes.length - 1) chain += '<div class="conn ' + (n.status === "done" ? "done" : "") + '"></div>';
        } else if (isLastOfGroup) {
          chain += '<div class="fork">' + forkBuffer.map(function (f) {
            return '<div class="mini">' + esc(f.emoji) + " " + esc(f.key) + "</div>";
          }).join("") + "</div>";
          if (i < nodes.length - 1) chain += '<div class="conn"></div>';
          forkBuffer = [];
        }
      });
      var gatesArr = ap.gates ? Object.keys(ap.gates).map(function (g) { return ap.gates[g]; }) : [];
      var blocked = gatesArr.filter(function (g) { return g.status === "blocked"; });
      var note = blocked.length ? ("paused at " + blocked[0].gate + " gate") : "spine advancing";
      spine.innerHTML =
        '<div class="card nav-hint" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(ap.slug) + '">' +
        '<h3>Production spine — "' + esc(ellip(ap.title, 70)) + '"<span class="t">open pipeline →</span></h3>' +
        '<div class="chain">' + chain + "</div>" +
        '<div class="legend">' +
        '<span><i style="background:var(--done)"></i>done</span>' +
        '<span><i style="background:var(--gate)"></i>awaiting you</span>' +
        '<span><i style="background:var(--wait)"></i>pending</span>' +
        '<span style="margin-left:auto;color:var(--blue)">' + esc(note) + "</span></div></div>";
    } else {
      spine.innerHTML = '<div class="card"><div class="state-msg">No active production in the spine right now.</div></div>';
    }

    // gate card + scorecard
    var gateCard;
    if (d.gate && (d.gate.kind === "factcheck" || d.gate.kind === "final_render" || d.gate.gate)) {
      var g = d.gate, slug = g.slug;
      var sm = (g.preview && g.preview.summary) || g.summary || {};
      var blockedMeta = g.hard_block ? "a hard block returns the script to Marlow" : "a block here returns the script to Marlow";
      var claims = ((g.preview && g.preview.flagged) || g.flagged || []).slice(0, 3).map(function (c) {
        return '<div class="claim"><span class="b">FLAG</span><span>' + esc(ellip(c.claim_text, 110)) + "</span></div>";
      }).join("") || '<div class="claim"><span class="b">OK</span><span>No flagged claims — verdict ' + esc((g.preview && g.preview.verdict) || g.verdict || "pending") + ".</span></div>";
      gateCard =
        '<div class="card gatecard">' +
        "<h3>Awaiting your sign-off</h3>" +
        '<div class="ttl">' + esc(prettyGate(g.kind || g.gate)) + "</div>" +
        '<div class="meta">' + esc(sm.flagged || 0) + " flagged of " + esc((sm.verified || 0) + (sm.flagged || 0) + (sm.unverifiable || 0)) + " · " + esc(blockedMeta) + "</div>" +
        claims +
        '<div class="actions">' +
        '<button class="btn primary" data-go="v-gate" data-rail="projects" data-gate="' + esc(slug) + '">Review &amp; approve</button>' +
        '<button class="btn" data-go="v-gate" data-rail="projects" data-gate="' + esc(slug) + '">Open gate</button></div></div>';
    } else {
      gateCard = '<div class="card gatecard"><h3>Awaiting your sign-off</h3><div class="state-msg">No gate is waiting on you.</div></div>';
    }

    var scoreCard;
    if (d.quality && d.quality.scorecard) {
      scoreCard = '<div class="card nav-hint" data-go="v-quality" data-rail="quality"><h3>Latest scorecard</h3>' +
        meters(d.quality.scorecard) + "</div>";
    } else {
      scoreCard = '<div class="card nav-hint" data-go="v-quality" data-rail="quality"><h3>Latest scorecard</h3>' +
        '<div class="state-msg">No scorecard yet — the eval layer hasn\'t scored a render. <span style="color:var(--blue)">open quality →</span></div></div>';
    }
    mid.innerHTML = gateCard + scoreCard;

    // fleet preview + activity log
    var fleetRows = (d.fleet || []).map(function (a) {
      return '<div class="frow" data-go="v-agent" data-rail="fleet" data-agent="' + esc(a.name) + '">' +
        '<div class="em">' + esc(a.emoji) + "</div>" +
        '<div class="nm"><b>' + esc(a.display) + "</b><small>" + esc(a.role) + "</small></div>" +
        '<div class="st"><span class="stled" style="background:' + statusColor(a.status) + '"></span>' +
        esc(a.detail || a.status) + '<span class="pv">' + esc(a.provider) + "</span></div></div>";
    }).join("") || '<div class="state-msg">No agents.</div>';

    var log = (d.activity || []).slice(0, 6).map(function (e) {
      var line = e.decision || e.stage || "";
      if (e.project) line += " — " + ellip(e.project, 48);
      return "<div><span class=\"ts\">" + esc(e.rel || "") + "</span><span>" + esc(line) + "</span></div>";
    }).join("") || '<div class="state-msg">No recent activity.</div>';

    bottom.innerHTML =
      '<div class="card"><h3>Fleet<span class="t">click an agent →</span></h3>' + fleetRows + "</div>" +
      '<div class="card"><h3>Activity log</h3><div class="log">' + log + "</div></div>";
  }

  function meters(sc) {
    var dims = sc.dimensions || sc.global || sc.scores || null;
    if (!dims) return '<div class="state-msg">Scorecard present but no dimensions to show.</div>';
    var out = "";
    Object.keys(dims).forEach(function (key) {
      var v = dims[key];
      var val = (typeof v === "object") ? (v.value !== undefined ? v.value : v.score) : v;
      var name = (typeof v === "object" && v.name) ? v.name : key;
      var pct = Math.max(0, Math.min(100, Math.round((Number(val) || 0) * 100)));
      out += '<div class="meter"><div class="row"><span>' + esc(name) + '</span><span class="vv">' + esc(val) +
        '</span></div><div class="mbar"><i style="width:' + pct + '%"></i></div></div>';
    });
    return out;
  }

  // ================================================================ PROJECTS
  var projectsCache = null, projTab = "all", projSearch = "";

  async function renderProjects() {
    var kpis = $("pr-kpis"), tabs = $("pr-tabs"), list = $("pr-list");
    loading(list, "Loading projects…");
    kpis.innerHTML = ""; tabs.innerHTML = "";
    var d;
    try { d = await getJSON("/api/projects"); }
    catch (e) { errState(list, "Couldn't load projects. " + e.message); return; }
    projectsCache = d;
    var c = d.counts || {};
    kpis.innerHTML =
      kpi("Total", '<div class="v">' + num(c.total, 0) + "</div>") +
      kpi("Awaiting you", '<div class="v warn">' + num(c.needs_you, 0) + "</div>") +
      kpi("In production", '<div class="v">' + num(c.in_production, 0) + "</div>") +
      kpi("Blocked", '<div class="v" style="color:var(--red)">' + num(c.blocked, 0) + "</div>") +
      kpi("Done", '<div class="v ok">' + num(c.done, 0) + "</div>") +
      kpi("Avg quality", '<div class="v">' + num(c.avg_quality) + "</div>");

    var n = (d.projects || []).length;
    tabs.innerHTML =
      tab("All", n, "all") + tab("Needs you", c.needs_you, "needs") +
      tab("In production", c.in_production, "prod") + tab("Done", c.done, "done") +
      tab("Blocked", c.blocked, "block");
    bindProjectControls();
    drawProjectRows();
  }

  function bindProjectControls() {
    document.querySelectorAll("#pr-tabs .tab").forEach(function (t) {
      t.onclick = function () { projTab = t.dataset.tab; drawProjectRows(); };
    });
    var s = $("pr-search");
    if (s) s.oninput = function () { projSearch = s.value.toLowerCase(); drawProjectRows(); };
  }

  function drawProjectRows() {
    var list = $("pr-list");
    document.querySelectorAll("#pr-tabs .tab").forEach(function (t) {
      t.classList.toggle("on", t.dataset.tab === projTab);
    });
    var rows = (projectsCache.projects || []).filter(function (p) {
      if (projTab === "needs") return /blocked_at_/.test(p.status) && p.gate;
      if (projTab === "prod") return p.status === "running";
      if (projTab === "done") return p.status === "done";
      if (projTab === "block") return /blocked/.test(p.status) || p.status === "failed" || p.status === "interrupted";
      return true;
    }).filter(function (p) {
      return !projSearch || (p.label || p.topic || p.slug).toLowerCase().indexOf(projSearch) >= 0;
    });
    if (!rows.length) { emptyState(list, "No projects match this filter."); return; }
    var head = '<div class="lhead"><span></span><span>Project</span><span>Status</span><span>Scenes · runtime</span><span>Quality</span><span></span></div>';
    list.innerHTML = head + rows.map(projectRow).join("");
  }

  function projectRow(p) {
    var m = statusMap(p);          // {badge, tile, glyph, rowcls, stage}
    var title = esc(p.label || p.topic || p.slug);
    var rt = p.runtime_sec ? ("~" + Math.round(p.runtime_sec) + "s") : "—";
    var scenes = p.scenes ? (p.scenes + " scenes") : "—";
    var q = qualityPill(p.quality);
    // action buttons depend on state
    var act;
    if (m.badge === "prod" && p.gate) {
      act = '<button class="btn sm primary" data-go="v-gate" data-rail="projects" data-gate="' + esc(p.slug) + '">Approve gate</button>' +
            '<button class="btn sm" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(p.slug) + '">Open</button>';
    } else if (m.badge === "block") {
      act = '<button class="btn sm" data-go="v-gate" data-rail="projects" data-gate="' + esc(p.slug) + '">View report</button>' +
            '<button class="btn sm" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(p.slug) + '">Open</button>';
    } else {
      act = '<button class="btn sm" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(p.slug) + '">Open</button>';
    }
    return '<div class="row ' + m.rowcls + '" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(p.slug) + '">' +
      '<div class="tile ' + m.tile + '">' + m.glyph + "</div>" +
      '<div class="ti"><div class="nm">' + ellipEsc(title, 70) + '</div><div class="sl">' + esc(p.slug) + " · updated <b>" + esc(p.updated_rel || "") + "</b></div></div>" +
      '<div class="st"><span class="badge ' + m.badge + '">' + esc(m.label) + '</span><div class="stg">' + esc(m.stage) + "</div></div>" +
      '<div class="meta">' + esc(scenes) + "<br><small>" + esc(rt) + "</small></div>" +
      '<div class="q">' + q + "</div>" +
      '<div class="act">' + act + "</div></div>";
  }

  function statusMap(p) {
    var s = p.status || "";
    if (s === "done") return { badge: "done", tile: "done", glyph: '<span class="pl">▶</span>', rowcls: "", label: "done", stage: "rendered" };
    if (s === "running") return { badge: "prod", tile: "prod", glyph: "⚑", rowcls: "", label: "in production", stage: "pipeline running" };
    if (/blocked_at_/.test(s) && p.gate) {
      // an awaiting-you gate; a hard fact-check block is shown red
      return { badge: "prod", tile: "prod", glyph: "⚑", rowcls: "attn", label: "awaiting you", stage: (p.gate + " gate") };
    }
    if (/blocked/.test(s)) return { badge: "block", tile: "block", glyph: "⛔", rowcls: "block", label: "blocked", stage: "sent back · can't be approved away" };
    if (s === "failed") return { badge: "block", tile: "block", glyph: "⛔", rowcls: "block", label: "failed", stage: "run failed" };
    if (s === "interrupted") return { badge: "block", tile: "block", glyph: "⏸", rowcls: "attn", label: "interrupted", stage: "stopped mid-run · re-run when ready" };
    if (s === "queued") return { badge: "queue", tile: "queue", glyph: "☰", rowcls: "", label: "queued", stage: "waiting for a slot" };
    return { badge: "queue", tile: "queue", glyph: "☰", rowcls: "", label: esc(s || "—"), stage: "" };
  }

  function qualityPill(q) {
    if (!q) return '<span class="pill none">—</span>';
    var v = q.overall !== undefined ? q.overall : q.quality_score;
    if (v === null || v === undefined) return '<span class="pill none">not scored</span>';
    return '<span class="pill ok"><b>' + esc(v) + "</b> in band</span>";
  }

  // ================================================================ PIPELINE
  async function renderPipeline(slug) {
    var head = $("pl-head"), kpis = $("pl-kpis"), body = $("pl-body"), crumb = $("pl-crumb-slug");
    crumb.textContent = slug || "";
    if (!slug) {
      head.innerHTML = "<h1>Pipeline</h1>";
      kpis.innerHTML = "";
      emptyState(body, "Pick a project from Projects to open its pipeline.");
      return;
    }
    head.innerHTML = "<h1>Pipeline</h1>";
    loading(body, "Loading pipeline…");
    var d;
    try { d = await getJSON("/api/projects/" + encodeURIComponent(slug)); }
    catch (e) { errState(body, "Couldn't load pipeline. " + e.message); return; }

    var sm = d.summary || {};
    var smap = statusMap(sm);

    // Re-run is offered for a video that has SETTLED (failed / cancelled / done /
    // blocked) — never one mid-flight (running / queued), where a re-run would race the
    // live worker. The dropdown re-runs FROM a previously-run station only.
    var stStr = String(sm.status || "");
    var rerunnable = stStr === "done" || stStr === "failed" || stStr === "cancelled" ||
      stStr === "interrupted" || /blocked/.test(stStr);
    var runnableStages = (d.stages || []).filter(function (s) { return s.status !== "pending"; });
    var rerunMenu = runnableStages.map(function (s) {
      return '<button class="ri" data-from="' + esc(s.key) + '" role="menuitem">From <b>' +
        esc(s.key) + "</b></button>";
    }).join("");
    var rerunBtn = rerunnable
      ? '<div class="rerun-split"><button class="btn" id="pl-rerun" title="Re-run the whole video from the start">↻ Re-run</button>' +
        (runnableStages.length
          ? '<button class="btn caret" id="pl-rerun-caret" aria-haspopup="true" aria-expanded="false" aria-label="Re-run from a stage">▾</button>' +
            '<div class="rerun-menu" id="pl-rerun-menu" role="menu" hidden><div class="rm-h">Re-run from…</div>' + rerunMenu + "</div>"
          : "") +
        "</div>"
      : "";

    head.innerHTML =
      "<div><h1>" + ellipEsc(sm.label || sm.topic || slug, 80) +
      ' <span class="badge" style="vertical-align:middle">' + esc(smap.label) + "</span></h1>" +
      '<div class="slug">atlas/projects/' + esc(slug) + '/project.json</div></div>' +
      '<div class="acts">' + rerunBtn + '<button class="btn">Open folder</button>' +
      (d.has_video ? '<button class="btn" id="pl-publish">Publish…</button>' : "") +
      (d.has_video ? '<button class="btn primary" id="pl-openvid">Open video.mp4</button>' : "") + "</div>";

    var stages = d.stages || [];
    var doneCount = stages.filter(function (s) { return s.status === "done"; }).length;
    kpis.innerHTML =
      kpi("Status", '<div class="v ' + (sm.status === "done" ? "ok" : "") + '">' + esc(smap.label) + "</div>") +
      kpi("Stages", '<div class="v">' + doneCount + "<small>/" + stages.length + "</small></div>") +
      kpi("Scenes", '<div class="v">' + num(sm.scenes, "—") + "</div>") +
      kpi("Runtime", '<div class="v">' + (sm.runtime_sec ? Math.round(sm.runtime_sec) : "—") + "<small>s</small></div>") +
      kpi("Updated", '<div class="v" style="font-size:15px">' + esc(sm.updated_rel || "—") + "</div>") +
      kpi("Quality", (sm.quality ? '<div class="v ok">' + esc(sm.quality.overall || sm.quality.quality_score) + "<small>in band</small></div>" : '<div class="v">—<small>not scored</small></div>'));

    // ladder
    var gates = d.gates || {};
    var ladder = stages.map(function (s) {
      var ledCls = s.status === "done" ? "done" : (s.status === "blocked" ? "gate" : "wait");
      var lineCls = s.status === "done" ? "done" : "";
      var gate = gates[s.key];
      if (gate) {
        var st = gate.status === "approved" ? "approved" : (gate.status === "blocked" ? (gate.hard_block ? "blocked" : "review") : gate.status);
        return '<div class="stage"><div class="gut"><span class="led gate"></span><span class="line-v ' + lineCls + '"></span></div><div>' +
          '<div class="gaterow"><div class="stop"><span class="nm" style="color:var(--gate)">⚑ ' + esc(prettyGate(s.key)) +
          '</span><span class="sp"><span class="state gate">' + esc(st) + "</span></span></div>" +
          (gate.details && gate.details.verdict ? '<div class="gdetail"><b>verdict: ' + esc(gate.details.verdict) + "</b></div>" : "") +
          "</div></div></div>";
      }
      var sub = s.detail ? ('<div class="sub"><span class="file">' + esc(s.artifact || "") + '</span>' + (s.detail ? '<span class="dot">·</span>' + esc(s.detail) : "") + "</div>") :
        (s.artifact ? '<div class="sub"><span class="file">' + esc(s.artifact) + "</span></div>" : "");
      var autogate = s.autogate ? '<div class="autogate">▲ <b>auto-gate</b> · self-scan ✓ · lint ✓ · validate ✓</div>' : "";
      var failCls = (s.status === "failed") ? " failed" : "";
      var when = s.updated_rel ? '<span class="when">' + esc(s.updated_rel) + "</span>" : "";
      return '<div class="stage clickable' + failCls + '" data-stage="' + esc(s.key) + '" tabindex="0" role="button" aria-label="Inspect ' + esc(s.key) + ' stage">' +
        '<div class="gut"><span class="led ' + ledCls + '"></span><span class="line-v ' + lineCls + '"></span></div><div>' +
        '<div class="stop"><span class="nm">' + esc(s.key) + '</span><span class="ag">' + esc(s.agent ? s.agent.emoji : "") + " <b>" + esc(s.agent ? s.agent.display : "") + "</b></span>" +
        '<span class="sp">' + when + '<span class="state ' + (s.status === "done" ? "done" : (s.status === "failed" ? "fail" : "")) + '">' + esc(s.status) + "</span>" + (s.validated ? '<span class="ck">✓</span>' : "") + '<span class="insp">inspect →</span></span></div>' +
        sub + autogate + "</div></div>";
    }).join("") || '<div class="state-msg">No stages.</div>';

    // right column: video / artifacts / contracts
    var vid;
    if (d.has_video) {
      vid = '<div class="card"><h3>Final video</h3><div class="vid"><span class="ystrip"></span>' +
        '<video controls preload="metadata" src="/api/media/' + encodeURIComponent(slug) + '/video"></video>' +
        '<span class="tlabel">' + (sm.runtime_sec ? Math.round(sm.runtime_sec) + "s" : "video") + "</span></div>" +
        (sm.quality ? '<div class="qline"><span>Inspector score</span><span><span class="q">' + esc(sm.quality.overall || sm.quality.quality_score) + '</span> <span class="band">in band</span></span></div>' : "") +
        "</div>";
    } else {
      vid = '<div class="card"><h3>Final video</h3><div class="state-msg">No render yet — video appears here once the pipeline reaches render.</div></div>';
    }
    var artifacts = (d.artifacts || []).map(function (a) {
      return '<div class="f"><span class="fn">' + esc(a.name) + '</span><span class="sz">' + fmtSize(a.size) +
        '</span><a href="#" data-art="' + esc(a.name) + '">open</a></div>';
    }).join("") || '<div class="state-msg">No artifacts.</div>';
    var contracts = (d.contracts || []).map(function (c) {
      var ok = c.status === "valid";
      return '<div class="row"><span>' + esc(c.contract) + '</span><span class="' + (ok ? "ok" : "") + '" style="' + (ok ? "" : "color:var(--red)") + '">' +
        esc(c.status) + (ok ? " ✓" : " ✗") + "</span></div>";
    }).join("") || '<div class="state-msg">No contracts validated.</div>';

    // event history (project.json history, newest-first from the API)
    var history = (d.history || []).map(function (h) {
      var line = h.decision || h.stage || "event";
      return '<div class="ev"><span class="ts">' + esc(relTime(h.ts) || "") + '</span>' +
        '<div class="evb"><div class="evd">' + esc(line) +
        (h.stage ? '<span class="evs">' + esc(h.stage) + "</span>" : "") + "</div>" +
        (h.why ? '<div class="evw">' + esc(h.why) + "</div>" : "") + "</div></div>";
    }).join("") || '<div class="state-msg">No events recorded yet.</div>';

    body.innerHTML =
      '<div class="ladder"><div class="lh">Production spine <span class="r">' + stages.length + " stages · click a stage to inspect</span></div>" + ladder + "</div>" +
      "<div>" + vid +
      '<div class="card files"><h3>Artifacts</h3>' + artifacts + "</div>" +
      '<div class="card ctr"><h3>Contracts</h3>' + contracts + "</div>" +
      '<div class="card hist"><h3>Event history <span class="r">project.json</span></h3>' + history + "</div></div>";

    // wire artifact "open" links + video button
    body.querySelectorAll("a[data-art]").forEach(function (a) {
      a.onclick = function (e) { e.preventDefault(); openArtifact(slug, a.dataset.art); };
    });
    // clicking (or Enter on) a stage opens the depth-2 inspector drawer
    body.querySelectorAll(".stage.clickable").forEach(function (el) {
      el.onclick = function () { openStageInspector(slug, el.dataset.stage); };
      el.onkeydown = function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openStageInspector(slug, el.dataset.stage); }
      };
    });
    var ov = $("pl-openvid");
    if (ov) ov.onclick = function () { window.open("/api/media/" + encodeURIComponent(slug) + "/video", "_blank"); };
    var pub = $("pl-publish");
    if (pub) pub.onclick = function () { openPublishModal(slug); };

    // Re-run split-button: main = from start; caret opens the "from <stage>" menu.
    var rr = $("pl-rerun");
    if (rr) rr.onclick = function () { doRerun(slug, null); };
    var rrc = $("pl-rerun-caret"), rrm = $("pl-rerun-menu");
    if (rrc && rrm) {
      rrc.onclick = function (e) {
        e.stopPropagation();
        var open = rrm.hasAttribute("hidden");
        if (open) { rrm.removeAttribute("hidden"); rrc.setAttribute("aria-expanded", "true"); }
        else { rrm.setAttribute("hidden", ""); rrc.setAttribute("aria-expanded", "false"); }
      };
      rrm.querySelectorAll(".ri").forEach(function (b) {
        b.onclick = function () { doRerun(slug, b.dataset.from); };
      });
      document.addEventListener("click", function closeRerun(ev) {
        if (rrm && !rrm.contains(ev.target) && ev.target !== rrc) {
          rrm.setAttribute("hidden", "");
          if (rrc) rrc.setAttribute("aria-expanded", "false");
        }
      });
    }
  }

  // POST a re-run (whole video when fromStage is null, else from that station) and
  // refresh the spine. Reuses the belt-flash + poll the trigger/retry paths use.
  async function doRerun(slug, fromStage) {
    var body = fromStage ? JSON.stringify({ from_stage: fromStage }) : "{}";
    try {
      var r = await fetch("/api/rerun/" + encodeURIComponent(slug),
        { method: "POST", headers: { "Content-Type": "application/json" }, body: body });
      var out = await r.json().catch(function () { return {}; });
      if (!r.ok) throw new Error(out.reason || out.error || ("HTTP " + r.status));
      flashSlug = slug;
      scheduleBeltRefresh();
      renderPipeline(slug);
    } catch (e) {
      alert("Couldn't re-run: " + e.message);
    }
  }

  async function openArtifact(slug, name) {
    var root = $("modal-root");
    root.innerHTML = '<div class="jmodal"><div class="box"><div class="hd"><b>' + esc(name) +
      '</b><span class="state-msg" style="padding:0">loading…</span><span class="x">✕</span></div><pre>Loading…</pre></div></div>';
    root.querySelector(".x").onclick = function () { root.innerHTML = ""; };
    root.querySelector(".jmodal").onclick = function (e) { if (e.target.classList.contains("jmodal")) root.innerHTML = ""; };
    try {
      var d = await getJSON("/api/artifact/" + encodeURIComponent(slug) + "/" + encodeURIComponent(name));
      root.querySelector(".hd .state-msg").textContent = d.valid ? "valid ✓" : "invalid";
      root.querySelector("pre").textContent = JSON.stringify(d.data, null, 2);
    } catch (e) {
      root.querySelector("pre").textContent = "Couldn't load artifact: " + e.message;
    }
  }

  // ================================================================ FLEET
  async function renderFleet() {
    var kpis = $("fl-kpis"), grid = $("fl-grid");
    loading(grid, "Loading fleet…");
    kpis.innerHTML = "";
    var d;
    try { d = await getJSON("/api/fleet"); }
    catch (e) { errState(grid, "Couldn't load fleet. " + e.message); return; }
    var s = d.summary || {};
    $("fl-sub").textContent = "// " + (s.total || 0) + " employees — select one to open their profile";
    kpis.innerHTML =
      kpi("Agents", '<div class="v">' + num(s.total, 0) + "</div>") +
      kpi("Working", '<div class="v">' + num(s.working, 0) + "</div>") +
      kpi("Idle", '<div class="v">' + num(s.idle, 0) + "</div>") +
      kpi("Non-claude", '<div class="v">' + num(s.non_claude, 0) + "</div>") +
      kpi("Holding for you", '<div class="v warn">' + num(s.holding, 0) + "</div>");
    grid.innerHTML = (d.agents || []).map(function (a) {
      var lead = a.status === "holding" ? " lead" : (a.status === "running" ? " busy" : "");
      var now = a.current
        ? '<div class="nowon" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(a.current.slug) +
          '"><span class="onstage">' + esc(a.current.stage) + "</span> " + ellipEsc(a.current.label, 30) + "</div>"
        : "";
      return '<div class="ac' + lead + '" data-go="v-agent" data-rail="fleet" data-agent="' + esc(a.name) + '">' +
        '<div class="em">' + esc(a.emoji) + "</div>" +
        '<div class="nm">' + esc(a.display) + "</div>" +
        '<div class="role">' + esc(a.role) + "</div>" +
        '<div class="st"><span class="led" style="background:' + statusColor(a.status) + '"></span>' + esc(a.detail || a.status) + "</div>" +
        now +
        '<div class="foot"><span class="pv">' + esc(a.provider) + '</span><span class="arrow">→</span></div></div>';
    }).join("") || '<div class="state-msg">No agents in the fleet.</div>';
  }

  // ================================================================ AGENT
  var fleetForSwitch = null;

  async function renderAgent(name) {
    var body = $("ag-body");
    if (!name) { emptyState(body, "Pick an agent from the Fleet."); $("ag-crumb").textContent = "Agent"; return; }
    loading(body, "Loading agent…");
    var d;
    try { d = await getJSON("/api/agents/" + encodeURIComponent(name)); }
    catch (e) { errState(body, "Couldn't load agent. " + e.message); return; }
    $("ag-crumb").textContent = (d.display || name);

    var jobs = (d.jobs || []).map(function (j) {
      return '<div class="job"><div class="top"><span class="jn">' + esc(j.name) + '</span><span class="tool">' + esc(j.tool) +
        "(" + esc(Object.keys(j.params || {}).join(", ")) + ')</span><span class="to">timeout ' + esc(j.timeout) + "s</span></div>" +
        '<div class="flow"><span class="f">' + esc(j.description || "") + "</span></div></div>";
    }).join("") || '<div class="state-msg">No structured jobs defined.</div>';

    var recent = (d.recent_jobs || []).map(function (r) {
      return '<div class="r"><div class="pj"><b>' + ellipEsc(r.project, 56) + "</b><small>" + esc(r.stage) + " · " + esc(r.status) +
        '</small></div><div class="meta">' + esc(r.updated_rel || "") + "</div></div>";
    }).join("") || '<div class="state-msg">No recent jobs.</div>';

    var soul = d.soul || {};
    var soulFiles = (soul.files || []).map(function (f) { return "<span><b>file</b> " + esc(f) + "</span>"; }).join("") || "<span>no soul bundle</span>";

    var owns = (d.owned_bands || []).map(function (b) {
      var judged = /hook|editorial|narrative|cta/.test(b);
      return '<span class="' + (judged ? "judged" : "") + '">' + esc(b.split(":").pop()) + (judged ? " ◆" : "") + "</span>";
    }).join("") || '<span style="color:var(--mut)">no owned rubric properties</span>';

    var persona = soul.identity
      ? '<div class="persona"><div class="q">"' + esc(soul.identity) + '"</div>' + (soul.voice ? '<span style="color:var(--mut);font-size:13px;font-family:Inter">' + esc(soul.voice) + "</span>" : "") + "</div>"
      : '<div class="state-msg">No persona bundle for this agent.</div>';

    body.innerHTML =
      '<div class="phead"><div class="ava">' + esc(d.emoji) + "</div><div>" +
      '<div class="nmh">' + esc(d.display) + ' <span class="switch" id="ag-switch">▾ switch agent</span></div>' +
      '<div class="role"><span class="stled" style="background:' + statusColor(d.status) + '"></span>' + esc(d.status) +
      " &nbsp;·&nbsp; <b>" + esc(d.blurb || "") + '</b> &nbsp; <span class="prov">brain: ' + esc(d.provider) + "</span>" +
      (d.current ? ' <span class="nowchip nav-hint" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(d.current.slug) +
        '">▶ on ' + esc(d.current.stage) + " · " + ellipEsc(d.current.label, 34) + "</span>" : "") +
      "</div></div>" +
      '<div class="acts"><button class="btn" data-go="v-fleet" data-rail="fleet">Back to fleet</button>' +
      '<button class="btn primary" data-go="v-quality" data-rail="quality">View quality loop</button></div></div>' +
      '<div id="ag-switchmenu"></div>' +
      '<div class="kpis k6">' +
        kpi("Jobs run", '<div class="v">' + num(d.jobs_run, 0) + "</div>") +
        kpi("Role", '<div class="v" style="font-size:14px">' + esc(d.role) + "</div>") +
        kpi("Brain", '<div class="v" style="font-size:16px">' + esc(d.provider) + "</div>") +
        kpi("Model", '<div class="v" style="font-size:13px">' + esc(d.model || "—") + "</div>") +
        kpi("Owns props", '<div class="v">' + (d.owned_bands || []).length + " <small>rubric</small></div>") +
        kpi("Pipeline", '<div class="v" style="font-size:15px">' + (d.is_stage ? "stage" : "intake") + "</div>") +
      "</div>" +
      '<div class="cols"><div>' +
      '<div class="card"><h3>Identity <span class="r">soul/ bundle</span></h3>' + persona +
      '<div class="soulfiles">' + soulFiles + "</div></div>" +
      '<div class="card"><h3>Jobs <span class="r">JOB — structured output</span></h3>' + jobs + "</div>" +
      '<div class="card rlog"><h3>Recent jobs <span class="r">across projects</span></h3>' + recent + "</div>" +
      "</div><div>" +
      '<div class="card"><h3>Brain <span class="r">llm seam</span></h3><div class="seam">' +
        seamOpt("claude", d.provider) + seamOpt("gemini", d.provider) + seamOpt("deepseek", d.provider) +
        '</div><div class="brainnote">Runs on <b>' + esc(d.provider) + "</b> via the configured seam (" + esc(d.switch || "—") + ").</div></div>" +
      '<div class="card train"><h3 style="color:#bcd1a3">Self-improvement <span class="r" style="color:var(--done)">soft-tier only</span></h3>' +
      '<div class="lbl">OWNS RUBRIC PROPERTIES</div><div class="owns">' + owns + "</div>" +
      '<div class="lock">🔒 The success bar and rubric belong to the <b>CEO</b>. This agent can tune its prompt, persona, and playbook — but <b>cannot edit its own eval, the contracts, or the gates</b>.</div></div>' +
      "</div></div>";

    // switch-agent affordance: lazy-load fleet list
    var sw = $("ag-switch");
    if (sw) sw.onclick = function () { toggleSwitchMenu(); };
  }

  function seamOpt(name, active) {
    return '<span class="opt' + (name === active ? " on" : "") + '">' + esc(name) + "</span>";
  }

  async function toggleSwitchMenu() {
    var holder = $("ag-switchmenu");
    if (holder.innerHTML) { holder.innerHTML = ""; return; }
    holder.innerHTML = '<div class="card"><div class="state-msg">loading agents…</div></div>';
    if (!fleetForSwitch) {
      try { fleetForSwitch = (await getJSON("/api/fleet")).agents; }
      catch (e) { holder.innerHTML = '<div class="card"><div class="state-msg err">Couldn\'t load agent list.</div></div>'; return; }
    }
    holder.innerHTML = '<div class="card"><h3>Switch agent</h3><div class="switchmenu">' +
      fleetForSwitch.map(function (a) {
        return '<div class="opt" data-go="v-agent" data-rail="fleet" data-agent="' + esc(a.name) + '">' +
          esc(a.emoji) + " " + esc(a.display) + " — " + esc(a.role) + "</div>";
      }).join("") + "</div></div>";
  }

  // ================================================================ QUALITY
  async function renderQuality() {
    var kpis = $("ql-kpis"), body = $("ql-body");
    loading(body, "Loading quality standard…");
    kpis.innerHTML = "";
    var d;
    try { d = await getJSON("/api/quality"); }
    catch (e) { errState(body, "Couldn't load quality. " + e.message); return; }

    var rub = d.rubric || {};
    var dims = rub.global_dimensions || {};
    var weights = rub.global_weights || {};
    $("ql-sub").textContent = "// rubric " + (d.rubric_version || "") + " · " + (d.scored_count || 0) + " scored renders";

    kpis.innerHTML =
      kpi("Latest quality", d.available && d.latest ? '<div class="v ok">' + esc(scoreOf(d.latest)) + " <small>in band</small></div>" : '<div class="v">— <small>no score</small></div>') +
      kpi("Scored renders", '<div class="v">' + num(d.scored_count, 0) + "</div>") +
      kpi("Dimensions", '<div class="v">' + Object.keys(dims).length + "</div>") +
      kpi("Rubric props", '<div class="v">' + (rub.bands ? Object.keys(rub.bands).length : 0) + "</div>") +
      kpi("Owner", '<div class="v" style="font-size:15px">' + esc(rub.owner || "CEO") + "</div>") +
      kpi("Frozen", '<div class="v ' + (rub.frozen ? "ok" : "warn") + '" style="font-size:15px">' + (rub.frozen ? "yes" : "no") + "</div>");

    // scorecard OR empty state — but always show the rubric standard
    var scorecard;
    if (d.available && d.latest && d.latest.scorecard) {
      scorecard = '<div class="card"><h3>Scorecard <span class="r">value vs CEO band</span></h3>' + meters(d.latest.scorecard) + "</div>";
    } else {
      scorecard = '<div class="card"><h3>Scorecard</h3>' +
        '<div class="pend"><span class="htag">NO SCORE YET</span><div class="t">No scorecard yet — the eval layer hasn\'t scored a render. ' +
        'The standard below is live; scores appear here once a render is inspected.</div></div></div>';
    }

    // the standard: render dimensions + weights from rubric (always available)
    var dimRows = Object.keys(dims).map(function (k) {
      var dm = dims[k];
      var w = weights[k];
      return '<div class="dim"><div class="top"><span class="nm">' + esc(dm.name) + '</span>' +
        '<span class="tag">' + esc(k) + '</span><span class="val">weight <b>' + esc(w !== undefined ? w : "—") + "</b></span></div>" +
        '<div class="note">' + esc(dm.captures || "") + "</div></div>";
    }).join("") || '<div class="state-msg">Rubric has no dimensions.</div>';

    var stdCard = '<div class="card std"><h3>The standard</h3>' +
      '<p>CEO-owned rubric <b>' + esc(d.rubric_version || "") + '</b>. ' + esc(rub.note ? ellip(rub.note, 220) : "") + "</p>" +
      '<div class="lock">🔒 Bands &amp; weights are <b>frozen and CEO-owned</b>. The loop reads the rubric — it can <b>never write it</b>.</div></div>';

    // loop ledger
    var ll = d.loop_ledger || {};
    var ledger;
    if (ll.available && (ll.rows || []).length) {
      ledger = '<div class="card led"><h3>Loop ledger <span class="r">append-only</span></h3>' +
        ll.rows.slice(0, 12).map(function (r) {
          return '<div class="e"><div class="h"><span class="who"><b>' + esc(r.change_id) + '</b></span><span class="vd kept">' + esc(r.rows) + ' rows</span></div>' +
            '<div class="d">change applied · ' + esc(relTime(r.ts)) + "</div></div>";
        }).join("") + "</div>";
    } else {
      ledger = '<div class="card led"><h3>Loop ledger</h3><div class="state-msg">No loop runs recorded yet.</div></div>';
    }

    var trend = (d.trend || []).length
      ? '<div class="card"><h3>Quality trend <span class="r">recent renders</span></h3><div class="trend">' +
        d.trend.map(function (t, i, arr) {
          var h = Math.max(8, Math.round((Number(t.quality_score) || 0) * 100));
          var cur = i === arr.length - 1 ? " cur" : "";
          return '<div class="col"><div class="bar' + cur + '" style="height:' + h + '%"><span class="vv">' + esc(t.quality_score) + '</span></div><span class="xl">' + esc(ellip(t.label, 10)) + "</span></div>";
        }).join("") + "</div></div>"
      : '<div class="card"><h3>Quality trend</h3><div class="state-msg">No scored renders to chart yet.</div></div>';

    body.innerHTML =
      '<div class="cols" style="grid-template-columns:1fr 330px"><div>' + scorecard +
      '<div class="card"><h3>The rubric standard <span class="r">' + Object.keys(dims).length + ' dimensions</span></h3>' + dimRows + "</div>" +
      "</div><div>" + stdCard + ledger + "</div></div>" +
      '<div class="cols" style="grid-template-columns:1fr 1fr">' + trend + ledger + "</div>";
  }

  function scoreOf(latest) {
    var sc = latest.scorecard || latest;
    return sc.overall !== undefined ? sc.overall : (sc.quality_score !== undefined ? sc.quality_score : "—");
  }

  // ================================================================ GATE
  async function renderGate(slug) {
    var main = $("gt-main");
    if (!slug) { emptyState(main, "No gate selected. Open one from Projects or Overview."); return; }
    loading(main, "Loading gate…");
    var d;
    try { d = await getJSON("/api/gate/" + encodeURIComponent(slug)); }
    catch (e) { errState(main, "Couldn't load gate. " + e.message); return; }

    if (d.kind === "none") {
      main.innerHTML =
        '<div class="crumb"><a data-go="v-projects" data-rail="projects">Projects</a> / <b>Gate</b></div>' +
        '<div class="gh"><div class="ico">⚑</div><div><h1>No open gate</h1><div class="pj">' + ellipEsc(d.label || slug, 70) + "</div></div></div>" +
        '<div class="state-msg">This project has no gate awaiting a decision (status: ' + esc(d.status) + ").</div>";
      return;
    }
    if (d.kind === "final_render") return renderFinalGate(slug, d);
    return renderFactGate(slug, d);
  }

  function renderFactGate(slug, d) {
    var main = $("gt-main");
    var sm = d.summary || {};
    var flagged = d.flagged || [];
    var verified = d.verified_claims || [];
    var hard = d.hard_block;
    var approvable = d.approvable;

    var verdictBadge = hard ? "⛔ BLOCK — ROUTED BACK"
      : (flagged.length ? ("⚑ REVIEW — " + flagged.length + " FLAGGED") : "✓ PASS");

    var stake = hard
      ? "Sage raised a <b>hard fact-check block</b>. This <b>cannot be approved away</b> — the spine routes the script back to Marlow until it's fixed."
      : (flagged.length
        ? "Sage flagged <b>" + flagged.length + " claim" + (flagged.length > 1 ? "s" : "") + "</b>. Approve to send the script on, or return it to Marlow. <b>Nothing renders until you decide.</b>"
        : "Sage verified the script with no flags. You can approve it through.");

    var flagsHtml = flagged.map(function (c) {
      var srcs = (c.sources || []).length ? ((c.sources || []).length + " source(s)") : "no primary source";
      return '<div class="flag"><div class="ch"><span class="cno">scene ' + esc(c.scene_no) + '</span><span class="ct">"' + esc(c.claim_text) + '"</span></div>' +
        '<div class="st"><span><span class="k">status</span> <span class="vf">' + esc((c.status || "flagged").toUpperCase()) + '</span></span></div>' +
        (c.note ? '<div class="find"><span class="lab">SAGE\'S FINDING</span>' + esc(c.note) + "</div>" : "") +
        '<div class="src">source: <span class="rel">' + esc(srcs) + "</span></div>" +
        '<label class="inc"><input type="checkbox" checked> Include in send-back to Marlow</label></div>';
    }).join("") || '<div class="state-msg">No flagged claims.</div>';

    var verifiedHtml = verified.slice(0, 8).map(function (v) {
      return '<div class="vrow"><span class="vk">✓</span>' + esc(ellip(v.claim_text, 120)) +
        '<span class="vs">' + esc((v.sources || 0) + " src · scene " + (v.scene_no || "—")) + "</span></div>";
    }).join("");
    var verifiedExtra = verified.length > 8 ? '<div class="vrow" style="color:var(--mut)"><span class="vk">✓</span>+ ' + (verified.length - 8) + " more verified claims</div>" : "";

    // decision column — approve button gated on approvable && !hard_block
    var approveBtn;
    if (approvable && !hard) {
      approveBtn = '<button class="bigbtn primary" id="gt-approve" data-gate="factcheck" data-slug="' + esc(slug) +
        '">Approve fact-check<small>script continues → 🎨 Iris (style)</small></button>';
    } else {
      approveBtn = '<button class="bigbtn" disabled style="opacity:.55;cursor:not-allowed">Can\'t approve — routed back<small>' +
        (hard ? "hard block · the spine refuses approval" : "not approvable in this state") + "</small></button>";
    }

    main.innerHTML =
      '<div class="crumb"><a data-go="v-projects" data-rail="projects">Projects</a> / ' +
      '<a data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(slug) + '">' + ellipEsc(d.label || slug, 40) + "</a> / <b>Fact-check gate</b></div>" +
      '<div class="gh"><div class="ico">⚑</div><div><h1>Fact-check gate</h1><div class="pj">📚 Sage checked the script · ' +
      esc((sm.verified || 0) + (sm.flagged || 0) + (sm.unverifiable || 0)) + ' claims</div></div><span class="verdict">' + esc(verdictBadge) + "</span></div>" +
      '<div class="stake">' + stake + "</div>" +
      '<div class="summ"><span class="chip"><b>' + esc((sm.verified || 0) + (sm.flagged || 0) + (sm.unverifiable || 0)) + '</b> claims</span>' +
      '<span class="chip v"><b>' + esc(sm.verified || 0) + '</b> verified</span>' +
      '<span class="chip f"><b>' + esc(sm.flagged || 0) + '</b> flagged</span>' +
      '<span class="chip b"><b>' + esc(sm.unverifiable || 0) + '</b> unverifiable</span></div>' +
      '<div class="cols"><div>' +
      '<div class="sec">Needs your judgment <span class="r">' + flagged.length + " flagged</span></div>" + flagsHtml +
      (verifiedHtml ? '<div class="sec" style="margin-top:22px">Verified — no action needed <span class="r">' + verified.length + ' claims</span></div><div class="vcard">' + verifiedHtml + verifiedExtra + "</div>" : "") +
      "</div><div>" +
      '<div class="decision"><h3>Your decision</h3>' +
      '<div class="vsum">verdict: ' + esc((d.verdict || "").toUpperCase()) + " · " + esc(sm.verified || 0) + " verified · " + esc(sm.flagged || 0) + " flagged · " + esc(sm.unverifiable || 0) + " unverifiable</div>" +
      (approvable && !hard ? '<label class="ack"><input type="checkbox" id="gt-ack"> I\'ve read the flagged claims and accept proceeding.</label>' : "") +
      approveBtn +
      '<button class="bigbtn send" data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(slug) + '">Send back to Marlow<small>he revises → Sage re-checks → gate re-opens</small></button>' +
      '<div class="gt-guide-kill">' +
      '<textarea id="gt-guide-text" class="gt-guide-textarea" placeholder="Instructions for Atlas (re-run guided)…" rows="3"></textarea>' +
      '<div class="gt-gk-btns">' +
      '<button class="bigbtn primary" id="gt-guide" disabled>Guide &amp; re-run<small>Atlas re-runs with your instructions</small></button>' +
      '<button class="bigbtn danger" id="gt-kill">Kill video<small>stop Atlas — no further auto-retries</small></button>' +
      '</div></div>' +
      '<div id="gt-result"></div>' +
      '<div class="rule">🔒 These are <b>flags</b> — your call. A fact-check <b>block</b> can <b>never be approved away</b> and always routes back until fixed.</div>' +
      "</div></div>" +
      (d.fix_history && d.fix_history.length
        ? '<div class="gt-fix-history"><div class="sec">Atlas auto-fix attempts <span class="r">' + d.fix_history.length + "</span></div>" +
          d.fix_history.map(function (a) {
            var ids = (a.flagged_before || []).map(function (c) { return esc(c.claim_id || c.claim_text || "?"); }).join(", ");
            return '<div class="gt-fix-attempt"><span class="gt-fix-n">Attempt ' + esc(a.n) + "</span>" +
              '<span class="gt-fix-instr">' + esc(a.instructions || "—") + "</span>" +
              (ids ? '<span class="gt-fix-claims">flagged before: ' + ids + "</span>" : "") +
              "</div>";
          }).join("")
          + "</div>"
        : "") +
      "</div>";

    wireApprove(slug);
    wireGuideKill(slug);
  }

  function renderFinalGate(slug, d) {
    var main = $("gt-main");
    var det = d.details || d.preview || {};
    var approvable = d.approvable, hard = d.hard_block;
    var approveBtn = (approvable && !hard)
      ? '<button class="bigbtn primary" id="gt-approve" data-gate="final_render" data-slug="' + esc(slug) + '">Approve final render<small>kicks off the render</small></button>'
      : '<button class="bigbtn" disabled style="opacity:.55;cursor:not-allowed">Can\'t approve<small>not approvable in this state</small></button>';

    main.innerHTML =
      '<div class="crumb"><a data-go="v-projects" data-rail="projects">Projects</a> / ' +
      '<a data-go="v-pipeline" data-rail="pipeline" data-slug="' + esc(slug) + '">' + ellipEsc(d.label || slug, 40) + "</a> / <b>Final-render gate</b></div>" +
      '<div class="gh"><div class="ico">🎞</div><div><h1>Final-render gate</h1><div class="pj">' + ellipEsc(d.label || slug, 70) + "</div></div></div>" +
      '<div class="stake">Last gate before the spine renders the video. Review the plan and approve to render.</div>' +
      '<div class="cols"><div>' +
      '<div class="card"><h3>Render plan</h3><div class="summ" style="flex-direction:column;align-items:flex-start;gap:6px">' +
      (det.plan ? '<span class="chip">' + esc(det.plan) + "</span>" : '<span class="state-msg">No plan detail.</span>') +
      (det.scenes ? '<span class="chip"><b>' + esc(det.scenes) + "</b> scenes</span>" : "") +
      (det.est_runtime_sec ? '<span class="chip"><b>' + esc(Math.round(det.est_runtime_sec)) + "</b>s est</span>" : "") +
      "</div></div>" +
      (det.draft_renders ? '<div class="card"><h3>Draft renders</h3><div class="state-msg">' + esc((det.draft_renders || []).length) + " draft(s)</div></div>" : "") +
      "</div><div>" +
      '<div class="decision"><h3>Your decision</h3><div class="vsum">final-render gate</div>' + approveBtn +
      '<div id="gt-result"></div>' +
      '<div class="rule">🔒 The render only starts once you approve.</div></div></div></div>';

    wireApprove(slug);
  }

  function wireApprove(slug) {
    var btn = $("gt-approve");
    if (!btn) return;
    btn.onclick = async function () {
      var ack = $("gt-ack");
      if (ack && !ack.checked) { $("gt-result").innerHTML = '<div class="state-msg err">Tick the acknowledgement first.</div>'; return; }
      var gate = btn.dataset.gate;
      btn.disabled = true;
      $("gt-result").innerHTML = '<div class="state-msg">Submitting approval…</div>';
      try {
        var r = await fetch("/api/gate/" + encodeURIComponent(slug) + "/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ gate: gate })
        });
        var out = await r.json().catch(function () { return {}; });
        if (!r.ok) throw new Error(out.error || ("HTTP " + r.status));
        $("gt-result").innerHTML = '<div class="state-msg" style="color:var(--done)">✓ ' + esc(out.status || "approved") + "</div>";
      } catch (e) {
        btn.disabled = false;
        $("gt-result").innerHTML = '<div class="state-msg err">Approval failed: ' + esc(e.message) + "</div>";
      }
    };
  }

  function wireGuideKill(slug) {
    var guideText = $("gt-guide-text");
    var guideBtn  = $("gt-guide");
    var killBtn   = $("gt-kill");
    if (!guideText || !guideBtn || !killBtn) return;
    guideText.oninput = function () {
      guideBtn.disabled = !guideText.value.trim();
    };
    guideBtn.onclick = async function () {
      var instr = guideText.value.trim();
      if (!instr) return;
      guideBtn.disabled = true;
      killBtn.disabled = true;
      $("gt-result").innerHTML = '<div class="state-msg">Submitting guidance…</div>';
      try {
        var r = await fetch("/api/gate/" + encodeURIComponent(slug) + "/guide", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instructions: instr })
        });
        var out = await r.json().catch(function () { return {}; });
        if (!r.ok) throw new Error(out.error || ("HTTP " + r.status));
        $("gt-result").innerHTML = '<div class="state-msg" style="color:var(--done)">✓ re-running (guided)</div>';
      } catch (e) {
        guideBtn.disabled = false;
        killBtn.disabled = false;
        $("gt-result").innerHTML = '<div class="state-msg err">Guide failed: ' + esc(e.message) + "</div>";
      }
    };
    killBtn.onclick = async function () {
      guideBtn.disabled = true;
      killBtn.disabled = true;
      $("gt-result").innerHTML = '<div class="state-msg">Killing video…</div>';
      try {
        var r = await fetch("/api/gate/" + encodeURIComponent(slug) + "/kill", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "" })
        });
        var out = await r.json().catch(function () { return {}; });
        if (!r.ok) throw new Error(out.error || ("HTTP " + r.status));
        $("gt-result").innerHTML = '<div class="state-msg" style="color:var(--gate)">✓ killed</div>';
      } catch (e) {
        guideBtn.disabled = false;
        killBtn.disabled = false;
        $("gt-result").innerHTML = '<div class="state-msg err">Kill failed: ' + esc(e.message) + "</div>";
      }
    };
  }

  // ---------------------------------------------------------------- small fns
  function kpi(label, valHtml, extraCls, attrs) {
    return '<div class="kpi ' + (extraCls || "") + '" ' + (attrs || "") + '><div class="l">' + esc(label) + "</div>" + valHtml + "</div>";
  }
  function tab(label, n, key) {
    return '<span class="tab' + (key === projTab ? " on" : "") + '" data-tab="' + key + '">' + esc(label) + " <b>" + num(n, 0) + "</b></span>";
  }
  function statusColor(s) {
    if (s === "holding" || s === "blocked") return "var(--gate)";
    if (s === "working" || s === "running" || s === "done") return "var(--done)";
    return "var(--wait)";
  }
  function prettyGate(g) {
    if (g === "factcheck") return "Fact-check gate";
    if (g === "final_render") return "Final-render gate";
    return (g || "Gate");
  }
  function fmtSize(b) {
    if (!b && b !== 0) return "—";
    if (b < 1024) return b + " B";
    if (b < 1048576) return Math.round(b / 1024) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }
  function ellip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function ellipEsc(s, n) { return esc(ellip(s, n)); }
  function relTime(ts) {
    if (!ts) return "";
    var diff = (Date.now() / 1000) - ts;
    if (diff < 3600) return Math.round(diff / 60) + "m";
    if (diff < 86400) return Math.round(diff / 3600) + "h";
    return Math.round(diff / 86400) + "d";
  }

  // ================================================================ THE BELT
  var STAGE_KEYS = ["research", "script", "factcheck", "style", "storyboard",
                    "assets", "narration", "compose", "audiomix", "render"];
  var beltTimer = null, flashSlug = null, es = null;

  function scheduleBeltRefresh() {
    if (beltTimer) return;
    beltTimer = setTimeout(function () { beltTimer = null; renderBelt(); }, 250);
  }

  async function renderBelt() {
    var belt = $("ov-belt"), tray = $("ov-needs");
    if (!belt) return;
    var d;
    try { d = await getJSON("/api/belt"); }
    catch (e) { errState(belt, "Couldn't load the belt. " + e.message); return; }
    renderNeedsTray(tray, d);

    var live = d.live || {}, occ = d.occupancy || {}, counts = d.counts || {};
    var strip = (d.stations || []).map(function (s) {
      var busy = occ[s.key];
      return '<div class="station' + (busy ? " busy" : "") + '" title="' + esc(s.key) +
        (busy ? " — " + esc(busy.label) : "") + '">' +
        (busy ? '<span class="live-dot"></span>' : "") +
        '<div class="em">' + esc(s.agent ? s.agent.emoji : "•") + "</div>" +
        '<div class="nm">' + esc(s.key) + "</div></div>";
    }).join("");

    var vids = d.videos || [];
    var rows = vids.length
      ? '<div class="spine">' + vids.map(spineRow).join("") + "</div>"
      : '<div class="belt-empty"><b>The belt is empty.</b><br>Drop a topic to start a ' +
        "production — it'll appear here and flow down the line.</div>";

    belt.innerHTML =
      '<div class="belt-head"><h3>Production belt</h3><div class="flow">' +
      '<span class="run">running <b>' + (counts.running || 0) + "</b></span>" +
      '<span class="gate">awaiting you <b>' + (counts.blocked || 0) + "</b></span>" +
      '<span class="fail">failed <b>' + (counts.failed || 0) + "</b></span>" +
      (counts.interrupted ? '<span class="fail">interrupted <b>' + counts.interrupted + "</b></span>" : "") +
      '<span class="done">done <b>' + (counts.done || 0) + "</b></span>" +
      "<span>in flight <b>" + ((live.running || []).length) + "</b>/" +
      (live.max_in_flight || "—") + "</span></div></div>" +
      '<div class="stations">' + strip + "</div>" + rows;

    belt.querySelectorAll(".spine-row").forEach(function (r) {
      r.onclick = function (e) {
        if (e.target.closest(".row-act")) return;
        current.slug = r.dataset.slug; go("v-pipeline", "pipeline");
      };
    });
    belt.querySelectorAll(".row-act.danger").forEach(function (b) {
      b.onclick = function (e) { e.stopPropagation(); cancelVideo(b.dataset.slug); };
    });
    if (flashSlug) {
      var fr = belt.querySelector('.spine-row[data-slug="' + cssEsc(flashSlug) + '"]');
      if (fr) fr.classList.add("flash");
      flashSlug = null;
    }
  }

  function spineRow(v) {
    var track = STAGE_KEYS.map(function (k) {
      var st = (v.stages || {})[k] || "pending";
      var cls = ["done", "running", "failed", "blocked"].indexOf(st) >= 0 ? st : "pending";
      return '<div class="seg ' + cls + '" data-k="' + esc(k) + '"></div>';
    }).join("");
    var bs = v.belt_state || "queued";
    var act = (bs === "running" || bs === "queued")
      ? '<button class="row-act danger" data-slug="' + esc(v.slug) + '">cancel</button>' : "";
    var atlasLine = v.atlas_activity
      ? '<div class="atlas-line">🤖 ' + esc(v.atlas_activity.text) + "</div>" : "";
    return '<div class="spine-row s-' + esc(bs) + '" data-slug="' + esc(v.slug) + '">' +
      '<div class="lbl"><div class="t">' + ellipEsc(v.label || v.topic || v.slug, 60) +
      '</div><div class="s">' + esc(v.station || "—") + " · " + esc(v.updated_rel || "") +
      "</div>" + atlasLine + "</div>" + '<div class="track">' + track + "</div>" +
      '<div class="rgt"><span class="pill-state ' + esc(bs) + '">' + esc(bs) + "</span>" +
      act + "</div></div>";
  }

  function renderNeedsTray(tray, d) {
    if (!tray) return;
    var items = (d.videos || []).filter(function (v) {
      return v.belt_state === "blocked" || v.belt_state === "failed";
    });
    if (!items.length) {
      tray.className = "tray";
      tray.innerHTML = "<h3>Needs you</h3><div class=\"ok\"><span class=\"dot\"></span>" +
        "Nothing needs you — the belt is flowing.</div>";
      return;
    }
    tray.className = "tray has-items";
    var digestHeader = items.length > 1
      ? '<div class="tray-digest">⚑ ' + items.length + " videos need you</div>" : "";
    tray.innerHTML = '<h3>Needs you <span class="badge-n">' + items.length + "</span></h3>" +
      digestHeader +
      items.map(function (v) {
        var fail = v.belt_state === "failed";
        return '<div class="tray-item ' + (fail ? "fail" : "gate") + '" data-slug="' +
          esc(v.slug) + '" data-kind="' + (fail ? "fail" : "gate") + '">' +
          '<div class="ic">' + (fail ? "⛔" : "⚑") + "</div>" +
          '<div class="tx"><div class="t">' + ellipEsc(v.label || v.slug, 64) + "</div>" +
          '<div class="s">' + (fail ? "failed at " + esc(v.station || "?")
            : "awaiting your sign-off · " + esc(v.gate || "gate") + " gate") + "</div></div>" +
          '<div class="go">' + (fail ? "review →" : "open gate →") + "</div></div>";
      }).join("");
    tray.querySelectorAll(".tray-item").forEach(function (it) {
      it.onclick = function () {
        current.slug = it.dataset.slug;
        if (it.dataset.kind === "gate") go("v-gate", "projects");
        else go("v-pipeline", "pipeline");
      };
    });
  }

  async function cancelVideo(slug) {
    try {
      await fetch("/api/cancel/" + encodeURIComponent(slug),
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (e) { /* surfaced on the next belt refresh */ }
    scheduleBeltRefresh();
  }

  // ================================================================ ACTIVITY FEED
  // The live audit trail (spec §4/§10): every belt event, newest-first, tagged with the
  // initiator PLANE (ceo / dispatcher / chat) — because "who set it off" is the property
  // an audit checks. Backfilled from /api/activity, then live-tailed by the SSE stream.
  var ACT_KINDS = ["triggered", "progress", "decision", "fixing", "approving", "retry",
                   "rerun", "rerunning", "interrupted", "blocked", "gate_approved",
                   "failed", "killed", "cancel_requested", "cancelled", "done"];
  var activityFilter = { kind: null, initiator: null };
  var actTimer = null;

  function scheduleActivityRefresh() {
    if (actTimer) return;
    actTimer = setTimeout(function () {
      actTimer = null;
      var active = document.querySelector(".view.active");
      if (active && active.id === "v-activity") drawActivityFeed();
    }, 250);
  }

  async function renderActivity() {
    buildActivityFilters($("ac-filters"));
    await drawActivityFeed();
  }

  function buildActivityFilters(el) {
    if (!el) return;
    var kc = '<span class="tab' + (!activityFilter.kind ? " on" : "") + '" data-k="">All events</span>' +
      ACT_KINDS.map(function (k) {
        return '<span class="tab' + (activityFilter.kind === k ? " on" : "") + '" data-k="' + k + '">' + esc(k) + "</span>";
      }).join("");
    var planes = ["ceo", "dispatcher", "chat"];
    var ic = '<span class="iflt' + (!activityFilter.initiator ? " on" : "") + '" data-i="">any</span>' +
      planes.map(function (p) {
        return '<span class="iflt' + (activityFilter.initiator === p ? " on" : "") + '" data-i="' + p + '">' + esc(p) + "</span>";
      }).join("");
    el.innerHTML = '<div class="tabs">' + kc + '</div>' +
      '<div class="ifltbar"><span class="lbl">initiated by</span>' + ic + "</div>";
    el.querySelectorAll(".tab[data-k]").forEach(function (t) {
      t.onclick = function () { activityFilter.kind = t.dataset.k || null; renderActivity(); };
    });
    el.querySelectorAll(".iflt[data-i]").forEach(function (t) {
      t.onclick = function () { activityFilter.initiator = t.dataset.i || null; renderActivity(); };
    });
  }

  async function drawActivityFeed() {
    var feed = $("ac-feed");
    if (!feed) return;
    var qs = [];
    if (activityFilter.kind) qs.push("kind=" + encodeURIComponent(activityFilter.kind));
    if (activityFilter.initiator) qs.push("initiator=" + encodeURIComponent(activityFilter.initiator));
    var d;
    try { d = await getJSON("/api/activity" + (qs.length ? "?" + qs.join("&") : "")); }
    catch (e) { errState(feed, "Couldn't load activity. " + e.message); return; }
    var rows = (d.events || []).map(activityRow).join("");
    feed.innerHTML = rows ||
      '<div class="state-msg">No events' + (activityFilter.kind || activityFilter.initiator ? " match this filter" : " yet — trigger a production and they'll stream in here") + ".</div>";
    feed.querySelectorAll(".ev-row[data-slug]").forEach(function (r) {
      r.onclick = function () { current.slug = r.dataset.slug; go("v-pipeline", "pipeline"); };
    });
  }

  function activityRow(e) {
    var slug = e.slug ? ' data-slug="' + esc(e.slug) + '"' : "";
    var plane = e.initiator || "—";
    return '<div class="ev-row' + (e.slug ? " nav-hint" : "") + '"' + slug + '>' +
      '<span class="ts">' + esc(relTime(e.ts) || "") + "</span>" +
      '<span class="kind ' + esc(e.kind) + '">' + esc(e.kind) + "</span>" +
      '<span class="plane p-' + esc(plane) + '">' + esc(plane) + "</span>" +
      '<span class="body"><b>' + ellipEsc(e.slug || "system", 38) + "</b>" +
      (e.message ? '<span class="m">' + ellipEsc(e.message, 96) + "</span>" : "") + "</span>" +
      (e.stage ? '<span class="stg">' + esc(e.stage) + "</span>" : "") + "</div>";
  }

  // ================================================================ SETTINGS (#4)
  // Niches / defaults / channels — the dashboard-owned config the launch pills read and the
  // pipeline gets PASSED at trigger time. T1 reversible write (Save; re-edit = undo).
  var settingsState = null;   // {niches[], defaults{}, channels[], quota, connection_states, length_options}

  async function renderSettings() {
    var body = $("set-body");
    loading(body, "Loading settings…");
    setSaveState("");
    try { settingsState = await getJSON("/api/settings"); }
    catch (e) { errState(body, "Couldn't load settings. " + e.message); return; }
    drawSettings();
    var sv = $("set-save"); if (sv) sv.onclick = saveSettings;
  }

  function setSaveState(msg, cls) {
    var el = $("set-state");
    if (el) el.innerHTML = msg ? '<span class="' + (cls || "") + '">' + esc(msg) + "</span>" : "";
  }

  function lengthToggle(name, val) {
    return '<div class="seg-toggle sm" data-field="' + name + '">' +
      '<button type="button" data-v="short" class="' + (val !== "long" ? "on" : "") + '">Short</button>' +
      '<button type="button" data-v="long" class="' + (val === "long" ? "on" : "") + '">Long</button></div>';
  }

  function nicheChannelOptions(sel) {
    var opts = '<option value="">— no channel —</option>';
    (settingsState.channels || []).forEach(function (c) {
      var id = c.channel_id || "";
      opts += '<option value="' + esc(id) + '"' + (id && id === sel ? " selected" : "") + '>' +
        esc(c.title || id || "untitled") + "</option>";
    });
    return opts;
  }

  function channelNicheOptions(sel) {
    var opts = '<option value="">— any niche —</option>';
    (settingsState.niches || []).forEach(function (n, i) {
      opts += '<option value="' + i + '"' + (String(i) === String(sel) ? " selected" : "") + '>' +
        esc(n.name) + "</option>";
    });
    return opts;
  }

  function drawSettings() {
    var body = $("set-body");
    var s = settingsState, q = s.quota || {};

    // --- niches ---
    var nicheRows = (s.niches || []).map(function (n, i) {
      return '<div class="srow niche" data-i="' + i + '">' +
        '<input class="f-name" type="text" value="' + esc(n.name || "") + '" placeholder="niche name, e.g. AI tools & productivity">' +
        lengthToggle("default_length", n.default_length) +
        '<select class="f-channel">' + nicheChannelOptions(n.channel_id) + "</select>" +
        '<button class="row-x" title="Remove niche" aria-label="Remove niche">✕</button></div>';
    }).join("") || '<div class="state-msg">No niches yet. Add one — it becomes a launch pill and carries its default length into the pipeline.</div>';

    // --- defaults ---
    var d = s.defaults || {};
    var defaults =
      '<div class="setrow"><label>Default target length</label>' + lengthToggle("def_length", d.target_length) + "</div>" +
      '<div class="setrow"><label>Default voice</label><input id="def-voice" type="text" value="' + esc(d.voice || "") + '" placeholder="(optional) preset voice name"></div>' +
      '<div class="setrow"><label>Default style preset</label><input id="def-style" type="text" value="' + esc(d.style_preset || "") + '" placeholder="(optional) preset name"></div>';

    // --- channels (the broadcast bay shell) ---
    var quota =
      '<div class="quota"><div class="qh"><span class="qn">' + num(q.max_uploads_per_day, 6) +
      '</span><span class="ql">uploads / day<br><b>shared across ALL channels</b></span></div>' +
      '<div class="qbreak">' + esc(q.insert_cost || 1600) + ' units / upload · ' + esc(q.daily_units || 10000) +
      ' units / day · project-wide ceiling</div>' +
      '<div class="qnote">' + esc(q.note || "") + "</div></div>";

    var chanCards = (s.channels || []).map(function (c, i) {
      var st = c.connection_status || "disconnected";
      var states = s.connection_states || ["disconnected"];
      var stateOpts = states.map(function (x) {
        return '<option value="' + esc(x) + '"' + (x === st ? " selected" : "") + ">" + esc(x) + "</option>";
      }).join("");
      return '<div class="chan" data-i="' + i + '">' +
        '<div class="chan-hd"><span class="conn ' + connClass(st) + '">' + esc(st) + "</span>" +
        '<button class="row-x" title="Remove channel" aria-label="Remove channel">✕</button></div>' +
        '<input class="c-title" type="text" value="' + esc(c.title || "") + '" placeholder="channel title">' +
        '<input class="c-id" type="text" value="' + esc(c.channel_id || "") + '" placeholder="channelId (read back from channels.list?mine=true)">' +
        '<div class="setrow"><label>Mapped niche</label><select class="c-niche">' + channelNicheOptions(c.niche_id) + "</select></div>" +
        '<div class="setrow"><label>Connection</label><select class="c-state">' + stateOpts + "</select></div>" +
        '<div class="vflags">' +
        '<label class="vf"><input type="checkbox" class="c-pv"' + (c.project_verified ? " checked" : "") + '> Cloud project sensitive-scope verified</label>' +
        '<label class="vf"><input type="checkbox" class="c-cv"' + (c.channel_phone_verified ? " checked" : "") + '> Channel phone-verified</label>' +
        "</div>" +
        '<button class="btn sm conn-btn" disabled title="OAuth connect arrives with Herald (#6)">Connect channel — arrives with Herald</button>' +
        "</div>";
    }).join("") || '<div class="state-msg">No channels yet. Add one to map a niche → channel (OAuth wiring lands with Herald).</div>';

    body.innerHTML =
      '<div class="card"><h3>Niches <span class="r">launch pills + per-niche defaults</span></h3>' +
      '<div class="srows">' + nicheRows + "</div>" +
      '<button class="btn sm add" id="add-niche">+ Add niche</button></div>' +
      '<div class="card"><h3>Defaults <span class="r">passed into the pipeline as args</span></h3>' + defaults + "</div>" +
      '<div class="card chans"><h3>Channels <span class="r">YouTube publishing shell · OAuth at #6</span></h3>' +
      quota +
      '<div class="chan-grid">' + chanCards + "</div>" +
      '<button class="btn sm add" id="add-channel">+ Add channel</button></div>';

    wireSettings();
  }

  function connClass(st) {
    if (st === "connected") return "ok";
    if (st === "needs-reconnect" || st === "expired") return "warn";
    if (st === "revoked") return "bad";
    return "off";
  }

  function wireSettings() {
    var body = $("set-body");
    // segmented length toggles (niche rows + the default)
    body.querySelectorAll(".seg-toggle[data-field]").forEach(function (tg) {
      tg.querySelectorAll("button").forEach(function (b) {
        b.onclick = function () {
          tg.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
          b.classList.add("on");
        };
      });
    });
    body.querySelectorAll(".srow.niche .row-x").forEach(function (x) {
      x.onclick = function () { syncSettingsFromDOM(); var i = +x.closest(".srow").dataset.i; settingsState.niches.splice(i, 1); drawSettings(); };
    });
    body.querySelectorAll(".chan .row-x").forEach(function (x) {
      x.onclick = function () { syncSettingsFromDOM(); var i = +x.closest(".chan").dataset.i; settingsState.channels.splice(i, 1); drawSettings(); };
    });
    var an = $("add-niche");
    if (an) an.onclick = function () { syncSettingsFromDOM(); settingsState.niches.push({ name: "", default_length: "short", channel_id: "" }); drawSettings(); };
    var ac = $("add-channel");
    if (ac) ac.onclick = function () { syncSettingsFromDOM(); settingsState.channels.push({ title: "", channel_id: "", niche_id: "", connection_status: "disconnected", project_verified: false, channel_phone_verified: false }); drawSettings(); };
  }

  function syncSettingsFromDOM() {
    var body = $("set-body");
    settingsState.niches = Array.prototype.map.call(body.querySelectorAll(".srow.niche"), function (r) {
      return {
        name: r.querySelector(".f-name").value.trim(),
        default_length: r.querySelector('.seg-toggle[data-field="default_length"] button.on').dataset.v,
        channel_id: r.querySelector(".f-channel").value,
      };
    });
    settingsState.channels = Array.prototype.map.call(body.querySelectorAll(".chan"), function (c) {
      return {
        title: c.querySelector(".c-title").value.trim(),
        channel_id: c.querySelector(".c-id").value.trim(),
        niche_id: c.querySelector(".c-niche").value,
        connection_status: c.querySelector(".c-state").value,
        project_verified: c.querySelector(".c-pv").checked,
        channel_phone_verified: c.querySelector(".c-cv").checked,
      };
    });
    var defLen = body.querySelector('.seg-toggle[data-field="def_length"] button.on');
    settingsState.defaults = {
      target_length: defLen ? defLen.dataset.v : "short",
      voice: ($("def-voice") || {}).value || "",
      style_preset: ($("def-style") || {}).value || "",
    };
  }

  async function saveSettings() {
    if (!settingsState) return;
    syncSettingsFromDOM();
    setSaveState("Saving…");
    var btn = $("set-save"); if (btn) btn.disabled = true;
    try {
      var r = await fetch("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ niches: settingsState.niches, defaults: settingsState.defaults, channels: settingsState.channels }),
      });
      var out = await r.json().catch(function () { return {}; });
      if (!r.ok) throw new Error(out.error || ("HTTP " + r.status));
      settingsState = out.public || settingsState;
      drawSettings();
      var note = (out.errors && out.errors.length) ? ("Saved — " + out.errors.join("; ")) : "Saved ✓";
      setSaveState(note, (out.errors && out.errors.length) ? "warn" : "ok");
    } catch (e) {
      setSaveState("Couldn't save: " + e.message, "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ================================================================ LAUNCH MODAL
  async function openLaunchModal() {
    var length = "short", gatesOn = true, niche = null;
    var settings = { niches: [] };
    try { settings = await getJSON("/api/settings"); } catch (e) { /* niche pills optional */ }
    var pills = (settings.niches || []).map(function (n) {
      return '<button type="button" class="pill-opt" data-niche="' + esc(n.name) +
        '" data-len="' + esc(n.default_length || "short") + '">' + esc(n.name) +
        '<span class="ch">' + esc(n.default_length || "short") + "</span></button>";
    }).join("");
    var intakeMode = ((settings.defaults || {}).intake_mode) || "pick";
    var nicheField = pills
      ? '<div class="field"><label>Niche (optional)</label><div class="pills" id="lm-niches">' + pills + "</div>" +
        '<div class="dlg-note">Pick a niche to pre-fill its default length, or let Scout find a topic for it.</div>' +
        '<div id="lm-intake" class="intake"></div></div>'
      : "";
    var body =
      nicheField +
      '<div class="field"><label>Topic</label>' +
      '<textarea id="lm-topic" rows="2" placeholder="e.g. How noise-cancelling headphones actually work"></textarea>' +
      '<div class="dlg-note">Type a specific topic to produce now.</div></div>' +
      '<div class="field"><label>Target length</label><div class="seg-toggle" id="lm-len">' +
      '<button data-v="short" class="on" type="button">Short<small>~60–90s</small></button>' +
      '<button data-v="long" type="button">Long<small>~5–8 min</small></button></div></div>' +
      '<div class="field"><label>Human gates</label>' +
      '<div class="toggle-row"><div class="tx"><div class="t">Pause at fact-check & final-render</div>' +
      '<div class="s">off = unattended run, straight through</div></div>' +
      '<button class="sw on" id="lm-gates" type="button" role="switch" aria-checked="true"></button></div></div>' +
      '<div id="lm-err"></div>';
    var box = openDialog({
      icon: "▶", title: "Generate new video",
      sub: "drops onto the belt as a reversible run", body: body,
      primary: { label: "Generate", onClick: submit }, secondaryLabel: "Cancel",
    });
    var lenWrap = box.querySelector("#lm-len");
    function setLength(v) {
      length = v;
      lenWrap.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x.dataset.v === v); });
    }
    lenWrap.querySelectorAll("button").forEach(function (b) {
      b.onclick = function () { setLength(b.dataset.v); };
    });
    var nichesWrap = box.querySelector("#lm-niches");
    if (nichesWrap) nichesWrap.querySelectorAll(".pill-opt").forEach(function (p) {
      p.onclick = function () {
        var was = p.classList.contains("on");
        nichesWrap.querySelectorAll(".pill-opt").forEach(function (x) { x.classList.remove("on"); });
        if (was) { niche = null; }                 // click again to clear
        else { p.classList.add("on"); niche = p.dataset.niche; setLength(p.dataset.len || length); }
        renderIntake();
      };
    });

    // --- niche intake (#1.5): niche → Scout find_topics → candidate cards ---
    function renderIntake() {
      var wrap = box.querySelector("#lm-intake");
      if (!wrap) return;
      if (!niche) { wrap.innerHTML = ""; return; }
      wrap.innerHTML =
        '<div class="intake-head"><span>Find a topic for <b>' + esc(niche) + "</b></span>" +
        '<div class="seg-toggle sm" id="lm-mode">' +
        '<button type="button" data-v="pick" class="' + (intakeMode !== "auto" ? "on" : "") + '">You pick</button>' +
        '<button type="button" data-v="auto" class="' + (intakeMode === "auto" ? "on" : "") + '">Auto-pick</button></div></div>' +
        '<button class="btn sm intake-find" type="button" id="lm-find">🔎 Find topics with Scout</button>' +
        '<div id="lm-cands"></div>';
      var modeWrap = wrap.querySelector("#lm-mode");
      modeWrap.querySelectorAll("button").forEach(function (b) {
        b.onclick = function () {
          modeWrap.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
          b.classList.add("on"); intakeMode = b.dataset.v;
        };
      });
      wrap.querySelector("#lm-find").onclick = findTopics;
    }

    async function findTopics() {
      var cands = box.querySelector("#lm-cands"), btn = box.querySelector("#lm-find");
      if (!niche) return;
      cands.innerHTML = '<div class="state-msg">🔎 Scout is scanning "' + esc(niche) + '" — this can take a moment…</div>';
      if (btn) btn.disabled = true;
      var d;
      try { d = await postJSON("/api/intake/topics", { niche: niche }); }
      catch (e) { cands.innerHTML = '<div class="state-msg err">Couldn\'t reach Scout. ' + esc(e.message) + "</div>"; if (btn) btn.disabled = false; return; }
      if (btn) btn.disabled = false;
      if (!d.ok || !(d.candidates || []).length) {
        cands.innerHTML = '<div class="state-msg">' + esc(d.error || "Scout found no topics for that niche. Try a broader niche, or type a topic above.") + "</div>";
        return;
      }
      cands.innerHTML = d.candidates.map(function (c) {
        return '<button type="button" class="cand" data-title="' + esc(c.title) + '">' +
          '<span class="ct">' + esc(c.title) + "</span>" +
          '<span class="cm"><span class="conf c-' + esc(String(c.confidence).toLowerCase()) + '">' + esc(c.confidence) + "</span>" +
          (c.why ? '<span class="why">' + esc(c.why) + "</span>" : "") + "</span></button>";
      }).join("");
      var cardEls = cands.querySelectorAll(".cand");
      cardEls.forEach(function (el) {
        el.onclick = function () {
          cardEls.forEach(function (x) { x.classList.remove("on"); });
          el.classList.add("on");
          var t = box.querySelector("#lm-topic"); if (t) t.value = el.dataset.title;
        };
      });
      // auto-pick: take the strongest candidate immediately (configurable, spec #1.5)
      if (intakeMode === "auto" && cardEls[0]) cardEls[0].click();
    }
    var sw = box.querySelector("#lm-gates");
    sw.onclick = function () {
      gatesOn = !gatesOn; sw.classList.toggle("on", gatesOn);
      sw.setAttribute("aria-checked", String(gatesOn));
    };
    setTimeout(function () { var t = box.querySelector("#lm-topic"); if (t) t.focus(); }, 30);

    async function submit() {
      var topic = (box.querySelector("#lm-topic").value || "").trim();
      if (!topic) {
        box.querySelector("#lm-err").innerHTML =
          '<div class="dlg-note warn">Enter a topic to produce.</div>'; return;
      }
      var btn = box.querySelector(".dlg-primary");
      if (btn) { btn.disabled = true; btn.textContent = "Starting…"; }
      try {
        var r = await fetch("/api/trigger", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic: topic, length: length, gates: gatesOn, niche: niche }),
        });
        var out = await r.json().catch(function () { return {}; });
        if (!r.ok) throw new Error(out.error || ("HTTP " + r.status));
        closeDialog();
        flashSlug = out.slug;
        renderBelt();
      } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = "Generate"; }
        box.querySelector("#lm-err").innerHTML =
          '<div class="dlg-note warn">Couldn\'t start: ' + esc(e.message) + "</div>";
      }
    }
  }
  window.openLaunchModal = openLaunchModal;

  // ================================================================ DRAWER (depth-2 inspect)
  // A right-side slide-in panel for inspect-in-context (modal-vs-panel-vs-page discipline:
  // page = full video detail, panel = inspect a stage without leaving, modal = a decision).
  var drawerPrevFocus = null;
  function openDrawer(opts) {
    var root = $("drawer-root");
    root.innerHTML =
      '<div class="dw" role="dialog" aria-modal="true" aria-label="' + esc(opts.title) + '">' +
      '<div class="dw-scrim"></div>' +
      '<div class="dw-panel"><div class="dw-hd"><div class="dw-ttl"><h2>' + esc(opts.title) + "</h2>" +
      (opts.sub ? '<div class="dw-sub">' + esc(opts.sub) + "</div>" : "") + "</div>" +
      '<button class="x" type="button" aria-label="Close">✕</button></div>' +
      '<div class="dw-body"></div></div></div>';
    var dw = root.querySelector(".dw");
    drawerPrevFocus = document.activeElement;
    root.querySelector(".x").onclick = closeDrawer;
    root.querySelector(".dw-scrim").onclick = closeDrawer;
    dw._keyh = function (e) {
      if (e.key === "Escape") { e.preventDefault(); closeDrawer(); return; }
      if (e.key === "Tab") trapFocus(dw, e);
    };
    document.addEventListener("keydown", dw._keyh);
    requestAnimationFrame(function () { dw.classList.add("in"); });
    return dw.querySelector(".dw-panel");
  }
  function closeDrawer() {
    var root = $("drawer-root"), dw = root.querySelector(".dw");
    if (dw && dw._keyh) document.removeEventListener("keydown", dw._keyh);
    root.innerHTML = "";
    if (drawerPrevFocus && drawerPrevFocus.focus) { try { drawerPrevFocus.focus(); } catch (e) {} }
    drawerPrevFocus = null;
  }
  window.closeDrawer = closeDrawer;

  // ================================================================ STAGE/AGENT INSPECTOR
  async function openStageInspector(slug, key) {
    var panel = openDrawer({ title: "Stage inspector", sub: key });
    var body = panel.querySelector(".dw-body");
    loading(body, "Loading stage…");
    var d;
    try { d = await getJSON("/api/projects/" + encodeURIComponent(slug) + "/stage/" + encodeURIComponent(key)); }
    catch (e) { errState(body, "Couldn't load this stage. " + e.message); return; }
    renderStageInspector(panel, slug, d);
  }
  window.openStageInspector = openStageInspector;

  function artChip(slug, name, exists) {
    var miss = exists ? "" : " miss";
    var open = exists ? ' data-art="' + esc(name) + '"' : "";
    return '<span class="chipf' + miss + '"' + open + '>' + esc(name) +
      (exists ? "" : ' <i>missing</i>') + "</span>";
  }

  function renderStageInspector(panel, slug, d) {
    var body = panel.querySelector(".dw-body");
    var ag = d.agent || {}, pv = d.provider || {};
    var bs = d.belt_state || "queued";

    // reads → writes flow
    var inputs = (d.inputs || []).length
      ? (d.inputs || []).map(function (i) { return artChip(slug, i.name, i.exists); }).join("")
      : '<span class="io-none">intake stage — no upstream inputs</span>';

    var writes;
    if (d.output) {
      var o = d.output, stamp;
      if (o.valid === true) stamp = '<div class="stamp ok">contract valid ✓<small>' + esc(o.contract || "") + "</small></div>";
      else if (o.valid === false) {
        var errs = (o.errors || []).map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("");
        stamp = '<div class="stamp bad">contract invalid ✗<small>' + esc(o.contract || "") + "</small></div>" +
          (errs ? '<ul class="slip">' + errs + "</ul>" : "");
      } else stamp = '<div class="stamp none">binary artifact · no contract</div>';
      writes = artChip(slug, o.artifact, o.exists) + stamp;
    } else {
      writes = '<span class="io-none">no artifact written yet</span>';
    }

    // failure surface — honest vocab: UNDERSTAND (what + what it means) + RETRY + CANCEL
    var failBlock = "";
    if (d.failure) {
      var det = d.failure.kind === "deterministic";
      var means = det
        ? "Re-running repeats the same result — the upstream artifact or contract needs a fix first, so retry won't help. Cancel it, fix upstream, and run a fresh production."
        : "A transient hiccup (network or runtime). Retrying the stage may clear it.";
      var acts = "";
      if (d.actions && d.actions.can_retry) acts += '<button class="dw-btn primary" data-act="retry">Retry stage</button>';
      if (d.actions && d.actions.can_cancel) acts += '<button class="dw-btn danger" data-act="cancel">Cancel video</button>';
      acts += '<button class="dw-btn" data-act="close">Close</button>';
      failBlock =
        '<div class="insp-fail ' + (det ? "det" : "tr") + '">' +
        '<div class="fk">' + (det ? "deterministic failure" : "transient failure") + "</div>" +
        '<div class="why"><span class="lab">What happened</span>' + esc(d.failure.reason || "stage failed") + "</div>" +
        '<div class="means"><span class="lab">What this means</span>' + esc(means) + "</div>" +
        '<div class="insp-acts">' + acts + "</div>" +
        '<div id="insp-result"></div></div>';
    }

    body.innerHTML =
      '<div class="insp-head"><div class="ava">' + esc(ag.emoji || "•") + "</div><div>" +
      '<div class="nm">' + esc(ag.display || ag.name || "—") +
      ' <span class="pill-state ' + esc(bs) + '">' + esc(d.status || "") + "</span></div>" +
      '<div class="role">' + esc(d.stage_label || "") + " · station <b>" + esc(d.key) + "</b></div>" +
      '<div class="brain">brain: ' + esc(pv.provider || "—") + " · " + esc(pv.model || "") + "</div></div>" +
      (d.updated_rel ? '<span class="when">' + esc(d.updated_rel) + "</span>" : "") + "</div>" +
      (d.detail ? '<div class="insp-detail">' + esc(d.detail) + "</div>" : "") +
      '<div class="insp-flow">' +
      '<div class="io"><div class="io-lbl">Reads</div><div class="io-row">' + inputs + "</div></div>" +
      '<div class="io-arrow">↓ ' + esc(ag.display || "agent") + " runs</div>" +
      '<div class="io"><div class="io-lbl">Writes</div><div class="io-row">' + writes + "</div></div></div>" +
      failBlock +
      (d.failure ? "" : '<div class="insp-foot">Read-only inspector. Open an artifact to see its contents.</div>');

    // wire artifact opens (jmodal layers above the drawer)
    body.querySelectorAll(".chipf[data-art]").forEach(function (c) {
      c.onclick = function () { openArtifact(slug, c.dataset.art); };
    });
    // wire honest fix actions
    body.querySelectorAll("[data-act]").forEach(function (b) {
      b.onclick = function () { inspectorAction(slug, d.key, b.dataset.act, body); };
    });
  }

  async function inspectorAction(slug, key, act, body) {
    if (act === "close") { closeDrawer(); return; }
    var res = body.querySelector("#insp-result");
    var path = act === "retry" ? "/api/retry/" + encodeURIComponent(slug)
      : "/api/cancel/" + encodeURIComponent(slug);
    if (res) res.innerHTML = '<div class="state-msg">' + (act === "retry" ? "Retrying…" : "Cancelling…") + "</div>";
    try {
      var r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      var out = await r.json().catch(function () { return {}; });
      if (!r.ok) throw new Error(out.error || ("HTTP " + r.status));
      if (res) res.innerHTML = '<div class="state-msg" style="color:var(--done)">✓ ' +
        (act === "retry" ? "Retry started — back on the belt." : "Cancellation requested.") + "</div>";
      flashSlug = slug;
      scheduleBeltRefresh();
      setTimeout(function () { closeDrawer(); renderPipeline(slug); }, 700);
    } catch (e) {
      if (res) res.innerHTML = '<div class="state-msg err">Couldn\'t ' + esc(act) + ": " + esc(e.message) + "</div>";
    }
  }

  // ================================================================ DIALOG SYSTEM
  var dlgPrevFocus = null;
  function openDialog(opts) {
    var root = $("dialog-root");
    var ft = (opts.primary || opts.secondaryLabel !== undefined)
      ? '<div class="ft">' +
        (opts.secondaryLabel !== undefined
          ? '<button class="btn dlg-cancel" type="button">' + esc(opts.secondaryLabel || "Cancel") + "</button>" : "") +
        (opts.primary ? '<button class="btn primary grow dlg-primary" type="button">' + esc(opts.primary.label) + "</button>" : "") +
        "</div>" : "";
    root.innerHTML =
      '<div class="dlg" role="dialog" aria-modal="true" aria-label="' + esc(opts.title) + '">' +
      '<div class="box"><div class="hd"><div class="ic">' + esc(opts.icon || "●") + "</div>" +
      "<div><h2>" + esc(opts.title) + "</h2>" +
      (opts.sub ? '<div class="sub">' + esc(opts.sub) + "</div>" : "") + "</div>" +
      '<button class="x" type="button" aria-label="Close">✕</button></div>' +
      '<div class="bd">' + opts.body + "</div>" + ft + "</div></div>";
    var dlg = root.querySelector(".dlg");
    dlgPrevFocus = document.activeElement;
    function close() { closeDialog(); if (opts.onClose) opts.onClose(); }
    root.querySelector(".x").onclick = close;
    var cancelBtn = root.querySelector(".dlg-cancel");
    if (cancelBtn) cancelBtn.onclick = close;
    var primary = root.querySelector(".dlg-primary");
    if (primary && opts.primary) primary.onclick = opts.primary.onClick;
    dlg.onclick = function (e) { if (e.target === dlg && !opts.hard) close(); };
    dlg._keyh = function (e) {
      if (e.key === "Escape" && !opts.hard) { e.preventDefault(); close(); return; }
      if (e.key === "Tab") trapFocus(dlg, e);
    };
    document.addEventListener("keydown", dlg._keyh);
    return dlg.querySelector(".box");
  }
  function closeDialog() {
    var root = $("dialog-root"), dlg = root.querySelector(".dlg");
    if (dlg && dlg._keyh) document.removeEventListener("keydown", dlg._keyh);
    root.innerHTML = "";
    if (dlgPrevFocus && dlgPrevFocus.focus) { try { dlgPrevFocus.focus(); } catch (e) {} }
    dlgPrevFocus = null;
  }
  window.closeDialog = closeDialog;
  function trapFocus(container, e) {
    var f = container.querySelectorAll(
      'button,[href],input,textarea,select,[tabindex]:not([tabindex="-1"])');
    f = Array.prototype.filter.call(f, function (el) {
      return !el.disabled && el.offsetParent !== null;
    });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  function cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
  }

  // ================================================================ AGENTIC CHAT
  // The LLM plane (lilac): read-grounded, agentic, but T1-ONLY. It streams Atlas's words,
  // can PROPOSE a reversible action (you confirm with one click), and can NAVIGATE you to a
  // gate/publish — but the authorising click for a T2/T3 always lives on the deterministic
  // UI (spec §4/§8). Nothing the chat returns can satisfy a gate or publish.
  var chatHistory = [];     // [{role:'user'|'atlas', content}]
  var chatOpen = false, chatBusy = false;

  function openChat() {
    var root = $("chat-root");
    if (chatOpen) return;
    chatOpen = true;
    $("chat-fab").classList.add("hidden");
    root.innerHTML =
      '<div class="chat-panel" role="dialog" aria-label="Chat with Atlas">' +
      '<div class="chat-hd"><div class="ava">✦</div>' +
      '<div><div class="ttl">Atlas</div><div class="sub">LLM plane · proposes, never approves</div></div>' +
      '<button class="x" type="button" aria-label="Close chat">✕</button></div>' +
      '<div class="chat-log" id="chat-log"></div>' +
      '<div class="chat-input"><textarea id="chat-ta" rows="1" placeholder="Ask about the belt, or ask me to start / cancel a run…"></textarea>' +
      '<button class="send" id="chat-send" type="button" aria-label="Send">➤</button></div></div>';
    var panel = root.querySelector(".chat-panel");
    requestAnimationFrame(function () { panel.classList.add("in"); });
    root.querySelector(".chat-hd .x").onclick = closeChat;
    var log = $("chat-log");
    if (!chatHistory.length) {
      log.innerHTML = '<div class="chat-intro">I can read the live belt, fleet, and gates, and ' +
        '<b>propose</b> reversible moves — start a production, cancel a run, change a default — ' +
        'which you confirm with one click. I\'ll point you to a gate or publish, but the ' +
        '<b>approving click stays on the deterministic screen</b>.</div>';
    } else {
      chatHistory.forEach(function (m) { appendMsg(log, m.role, m.content); });
    }
    var ta = $("chat-ta");
    ta.oninput = function () { ta.style.height = "auto"; ta.style.height = Math.min(120, ta.scrollHeight) + "px"; };
    ta.onkeydown = function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
    };
    $("chat-send").onclick = sendChat;
    setTimeout(function () { ta.focus(); }, 60);
  }
  function closeChat() {
    chatOpen = false;
    $("chat-root").innerHTML = "";
    $("chat-fab").classList.remove("hidden");
  }

  function appendMsg(log, role, text, opts) {
    opts = opts || {};
    var who = role === "user" ? "You" : "Atlas";
    var div = document.createElement("div");
    div.className = "msg " + (role === "user" ? "you" : "atlas");
    div.innerHTML = '<span class="who">' + esc(who) + '</span>' +
      '<div class="bub' + (opts.streaming ? " streaming" : "") + (opts.err ? " err" : "") + '"></div>';
    div.querySelector(".bub").textContent = text || "";
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div.querySelector(".bub");
  }

  async function sendChat() {
    if (chatBusy) return;
    var ta = $("chat-ta"), log = $("chat-log");
    var text = (ta.value || "").trim();
    if (!text) return;
    ta.value = ""; ta.style.height = "auto";
    chatBusy = true; $("chat-send").disabled = true;
    appendMsg(log, "user", text);
    chatHistory.push({ role: "user", content: text });
    var bub = appendMsg(log, "atlas", "", { streaming: true });
    var acc = "";
    try {
      var r = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: chatHistory.slice(0, -1) }),
      });
      if (!r.ok) {
        var e = await r.json().catch(function () { return {}; });
        throw new Error(e.error || ("HTTP " + r.status));
      }
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "", done = null;
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        var parts = buf.split("\n\n");
        buf = parts.pop();
        parts.forEach(function (p) {
          var line = p.trim();
          if (line.indexOf("data:") !== 0) return;
          var frame;
          try { frame = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
          if (frame.type === "text") { acc += frame.t; bub.textContent = acc; log.scrollTop = log.scrollHeight; }
          else if (frame.type === "error") { done = { error: frame.error }; }
          else if (frame.type === "done") { done = frame; }
        });
      }
      bub.classList.remove("streaming");
      if (done && done.error) {
        bub.classList.add("err"); bub.textContent = "Couldn't finish: " + done.error;
      } else {
        var reply = (done && done.reply) || acc || "(no reply)";
        bub.textContent = reply;
        chatHistory.push({ role: "atlas", content: reply });
        if (done && done.action) renderProposal(log, done.action);
        await maybeOfferGateNav(log);
      }
    } catch (e) {
      bub.classList.remove("streaming"); bub.classList.add("err");
      bub.textContent = "Couldn't reach Atlas: " + e.message;
    } finally {
      chatBusy = false; $("chat-send").disabled = false; $("chat-ta").focus();
    }
  }

  // A PROPOSED T1 action — rendered as a reversible proposal you confirm (the light confirm
  // of §4). The kinds are fixed (trigger/cancel/update_setting); there is no approve/publish.
  function renderProposal(log, action) {
    var kind = action.kind, args = action.args || {};
    var label = kind === "trigger"
      ? 'Start a production' + (args.topic ? ' — <b>' + esc(ellip(args.topic, 60)) + '</b>' : '')
      : kind === "cancel"
        ? 'Cancel / park <b>' + esc(ellip(args.slug || "", 40)) + '</b>'
        : 'Set <b>' + esc(args.field || "") + '</b> to <b>' + esc(String(args.value)) + '</b>';
    var box = document.createElement("div");
    box.className = "proposal";
    box.innerHTML =
      '<div class="ph"><span class="t1">T1 · reversible</span>Atlas proposes</div>' +
      '<div class="pd">' + label + "</div>" +
      '<div class="pacts"><button class="pbtn go" type="button">Confirm</button>' +
      '<button class="pbtn no" type="button">Dismiss</button></div>' +
      '<div class="pnote"></div>';
    log.appendChild(box); log.scrollTop = log.scrollHeight;
    var note = box.querySelector(".pnote");
    box.querySelector(".no").onclick = function () { box.remove(); };
    box.querySelector(".go").onclick = async function () {
      box.querySelectorAll(".pbtn").forEach(function (b) { b.disabled = true; });
      note.className = "pnote"; note.textContent = "Working…";
      try {
        var out = await postJSON("/api/chat/act", { kind: kind, args: args });
        note.className = "pnote ok";
        note.textContent = kind === "trigger" ? "Started — it's on the belt."
          : kind === "cancel" ? "Cancellation requested." : "Setting saved.";
        box.querySelector(".pacts").remove();
        scheduleBeltRefresh();
        if (kind === "trigger" && out.slug) flashSlug = out.slug;
      } catch (e) {
        note.className = "pnote err"; note.textContent = "Couldn't do it: " + e.message;
        box.querySelectorAll(".pbtn").forEach(function (b) { b.disabled = false; });
      }
    };
  }

  // Chat NAVIGATES to a gate — it never acts there. If something is blocked, offer a chip
  // that opens the deterministic gate-review drawer (where the authorising click lives).
  async function maybeOfferGateNav(log) {
    var d;
    try { d = await getJSON("/api/belt"); } catch (e) { return; }
    var blocked = (d.videos || []).filter(function (v) { return v.belt_state === "blocked"; });
    if (!blocked.length) return;
    var v = blocked[0];
    var chip = document.createElement("button");
    chip.type = "button"; chip.className = "gobtn";
    chip.innerHTML = "⚑ Review the " + esc(v.gate || "") + " gate → <b>" + ellipEsc(v.label, 28) + "</b>";
    chip.onclick = function () { openGateReview(v.slug); };
    log.appendChild(chip); log.scrollTop = log.scrollHeight;
  }

  // ================================================================ T2 GATE-REVIEW DRAWER
  // The DETERMINISTIC surface where the authorising APPROVE click lives. Reachable from the
  // chat's navigate chip and the belt, but the approve always posts to the deterministic
  // endpoint (which resumes through the belt, sharing station locks). A hard fact-check
  // `block` can never be approved away (the spine re-earns it; the UI never offers it).
  async function openGateReview(slug) {
    var panel = openDrawer({ title: "Gate review", sub: slug });
    var body = panel.querySelector(".dw-body");
    loading(body, "Loading gate…");
    var d;
    try { d = await getJSON("/api/gate/" + encodeURIComponent(slug)); }
    catch (e) { errState(body, "Couldn't load the gate. " + e.message); return; }
    if (d.kind === "none") {
      body.innerHTML = '<div class="state-msg">No gate is awaiting a decision (status: ' + esc(d.status) + ").</div>";
      return;
    }
    var hard = d.hard_block, approvable = d.approvable && !hard;
    var head, bodyHtml = "";
    if (d.kind === "factcheck") {
      var sm = d.summary || {}, flagged = d.flagged || [];
      var verdict = hard ? "block" : (flagged.length ? "review" : "pass");
      head = '<div class="gr-verdict ' + verdict + '">' +
        (hard ? "BLOCK — routed back" : flagged.length ? (flagged.length + " flagged · review") : "pass") + "</div>";
      bodyHtml =
        '<div class="gr-stake' + (hard ? " hard" : "") + '">' +
        (hard ? "Sage raised a <b>hard fact-check block</b> — it <b>cannot be approved away</b>. The script returns to Marlow until it's fixed and re-checked."
          : flagged.length ? "Sage flagged <b>" + flagged.length + " claim" + (flagged.length > 1 ? "s" : "") + "</b>. Approve to send the script on, or send it back. <b>Nothing renders until you decide.</b>"
            : "Sage verified the script with no flags. You can approve it through.") + "</div>" +
        '<div class="gr-summ"><span class="chip"><b>' + ((sm.verified || 0) + (sm.flagged || 0) + (sm.unverifiable || 0)) + '</b> claims</span>' +
        '<span class="chip v"><b>' + (sm.verified || 0) + '</b> verified</span>' +
        '<span class="chip f"><b>' + (sm.flagged || 0) + '</b> flagged</span>' +
        '<span class="chip b"><b>' + (sm.unverifiable || 0) + '</b> unverifiable</span></div>' +
        flagged.slice(0, 6).map(function (c) {
          return '<div class="gr-flag"><div class="cn">scene ' + esc(c.scene_no) + ' · ' + esc((c.status || "flagged").toUpperCase()) + '</div>' +
            '<div class="ct">"' + esc(ellip(c.claim_text, 160)) + '"</div>' +
            (c.note ? '<div class="note">' + esc(ellip(c.note, 180)) + "</div>" : "") + "</div>";
        }).join("");
    } else {
      var plan = d.plan || {};
      head = '<div class="gr-verdict review">final-render gate</div>';
      bodyHtml =
        '<div class="gr-stake">Last gate before the spine spends on the render. Review the plan and approve to render.</div>' +
        '<div class="gr-plan"><span class="gr-summ"><span class="chip"><b>' + num(plan.scenes, "—") + '</b> scenes</span>' +
        '<span class="chip"><b>' + (plan.est_runtime_sec ? Math.round(plan.est_runtime_sec) : "—") + '</b>s est</span>' +
        (d.draft_renders ? '<span class="chip"><b>' + d.draft_renders.length + '</b> draft(s)</span>' : "") + "</span>" +
        (plan.plan ? '<div class="note" style="font-family:Space Mono;font-size:11px;color:var(--mut);line-height:1.5">' + esc(plan.plan) + "</div>" : "") + "</div>";
    }
    var gate = d.kind;
    var btn = approvable
      ? '<label class="gr-ack"><input type="checkbox" id="gr-ack"> I\'ve reviewed this and accept proceeding.</label>' +
        '<button class="bigbtn primary" id="gr-approve" data-gate="' + esc(gate) + '">Approve ' + esc(prettyGate(gate)) +
        '<small>resumes through the belt</small></button>'
      : '<button class="bigbtn" disabled style="opacity:.55;cursor:not-allowed">Can\'t approve — ' +
        (hard ? "routed back" : "not approvable") + '<small>' + (hard ? "the spine refuses a block" : "not at an approvable gate") + "</small></button>";
    body.innerHTML = head + bodyHtml +
      '<div style="margin-top:14px">' + btn + '<div id="gr-result"></div></div>' +
      '<div class="gr-rule">🔒 This is the <b>deterministic</b> surface. The chat can bring you here and summarise it, but it can <b>never</b> satisfy this gate — the authorising click is yours, here.</div>';
    var ab = $("gr-approve");
    if (ab) ab.onclick = function () { submitGateApprove(slug, gate); };
  }

  async function submitGateApprove(slug, gate) {
    var ack = $("gr-ack"), res = $("gr-result"), btn = $("gr-approve");
    if (ack && !ack.checked) { res.innerHTML = '<div class="state-msg err">Tick the acknowledgement first.</div>'; return; }
    btn.disabled = true;
    res.innerHTML = '<div class="state-msg">Approving — resuming the pipeline…</div>';
    try {
      var out = await postJSON("/api/gate/" + encodeURIComponent(slug) + "/approve", { gate: gate });
      res.innerHTML = '<div class="state-msg" style="color:var(--done)">✓ ' + esc(out.status || "approved") +
        (out.next_gate ? " · now at " + esc(out.next_gate) + " gate" : "") + "</div>";
      flashSlug = slug; scheduleBeltRefresh();
      setTimeout(function () { closeDrawer(); renderPipeline(slug); }, 800);
    } catch (e) {
      btn.disabled = false;
      res.innerHTML = '<div class="state-msg err">Approval failed: ' + esc(e.message) + "</div>";
    }
  }
  window.openGateReview = openGateReview;

  // ================================================================ T3 PUBLISH-CONFIRM (shell)
  // The HARD, structured review of the EXACT final package (title/description/tags/thumbnail/
  // visibility/schedule). No stray Escape/backdrop close (openDialog hard:true). Scheduling
  // sets go-live only AFTER approval; real publishing arrives with Herald (#6), so the fire
  // button is disabled here — there is no auto-fire-unreviewed path (spec §4 T3 / E8).
  async function openPublishModal(slug) {
    var d;
    try { d = await getJSON("/api/publish/" + encodeURIComponent(slug)); }
    catch (e) { return; }
    var p = d.package || {};
    var chan = d.channel;
    var q = d.quota || {};
    var tags = (p.tags || []).length
      ? '<div class="pub-tags">' + p.tags.map(function (t) { return "<span>" + esc(t) + "</span>"; }).join("") + "</div>"
      : '<span class="mono" style="color:var(--mut)">— none —</span>';
    var chanRow = chan
      ? '<div class="pub-chan"><b>' + esc(chan.title || chan.channel_id || "untitled") + "</b> · " + esc(chan.connection_status || "disconnected") +
        '<br>project ' + (chan.project_verified ? "verified ✓" : "<span style=\"color:var(--gate)\">unverified</span>") +
        ' · channel ' + (chan.channel_phone_verified ? "verified ✓" : "<span style=\"color:var(--gate)\">unverified</span>") + "</div>"
      : '<span class="mono" style="color:var(--gate)">no channel mapped to this niche</span>';
    var body =
      '<div class="pub-band"><span class="lock">🔒</span><div>This is the <b>exact package</b> that would go live. ' +
      'Review every field — once approved, scheduling only sets the <b>go-live time</b>; nothing publishes before that.</div></div>' +
      '<div class="pub-pkg">' +
      pubRow("Title", '<div class="vv">' + esc(p.title || "—") + "</div>") +
      pubRow("Description", '<div class="vv">' + (p.description ? esc(ellip(p.description, 240)) : '<span class="mono" style="color:var(--mut)">— none —</span>') + "</div>") +
      pubRow("Tags", tags) +
      pubRow("Thumbnail", '<div class="pub-thumb">' + esc((p.thumbnail || {}).note || "thumbnail") + "</div>") +
      pubRow("Visibility", '<span class="pub-vis">' + esc(p.visibility || "private") + "</span>") +
      pubRow("Schedule", '<div class="vv"><span class="mono">' + (p.schedule ? esc(p.schedule) : "not scheduled — go-live is set after approval") + "</span></div>") +
      pubRow("Channel", '<div class="vv">' + chanRow + "</div>") +
      pubRow("Quota", '<div class="vv"><span class="mono">' + num(q.max_uploads_per_day, 6) + " uploads/day · shared across ALL channels</span></div>") +
      "</div>" +
      (d.blockers && d.blockers.length
        ? '<ul class="pub-blockers">' + d.blockers.map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul>" : "") +
      '<button class="pub-fire" disabled>Publish to YouTube<small>arrives with Herald (#6) — no upload fires from here</small></button>';
    openDialog({
      icon: "📡", title: "Publish — final review", hard: true,
      sub: "irreversible external · T3 · the enforced checkpoint",
      body: body, secondaryLabel: "Close",
    });
    // tag the dialog so the broadcast-red header styling applies
    var dlg = $("dialog-root").querySelector(".dlg");
    if (dlg) { dlg.querySelector(".box").classList.add("t3"); dlg.querySelector(".hd").classList.add("t3"); }
  }
  function pubRow(k, vHtml) {
    return '<div class="pub-row"><div class="k">' + esc(k) + "</div>" + vHtml + "</div>";
  }
  window.openPublishModal = openPublishModal;

  // ================================================================ SSE (live)
  function connectEvents() {
    if (es || typeof EventSource === "undefined") return;
    try { es = new EventSource("/api/events"); }
    catch (e) { return; }
    es.onmessage = function () { scheduleBeltRefresh(); scheduleActivityRefresh(); };
    es.onerror = function () { /* EventSource auto-reconnects + resumes via Last-Event-ID */ };
  }

  // ---------------------------------------------------------------- boot
  document.addEventListener("DOMContentLoaded", function () {
    renderOverview();
    renderBelt();
    connectEvents();
    var g = $("ov-generate");
    if (g) g.onclick = openLaunchModal;
    var fab = $("chat-fab");
    if (fab) fab.onclick = openChat;
  });
})();
