import { signIn, finishSignIn, token, signOut } from "../demo/auth.js";
import { createAgentUI } from "./agents.js";

const $ = (selector) => document.querySelector(selector);
const h = (value = "") =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const S = {
  config: null,
  user: null,
  tenant: sessionStorage.getItem("agentu.institution"),
  page: "overview",
  overview: null,
  records: [],
  cursor: null,
  accounts: [],
  invitations: [],
  invitationCursor: null,
  authRegister: false,
  agentCatalogue: [],
  agentKeys: [],
  capabilities: {},
};
let loadVersion = 0;
const money = (minor, currency = "GBP") =>
  new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(
    minor / 100,
  );
const date = (value) =>
  value
    ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(
        "en-GB",
        { dateStyle: "medium", timeStyle: "short" },
      )
    : "—";
const badge = (value) =>
  `<span class="badge ${h(value)}">${h(value.replaceAll("_", " "))}</span>`;
const can = (operation) => S.overview?.permissions.includes(operation);
const actionButton = (operation, label, id = "", style = "") =>
  `<button class="${style}" data-action="${operation}" data-id="${h(id)}">${h(label)}</button>`;
const table = (headers, rows) =>
  `<div class="table-wrap"><table><thead><tr>${headers.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const empty = (title, description, button = "") =>
  `<div class="empty"><h2>${h(title)}</h2><p>${h(description)}</p>${button}</div>`;
const heading = (eyebrow, title, description, button = "") =>
  `<div class="page-head"><div><span class="eyebrow">${h(eyebrow)}</span><h1>${h(title)}</h1><p>${h(description)}</p></div><div class="actions">${button}</div></div>`;
const field = (name, label, type = "text", value = "", help = "", attrs = "") =>
  `<label class="field">${h(label)}<input name="${name}" type="${type}" value="${h(value)}" required ${attrs}>${help ? `<small>${h(help)}</small>` : ""}</label>`;
const select = (name, label, options, selected = "") =>
  `<label class="field">${h(label)}<select name="${name}" required>${options.map(([v, l]) => `<option value="${h(v)}" ${v === selected ? "selected" : ""}>${h(l)}</option>`).join("")}</select></label>`;
const reason = (label) =>
  `<label class="field">${h(label)}<textarea name="reason" required minlength="5" maxlength="500" placeholder="Record the reason for this decision"></textarea></label>`;
const jsonDetails = (value, label = "View record") =>
  `<details class="detail"><summary>${h(label)}</summary><pre>${h(JSON.stringify(value, null, 2))}</pre></details>`;
const roles = ["owner", "administrator", "operator", "approver", "auditor"];

function notice(message = "", error = false) {
  $("#notice").className = message ? "notice" + (error ? " error" : "") : "";
  $("#notice").textContent = message;
}
async function hash(text) {
  return [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)),
    ),
  ]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
async function call(path, body, options = {}) {
  const headers = {};
  if (S.config.mode === "hosted" && token())
    headers.Authorization = "Bearer " + token();
  let pending;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    if (!path.includes("platform-auth")) {
      pending =
        "agentu-platform.pending." +
        (S.user?.sub || "anonymous") +
        "." +
        (await hash(path + JSON.stringify(body)));
      headers["Idempotency-Key"] =
        localStorage.getItem(pending) || crypto.randomUUID();
      localStorage.setItem(pending, headers["Idempotency-Key"]);
    }
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  let response, result;
  try {
    response = await fetch((S.config.apiBase || "") + path, {
      method: body === undefined ? "GET" : "POST",
      headers,
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    result = await response.json();
  } catch {
    throw new Error(
      "The connection was interrupted. Retry this submission: its request key is retained to prevent duplicate posting.",
    );
  } finally {
    clearTimeout(timer);
  }
  if (
    pending &&
    (response.ok ||
      (response.status < 500 &&
        response.status !== 429 &&
        result.code !== "concurrent_change"))
  )
    localStorage.removeItem(pending);
  if (!response.ok) {
    if (
      response.status === 401 &&
      !options.authProbe &&
      !path.includes("platform-auth")
    ) {
      S.user = null;
      renderAuth();
    }
    const error = new Error(
      result.error || "The request could not be completed.",
    );
    error.status = response.status;
    throw error;
  }
  return result;
}
const base = () => `/api/platform/institutions/${encodeURIComponent(S.tenant)}`;
const command = (operation, body) =>
  call(`${base()}/commands/${operation}`, body);

async function directory() {
  let records = [],
    cursor;
  do {
    const page = await call(
      "/api/platform/institutions" +
        (cursor ? "?after=" + encodeURIComponent(cursor) : ""),
    );
    records.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return records.filter((x) => x.status === "active");
}
async function accountCatalogue(root, collection = "accounts") {
  let items = [],
    after;
  do {
    const page = await call(
      root +
        "/" +
        collection +
        (after ? "?after=" + encodeURIComponent(after) : ""),
    );
    items.push(...page.items);
    after = page.next_cursor;
  } while (after);
  return items;
}
async function refresh() {
  if (!S.user) return renderAuth();
  const version = ++loadVersion,
    user = S.user.sub,
    view = S.page;
  const current = () => version === loadVersion && S.user?.sub === user;
  $("#identity").textContent = S.user.email;
  $("#logout").hidden = false;
  const institutions = await directory();
  if (!current()) return;
  if (!institutions.some((x) => x.id === S.tenant))
    S.tenant = institutions[0]?.id || null;
  $("#institution").innerHTML = institutions.length
    ? institutions
        .map(
          (x) =>
            `<option value="${h(x.id)}" ${x.id === S.tenant ? "selected" : ""}>${h(x.name)}</option>`,
        )
        .join("")
    : '<option value="">No institution yet</option>';
  $("#institution").insertAdjacentHTML(
    "beforeend",
    '<option value="new">＋ Create institution</option>',
  );
  if (!S.tenant) return renderOnboarding();
  sessionStorage.setItem("agentu.institution", S.tenant);
  const root = base(),
    page = view === "overview" ? "actions" : view;
  const [
    overview,
    accounts,
    collection,
    invitations,
    agents,
    agentKeys,
    capabilities,
  ] = await Promise.all([
    call(root + "/overview"),
    accountCatalogue(root),
    call(root + "/" + page),
    view === "members"
      ? call(root + "/invitations")
      : Promise.resolve({ items: [], next_cursor: null }),
    ["agents", "runs"].includes(view)
      ? accountCatalogue(root, "agents")
      : Promise.resolve([]),
    view === "agents"
      ? accountCatalogue(root, "agent_keys")
      : Promise.resolve([]),
    ["agents", "runs"].includes(view)
      ? call("/api/platform/capabilities")
      : Promise.resolve({}),
  ]);
  if (!current()) return;
  S.overview = overview;
  S.accounts = accounts;
  S.records = collection.items;
  S.cursor = collection.next_cursor;
  S.invitations = invitations.items;
  S.invitationCursor = invitations.next_cursor;
  S.agentCatalogue = agents;
  S.agentKeys = agentKeys;
  S.capabilities = capabilities;
  $("#mode-banner").textContent =
    `${S.config.environment} · ${S.overview.institution.mode === "sandbox" ? "Sandbox: simulated funds and internal ledger transfers." : "Live institution"} · ${S.overview.institution.status === "paused" ? "Operations paused" : "Controls active"}`;
  render();
}

function renderAuth() {
  loadVersion++;
  S.overview = null;
  S.records = [];
  S.accounts = [];
  S.invitations = [];
  S.agentCatalogue = [];
  S.agentKeys = [];
  S.capabilities = {};
  $("#identity").textContent = "Signed out";
  $("#logout").hidden = true;
  $("#institution").innerHTML =
    '<option value="">Sign in to select an institution</option>';
  $("#mode-banner").textContent =
    "Sandbox · Accounting entries use simulated funds.";
  if (S.config.mode === "unconfigured") {
    $("#content").innerHTML =
      heading(
        "Operations platform",
        "Deployment is being prepared",
        "This application needs its Agentu AWS environment before hosted sign-in is available.",
      ) +
      empty(
        "A separate business environment",
        "The local application runs independently while AWS access is configured.",
        '<a href="../demo/">View demonstration readiness</a>',
      );
    return;
  }
  const isLocal = S.config.mode === "local";
  $("#content").innerHTML =
    `<section class="intro-panel"><span class="eyebrow">YOUR INSTITUTION. YOUR CONTROLS.</span><h1>A workspace for<br>accountable operations.</h1><p class="muted">Connect the people, policies and accounts behind every decision.</p><div class="card auth-grid"><div class="auth-copy"><div class="number">→</div><h2>Every action has a mandate.</h2><p class="muted">Create an institution, invite independent reviewers and run treasury operations with a traceable ledger.</p><p class="hint">${isLocal ? "Local development uses separate user accounts on this computer. Email ownership is assumed here. Use development credentials." : "Sign in with your invited Agentu identity. Your institution controls determine what you can do."}</p></div><div>${isLocal ? `<h2>${S.authRegister ? "Create a local account" : "Welcome back"}</h2><form id="auth-form">${field("email", "Email", "email", "", "", 'autocomplete="username" maxlength="254"')}${field("password", "Password", "password", "", "At least 12 characters.", `minlength="12" maxlength="128" autocomplete="${S.authRegister ? "new-password" : "current-password"}"`)}<button class="primary" type="submit">${S.authRegister ? "Create account" : "Sign in"}</button></form><button class="quiet" data-action="auth-toggle">${S.authRegister ? "Already registered? Sign in" : "Create a development account"}</button>` : '<h2>Secure sign-in</h2><p class="muted">Continue through the Agentu identity service.</p><button class="primary" data-action="hosted-signin">Sign in to Agentu ↗</button>'}</div></div></section>`;
  $("#auth-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      button = form.querySelector("button");
    button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      const result = await call(
        "/api/platform-auth/" + (S.authRegister ? "register" : "login"),
        values,
      );
      form.reset();
      S.user = result.user;
      notice("");
      await acceptPendingInvite();
      await refresh();
    } catch (error) {
      notice(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
}
function renderOnboarding() {
  $("#content").innerHTML =
    heading(
      "Get started",
      "Create your institution",
      "Begin with an empty workspace and a controlled sandbox mandate.",
      actionButton("institution-create", "Create institution", "", "primary"),
    ) +
    `<div class="split"><section class="card"><h2>Build your operating environment</h2>${[
      [
        "Create an institution",
        "Give your team a separate workspace and reporting currency.",
      ],
      [
        "Add accounts and reviewers",
        "Invite colleagues with explicit roles. Policies and actions require independent approval.",
      ],
      [
        "Run controlled operations",
        "Choose accounts, propose a transfer and inspect its policy checks and ledger entries.",
      ],
    ]
      .map(
        ([a, b], i) =>
          `<div class="step"><b>${i + 1}</b><div><h3>${a}</h3><p class="muted">${b}</p></div></div>`,
      )
      .join(
        "",
      )}</section><section class="card accent"><span class="eyebrow">SAFE STARTING POINT</span><h2>Zero funds. Explicit authority.</h2><p>Your new institution starts with no accounts or money. Sandbox funding creates balanced accounting entries. External bank execution becomes available only after a provider integration is configured.</p></section></div>`;
}
function render() {
  document
    .querySelectorAll("[data-page]")
    .forEach((x) => x.classList.toggle("active", x.dataset.page === S.page));
  const views = {
    overview: renderOverview,
    accounts: renderAccounts,
    actions: renderActions,
    policies: renderPolicies,
    members: renderMembers,
    journal: renderJournal,
    audit: renderAudit,
    agents: agentUI.renderAgents,
    runs: agentUI.renderRuns,
  };
  $("#content").innerHTML = views[S.page]();
  if (S.cursor && !["overview", "accounts"].includes(S.page))
    $("#content").insertAdjacentHTML(
      "beforeend",
      '<button class="pagination" data-action="load-more">Load more records</button>',
    );
  if (S.page === "members" && S.invitationCursor)
    $("#content").insertAdjacentHTML(
      "beforeend",
      '<button class="pagination" data-action="invitations-more">Load more invitations</button>',
    );
}
function renderOverview() {
  const institution = S.overview.institution,
    config = S.overview.policy.config;
  const assets = S.accounts.filter((x) => x.kind === "asset");
  const totals = {};
  for (const account of assets)
    totals[account.currency] =
      (totals[account.currency] || 0) + account.balance - account.reserved;
  return (
    heading(
      "Institution overview",
      institution.name,
      "A single view of your accounts, mandates and decisions.",
      can("action_propose")
        ? actionButton("action-propose", "Propose transfer", "", "primary")
        : "",
    ) +
    `<div class="grid"><section class="card accent"><span class="eyebrow">Available ledger funds</span><div class="number">${
      Object.entries(totals)
        .map(([c, a]) => h(money(a, c)))
        .join("<br>") || money(0, institution.base_currency)
    }</div><small>After reservations · simulated funds</small></section><section class="card"><span class="eyebrow">Awaiting approval</span><div class="number">${institution.pending_count}</div><small>Independent decisions required</small></section><section class="card"><span class="eyebrow">Audit events</span><div class="number">${institution.event_sequence}</div><small>${institution.ledger_sequence} balanced journal entries</small></section></div>` +
    `<div class="split"><section class="card"><header><h2>Your accounts</h2>${can("account_create") ? actionButton("account-create", "Add account") : ""}</header>${
      assets.length
        ? assets
            .slice(0, 5)
            .map(
              (a) =>
                `<div class="list-row"><div><h3>${h(a.name)}</h3><small>${h(a.currency)} · ${h(a.status)}</small></div><div class="right"><strong>${h(money(a.balance, a.currency))}</strong><p><small>${h(money(a.reserved, a.currency))} reserved</small></p></div></div>`,
            )
            .join("")
        : empty(
            "Add your first account",
            "Use separate operating and reserve accounts to model your treasury.",
          )
    }</section><section class="card"><header><h2>Active mandate</h2>${badge("published")}</header><h3>${h(S.overview.policy.name)}</h3><div class="list-row"><span>Automatic limit</span><strong>${h(money(config.auto_limit, institution.base_currency))}</strong></div><div class="list-row"><span>Transaction limit</span><strong>${h(money(config.transaction_limit, institution.base_currency))}</strong></div><div class="list-row"><span>Daily limit</span><strong>${h(money(config.daily_limit, institution.base_currency))}</strong></div><div class="list-row"><span>Independent approvals</span><strong>${config.required_approvals}</strong></div><p class="hint">Limits apply separately to each permitted currency. Your role: ${h(S.overview.membership.role)}.</p>${can("institution_pause") ? actionButton("institution-status", institution.status === "active" ? "Pause operations" : "Resume operations", institution.status === "active" ? "paused" : "active") : ""}</section></div>`
  );
}
function renderAccounts() {
  return (
    heading(
      "Treasury",
      "Accounts",
      "Create and control internal accounts. Sandbox funding is recorded as balanced ledger entries.",
      can("account_create")
        ? actionButton("account-create", "Create account", "", "primary")
        : "",
    ) +
    (S.accounts.length
      ? table(
          [
            "Account",
            "Currency",
            "Balance",
            "Reserved",
            "Available",
            "Status",
            "",
          ],
          S.accounts.map(
            (a) =>
              `<tr><td><strong>${h(a.name)}</strong><br><small>${h(a.kind)}</small></td><td>${h(a.currency)}</td><td>${h(money(a.balance, a.currency))}</td><td>${h(money(a.reserved, a.currency))}</td><td>${h(money(a.balance - a.reserved, a.currency))}</td><td>${badge(a.status)}</td><td><div class="actions">${a.kind === "asset" && can("sandbox_fund") && a.status === "active" ? actionButton("sandbox-fund", "Fund sandbox", a.id) : ""}${a.kind === "asset" && can("account_status") ? actionButton("account-status", a.status === "active" ? "Suspend" : "Activate", a.id, "quiet") : ""}</div></td></tr>`,
          ),
        )
      : empty("No accounts yet", "Create an operating account to start."))
  );
}
function renderActions() {
  const records = S.records;
  return (
    heading(
      "Policy-controlled execution",
      "Operations",
      "Each proposal is checked against the current mandate. Pending requests reserve funds until resolved.",
      can("action_propose")
        ? actionButton("action-propose", "Propose transfer", "", "primary")
        : "",
    ) +
    (records.length
      ? `<div class="toolbar"><input id="operation-search" type="search" placeholder="Filter by purpose or account" aria-label="Filter operations"><select id="operation-status" aria-label="Filter by status"><option value="">All statuses</option>${["pending", "settled", "blocked", "cancelled", "declined", "expired"].map((x) => `<option value="${x}">${x}</option>`).join("")}</select></div><div class="list">${records.map((a) => `<article class="card operation" data-status="${h(a.status)}" data-search="${h((a.purpose + " " + a.source_name + " " + a.destination_name).toLowerCase())}"><header><div><span class="eyebrow">${h(a.kind.replaceAll("_", " "))} · ${h(date(a.created_at))}</span><h2>${h(money(a.amount, a.currency))} <small>→ ${h(a.destination_name)}</small></h2><p>${h(a.purpose)}</p><small>From ${h(a.source_name)} · ${h(a.approvals.length)} approval(s)${a.status === "pending" ? " · expires " + h(date(a.expires_at)) : ""}</small></div>${badge(a.status)}</header><details class="detail"><summary>Inspect policy checks and decision</summary>${a.checks.map((c) => `<div class="check"><span>${h(c.rule)}</span>${badge(c.result)}</div>`).join("")}<p class="mono">Action ${h(a.id)}<br>Policy ${h(a.policy_id)}</p>${a.close_reason ? `<p>${h(a.close_reason)}</p>` : ""}${a.approvals.map((x) => `<p><small>${h(x.sub)} · ${h(date(x.timestamp))}</small><br>${h(x.reason)}</p>`).join("")}</details>${a.status === "pending" ? `<div class="actions detail">${a.proposed_by !== S.user.sub && a.initiated_by !== S.user.sub && can("action_approve") ? actionButton("action-approve", "Approve", a.id, "primary") + actionButton("action-decline", "Decline", a.id, "danger") : ""}${can("action_cancel") && (a.proposed_by === S.user.sub || a.initiated_by === S.user.sub || ["owner", "administrator"].includes(S.overview.membership.role)) ? actionButton("action-cancel", "Cancel request", a.id) : ""}${a.expires_at * 1000 <= Date.now() ? actionButton("action-expire", "Release expired request", a.id) : ""}${a.proposed_by === S.user.sub || a.initiated_by === S.user.sub ? "<small>A different authorised person must approve this request.</small>" : ""}</div>` : ""}</article>`).join("")}</div>`
      : empty(
          "No operations yet",
          "Propose a transfer between two asset accounts.",
        ))
  );
}
function renderPolicies() {
  return (
    heading(
      "Operating mandate",
      "Policies",
      "Draft a version, then ask a different owner or administrator to publish it. Existing approvals are rechecked when the mandate changes.",
      can("policy_create")
        ? actionButton("policy-create", "Draft policy", "", "primary")
        : "",
    ) +
    `<div class="list">${S.records.map((p) => `<section class="card"><header><div><h2>${h(p.name)}</h2><small>${h(date(p.created_at))} · ${h(p.created_by)}</small></div>${badge(p.status)}</header><div class="grid"><div><span class="eyebrow">Automatic / transaction limit</span><h3>${h((p.config.auto_limit / 100).toLocaleString("en-GB"))} / ${h((p.config.transaction_limit / 100).toLocaleString("en-GB"))}</h3></div><div><span class="eyebrow">Daily limit / liquidity floor</span><h3>${h((p.config.daily_limit / 100).toLocaleString("en-GB"))} / ${h((p.config.liquidity_floor / 100).toLocaleString("en-GB"))}</h3></div><div><span class="eyebrow">Approvals / window</span><h3>${p.config.required_approvals} / ${p.config.approval_minutes} minutes</h3></div></div><p class="muted">Amounts apply separately in ${h(p.config.currencies.join(", "))}.</p>${p.status === "draft" && can("policy_publish") ? (p.created_by !== S.user.sub ? actionButton("policy-publish", "Publish policy", p.id, "primary") : '<p class="hint">Awaiting publication by a different owner or administrator.</p>') : ""}${jsonDetails(p, "Policy version and publication record")}</section>`).join("")}</div>`
  );
}
function renderMembers() {
  return (
    heading(
      "Institution access",
      "Team & access",
      "Invite named users and give each person the authority they need. Invitations are bound to the recipient’s verified email.",
      can("invite_create")
        ? actionButton("invite-create", "Create invitation", "", "primary")
        : "",
    ) +
    table(
      ["Member", "Role", "Status", "Joined", ""],
      S.records.map(
        (m) =>
          `<tr><td class="wrap"><strong>${h(m.email)}</strong>${m.sub === S.user.sub ? " <small>(you)</small>" : ""}</td><td>${h(m.role)}${m.kind === "agent" ? " · agent mandate" : ""}</td><td>${badge(m.status)}</td><td>${h(date(m.joined_at))}</td><td>${m.kind !== "agent" && can("member_update") && m.sub !== S.user.sub ? actionButton("member-update", "Edit access", m.sub) : ""}</td></tr>`,
      ),
    ) +
    `<section class="card detail"><h2>Invitations</h2>${S.invitations.length ? S.invitations.map((i) => `<div class="list-row"><div><strong>${h(i.email)}</strong><p><small>${h(i.role)} · expires ${h(date(i.expires_at))}</small></p></div><div class="actions">${badge(i.status)}${i.status === "pending" && can("invite_revoke") && (!["owner", "administrator"].includes(i.role) || S.overview.membership.role === "owner") ? actionButton("invite-revoke", "Revoke", i.id, "quiet") : ""}</div></div>`).join("") : '<p class="muted">No invitations created.</p>'}</section>`
  );
}
function renderJournal() {
  return (
    heading(
      "Double-entry accounting",
      "Ledger",
      "Every posting balances by currency. Entries are retained as evidence; the application provides no edit or delete operation.",
      actionButton("export-journal", "Export journal"),
    ) +
    (S.records.length
      ? `<div class="list">${S.records
          .map(
            (j) =>
              `<section class="card"><header><div><span class="eyebrow">Journal ${j.sequence} · ${h(date(j.timestamp))}</span><h3>${h(j.reason)}</h3><small>${h(j.reference)}</small></div>${badge("posted")}</header><div class="detail">${table(
                ["Account", "Currency", "Debit", "Credit"],
                j.postings.map(
                  (p) =>
                    `<tr><td>${h(S.accounts.find((a) => a.id === p.account_id)?.name || p.account_id)}</td><td>${h(p.currency)}</td><td>${p.debit ? h(money(p.debit, p.currency)) : "—"}</td><td>${p.credit ? h(money(p.credit, p.currency)) : "—"}</td></tr>`,
                ),
              )}</div></section>`,
          )
          .join("")}</div>`
      : empty(
          "No journal entries",
          "Fund a sandbox account to create your opening entries.",
        ))
  );
}
function renderAudit() {
  return (
    heading(
      "Decision evidence",
      "Audit trail",
      "A sequenced hash chain links membership, policy and financial decisions. Export a consistent snapshot for independent verification.",
      actionButton("export-audit", "Export audit trail"),
    ) +
    `<p class="hint">Current head · <span class="mono">${h(S.overview.institution.event_head)}</span></p>` +
    (S.records.length
      ? `<div class="list">${S.records.map((e) => `<section class="card"><header><div><span class="eyebrow">Event ${e.sequence} · ${h(date(e.timestamp))}</span><h3>${h(e.kind.replaceAll("_", " "))}</h3><small>Actor ${h(e.actor)}</small></div><span class="mono">${h(e.hash.slice(0, 14))}…</span></header>${jsonDetails(e, "Event evidence and chain hashes")}</section>`).join("")}</div>`
      : empty("No events", "Events appear as your institution takes action."))
  );
}

let submitForm = null,
  afterForm = null;
function openForm(
  title,
  intro,
  fields,
  callback,
  button = "Save",
  after = null,
) {
  $("#dialog-title").textContent = title;
  $("#dialog-intro").textContent = intro;
  $("#dialog-fields").innerHTML = fields;
  $("#dialog-error").textContent = "";
  $("#dialog-submit").textContent = button;
  $("#dialog-submit").hidden = false;
  submitForm = callback;
  afterForm = after;
  $("#dialog").showModal();
}
$("#close-dialog").addEventListener("click", () => $("#dialog").close());
$("#dialog").addEventListener("close", () => {
  if (!$("#dialog").open) {
    $("#dialog-fields").replaceChildren();
    submitForm = null;
    afterForm = null;
  }
});
$("#command-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!submitForm) return;
  const button = $("#dialog-submit"),
    close = $("#close-dialog");
  button.disabled = true;
  close.disabled = true;
  const after = afterForm;
  try {
    const result = await submitForm(new FormData(event.currentTarget));
    $("#dialog").close();
    notice("Saved. The institution records have been refreshed.");
    await refresh();
    if (after) after(result);
  } catch (error) {
    $("#dialog-error").textContent = error.message;
  } finally {
    button.disabled = false;
    close.disabled = false;
  }
});
$("#dialog").addEventListener("cancel", (event) => {
  if ($("#dialog-submit").disabled) event.preventDefault();
});
function minor(value) {
  if (!/^\d{1,10}(\.\d{1,2})?$/.test(value))
    throw new Error("Enter an amount with up to two decimal places.");
  const [whole, fraction = ""] = value.split(".");
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(result) || result > 100_000_000_000)
    throw new Error("Amount exceeds the supported limit.");
  return result;
}
function showInvitation(result) {
  if (!result.invite_token) {
    notice(
      "This invitation was already created. Its link is shown only once; revoke and create a new invitation if the link was lost.",
    );
    return;
  }
  const url = new URL(location.origin + location.pathname);
  url.hash = "invite=" + result.invite_token;
  openForm(
    "Invitation created",
    "Share this link directly with " +
      result.invitation.email +
      ". It expires in seven days. The link is shown once.",
    `<label class="field">Invitation link<input class="invite-link" id="invite-link" readonly value="${h(url.href)}"></label><button type="button" data-action="copy-invite">Copy invitation link</button>`,
    null,
  );
  $("#dialog-submit").hidden = true;
}
async function acceptPendingInvite() {
  const pending = sessionStorage.getItem("agentu.invite");
  if (!pending) return;
  try {
    const result = await call("/api/platform/invitations/accept", {
      token: pending,
    });
    S.tenant = result.tenant_id;
    sessionStorage.removeItem("agentu.invite");
    notice("Invitation accepted. Your institution is ready.");
  } catch (error) {
    notice(error.message, true);
    if (error.status && error.status !== 401)
      sessionStorage.removeItem("agentu.invite");
  }
}
async function exportCollection(collection) {
  const start = (await call(base() + "/overview")).institution;
  let items = [],
    after;
  do {
    const page = await call(
      base() +
        "/" +
        collection +
        (after ? "?after=" + encodeURIComponent(after) : ""),
    );
    if (page.as_of_head !== start.event_head)
      throw new Error(
        "The institution changed during export. Retry for a consistent snapshot.",
      );
    items.push(...page.items);
    after = page.next_cursor;
  } while (after);
  const output = {
    schema: "agentu.platform.export.v1",
    institution_id: S.tenant,
    collection,
    exported_at: new Date().toISOString(),
    mode: start.mode,
    as_of_sequence: start.event_sequence,
    as_of_head: start.event_head,
    journal_sequence: start.ledger_sequence,
    items,
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(output, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `agentu-${collection}-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notice(
    `Exported ${items.length} ${collection} records at event ${start.event_sequence}.`,
  );
}

