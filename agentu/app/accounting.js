import { parseStatement, signedMinor } from "./statement.js";

export function createAccountingUI(d) {
  const {
    S,
    h,
    can,
    badge,
    heading,
    empty,
    table,
    actionButton: button,
    field,
    select,
    reason,
    jsonDetails,
    money,
    date,
    openForm,
    command,
    call,
    base,
    notice,
    refresh,
  } = d;
  const finalStates = ["ready", "reconciled", "accepted_with_exceptions"];
  const current = () => S.reconciliation?.record;
  const editable = (r) =>
    ["owner", "administrator"].includes(S.overview.membership.role) ||
    r.created_by === S.user.sub;
  async function load(root, id) {
    const path = root + "/reconciliations/" + encodeURIComponent(id);
    let record = (await call(path)).reconciliation;
    if (!finalStates.includes(record.status))
      return { record, statement: { items: [] }, ledger: { items: [] } };
    const [statement, ledger] = await Promise.all([
      call(path + "/statement"),
      call(path + "/ledger"),
    ]);
    record = (await call(path)).reconciliation;
    if (
      statement.revision !== record.revision ||
      ledger.revision !== record.revision
    )
      throw new Error(
        "The comparison changed while loading. Refresh to inspect its current revision.",
      );
    return { record, statement, ledger };
  }
  function renderList() {
    return (
      heading(
        "Accounting assurance",
        "Reconciliation",
        "Most recent first. Compare a supplied statement with a fixed ledger snapshot, investigate differences and record an independent review.",
        can("reconciliation_create")
          ? button("recon-create", "Import statement", "", "primary")
          : "",
      ) +
      '<p class="hint">Statements are supplied by your team. A comparison does not authenticate the source or change ledger balances.</p>' +
      (S.records.length
        ? `<div class="list">${S.records
            .map(
              (r) =>
                `<article class="card"><header><div><span class="eyebrow">${h(r.account_name)} · ${h(r.currency)} · ${h(date(r.created_at))}</span><h2>${h(r.statement_reference)}</h2><p>${h(r.period_start)} to ${h(r.period_end)}</p></div>${badge(r.status)}</header><p class="muted">${r.uploaded_rows} / ${r.total_rows} statement rows · journals ${r.ledger_start + 1}–${r.ledger_end}${r.closing_variance !== undefined ? " · closing difference " + h(money(r.closing_variance, r.currency)) : ""}</p>${button("recon-open", "Open comparison", r.id)}</article>`,
            )
            .join("")}</div>`
        : empty(
            "No statements compared",
            "Import a CSV statement, choose the ledger cut-offs and let the worker prepare a comparison.",
          ))
    );
  }
  function renderDetail() {
    const r = current(),
      state = S.reconciliation;
    const prepared = finalStates.includes(r.status);
    const edit =
      r.status === "ready" && editable(r) && can("reconciliation_match");
    return (
      heading(
        "Statement comparison",
        r.statement_reference,
        `${r.account_name} · ${r.currency} · ${r.period_start} to ${r.period_end}`,
        button("recon-list", "All comparisons"),
      ) +
      `<div class="card"><header><div><strong>Supplied statement · ${h(r.file_name)}</strong><p class="muted">Journals after ${r.ledger_start}, up to ${r.ledger_end}. Later postings do not change this comparison.</p></div>${badge(r.status)}</header>${prepared ? `<div class="grid detail"><div><span class="eyebrow">Statement / ledger closing</span><h3>${h(money(r.closing_balance, r.currency))}<br>${h(money(r.ledger_closing, r.currency))}</h3></div><div><span class="eyebrow">Closing / opening difference</span><h3>${h(money(r.closing_variance, r.currency))}<br>${h(money(r.opening_variance, r.currency))}</h3></div><div><span class="eyebrow">Matched / unmatched</span><h3>${r.matched_rows} matched</h3><small>${r.unmatched_statement} statement · ${r.unmatched_ledger} ledger unmatched</small></div></div>` : `<p class="hint">${r.status === "uploading" ? `Uploaded ${r.uploaded_rows} of ${r.total_rows} rows. Import the same file and metadata to resume.` : r.status === "failed" ? h(r.error?.message || "Comparison failed.") : r.status === "cancelled" ? h(r.cancellation_reason) : `Comparing ${h(r.phase)} records. Processed ${r.processed_rows} of ${r.total_rows} statement rows.`}</p>`}<div class="actions detail">${prepared ? button("recon-export", "Export comparison", r.id) : ""}${r.status === "ready" && can("reconciliation_approve") && !r.contributors.includes(S.user.sub) ? button("recon-approve", "Review comparison", r.id, "primary") : ""}${r.status === "failed" && can("reconciliation_retry") && editable(r) ? button("recon-retry", "Retry comparison", r.id) : ""}${["uploading", "queued", "reconciling", "ready", "failed"].includes(r.status) && can("reconciliation_cancel") && editable(r) ? button("recon-cancel", "Cancel comparison", r.id, "danger") : ""}</div>${r.status === "ready" && r.contributors.includes(S.user.sub) ? '<p class="hint">A different owner or approver must review this comparison.</p>' : ""}${r.reviewed_at ? `<p class="hint">Reviewed ${h(date(r.reviewed_at))} · ${h(r.reviewed_by)}<br>${h(r.review_reason)}</p>` : ""}${jsonDetails(r, "Snapshot, provenance and review evidence")}</div>` +
      (prepared
        ? `<section class="card detail"><h2>Statement rows</h2>${table(
            ["Row / date", "Reference / description", "Amount", "Match", ""],
            state.statement.items.map(
              (row) =>
                `<tr><td>${row.index}<br><small>${h(row.booked_date)}</small></td><td class="wrap"><strong>${h(row.external_id)}</strong><br><small>${h(row.reference || "No ledger reference")}<br>${h(row.description)}</small></td><td>${h(money(row.amount, r.currency))}</td><td>${badge(row.match_status)}<br><small>${row.match_status === "matched" ? "Journal " + row.journal_sequence : h(row.exception?.replaceAll("_", " ") || "")}</small></td><td>${edit ? (row.match_status === "unmatched" ? button("recon-match", "Match", String(row.index)) : button("recon-unmatch", "Remove match", String(row.index), "quiet")) : ""}</td></tr>`,
            ),
          )}${state.statement.next_cursor ? button("recon-more-statement", "More statement rows") : ""}</section><section class="card detail"><h2>Ledger rows</h2>${table(
            ["Journal", "Reference / description", "Amount", "Match"],
            state.ledger.items.map(
              (row) =>
                `<tr><td>${row.sequence}<br><small>${h(date(row.timestamp))}</small></td><td class="wrap"><span class="mono">${h(row.reference)}</span><br><small>${h(row.description)}<br>Journal ID ${h(row.journal_id)}</small></td><td>${h(money(row.amount, r.currency))}</td><td>${badge(row.match_status)}${row.statement_index ? "<br><small>Statement row " + row.statement_index + "</small>" : ""}</td></tr>`,
            ),
          )}${state.ledger.next_cursor ? button("recon-more-ledger", "More ledger rows") : ""}</section>`
        : "")
    );
  }
  function render() {
    return current() ? renderDetail() : renderList();
  }
  function importForm() {
    const accounts = S.accounts.filter((a) => a.kind === "asset");
    if (!accounts.length)
      throw new Error("Create an asset account before importing a statement.");
    const today = new Date().toISOString().slice(0, 10);
    openForm(
      "Import account statement",
      "Import a UTF-8 CSV file or paste its contents. Amounts use signed currency units: -75000.00 is an outgoing GBP 75,000 transaction for a GBP account.",
      select(
        "account_id",
        "Account",
        accounts.map((a) => [a.id, `${a.name} · ${a.currency}`]),
      ) +
        select(
          "currency",
          "Statement currency",
          ["GBP", "EUR", "USD"].map((c) => [c, c]),
          accounts[0].currency,
        ) +
        field(
          "statement_reference",
          "Statement reference",
          "text",
          "",
          "",
          'minlength="2" maxlength="120"',
        ) +
        `<div class="split">${field("period_start", "Period start", "date", today.slice(0, 8) + "01")}${field("period_end", "Period end", "date", today)}</div>` +
        `<div class="split">${field("opening_balance", "Opening statement balance", "text", "0", "Signed currency units.", 'inputmode="decimal"')}${field("closing_balance", "Closing statement balance", "text", "", "Signed currency units.", 'inputmode="decimal"')}</div>` +
        `<div class="split">${field("ledger_start", "Opening journal cut-off", "number", 0, "Use 0 to compare from the beginning.", 'min="0" step="1"')}${field("ledger_end", "Closing journal cut-off", "number", S.overview.institution.ledger_sequence, "Freeze at this existing journal sequence.", `min="0" max="${S.overview.institution.ledger_sequence}" step="1"`)}</div>` +
        '<p><a href="./statement-template.txt" download="agentu-statement-template.csv">Download CSV template</a></p><label class="field">CSV file<input name="statement_file" type="file" accept=".csv,text/csv"><small>Up to 5 MB and 10,000 rows.</small></label><label class="field">Or paste CSV<textarea name="statement_csv" rows="6" placeholder="external_id,booked_date,amount,reference,description"></textarea><small>Required headers: external_id, booked_date, amount. Optional: reference, description. Use a journal ID or unique operation reference to match.</small></label>',
      async (fd) => {
        const file = fd.get("statement_file"),
          pasted = String(fd.get("statement_csv") || "").trim();
        if (file?.size && pasted)
          throw new Error("Choose a file or pasted CSV, not both.");
        if (file?.size > 5_000_000)
          throw new Error("Use a CSV file no larger than 5 MB.");
        const bytes = file?.size
          ? await file.arrayBuffer()
          : new TextEncoder().encode(pasted);
        const csv = new TextDecoder("utf-8", { fatal: true }).decode(bytes),
          rows = parseStatement(csv);
        const opening = signedMinor(fd.get("opening_balance")),
          closing = signedMinor(fd.get("closing_balance"));
        if (
          opening + rows.reduce((total, r) => total + r.amount, 0) !==
          closing
        )
          throw new Error(
            "The statement's opening balance plus its signed transactions must equal its closing balance.",
          );
        if (
          rows.some(
            (r) =>
              r.booked_date < fd.get("period_start") ||
              r.booked_date > fd.get("period_end"),
          )
        )
          throw new Error(
            "A transaction date falls outside the selected statement period.",
          );
        const hash = [
          ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
        ]
          .map((x) => x.toString(16).padStart(2, "0"))
          .join("");
        let record = (
          await command("reconciliation_create", {
            account_id: fd.get("account_id"),
            currency: fd.get("currency"),
            statement_reference: fd.get("statement_reference"),
            file_name: file?.size ? file.name : "pasted-statement.csv",
            source_sha256: hash,
            total_rows: rows.length,
            period_start: fd.get("period_start"),
            period_end: fd.get("period_end"),
            opening_balance: opening,
            closing_balance: closing,
            ledger_start: Number(fd.get("ledger_start")),
            ledger_end: Number(fd.get("ledger_end")),
          })
        ).reconciliation;
        if (record.status === "uploading") {
          for (
            let offset = record.uploaded_rows;
            offset < rows.length;
            offset += 20
          ) {
            document.querySelector("#dialog-submit").textContent =
              `Uploading ${offset + 1}–${Math.min(offset + 20, rows.length)} of ${rows.length}`;
            record = (
              await command("reconciliation_append", {
                reconciliation_id: record.id,
                offset,
                rows: rows.slice(offset, offset + 20),
              })
            ).reconciliation;
          }
          record = (
            await command("reconciliation_finish", {
              reconciliation_id: record.id,
            })
          ).reconciliation;
        }
        S.reconciliationId = record.id;
        return { reconciliation: record };
      },
      "Import and compare",
    );
  }
  async function exportReport() {
    const id = current().id,
      root = base() + "/reconciliations/" + id;
    const record = (await call(root)).reconciliation;
    if (!finalStates.includes(record.status))
      throw new Error("Wait for a completed comparison before exporting.");
    const result = {
      schema: "agentu.reconciliation.export.v1",
      institution_id: S.tenant,
      mode: S.overview.institution.mode,
      report: record,
    };
    for (const kind of ["statement", "ledger"]) {
      result[kind] = [];
      let cursor;
      do {
        const page = await call(
          root +
            "/" +
            kind +
            (cursor ? "?after=" + encodeURIComponent(cursor) : ""),
        );
        if (page.revision !== record.revision)
          throw new Error(
            "The comparison changed during export. Retry its current revision.",
          );
        result[kind].push(...page.items);
        cursor = page.next_cursor;
      } while (cursor);
    }
    const latest = (await call(root)).reconciliation;
    if (latest.revision !== record.revision || latest.status !== record.status)
      throw new Error(
        "The review changed during export. Retry its current revision.",
      );
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `agentu-reconciliation-${id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    notice(
      `Export prepared: ${result.statement.length} statement rows and ${result.ledger.length} ledger rows.`,
    );
  }
  async function act(action, id) {
    if (action === "recon-create") return importForm();
    if (action === "recon-open" || action === "recon-list") {
      S.reconciliationId = action === "recon-open" ? id : null;
      await refresh();
      return;
    }
    const r = current();
    if (!r) throw new Error("Open a reconciliation first.");
    const common = { reconciliation_id: r.id, revision: r.revision };
    if (action === "recon-export") return exportReport();
    if (action === "recon-approve")
      return openForm(
        "Review reconciliation",
        `${r.unmatched_statement} statement rows and ${r.unmatched_ledger} ledger rows unmatched. Closing difference ${money(r.closing_variance, r.currency)}; opening difference ${money(r.opening_variance, r.currency)}. Review does not post an adjustment.`,
        reason("Review evidence") +
          '<label class="choice"><input type="checkbox" name="accept_exceptions"> Accept the remaining differences and unmatched rows explicitly</label>',
        (fd) =>
          command("reconciliation_approve", {
            ...common,
            reason: fd.get("reason"),
            accept_exceptions: fd.has("accept_exceptions"),
          }),
        "Record independent review",
      );
    if (action === "recon-match")
      return openForm(
        "Match statement row",
        `Statement row ${id}. Select an unmatched ledger journal with exactly the same signed amount. Both records remain visible.`,
        field(
          "journal_sequence",
          "Ledger journal sequence",
          "number",
          "",
          "Inspect the ledger rows in this comparison.",
          'min="1" step="1"',
        ) + reason("Matching evidence"),
        (fd) =>
          command("reconciliation_match", {
            ...common,
            statement_index: Number(id),
            journal_sequence: Number(fd.get("journal_sequence")),
            reason: fd.get("reason"),
          }),
        "Record match",
      );
    if (action === "recon-unmatch")
      return openForm(
        "Remove match",
        "Reopen the statement and ledger rows for investigation. This records an audit event.",
        reason("Unmatching evidence"),
        (fd) =>
          command("reconciliation_unmatch", {
            ...common,
            statement_index: Number(id),
            reason: fd.get("reason"),
          }),
        "Remove match",
      );
    if (action === "recon-cancel")
      return openForm(
        "Cancel comparison",
        "Imported evidence is retained. A late worker result cannot resume this comparison.",
        reason("Cancellation reason"),
        (fd) =>
          command("reconciliation_cancel", {
            ...common,
            reason: fd.get("reason"),
          }),
        "Cancel comparison",
      );
    if (action === "recon-retry") {
      await command("reconciliation_retry", common);
      await refresh();
      return;
    }
    if (action.startsWith("recon-more-")) {
      const kind = action.slice(11),
        state = S.reconciliation[kind],
        page = await call(
          base() +
            "/reconciliations/" +
            r.id +
            "/" +
            kind +
            "?after=" +
            encodeURIComponent(state.next_cursor),
        );
      if (S.reconciliationId !== r.id || current()?.revision !== r.revision)
        return;
      if (page.revision !== r.revision) return refresh();
      state.items.push(...page.items);
      state.next_cursor = page.next_cursor;
      d.render();
    }
  }
  return { load, render, act };
}
