The scenario
You are joining Larkfield Home, a US online retailer of furniture and home goods, as an AI engineer. Customer Care answers about 1,200 emails a day and cannot keep pace: answers are inconsistent, some actions go through that should not, and some requests are lost. The Head of Customer Care wants a service that handles these emails, can be trusted, stops for a person when it should, and shows what it costs.

Your task
Build, deploy and evaluate an agent service that takes a support email as it arrives and returns the desk's decision in the contract below. What the desk handles, what it checks and how it decides are for you to work out from the data room. Wherever the policy leaves a case open, state the rule you applied, in one line, in APPROACH.md or ARTEFACT.md.

Your service is also called with tickets you will not see. Partial builds are expected; explain what is unfinished. Explore before you build.

Contract
A public HTTP service, no authentication, reachable until the review ends, reading its port from PORT (Render and Vercel both work). It is called automatically; follow the contract exactly. Pydantic models of every body are in schema/response_schema.py.

GET /health → 200 {"status": "ok"}.
POST /tickets takes one ticket shaped like the items in inbox/public_tickets.json and returns a run (fields below).
GET /runs/{run_id} → the current state of that run, including a run still waiting for a person.
POST /runs/{run_id}/decision with {"decision": "approve" | "reject", "approver": "string"} → the updated run.
GET /ledger → the actions your service has recorded: run_id, ticket_id, action (REFUND | REPLY | ESCALATION), amount_usd, status (PROPOSED | APPROVED | REJECTED), recorded_at.

Run fields:
ticket_id, run_id	the ticket you were sent; your id for this run
ticket_type	REFUND, RETURN, ORDER_STATUS, DELIVERY_ISSUE or OTHER
resolved	order_id and customer_id from the data the ticket refers to; null when not established
outcome	exactly one value, below
reason_codes	the reason codes defined in policies/ that apply; empty when none does
checks[]	each check your service made: check_id, result (PASS, FAIL, NOT_APPLICABLE, UNABLE_TO_VERIFY), the policy_clause it rests on, optional evidence[] (source, ref, value)
human_approval_required	true when a person must act before anything further happens on this run
approver_role	who that person is: SUPPORT_LEAD, TRUST_SAFETY or CARRIER_CLAIMS; null otherwise
draft_reply	to, subject, body — the customer-facing reply the desk would send for this decision (facts and amounts must match the decision); never sent by you
trace	what actually happened on this request: model_calls[] (model, input_tokens, output_tokens), tools_called[], documents_used[] (document, version), measured cost_usd

Outcome values:
PROCEED	the request can be completed as asked without a person, including a request answered with information only
PROCEED_MODIFIED	can be completed without a person, but not entirely as asked
PEND	an action is proposed and waits for a person's approval
ESCALATE	handed to a team; no action proposed
REJECT	declined
INVALID_REQUEST	the order cannot be established as this sender's
UNABLE_TO_VERIFY	a fact the decision needs could not be obtained

Your service proposes; it never refunds, sends or changes anything itself. A check it could not complete is never a PASS. An approved action is recorded once, whatever number of decisions arrive. You may add fields; document them in README.md.

Data provided
ecommerce-support-agent-ai-engineer-mid.zip: data/ (customers, orders, order lines, shipments, issued refunds), policies/, tickets/ (30 inbox emails; 15 with a senior agent's labels), inbox/public_tickets.json (10 tickets without outcomes), schema/, DATA_DICTIONARY.md. Files are as exported. The inbox export and public tickets are a sample of recent traffic; the live service is called with further requests written the way customers actually write them, including through the same email channel. Prepare the files in code, reproducibly with one command; never edit them.

Deliverables
Submit the service URL and a public GitHub repository started from the template you were given, containing:

the service source and README.md (prepare data, run, configure, added fields);
APPROACH.md, at most 250 words, the first thing a reviewer reads: what you found, your architecture and why, and how you evaluate, linking to the code behind each claim;
ARTEFACT.md: in real numbers, what preparing the data found and did, which policy you use, your eval results before and after one change, how you measured cost and latency (3 lines) and median cost per request, and what is not built;
your evaluation: at least 12 cases including at least 4 new negative cases of at least 3 kinds, a stated pass rule, a runner, and two committed, timestamped result files with the change between them.

Rules
Time: Roughly 120 minutes in one tracked session, roughly 35 understanding the problem and deciding, 85 building. Deploying counts against the time. Keep the last 10 minutes to run and check your evaluation.
Your working session is tracked and is part of your submission. Work through the Cadra proxy with your AI tool. Decisions we cannot see you make are recorded as not evidenced.
Running service: use your own model provider and key, never the coding agent's build-time token. A free tier is fine. Configure it on your host; never commit it. Every response carries the model id and token counts.