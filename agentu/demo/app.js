import { signIn, finishSignIn, token, workspace, signOut } from "./auth.js";
const $ = (selector) => document.querySelector(selector),
  esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
const money = (pence) =>
  new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    minimumFractionDigits: 2,
  }).format(pence / 100);
const time = (value) =>
  new Date(value).toLocaleTimeString("en-GB", {
    timeZone: "UTC",
    hour12: false,
  });
const presets = { sweep: 7500000, blocked: 2500000, approval: 17500000 };
const outcomes = {
  executed: ["Executed", "success"],
  blocked: ["Blocked", "danger"],
  pending: ["Awaiting approval", "warning"],
  declined: ["Declined", "neutral"],
};
const eventNames = {
  request_received: "Request received",
  policy_evaluated: "Policy evaluated",
  simulated_transfer: "Simulated transfer recorded",
  operator_approve: "Operator approval recorded",
  operator_decline: "Operator decline recorded",
  approval_rechecked: "Controls rechecked at approval",
  execution_blocked: "Execution blocked after recheck",
};
let config,
  state,
  selectedId = null,
  busy = false,
  scenario = "sweep";
function notice(message, error = false) {
  $("#notice").textContent = message;
  $("#notice").classList.toggle("error", error);
}
function setBusy(value) {
  busy = value;
  document
    .querySelectorAll("#connected button, #new-session, #export")
    .forEach((b) => (b.disabled = value));
  $("#run").textContent = value ? "Evaluating…" : "Run policy checks →";
}
async function request(path, body) {
  const headers = { "X-Agentu-Workspace": workspace() };
  if (config.mode === "hosted") {
    const access = token();
    if (!access) {
      $("#auth-panel").hidden = false;
      $("#connected").hidden = true;
      throw new Error("Your session has expired. Sign in again to continue.");
    }
    headers.Authorization = "Bearer " + access;
  }
  if (body) headers["Content-Type"] = "application/json";
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(config.apiBase + "/api/" + path, {
      method: body ? "POST" : "GET",
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      cache: "no-store",
    });
    const data = await response
      .json()
      .catch(() => ({ error: "The demonstration service is unavailable." }));
    if (!response.ok)
      throw new Error(data.error || "Request failed. Please try again.");
    return data;
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error(
        "The service took too long to respond. Refresh the rehearsal before retrying; it may have completed.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
function choose(value) {
  if (!presets[value]) return;
  scenario = value;
  document
    .querySelectorAll("[data-scenario]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.scenario === value)),
    );
  $("#amount").value = (presets[value] / 100).toFixed(2);
  $("#destination").textContent =
    value === "blocked"
      ? "Unapproved external beneficiary"
      : "Treasury reserve";
}
function badge(status) {
  const [label, tone] = outcomes[status] || [status, "neutral"];
  return `<span class="badge ${tone}">${esc(label)}</span>`;
}
function decision(action) {
  if (!action) return;
  const [label, tone] = outcomes[action.status];
  $("#decision-status").className = "badge " + tone;
  $("#decision-status").textContent = label;
  const messages = {
    executed:
      "The simulated transfer is complete. Both account balances and the execution record were updated together.",
    blocked: "No funds moved. The request failed a hard policy check.",
    pending:
      "No funds have moved. An operator must record a decision before this action can proceed.",
    declined: "The operator declined this request. No funds moved.",
  };
  const checks = action.approval_checks || action.checks;
  $("#decision-content").innerHTML =
    `<div class="decision-detail"><div class="decision-summary"><div><span class="eyebrow">${esc(action.policy_version)}</span><h3>${esc(action.name)}</h3><p>${esc(action.purpose)}</p></div><span class="decision-amount">${money(action.amount)}</span></div><ul class="rule-list">${checks.map((c) => `<li><span class="rule-indicator ${esc(c.result)}">${c.result === "pass" ? "✓" : c.result === "fail" ? "×" : "!"}</span><div><strong>${esc(c.rule)}</strong><p>${esc(c.detail)}</p></div></li>`).join("")}</ul><div class="decision-result ${esc(action.status)}">${messages[action.status]}</div>${action.status === "pending" ? `<form class="approval-form" id="approval-form"><label>Reason for your decision<textarea id="approval-note" rows="2" required minlength="5" maxlength="500" placeholder="For example: Reserve increase reviewed for this demo."></textarea></label><div class="actions"><button class="button small" name="decision" value="approve">Approve simulated transfer</button><button class="button secondary small" name="decision" value="decline">Decline</button></div><p class="form-note">You are acting as the demo operator. Production separation of duties is a future control.</p></form>` : ""}${action.review ? `<p class="form-note" style="margin-top:1rem">Operator reason: ${esc(action.review.note)}</p>` : ""}<p class="action-id">Action ${esc(action.id)}</p></div>`;
  $("#approval-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const operation = event.submitter?.value;
    if (!["approve", "decline"].includes(operation)) return;
    await mutate({
      operation,
      action_id: action.id,
      note: $("#approval-note").value,
    });
  });
}
function render() {
  if (!state) return;
  $("#connected").hidden = false;
  $("#auth-panel").hidden = true;
  $("#operating-balance").textContent = money(state.accounts.operating.balance);
  $("#reserve-balance").textContent = money(state.accounts.reserve.balance);
  $("#action-count").textContent =
    `${state.actions.length} action${state.actions.length === 1 ? "" : "s"}`;
  $("#activity").innerHTML = state.actions.length
    ? [...state.actions]
        .reverse()
        .map(
          (a) =>
            `<tr><td class="mono">${time(a.created_at)}</td><td>${esc(a.name)}</td><td class="amount">${money(a.amount)}</td><td>${badge(a.status)}</td><td><button class="inspect" data-action="${esc(a.id)}" aria-label="Inspect ${esc(a.name)} at ${time(a.created_at)}">Inspect →</button></td></tr>`,
        )
        .join("")
    : '<tr><td colspan="5" class="empty-row">No actions yet. Choose a scenario to begin.</td></tr>';
  const events = state.events;
  $("#audit-events").innerHTML = events.length
    ? [...events]
        .reverse()
        .map(
          (e) =>
            `<div class="audit-event"><span class="sequence">${e.sequence.toString().padStart(2, "0")}</span><time datetime="${esc(e.timestamp)}">${time(e.timestamp)} UTC</time><div><strong>${esc(eventNames[e.kind] || e.kind)}</strong><code>SHA-256 ${esc(e.hash)}</code></div></div>`,
        )
        .join("")
    : '<p class="muted">Your first request will start this rehearsal’s record chain.</p>';
  $("#integrity").textContent = events.length
    ? `${state.integrity.valid ? "✓ Chain consistent" : "× Verification failed"} · ${events.length} records · head ${state.integrity.head.slice(0, 16)}…`
    : "No records to verify yet.";
  const chosen =
    state.actions.find((a) => a.id === selectedId) || state.actions.at(-1);
  if (chosen) {
    selectedId = chosen.id;
    decision(chosen);
  }
  $("#new-session").disabled = false;
  $("#export").disabled = false;
}
async function mutate(payload) {
  setBusy(true);
  notice("Checking the request and recording the decision…");
  try {
    state = await request("actions", {
      ...payload,
      request_id: crypto.randomUUID(),
    });
    if (payload.operation === "propose") selectedId = state.actions.at(-1)?.id;
    render();
    notice(
      "Decision recorded. Inspect the checks, balances and audit trail below.",
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
}
document
  .querySelectorAll("[data-scenario]")
  .forEach((b) =>
    b.addEventListener("click", () => choose(b.dataset.scenario)),
  );
$("#propose-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const value = $("#amount").value.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(value)) {
    notice("Enter an amount in pounds with up to two decimal places.", true);
    return;
  }
  const amount = Math.round(Number(value) * 100);
  if (!Number.isSafeInteger(amount) || amount <= 0 || amount > 100000000) {
    notice("Enter an amount between £0.01 and £1,000,000.", true);
    return;
  }
  await mutate({ operation: "propose", scenario, amount });
});
$("#activity").addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (button) {
    selectedId = button.dataset.action;
    decision(state.actions.find((a) => a.id === selectedId));
  }
});
$("#new-session").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  workspace(true);
  selectedId = null;
  try {
    state = await request("state");
    location.reload();
  } catch (error) {
    notice(error.message, true);
    setBusy(false);
  }
});
$("#export").addEventListener("click", async () => {
  setBusy(true);
  try {
    const data = await request("export");
    const blob = new Blob(
      [
        JSON.stringify(
          {
            format: "agentu-demo-audit-v1",
            exported_at: new Date().toISOString(),
            ...data,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download =
      "agentu-demo-audit-" + new Date().toISOString().slice(0, 10) + ".json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notice(
      "Audit export downloaded. It includes requests, decisions, balances and the record chain.",
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});
$("#verify").addEventListener("click", async () => {
  setBusy(true);
  try {
    state = await request("export");
    render();
    notice(
      state.integrity.valid
        ? `Verified ${state.integrity.count} hash-linked records. The chain is internally consistent.`
        : "Verification failed. Inspect the exported record.",
      !state.integrity.valid,
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});
$("#sign-in").addEventListener("click", () =>
  signIn(config).catch((error) => notice(error.message, true)),
);
$("#sign-out").addEventListener("click", () => signOut(config));
async function init() {
  try {
    config = await fetch("config.json", { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error("Demo configuration is unavailable.");
      return r.json();
    });
    $("#environment").textContent = config.environment || "Founder demo";
    if (config.mode === "unconfigured") {
      notice(
        "The hosted demonstration has not been connected yet. The local rehearsal is available from the project launcher.",
        true,
      );
      $("#auth-panel").hidden = false;
      $("#auth-panel h2").textContent = "Demonstration setup in progress.";
      $("#auth-panel>p").textContent =
        "The website is available. A backend environment must be connected before you can run these scenarios here.";
      $("#sign-in").hidden = true;
      return;
    }
    if (config.mode === "hosted") {
      await finishSignIn(config);
      $("#sign-out").hidden = !token();
      if (!token()) {
        $("#auth-panel").hidden = false;
        notice("Sign in to open your own demonstration rehearsal.");
        return;
      }
    } else if (config.mode !== "local") {
      throw new Error("Unknown demo configuration.");
    }
    choose(new URLSearchParams(location.search).get("scenario") || "sweep");
    state = await request("state");
    render();
    notice(
      config.mode === "local"
        ? "Local rehearsal · data stays on this computer. No internet connection is required for the policy engine."
        : "Hosted rehearsal · your demo state is saved in the dedicated AWS environment.",
    );
  } catch (error) {
    notice(error.message || "Unable to connect to the demo service.", true);
  }
}
init();
