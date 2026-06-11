/* Claims Pipeline UI — no build step, no framework. Talks to the same API that
   curl and the eval runner use. All data is rendered with textContent (document
   contents are untrusted input). */

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function money(value, currency) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency", currency: currency || "INR",
    minimumFractionDigits: 0, maximumFractionDigits: 2,
  }).format(value);
}

function chip(text, kind) {
  return el("span", `chip chip-${kind || text}`, text);
}

let META = null;
let CASES = [];

/* ---------------------------------------------------------------- navigation */

function show(view) {
  $("#view-submit").classList.toggle("active", view === "submit");
  $("#view-claims").classList.toggle("active", view === "claims");
  $("#nav-submit").classList.toggle("active", view === "submit");
  $("#nav-claims").classList.toggle("active", view === "claims");
  if (view === "claims") loadClaims();
}
$("#nav-submit").onclick = () => show("submit");
$("#nav-claims").onclick = () => show("claims");
$("#refresh").onclick = () => loadClaims();

/* -------------------------------------------------------------------- set-up */

async function init() {
  META = await (await fetch("/policy/meta")).json();
  $("#policy-name").textContent = `${META.policy_name} (${META.policy_id})`;
  $("#policy-id").value = META.policy_id;

  for (const m of META.members) {
    const opt = el("option", null, `${m.member_id} — ${m.name} (${m.relationship})`);
    opt.value = m.member_id;
    $("#member").appendChild(opt);
  }
  for (const c of META.claim_categories) {
    const opt = el("option", null, c);
    opt.value = c;
    $("#category").appendChild(opt);
  }
  $("#category").onchange = updateRequirementsHint;
  updateRequirementsHint();

  try {
    const data = await (await fetch("/test-cases")).json();
    CASES = data.cases || [];
    for (const c of CASES) {
      const opt = el("option", null, `${c.case_id} — ${c.case_name}`);
      opt.value = c.case_id;
      $("#tc-select").appendChild(opt);
    }
    $("#tc-select").onchange = () => {
      const c = CASES.find((x) => x.case_id === $("#tc-select").value);
      $("#tc-description").textContent = c ? c.description : "";
    };
    $("#tc-select").onchange();
  } catch {
    $("#tc-select").closest(".card").style.display = "none";
  }
}

function humanType(t) {
  return t.replaceAll("_", " ").toLowerCase();
}

function updateRequirementsHint() {
  const reqs = META.document_requirements[$("#category").value];
  if (!reqs) { $("#requirements-hint").textContent = ""; return; }
  let text = `A ${$("#category").value} claim needs: ${reqs.required.map(humanType).join(", ")}.`;
  if (reqs.optional.length) text += ` Optional: ${reqs.optional.map(humanType).join(", ")}.`;
  $("#requirements-hint").textContent = text;
}

/* ------------------------------------------------------------ file type rows */

$("#files").onchange = () => {
  const container = $("#file-types");
  container.replaceChildren();
  for (const file of $("#files").files) {
    const row = el("div", "file-row");
    row.appendChild(el("span", "fname", file.name));
    const select = el("select", "dtype");
    select.appendChild(Object.assign(el("option", null, "type: let the classifier decide"), { value: "" }));
    for (const t of META.document_types) {
      const opt = el("option", null, `declared: ${humanType(t)}`);
      opt.value = t;
      select.appendChild(opt);
    }
    row.appendChild(select);
    container.appendChild(row);
  }
};

/* ------------------------------------------------------------------- submit */

$("#claim-form").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("#submit-btn");
  btn.disabled = true;
  btn.textContent = "Processing…";
  try {
    const fd = new FormData();
    fd.append("member_id", $("#member").value);
    fd.append("policy_id", $("#policy-id").value);
    fd.append("claim_category", $("#category").value);
    fd.append("treatment_date", $("#treatment-date").value);
    fd.append("claimed_amount", $("#amount").value);
    if ($("#hospital").value) fd.append("hospital_name", $("#hospital").value);
    fd.append("ytd_claims_amount", $("#ytd").value || "0");
    fd.append("simulate_component_failure", $("#simulate").checked ? "true" : "false");
    for (const file of $("#files").files) fd.append("files", file);
    const declared = [...document.querySelectorAll("#file-types select")].map(
      (s) => s.value || null
    );
    fd.append("declared_types", JSON.stringify(declared));

    const res = await fetch("/claims", { method: "POST", body: fd });
    renderOutcome(res.status, await res.json());
  } catch (err) {
    renderError(String(err));
  } finally {
    btn.disabled = false;
    btn.textContent = "Submit claim";
  }
};