async function act(action, id) {
  const form = (...args) => openForm(...args);
  if (action === "auth-toggle") {
    S.authRegister = !S.authRegister;
    return renderAuth();
  }
  if (action === "hosted-signin") return signIn(S.config);
  if (action === "copy-invite") {
    await navigator.clipboard.writeText($("#invite-link").value);
    $("#dialog-intro").textContent = "Invitation link copied.";
    return;
  }
  if (action === "institution-create")
    return form(
      "Create institution",
      "A new institution starts in sandbox mode with no accounts or funds.",
      field(
        "name",
        "Institution name",
        "text",
        "",
        "",
        'minlength="2" maxlength="120"',
      ) +
        select("currency", "Reporting currency", [
          ["GBP", "GBP · Pound sterling"],
          ["EUR", "EUR · Euro"],
          ["USD", "USD · US dollar"],
        ]),
      async (fd) => {
        const r = await call(
          "/api/platform/institutions",
          Object.fromEntries(fd),
        );
        S.tenant = r.institution.id;
        S.page = "overview";
        return r;
      },
      "Create institution",
    );
  if (!S.overview) throw new Error("Sign in and select an institution first.");
  if (action.startsWith("agent-")) return agentUI.act(action, id);
  if (action === "account-create")
    return form(
      "Create account",
      "Add an internal asset account for this institution.",
      field(
        "name",
        "Account name",
        "text",
        "",
        "",
        'minlength="2" maxlength="100"',
      ) +
        select(
          "currency",
          "Account currency",
          ["GBP", "EUR", "USD"].map((x) => [x, x]),
          S.overview.institution.base_currency,
        ),
      (fd) => command("account_create", Object.fromEntries(fd)),
      "Create account",
    );
  if (action === "sandbox-fund")
    return form(
      "Fund sandbox account",
      "This creates simulated funds and balanced opening entries. It does not move bank money.",
      field("amount", "Amount", "number", "", "", 'min="0.01" step="0.01"') +
        reason("Funding reason"),
      (fd) =>
        command("sandbox_fund", {
          account_id: id,
          amount: minor(fd.get("amount")),
          reason: fd.get("reason"),
        }),
      "Post sandbox funding",
    );
  if (action === "account-status") {
    const account = S.accounts.find((x) => x.id === id),
      status = account.status === "active" ? "suspended" : "active";
    return form(
      status === "active" ? "Activate account" : "Suspend account",
      `${account.name}: future transfers are checked against the account status.`,
      reason("Status change reason"),
      (fd) =>
        command("account_status", {
          account_id: id,
          status,
          reason: fd.get("reason"),
        }),
      "Update status",
    );
  }
  if (action === "action-propose") {
    const assets = S.accounts.filter(
      (x) => x.kind === "asset" && x.status === "active",
    );
    if (assets.length < 2)
      throw new Error(
        "Create two active asset accounts before proposing a transfer.",
      );
    const options = assets.map((x) => [
      x.id,
      `${x.name} · ${x.currency} · ${money(x.balance - x.reserved, x.currency)} available`,
    ]);
    return form(
      "Propose internal transfer",
      "The server evaluates the current policy and reserves funds if independent approval is needed.",
      select("source_id", "From account", options) +
        select("destination_id", "To account", options, options[1][0]) +
        field(
          "amount",
          "Amount in account currency",
          "number",
          "",
          "",
          'min="0.01" step="0.01"',
        ) +
        `<label class="field">Purpose<textarea name="purpose" required minlength="5" maxlength="500"></textarea></label>`,
      (fd) =>
        command("action_propose", {
          ...Object.fromEntries(fd),
          amount: minor(fd.get("amount")),
        }),
      "Submit for policy checks",
      (r) =>
        notice(
          `Transfer ${r.action.status}. Inspect its policy checks in Operations.`,
        ),
    );
  }
  if (
    [
      "action-approve",
      "action-decline",
      "action-cancel",
      "action-expire",
    ].includes(action)
  ) {
    const label = {
      "action-approve": "Approve transfer",
      "action-decline": "Decline transfer",
      "action-cancel": "Cancel transfer",
      "action-expire": "Release expired request",
    }[action];
    const operation = S.records.find((x) => x.id === id);
    return form(
      label,
      `${money(operation.amount, operation.currency)} from ${operation.source_name} to ${operation.destination_name}. Current roles, balances and policies are checked before execution.`,
      reason("Decision reason"),
      (fd) =>
        command(action.replaceAll("-", "_"), {
          action_id: id,
          reason: fd.get("reason"),
        }),
      label,
      (r) => notice(`Transfer ${r.action.status}.`),
    );
  }
  if (action === "policy-create") {
    const c = S.overview.policy.config;
    return form(
      "Draft policy",
      "All amounts use the major units of each selected currency. A different administrator must publish this draft.",
      field(
        "name",
        "Policy name",
        "text",
        "",
        "",
        'minlength="2" maxlength="120"',
      ) +
        `<div class="field-grid">${[
          ["auto_limit", "Automatic limit"],
          ["transaction_limit", "Transaction limit"],
          ["daily_limit", "Daily limit"],
          ["liquidity_floor", "Liquidity floor"],
        ]
          .map(([n, l]) =>
            field(n, l, "number", c[n] / 100, "", 'min="0" step="0.01"'),
          )
          .join(
            "",
          )}${field("required_approvals", "Independent approvals", "number", c.required_approvals, "", 'min="1" max="3" step="1"')}${field("approval_minutes", "Approval window (minutes)", "number", c.approval_minutes, "", 'min="5" max="1440" step="1"')}</div><span class="field">Permitted currencies</span><div class="checkboxes">${["GBP", "EUR", "USD"].map((x) => `<label><input type="checkbox" name="currencies" value="${x}" ${c.currencies.includes(x) ? "checked" : ""}> ${x}</label>`).join("")}</div>`,
      (fd) =>
        command("policy_create", {
          name: fd.get("name"),
          config: {
            auto_limit: minor(fd.get("auto_limit")),
            transaction_limit: minor(fd.get("transaction_limit")),
            daily_limit: minor(fd.get("daily_limit")),
            liquidity_floor: minor(fd.get("liquidity_floor")),
            required_approvals: Number(fd.get("required_approvals")),
            approval_minutes: Number(fd.get("approval_minutes")),
            currencies: fd.getAll("currencies"),
          },
        }),
      "Save draft",
    );
  }
  if (action === "policy-publish")
    return form(
      "Publish policy",
      "This version replaces the active mandate. Pending transfers are rechecked against it and earlier approvals are invalidated.",
      reason("Publication reason"),
      (fd) =>
        command("policy_publish", { policy_id: id, reason: fd.get("reason") }),
      "Publish mandate",
    );
  if (action === "institution-status")
    return form(
      id === "paused" ? "Pause institution" : "Resume institution",
      "Changes apply to all new transfers and pending approval checks.",
      reason("Status reason"),
      (fd) =>
        command("institution_pause", { status: id, reason: fd.get("reason") }),
      "Update institution",
    );
  if (action === "invite-create")
    return form(
      "Invite a team member",
      "The invitation creates access only after the recipient signs in and accepts. No email is sent automatically.",
      field("email", "Recipient email", "email", "", "", 'maxlength="254"') +
        select(
          "role",
          "Institution role",
          roles
            .filter(
              (x) =>
                S.overview.membership.role === "owner" ||
                !["owner", "administrator"].includes(x),
            )
            .map((x) => [x, x]),
          "auditor",
        ),
      (fd) => command("invite_create", Object.fromEntries(fd)),
      "Create invitation",
      showInvitation,
    );
  if (action === "invite-revoke")
    return form(
      "Revoke invitation",
      "The invitation link will stop granting access.",
      reason("Revocation reason"),
      (fd) =>
        command("invite_revoke", {
          invitation_id: id,
          reason: fd.get("reason"),
        }),
      "Revoke invitation",
    );
  if (action === "member-update") {
    const member = S.records.find((x) => x.sub === id);
    return form(
      "Update member access",
      member.email,
      select(
        "role",
        "Role",
        roles.map((x) => [x, x]),
        member.role,
      ) +
        select(
          "status",
          "Status",
          [
            ["active", "Active"],
            ["suspended", "Suspended"],
          ],
          member.status,
        ) +
        reason("Access change reason"),
      (fd) => command("member_update", { sub: id, ...Object.fromEntries(fd) }),
      "Update access",
    );
  }
  if (action === "export-audit" || action === "export-journal")
    return exportCollection(action.slice(7));
  if (action === "load-more") {
    const version = loadVersion,
      route = base() + "/" + S.page;
    const page = await call(route + "?after=" + encodeURIComponent(S.cursor));
    if (version !== loadVersion) return;
    S.records.push(...page.items);
    S.cursor = page.next_cursor;
    render();
  }
  if (action === "invitations-more") {
    const version = loadVersion;
    const page = await call(
      base() + "/invitations?after=" + encodeURIComponent(S.invitationCursor),
    );
    if (version !== loadVersion) return;
    S.invitations.push(...page.items);
    S.invitationCursor = page.next_cursor;
    render();
  }
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  button.disabled = true;
  try {
    await act(button.dataset.action, button.dataset.id);
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
  }
});
document.querySelectorAll("[data-page]").forEach((button) =>
  button.addEventListener("click", async () => {
    if (!S.user) return;
    S.page = button.dataset.page;
    notice("");
    try {
      await refresh();
    } catch (error) {
      notice(error.message, true);
    }
  }),
);
$("#institution").addEventListener("change", async (event) => {
  if (event.target.value === "new") {
    await act("institution-create");
    event.target.value = S.tenant || "";
    return;
  }
  S.tenant = event.target.value;
  S.page = "overview";
  try {
    await refresh();
  } catch (error) {
    notice(error.message, true);
  }
});
$("#refresh").addEventListener("click", async () => {
  try {
    await refresh();
    notice("Workspace refreshed.");
  } catch (error) {
    notice(error.message, true);
  }
});
$("#logout").addEventListener("click", async () => {
  try {
    if (S.config.mode === "local") {
      await call("/api/platform-auth/logout", {});
      S.user = null;
      S.overview = null;
      $("#content").innerHTML = "";
      notice("");
      renderAuth();
    } else signOut(S.config);
  } catch (error) {
    notice(error.message, true);
  }
});
function filterOperations() {
  const query = $("#operation-search")?.value.toLowerCase() || "",
    status = $("#operation-status")?.value || "";
  document
    .querySelectorAll(".operation")
    .forEach(
      (x) =>
        (x.hidden = !(
          x.dataset.search.includes(query) &&
          (!status || x.dataset.status === status)
        )),
    );
}
document.addEventListener("input", (event) => {
  if (event.target.id === "operation-search") filterOperations();
});
document.addEventListener("change", (event) => {
  if (event.target.id === "operation-status") filterOperations();
});

