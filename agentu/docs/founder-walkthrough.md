# Founder walkthrough — Tuesday 8 September 2026

Allow 10–12 minutes. Start a new rehearsal before the meeting. Keep the local launcher available if venue internet fails.

## 1. Position the venture — 2 minutes

Open the Agentu website. Lead with: “Agentu is the control layer we want between an AI request and a financial action. The agent proposes; the mandate determines what can happen; the record explains the outcome.”

Explain the stage plainly: the company has not yet been incorporated. This is working demonstration software, with fictional accounts and simulated funds. Banking partnerships, commercial network terms and real financial operations are future work.

Show the four steps: propose, check, decide, record. The shared-network section is the ambition, not a claim of an existing network of banks.

## 2. An allowed treasury sweep — 2 minutes

Open the control room. Initial balances: £2.5m operating and £900k reserve. The mandate allows automatic execution up to £100k, with a £500k hard maximum and £1m operating liquidity floor.

Select **Treasury sweep** and run the checks at £75,000. Show all four passing controls, the executed result and updated balances: £2.425m operating / £975k reserve. Inspect the three audit records: request, policy decision and simulated transfer.

## 3. A blocked payment — 1 minute

Select **Unknown beneficiary** and run the £25,000 request. Show the failed destination check. The balances remain unchanged. Explain that a blocked attempt is still recorded and inspectable.

## 4. An approval — 2 minutes

Select **Large transfer** and run £175,000. The request is held because it exceeds the £100k automatic limit. Balances do not change while awaiting approval.

Enter a decision reason, such as “Reserve increase reviewed for this demonstration”, and choose **Approve simulated transfer**. The controls run again against current balances. Final balances after these three scenarios are £2.25m operating and £1.15m reserve.

Explain that this guided page uses a presenter simulation. Open the institution application to demonstrate separately authenticated users and independent controls. Production hosting and approval of real bank instructions remain unverified.

## 5. Show the evidence — 1 minute

Inspect an earlier action. Select **Verify record chain** and **Export audit**. The three scenarios with approval produce 10 linked records. The JSON export includes the requests, policy checks, balances, approval reason and hashes.

Be exact about the proof: this checks internal consistency of the hash chain. It is not a blockchain, an external signature, an immutable archive or a regulatory certification.

## 6. Show the operational product and agent — 5 minutes

Open `/agentu/app/` and the prepared fictional verification institution. Show Accounts, Team & access, Policies and the balanced Ledger. These pages use the durable institution service.

In **Agents**, inspect the published Reserve steward mandate: explicit accounts, GBP 75,000 transaction limit, GBP 250,000 daily limit, GBP 1.25m source floor, GBP 150,000 reserve target and independent approval. **Agent runs** retains the worker-produced GBP 75,000 proposal and a second no-action run after the reserve reached its target. Open Operations to inspect the independent approval and the resulting settled transfer. The requester could not approve their own agent action.

To make a new live local run without altering funds, start Reserve steward again. With the target met it records no action. Creating another transfer requires a reviewed mandate/target revision. Identify the provider correctly: this is a deterministic treasury rule. The Bedrock adapter is built but has not completed a real model invocation.

Show the external-agent credential lifecycle and explain that its credential can submit a bounded proposal only. It cannot administer the institution or approve transfers. Company formation, hosted AWS verification and real bank integration remain open.

## 7. Agree the next decisions — 2 minutes

- The founder’s preferred initial customer and workflow.
- Which existing platform/IP, if any, should be integrated with this prototype.
- Entity formation, ownership of code and operational responsibility.
- Pilot counterparties and the systems they would need to connect.
- Who can propose, approve, suspend and investigate a live action.
- Evidence retention, independent assurance, support and commercial terms before a pilot.

## Rehearsal recovery

- **No internet:** run `agentu/scripts/launch-demo.ps1`, or start `python agentu/backend/local.py --port 4322`, then open the local demo. The policy engine works offline; web fonts fall back to system fonts.
- **Expired hosted login:** sign in again. State is scoped to the signed-in identity and workspace.
- **Too many test actions:** choose **New rehearsal**. Existing records are retained until their scheduled expiry or local removal.
- **Request times out:** refresh and inspect action history before retrying.
- **Cannot reach AWS:** use the local version and state clearly that the cloud deployment is unavailable.