$("#tc-run").onclick = async () => {
  const c = CASES.find((x) => x.case_id === $("#tc-select").value);
  if (!c) return;
  const btn = $("#tc-run");
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    // pin the submission date so the dated scenarios evaluate deterministically
    const payload = { ...c.input, submission_date: c.input.treatment_date };
    const res = await fetch("/claims/json", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderOutcome(res.status, await res.json());
  } catch (err) {
    renderError(String(err));
  } finally {
    btn.disabled = false;
    btn.textContent = "Run";
  }
};

function renderError(text) {
  const box = $("#submit-result");
  box.replaceChildren();
  const card = el("div", "card");
  card.appendChild(el("div", "banner error", text));
  box.appendChild(card);
  card.scrollIntoView({ behavior: "smooth" });
}

function problemNode(p) {
  const node = el("div", "problem");
  const head = el("div");
  head.appendChild(chip(p.error_code, "FAIL"));
  if (p.file_name) head.appendChild(el("span", "muted", `  ${p.file_name}`));
  node.appendChild(head);
  node.appendChild(el("div", null, p.message));
  if (p.what_to_do_next) node.appendChild(el("div", "next", `What to do next: ${p.what_to_do_next}`));
  return node;
}

function renderOutcome(httpStatus, body) {
  const box = $("#submit-result");
  box.replaceChildren();
  const card = el("div", "card");

  if (httpStatus === 422) {
    card.appendChild(el("div", "banner error", "Submission rejected at intake — nothing was processed."));
    for (const p of body.errors || []) card.appendChild(problemNode(p));
  } else if (body.status === "NEEDS_RESUBMISSION") {
    card.appendChild(el("div", "banner warn",
      `Claim ${body.claim_id} stopped early — it needs resubmission. No decision was made.`));
    for (const p of body.problems || []) card.appendChild(problemNode(p));
    card.appendChild(reviewLink(body.claim_id));
  } else {
    const d = body.decision;
    card.appendChild(el("div", `banner ${d && d.decision === "REJECTED" ? "error" : d && d.decision === "MANUAL_REVIEW" ? "info" : "ok"}`,
      `Claim ${body.claim_id} processed.`));
    if (d) {
      const kv = el("div", "kv");
      kv.appendChild(kvItem("Decision", chip(d.decision)));
      kv.appendChild(kvItem("Approved amount", el("b", null, money(d.approved_amount, d.currency))));
      kv.appendChild(kvItem("Confidence", el("span", null, d.confidence.toFixed(2))));
      card.appendChild(kv);
      for (const r of d.reasons || []) card.appendChild(el("div", "muted", `• ${r.detail}`));
    }
    card.appendChild(reviewLink(body.claim_id));
  }
  box.appendChild(card);
  card.scrollIntoView({ behavior: "smooth" });
}

function kvItem(label, valueNode) {
  const wrap = el("div");
  wrap.appendChild(el("span", null, label));
  wrap.appendChild(valueNode);
  return wrap;
}

function reviewLink(claimId) {
  const btn = el("button", "primary", "Open the full trace in Review");
  btn.type = "button";
  btn.onclick = async () => {
    show("claims");
    await openClaim(claimId);
  };
  return btn;
}

/* ------------------------------------------------------------------- claims */