const agentUI = createAgentUI({
  S,
  h,
  can,
  badge,
  heading,
  empty,
  actionButton,
  field,
  select,
  reason,
  jsonDetails,
  money,
  date,
  openForm,
  minor,
  command,
  notice,
  refresh,
});
let polling = false;
setInterval(async () => {
  if (
    polling ||
    document.hidden ||
    !S.user ||
    S.page !== "runs" ||
    $("#dialog").open ||
    $("#content details[open]")
  )
    return;
  polling = true;
  try {
    await refresh();
  } catch (e) {
    notice(e.message, true);
  } finally {
    polling = false;
  }
}, 10000);

async function start() {
  const invite = new URLSearchParams(location.hash.slice(1)).get("invite");
  if (invite) {
    sessionStorage.setItem("agentu.invite", invite);
    history.replaceState({}, "", location.pathname + location.search);
  }
  S.config = await (await fetch("./config.json", { cache: "no-store" })).json();
  $("#environment").textContent = S.config.environment;
  if (S.config.mode === "unconfigured") return renderAuth();
  if (S.config.mode === "hosted") {
    await finishSignIn(S.config);
    if (!token()) return renderAuth();
  }
  try {
    S.user = (
      await call("/api/platform/me", undefined, { authProbe: true })
    ).user;
  } catch (error) {
    if (error.status === 401) return renderAuth();
    throw error;
  }
  await acceptPendingInvite();
  await refresh();
}
start().catch((error) => {
  notice(error.message, true);
  $("#content").innerHTML = empty(
    "Workspace could not be opened",
    "Reload this page after the service is available.",
  );
});
