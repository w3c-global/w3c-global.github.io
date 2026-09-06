export function createAgentUI(d) {
  const {
    S,
    h,
    can,
    badge,
    heading,
    empty,
    actionButton: button,
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
  } = d;
  const providers = {
    treasury_rule: "Treasury rule",
    bedrock: "Amazon Bedrock",
    external: "External agent",
  };
  const name = (id) => S.accounts.find((a) => a.id === id)?.name || id;
  const eligible = () =>
    S.agentCatalogue.filter(
      (a) =>
        a.status === "active" &&
        a.config.provider !== "external" &&
        (a.config.provider !== "bedrock" ||
          S.capabilities?.bedrock?.configured),
    );
  const inspect = (id) => S.agentCatalogue.find((a) => a.id === id);
  const manager = () =>
    ["owner", "administrator"].includes(S.overview.membership.role);
  function credentials(a) {
    const keys = S.agentKeys.filter((k) => k.agent_id === a.id);
    return `<details class="detail"><summary>Credentials (${keys.length})</summary><p class="muted">Credentials are shown once, expire automatically and grant proposal access within this mandate. Issuing a new mandate invalidates earlier credentials.</p>${
      keys
        .map((k) => {
          const status =
            k.status === "active"
              ? k.expires_at * 1000 <= Date.now()
                ? "expired"
                : k.revision !== a.revision
                  ? "superseded"
                  : "active"
              : k.status;
          return `<div class="list-row"><div><strong>${h(k.label)}</strong><p><small>Expires ${h(date(k.expires_at))}</small></p></div><div class="actions">${badge(status)}${status === "active" && can("agent_key_revoke") ? button("agent-key-revoke", "Revoke", k.id, "danger") : ""}</div></div>`;
        })
        .join("") || '<p class="muted">No credentials issued.</p>'
    }</details>`;
  }
  function renderAgents() {
    const enabled = S.capabilities?.bedrock?.configured;
    return (
      heading(
        "Governed automation",
        "Agents & mandates",
        "Give each agent a purpose, a bounded account scope and independently approved authority.",
        can("agent_create")
          ? button("agent-create", "Create agent", "", "primary")
          : "",
      ) +
      `<div class="card capability-strip"><div><strong>Treasury rule</strong><p class="muted">Deterministic reserve rebalancing. No language model.</p></div><div><strong>Amazon Bedrock</strong><p class="muted">${enabled ? "Model configured. Inspect completed run evidence to verify execution." : "Model connection pending in the business AWS account."}</p></div><div><strong>External agents</strong><p class="muted">Scoped, expiring credentials for the proposal API.</p></div></div>` +
      (S.records.length
        ? `<div class="list">${S.records.map((a) => `<article class="card agent-card"><header><div><span class="eyebrow">${h(providers[a.config.provider])}</span><h2>${h(a.name)}</h2><p>${h(a.config.objective)}</p></div>${badge(a.status)}</header><div class="grid detail"><div><span class="eyebrow">From → To</span><p>${h(a.config.source_ids.map(name).join(", "))}<br>→ ${h(a.config.destination_ids.map(name).join(", "))}</p></div><div><span class="eyebrow">Transaction / daily limit</span><h3>${h((a.config.transaction_limit / 100).toLocaleString("en-GB"))} / ${h((a.config.daily_limit / 100).toLocaleString("en-GB"))}</h3><small>Separately in ${h(a.config.currencies.join(", "))}</small></div><div><span class="eyebrow">Review / run budget</span><h3>${a.config.require_review ? "Always review" : "Institution policy"}</h3><small>Up to ${a.config.daily_runs} runs per UTC day</small></div></div>${a.config.provider === "treasury_rule" ? `<p class="hint">Maintain a source floor of ${h(money(a.config.source_floor, S.accounts.find((x) => x.id === a.config.source_ids[0])?.currency))} and a reserve target of ${h(money(a.config.destination_target, S.accounts.find((x) => x.id === a.config.destination_ids[0])?.currency))}.</p>` : ""}<div class="actions">${a.status === "draft" && can("agent_publish") && a.created_by !== S.user.sub ? button("agent-publish", "Publish mandate", a.id, "primary") : ""}${a.status === "active" && can("agent_run") && eligible().some((x) => x.id === a.id) ? button("agent-run", "Run agent", a.id, "primary") : ""}${can("agent_revision") ? button("agent-revision", "Draft revision", a.id) : ""}${a.status !== "suspended" && can("agent_suspend") ? button("agent-suspend", "Suspend", a.id, "danger") : ""}${a.status === "active" && a.config.provider === "external" && can("agent_key_create") ? button("agent-key-create", "Issue credential", a.id) : ""}</div>${a.status === "draft" && a.created_by === S.user.sub ? '<p class="hint">A different owner or administrator must publish this mandate.</p>' : ""}${a.config.provider === "external" ? credentials(a) : ""}${jsonDetails(a, "Mandate version and publication record")}</article>`).join("")}</div>`
        : empty(
            "No agents yet",
            "Create a mandate, then have a different administrator publish it.",
          ))
    );
  }
  function renderRuns() {
    return (
      heading(
        "Execution history",
        "Agent runs",
        "Follow each request from its account snapshot through the decision and resulting operation.",
        can("agent_run") && eligible().length
          ? button("agent-run", "Start run", "", "primary")
          : "",
      ) +
      '<p class="hint">Active runs refresh automatically. Runs can propose an internal transfer or take no action. The institution policy and agent mandate govern every proposal.</p>' +
      (S.records.length
        ? `<div class="list">${[...S.records]
            .sort((a, b) => b.created_at.localeCompare(a.created_at))
            .map(
              (r) =>
                `<article class="card"><header><div><span class="eyebrow">${h(providers[r.provider])} · ${h(date(r.created_at))}</span><h2>${h(r.agent_name)}</h2><p>${h(r.instruction || r.output?.purpose || "External proposal")}</p></div>${badge(r.status)}</header>${r.output ? `<div class="detail"><strong>${r.output.action === "no_action" ? "No transfer needed" : r.output.amount ? h(money(r.output.amount, S.accounts.find((a) => a.id === r.output.source_id)?.currency)) + " → " + h(name(r.output.destination_id)) : "Decision recorded"}</strong><p>${h(r.output.reason || r.output.purpose || "")}</p>${r.decision && r.decision !== "no_action" ? `<small>Decision when proposed: </small>${badge(r.decision)}` : ""}</div>` : ""}${r.error ? `<p class="notice error">${h(r.error.message)}</p>` : ""}<div class="actions detail">${r.action_id ? button("agent-operation", "Open operations", r.action_id) : ""}${["queued", "running"].includes(r.status) && can("agent_run_cancel") && (r.requested_by === S.user.sub || manager()) ? button("agent-run-cancel", "Cancel run", r.id, "danger") : ""}</div>${jsonDetails(r, "Run evidence and identifiers")}</article>`,
            )
            .join("")}</div>`
        : empty(
            "No runs yet",
            "Publish an agent mandate to begin. External agents submit through the proposal API.",
          ))
    );
  }
  function scope(name, label, selected) {
    return `<label class="field">${h(label)}<select name="${name}" multiple size="4" required>${S.accounts
      .filter((a) => a.kind === "asset" && a.status === "active")
      .map(
        (a) =>
          `<option value="${h(a.id)}" ${selected.includes(a.id) ? "selected" : ""}>${h(a.name)} · ${h(a.currency)}</option>`,
      )
      .join(
        "",
      )}</select><small>Select up to ten. A treasury rule uses one account on each side.</small></label>`;
  }
  function edit(agent) {
    if (
      S.accounts.filter((a) => a.kind === "asset" && a.status === "active")
        .length < 2
    )
      throw new Error(
        "Create at least two active asset accounts before drafting an agent.",
      );
    const config = agent?.config || {
      provider: "treasury_rule",
      objective:
        "Maintain a treasury reserve while preserving operating liquidity.",
      transaction_limit: 7500000,
      daily_limit: 25000000,
      require_review: true,
      daily_runs: 20,
      source_ids: [],
      destination_ids: [],
      currencies: [S.overview.institution.base_currency],
      source_floor: 100000000,
      destination_target: 15000000,
    };
    const inputMoney = (key, label) =>
      field(
        key,
        label,
        "text",
        (config[key] || 0) / 100,
        "In currency units, up to two decimal places.",
        'inputmode="decimal"',
      );
    openForm(
      agent ? "Draft agent revision" : "Create agent mandate",
      agent
        ? "A new draft suspends the current mandate immediately. A different administrator must publish it; old credentials become invalid."
        : "Define explicit authority. A different owner or administrator must publish this draft.",
      (!agent
        ? field(
            "name",
            "Agent name",
            "text",
            "",
            "",
            'minlength="2" maxlength="100"',
          )
        : "") +
        select(
          "provider",
          "Decision provider",
          Object.entries(providers),
          config.provider,
        ) +
        `<label class="field">Objective<textarea name="objective" required minlength="10" maxlength="1000">${h(config.objective)}</textarea></label>` +
        `<div class="split">${scope("source_ids", "Source accounts", config.source_ids)}${scope("destination_ids", "Destination accounts", config.destination_ids)}</div>` +
        `<fieldset class="choice-group"><legend>Permitted currencies</legend>${["GBP", "EUR", "USD"].map((c) => `<label><input type="checkbox" name="currencies" value="${c}" ${config.currencies.includes(c) ? "checked" : ""}> ${c}</label>`).join("")}</fieldset>` +
        `<div class="split">${inputMoney("transaction_limit", "Transaction limit")}${inputMoney("daily_limit", "Daily limit")}</div>` +
        select(
          "require_review",
          "Independent review",
          [
            ["true", "Always require an independent reviewer"],
            ["false", "Use institution policy approval thresholds"],
          ],
          String(config.require_review),
        ) +
        field(
          "daily_runs",
          "Daily run budget",
          "number",
          config.daily_runs,
          "Counts requests, including no-action and blocked decisions, per UTC day.",
          'min="1" max="1000" step="1"',
        ) +
        `<fieldset id="rule-fields"><legend>Reserve rule</legend><div class="split">${inputMoney("source_floor", "Source balance floor")}${inputMoney("destination_target", "Destination target")}</div></fieldset>`,
      (fd) => {
        const c = {
          provider: fd.get("provider"),
          objective: fd.get("objective"),
          source_ids: fd.getAll("source_ids"),
          destination_ids: fd.getAll("destination_ids"),
          currencies: fd.getAll("currencies"),
          transaction_limit: minor(fd.get("transaction_limit")),
          daily_limit: minor(fd.get("daily_limit")),
          require_review: fd.get("require_review") === "true",
          daily_runs: Number(fd.get("daily_runs")),
        };
        if (c.provider === "treasury_rule")
          Object.assign(c, {
            source_floor: minor(fd.get("source_floor")),
            destination_target: minor(fd.get("destination_target")),
          });
        return command(agent ? "agent_revision" : "agent_create", {
          ...(agent ? { agent_id: agent.id } : { name: fd.get("name") }),
          config: c,
        });
      },
      "Save draft",
    );
    const provider = document.querySelector('[name="provider"]'),
      rule = document.querySelector("#rule-fields");
    const toggle = () => {
      rule.hidden = provider.value !== "treasury_rule";
      rule.disabled = rule.hidden;
    };
    provider.addEventListener("change", toggle);
    toggle();
  }
  function showCredential(result) {
    if (!result.agent_token)
      return notice(
        "This credential was already issued. Revoke it and issue a new one if its value was lost.",
      );
    openForm(
      "Agent credential issued",
      "Copy this credential into your agent's secret store. It is shown once and must be sent only to this Agentu environment.",
      `<label class="field">Credential<input id="agent-token" class="invite-link" type="password" autocomplete="off" readonly value="${h(result.agent_token)}"></label><button type="button" data-action="agent-copy-token">Copy credential</button><p class="hint">POST /api/agent/proposals · Authorization: Bearer &lt;credential&gt;</p>`,
      null,
    );
    document.querySelector("#dialog-submit").hidden = true;
  }
  async function act(action, id) {
    if (action === "agent-copy-token") {
      await navigator.clipboard.writeText(
        document.querySelector("#agent-token").value,
      );
      document.querySelector("#dialog-intro").textContent =
        "Credential copied. Store it securely; it is shown only once.";
      return;
    }
    if (action === "agent-create") return edit();
    if (action === "agent-revision") return edit(inspect(id));
    if (action === "agent-publish" || action === "agent-suspend") {
      const publish = action === "agent-publish";
      return openForm(
        publish ? "Publish agent mandate" : "Suspend agent",
        inspect(id).name,
        reason(publish ? "Publication reason" : "Suspension reason"),
        (fd) =>
          command(publish ? "agent_publish" : "agent_suspend", {
            agent_id: id,
            reason: fd.get("reason"),
          }),
        publish ? "Publish mandate" : "Suspend agent",
      );
    }
    if (action === "agent-key-create")
      return openForm(
        "Issue agent credential",
        inspect(id).name,
        field(
          "label",
          "Credential label",
          "text",
          "",
          "",
          'minlength="2" maxlength="100"',
        ) +
          field(
            "days",
            "Lifetime in days",
            "number",
            7,
            "Maximum 90 days.",
            'min="1" max="90" step="1"',
          ),
        (fd) =>
          command("agent_key_create", {
            agent_id: id,
            label: fd.get("label"),
            days: Number(fd.get("days")),
          }),
        "Issue credential",
        showCredential,
      );
    if (action === "agent-key-revoke") {
      const key = S.agentKeys.find((k) => k.id === id);
      return openForm(
        "Revoke credential",
        key.label,
        reason("Revocation reason"),
        (fd) =>
          command("agent_key_revoke", {
            agent_id: key.agent_id,
            key_id: id,
            reason: fd.get("reason"),
          }),
        "Revoke credential",
      );
    }
    if (action === "agent-run") {
      const choices = eligible();
      if (!choices.length)
        throw new Error(
          "Publish a configured agent mandate before requesting a run.",
        );
      const selected = inspect(id) || choices[0];
      openForm(
        "Start agent run",
        "The worker reads current scoped accounts, records its decision and applies all current controls. The person requesting this run cannot approve its resulting transfer.",
        select(
          "agent_id",
          "Agent",
          choices.map((a) => [a.id, a.name]),
          selected.id,
        ) +
          `<label class="field">Run instruction<textarea name="instruction" minlength="10" maxlength="1000" required>${h(selected.config.objective)}</textarea></label>`,
        (fd) => command("agent_run", Object.fromEntries(fd)),
        "Start run",
        async () => {
          S.page = "runs";
          try {
            await refresh();
          } catch (e) {
            notice(e.message, true);
          }
        },
      );
      document
        .querySelector('[name="agent_id"]')
        .addEventListener("change", (e) => {
          document.querySelector('[name="instruction"]').value = inspect(
            e.target.value,
          ).config.objective;
        });
      return;
    }
    if (action === "agent-run-cancel")
      return openForm(
        "Cancel agent run",
        "A late worker result will be ignored. Completed operations must be managed separately.",
        reason("Cancellation reason"),
        (fd) =>
          command("agent_run_cancel", { run_id: id, reason: fd.get("reason") }),
        "Cancel run",
      );
    if (action === "agent-operation") {
      S.page = "actions";
      await refresh();
    }
  }
  return { renderAgents, renderRuns, act };
}