async function loadClaims() {
  const data = await (await fetch("/claims")).json();
  const tbody = $("#claims-table tbody");
  tbody.replaceChildren();
  $("#claims-empty").style.display = data.claims.length ? "none" : "block";
  for (const c of data.claims) {
    const tr = el("tr");
    tr.appendChild(el("td")).appendChild(el("code", null, c.claim_id));
    tr.appendChild(el("td", null, c.member_id));
    tr.appendChild(el("td", null, c.claim_category));
    tr.appendChild(el("td", null, money(c.claimed_amount)));
    tr.appendChild(el("td")).appendChild(chip(c.status, "neutral"));
    tr.appendChild(el("td")).appendChild(c.decision ? chip(c.decision) : el("span", "muted", "—"));
    tr.appendChild(el("td", null, c.approved_amount !== null ? money(c.approved_amount) : "—"));
    tr.appendChild(el("td", null, c.confidence !== null ? c.confidence.toFixed(2) : "—"));
    tr.appendChild(el("td", "muted", new Date(c.created_at).toLocaleString()));
    tr.onclick = () => openClaim(c.claim_id);
    tbody.appendChild(tr);
  }
}

async function openClaim(claimId) {
  const record = await (await fetch(`/claims/${claimId}`)).json();
  renderDetail(record);
  $("#claim-detail").scrollIntoView({ behavior: "smooth" });
}

function renderDetail(r) {
  const box = $("#claim-detail");
  box.replaceChildren();

  // ---- header
  const head = el("div", "card");
  const title = el("h2");
  title.appendChild(el("code", null, r.claim_id));
  title.appendChild(document.createTextNode("  "));
  title.appendChild(chip(r.status, "neutral"));
  head.appendChild(title);
  const kv = el("div", "kv");
  kv.appendChild(kvItem("Member", el("b", null, r.submission.member_id)));
  kv.appendChild(kvItem("Category", el("b", null, r.submission.claim_category)));
  kv.appendChild(kvItem("Treatment date", el("b", null, r.submission.treatment_date)));
  kv.appendChild(kvItem("Claimed", el("b", null, money(r.claimed_amount, r.currency))));
  kv.appendChild(kvItem("Running confidence", el("b", null, r.confidence.toFixed(2))));
  head.appendChild(kv);
  box.appendChild(head);

  // ---- problems (stopped claims)
  if (r.problems && r.problems.length) {
    const card = el("div", "card");
    card.appendChild(el("h2", null, "Problems — claim stopped, needs resubmission"));
    for (const p of r.problems) card.appendChild(problemNode(p));
    box.appendChild(card);
  }

  // ---- decision
  if (r.decision) box.appendChild(decisionCard(r.decision, "Decision"));

  // ---- fraud
  if (r.fraud) {
    const card = el("div", "card");
    card.appendChild(el("h2", null, "Fraud assessment"));
    const kv2 = el("div", "kv");
    kv2.appendChild(kvItem("Score", el("b", null, r.fraud.fraud_score.toFixed(2))));
    kv2.appendChild(kvItem("Source", el("span", null, r.fraud.source)));
    card.appendChild(kv2);
    for (const s of r.fraud.signals || []) {
      const row = el("div", "doc");
      row.appendChild(chip(s.name, s.severity === "HIGH" ? "FAIL" : "WARN"));
      row.appendChild(el("span", null, ` ${s.severity} — ${s.explanation}`));
      card.appendChild(row);
    }
    box.appendChild(card);
  }

  // ---- documents
  if (r.classifications && r.classifications.length) {
    const card = el("div", "card");
    card.appendChild(el("h2", null, "Documents"));
    for (const c of r.classifications) {
      const doc = el("div", "doc");
      const head2 = el("div");
      head2.appendChild(el("b", null, c.file_name || c.file_id));
      head2.appendChild(document.createTextNode("  "));
      head2.appendChild(chip(c.detected_type, "neutral"));
      head2.appendChild(document.createTextNode(` quality ${c.quality} · confidence ${c.confidence.toFixed(2)} · via ${c.source}`));
      doc.appendChild(head2);
      if (c.evidence) doc.appendChild(el("div", "evidence", c.evidence));
      if (c.notes) doc.appendChild(el("div", "notes", `⚑ ${c.notes}`));
      const read = (r.reads || []).find((x) => x.file_id === c.file_id);
      if (read) {
        if (read.read_failed) {
          doc.appendChild(el("div", "notes", `read failed: ${read.failure_reason}`));
        } else {
          const details = el("details");
          details.appendChild(el("summary", null,
            `extracted content (extraction confidence ${read.extraction_confidence.toFixed(2)})`));
          details.appendChild(el("pre", null, JSON.stringify(read.content, null, 2)));
          doc.appendChild(details);
        }
      }
      card.appendChild(doc);
    }
    box.appendChild(card);
  }

  // ---- soft signals
  if (r.soft_signals && r.soft_signals.length) {
    const card = el("div", "card");
    card.appendChild(el("h2", null, "Soft signals collected"));
    for (const s of r.soft_signals) card.appendChild(el("div", "muted", `• ${s}`));
    box.appendChild(card);
  }

  // ---- trace
  const traceCard = el("div", "card");
  traceCard.appendChild(el("h2", null, `Trace — ${r.trace.length} events`));
  let currentStage = null;
  for (const ev of r.trace) {
    if (ev.stage !== currentStage) {
      currentStage = ev.stage;
      traceCard.appendChild(el("div", "trace-stage", currentStage.replaceAll("_", " ").toUpperCase()));
    }
    const row = el("div", "trace-event");
    row.appendChild(chip(ev.result));
    row.appendChild(el("span", "check", ev.check_name));
    row.appendChild(el("span", "detail", ev.detail));
    row.appendChild(el("span", "time", new Date(ev.timestamp).toLocaleTimeString()));
    traceCard.appendChild(row);
  }
  box.appendChild(traceCard);
}

