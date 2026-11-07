# Approach

## What I found
[RP-3.2](8ce6fbec-aed5-4977-a750-f5e59ea9ad68-ecommerce-support-agent-ai-engineer-mid/policies/refund_policy_RP-3.2.md) decides everything; the FAQ is older and the warehouse doc is internal. The data is messy on purpose: duplicate orders, mixed date and money formats, senders in mixed case, five ways of writing an order number, and emails that try to talk the agent into a refund. Once the data is cleaned ([prepare_data.py](src/prepare_data.py)), all 15 labels agree with RP-3.2. Where the policy is silent, I wrote one rule per case in [NOTES.md](NOTES.md).

## How it works
The model reads the email; the code makes the decision. One cheap model call ([extract.py](src/extract.py)) pulls out the order, the request and the amount. Then plain Python ([policy.py](src/policy.py)) checks, in order: manipulation, that the order belongs to the sender, carrier status, the return window, the items, earlier refunds, and the $150 approval limit. Each check cites its policy clause and evidence, so an email can never approve its own refund. Replies are filled in from the decision ([reply.py](src/reply.py)), so the amounts always match, and the ledger ([store.py](src/store.py)) records each action once.

## How I test it
[eval/run.py](eval/run.py) runs the 15 labelled tickets plus 13 new negative cases: injection, wrong sender, unknown order, already refunded, forwarded duplicates and over-asking. A case passes when the outcome and approver match and every expected reason code is present. The first run scored 22/23; the miss was a "delivered but never arrived" claim. After fixing that, and seven more bugs found by testing the running service with tricky emails, the suite scores 28/28. A ticket costs about $0.0001 ([ARTEFACT.md](ARTEFACT.md)).