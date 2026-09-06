import assert from "node:assert/strict";
import { parseStatement, signedMinor } from "../app/statement.js";

const header = "external_id,booked_date,amount,reference,description\r\n";
const rows = parseStatement(
  "\uFEFF" +
    header +
    'b1,2026-09-06,-75000.01,action1,"Treasury, reserve"\r\nb2,2026-09-07,+12.5,,"Two ""quoted""\nlines"\r\n',
);
assert.equal(rows[0].amount, -7500001);
assert.equal(rows[1].amount, 1250);
assert.equal(rows[0].description, "Treasury, reserve");
assert.equal(rows[1].description, 'Two "quoted"\nlines');
assert.equal(signedMinor("-0.01"), -1);
assert.deepEqual(parseStatement("external_id,booked_date,amount\n"), []);
for (const value of ["1e3", "1,000", "1.005", "Infinity", "10000000000"])
  assert.throws(() => signedMinor(value));
for (const text of [
  "b1,2026-09-06,1,r,one\nb1,2026-09-06,1,r,two",
  "b1,2026-02-30,1,r,impossible",
  "b1,2026-09-06,0,r,zero",
  'b1,2026-09-06,1,r,"unterminated',
  'b1,2026-09-06,1,r,"quoted"suffix',
  "b1,2026-09-06,1,r,extra,column",
])
  assert.throws(() => parseStatement(header + text));
assert.throws(() => parseStatement("external_id,external_id,amount\nb1,b1,1"));
console.log(
  "Statement parser: quoting, dates, duplicate IDs, exact money and malformed input verified.",
);