function decisionCard(d, heading) {
  const card = el("div", "card");
  const h = el("h2", null, `${heading}: `);
  h.appendChild(chip(d.decision));
  card.appendChild(h);

  const kv = el("div", "kv");
  kv.appendChild(kvItem("Approved amount", el("b", null, money(d.approved_amount, d.currency))));
  kv.appendChild(kvItem("Confidence", el("b", null, d.confidence.toFixed(2))));
  if (d.eligibility_date) kv.appendChild(kvItem("Eligible from", el("b", null, d.eligibility_date)));
  card.appendChild(kv);

  if (d.manual_review_recommended && d.decision !== "MANUAL_REVIEW") {
    card.appendChild(el("div", "banner info",
      `Manual review recommended: ${(d.manual_review_notes || []).join("; ")}`));
  }
  if (d.what_to_do_next) {
    card.appendChild(el("div", "banner info", `What to do next: ${d.what_to_do_next}`));
  }

  if (d.reasons && d.reasons.length) {
    card.appendChild(el("h3", null, "Reasons"));
    for (const r of d.reasons) {
      const row = el("div", "trace-event");
      row.appendChild(chip(r.code, "neutral"));
      row.appendChild(el("span", "detail", r.detail));
      card.appendChild(row);
    }
  }

  if (d.money_breakdown && d.money_breakdown.length) {
    card.appendChild(el("h3", null, "Money breakdown"));
    const table = el("table");
    const thead = el("thead");
    const hr = el("tr");
    for (const t of ["Step", "Before", "After"]) hr.appendChild(el("th", null, t));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const s of d.money_breakdown) {
      const tr = el("tr");
      tr.appendChild(el("td", null, s.description));
      tr.appendChild(el("td", null, money(s.amount_before, d.currency)));
      tr.appendChild(el("td", null, money(s.amount_after, d.currency)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    card.appendChild(table);
  }

  if (d.line_item_breakdown && d.line_item_breakdown.length) {
    card.appendChild(el("h3", null, "Line items"));
    const table = el("table");
    const thead = el("thead");
    const hr = el("tr");
    for (const t of ["Item", "Amount", "Outcome", "Reason"]) hr.appendChild(el("th", null, t));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const item of d.line_item_breakdown) {
      const tr = el("tr");
      tr.appendChild(el("td", null, item.description));
      tr.appendChild(el("td", null, money(item.amount, d.currency)));
      tr.appendChild(el("td")).appendChild(chip(item.outcome));
      tr.appendChild(el("td", "muted", item.reason || ""));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    card.appendChild(table);
  }

  if (d.computed_policy_outcome) {
    const details = el("details");
    details.appendChild(el("summary", null,
      "Computed policy outcome (attached for the reviewer)"));
    details.appendChild(decisionCard(d.computed_policy_outcome, "Policy outcome"));
    card.appendChild(details);
  }
  return card;
}

/* --------------------------------------------------------------------- boot */

init().then(() => {
  // default the treatment date to today for convenience
  $("#treatment-date").value = new Date().toISOString().slice(0, 10);
});
