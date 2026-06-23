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
      case "v-overview": return renderOverview();
      case "v-projects": return renderProjects();
      case "v-pipeline": return renderPipeline(current.slug);
      case "v-fleet": return renderFleet();
      case "v-agent": return renderAgent(current.agent);
      case "v-quality": return renderQuality();
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
      if (projTab === "block") return /blocked/.test(p.status) || p.status === "failed";
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
    head.innerHTML =
      "<div><h1>" + ellipEsc(sm.label || sm.topic || slug, 80) +
      ' <span class="badge" style="vertical-align:middle">' + esc(smap.label) + "</span></h1>" +
      '<div class="slug">atlas/projects/' + esc(slug) + '/project.json</div></div>' +
      '<div class="acts"><button class="btn">Open folder</button>' +
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
      return '<div class="stage"><div class="gut"><span class="led ' + ledCls + '"></span><span class="line-v ' + lineCls + '"></span></div><div>' +
        '<div class="stop"><span class="nm">' + esc(s.key) + '</span><span class="ag">' + esc(s.agent ? s.agent.emoji : "") + " <b>" + esc(s.agent ? s.agent.display : "") + "</b></span>" +
        '<span class="sp"><span class="state ' + (s.status === "done" ? "done" : "") + '">' + esc(s.status) + "</span>" + (s.validated ? '<span class="ck">✓</span>' : "") + "</span></div>" +
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

    body.innerHTML =
      '<div class="ladder"><div class="lh">Production spine <span class="r">' + stages.length + " stages · contracts</span></div>" + ladder + "</div>" +
      "<div>" + vid +
      '<div class="card files"><h3>Artifacts</h3>' + artifacts + "</div>" +
      '<div class="card ctr"><h3>Contracts</h3>' + contracts + "</div></div>";

    // wire artifact "open" links + video button
    body.querySelectorAll("a[data-art]").forEach(function (a) {
      a.onclick = function (e) { e.preventDefault(); openArtifact(slug, a.dataset.art); };
    });
    var ov = $("pl-openvid");
    if (ov) ov.onclick = function () { window.open("/api/media/" + encodeURIComponent(slug) + "/video", "_blank"); };
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
      var lead = a.status === "holding" ? " lead" : "";
      return '<div class="ac' + lead + '" data-go="v-agent" data-rail="fleet" data-agent="' + esc(a.name) + '">' +
        '<div class="em">' + esc(a.emoji) + "</div>" +
        '<div class="nm">' + esc(a.display) + "</div>" +
        '<div class="role">' + esc(a.role) + "</div>" +
        '<div class="st"><span class="led" style="background:' + statusColor(a.status) + '"></span>' + esc(a.detail || a.status) + "</div>" +
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
      " &nbsp;·&nbsp; <b>" + esc(d.blurb || "") + '</b> &nbsp; <span class="prov">brain: ' + esc(d.provider) + "</span></div></div>" +
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
      '<div id="gt-result"></div>' +
      '<div class="rule">🔒 These are <b>flags</b> — your call. A fact-check <b>block</b> can <b>never be approved away</b> and always routes back until fixed.</div>' +
      "</div></div></div>";

    wireApprove(slug);
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

  // ---------------------------------------------------------------- boot
  document.addEventListener("DOMContentLoaded", function () { renderOverview(); });
})();
