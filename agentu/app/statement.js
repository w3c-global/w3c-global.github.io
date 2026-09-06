export function signedMinor(value) {
  const raw = String(value).trim();
  if (!/^[+-]?\d{1,10}(\.\d{1,2})?$/.test(raw))
    throw new Error(
      "Use signed decimal amounts, without separators, with at most two decimal places.",
    );
  const negative = raw.startsWith("-"),
    [whole, fraction = ""] = raw.replace(/^[+-]/, "").split(".");
  const minor = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(minor) || minor > 100_000_000_000)
    throw new Error("Amount exceeds the supported limit.");
  return negative ? -minor : minor;
}

export function parseStatement(csv) {
  if (
    typeof csv !== "string" ||
    new TextEncoder().encode(csv).length > 5_000_000
  )
    throw new Error("Use a UTF-8 CSV file of no more than 5 MB.");
  csv = csv.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [],
    field = "",
    quoted = false,
    endedQuote = false;
  const pushField = () => {
    row.push(field);
    field = "";
    endedQuote = false;
  };
  const pushRow = () => {
    pushField();
    if (row.some((x) => x.trim())) rows.push(row);
    row = [];
  };
  for (let i = 0; i < csv.length; i++) {
    const c = csv[i];
    if (quoted) {
      if (c === '"' && csv[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        quoted = false;
        endedQuote = true;
      } else field += c;
    } else if (c === ",") pushField();
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && csv[i + 1] === "\n") i++;
      pushRow();
    } else if (c === '"' && !field && !endedQuote) quoted = true;
    else if (c === '"' || endedQuote) throw new Error("Malformed CSV quoting.");
    else field += c;
  }
  if (quoted) throw new Error("A quoted CSV field was not closed.");
  if (field || row.length || endedQuote) pushRow();
  const headers = (rows.shift() || []).map((x) => x.trim().toLowerCase());
  const required = ["external_id", "booked_date", "amount"],
    allowed = [...required, "reference", "description"];
  if (
    required.some((x) => !headers.includes(x)) ||
    headers.some((x) => !allowed.includes(x)) ||
    new Set(headers).size !== headers.length
  )
    throw new Error(
      "CSV headers must include external_id, booked_date, amount; reference and description are optional.",
    );
  if (rows.length > 10000)
    throw new Error(
      "Split statements larger than 10,000 transactions into separate periods.",
    );
  const ids = new Set();
  return rows.map((values, index) => {
    if (values.length !== headers.length)
      throw new Error(`CSV row ${index + 2} has the wrong number of fields.`);
    const r = Object.fromEntries(
      headers.map((key, i) => [key, values[i].trim()]),
    );
    if (!r.external_id || r.external_id.length > 100 || ids.has(r.external_id))
      throw new Error(
        `CSV row ${index + 2} has a missing, duplicate or overlong transaction ID.`,
      );
    ids.add(r.external_id);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(r.booked_date) ||
      !Number.isFinite(Date.parse(r.booked_date)) ||
      new Date(r.booked_date).toISOString().slice(0, 10) !== r.booked_date
    )
      throw new Error(
        `CSV row ${index + 2} needs a valid YYYY-MM-DD booked_date.`,
      );
    r.amount = signedMinor(r.amount);
    if (!r.amount) throw new Error(`CSV row ${index + 2} has a zero amount.`);
    r.reference ||= "";
    r.description ||= "";
    if (r.reference.length > 100 || r.description.length > 250)
      throw new Error(
        `CSV row ${index + 2} has an overlong reference or description.`,
      );
    return r;
  });
}
