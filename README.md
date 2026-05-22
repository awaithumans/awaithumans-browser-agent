# 🛒 Browser agent that asks before it buys

A template repo: **[browser-use](https://github.com/browser-use/browser-use) + [awaithumans](https://github.com/awaithumans/awaithumans) in ~90 lines.**

Your AI agent navigates a real web page, fills the cart, reaches the order-review screen — and then **stops to ask a human**. Slack DM or email. Approval card with cart screenshot, line items, total, shipping address. One tap to approve or reject.

[![Powered by awaithumans](https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/images/badges/powered-by-awaithumans.svg)](https://github.com/awaithumans/awaithumans)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

---

> "Trained to ask approval before finalizing any significant action, such as submitting an order."
> — [OpenAI Operator design guideline](https://openai.com/index/introducing-operator/)

Operator forces this. OSS browser-agent frameworks don't ship the primitive — there are open feature requests for it across [browser-use #221](https://github.com/browser-use/browser-use/issues/221), [browser-use #3341](https://github.com/browser-use/browser-use/issues/3341), and [browser-use #4798](https://github.com/browser-use/browser-use/issues/4798).

**This repo is the missing primitive, plugged in.**

---

## 📸 What you'll see

| Channel | Screenshot |
|---|---|
| **Slack DM** (default) | `![Slack approval card](docs/images/slack-approval.png)` |
| **Email magic-link** (alternative) | `![Email approval](docs/images/email-approval.png)` |

The agent pauses on the order-review screen, sends one of these to you, and waits for your tap. Approve → agent clicks **Place order**. Reject (with reason) → agent revises and tries again.

---

## ⚡ Quick start (~5 minutes)

```bash
# 1. Clone
git clone https://github.com/awaithumans/awaithumans-browser-agent
cd awaithumans-browser-agent

# 2. Configure
cp .env.example .env
# Edit .env: pick "slack" or "email" for AWAITHUMANS_DEMO_CHANNEL,
# fill in the matching credentials + your OpenAI API key.

# 3. Start the awaithumans server locally
docker compose up -d
# Verify: curl http://localhost:3001/health  → {"status":"ok"}

# 4. Install Python deps (uv recommended; pip works fine)
uv pip install -e .
# or: pip install -e .

# 5. Install Playwright browsers (required by browser-use)
playwright install chromium

# 6. Run the demo
python buy_usb_hub.py
```

The agent logs into [saucedemo.com](https://www.saucedemo.com), adds a backpack to the cart, walks through checkout — and right before clicking **Finish**, it pings your Slack or email and waits. Approve in your channel, the agent submits. Reject, it stops.

Whole loop takes ~60-90 seconds from `python buy_usb_hub.py` to "agent paused, your turn."

### Don't have Slack? Use email

Edit `.env`:

```bash
AWAITHUMANS_DEMO_CHANNEL=email
DEMO_EMAIL_NOTIFY=you@example.com
# Free Resend dev tier — 60-second signup at https://resend.com
AWAITHUMANS_SMTP_HOST=smtp.resend.com
AWAITHUMANS_SMTP_PORT=587
AWAITHUMANS_SMTP_USER=resend
AWAITHUMANS_SMTP_PASS=re_...
AWAITHUMANS_SMTP_FROM=onboarding@resend.dev
```

The approval lands in your inbox as a magic-link email. One click opens a single-page form. Approve. Done.

---

## 🧠 How it works

Three moving parts:

```
┌──────────────────┐       request_human_approval(...)        ┌─────────────────────┐
│ browser-use      │ ───────────────────────────────────────► │ awaithumans server  │
│ Agent (your LLM) │                                          │  - Pydantic typed   │
│                  │ ◄─── ActionResult(extracted_content=...) │  - Pluggable channel│
│ Has a custom     │       "HUMAN APPROVED" / "HUMAN REJECTED"│  - Audit trail      │
│ Tool that calls  │                                          │  - Built-in UI      │
│ await_human()    │                                          └──────────┬──────────┘
└──────────────────┘                                                     │
                                                                         │ Slack / email
                                                                         ▼
                                                                ┌────────────────┐
                                                                │  You, on phone │
                                                                │   ✅ Approve   │
                                                                │   ❌ Reject    │
                                                                └────────────────┘
```

The agent's LLM is given a system instruction: **"Before any irreversible action — checkout, submit, send — call `request_human_approval` first."** The custom tool wraps `await_human()`, which is the one function call that:

- Persists the task to a typed database row (so it survives worker restarts)
- Routes the payload to your chosen channel (Slack DM with screenshot card, or email magic-link)
- Waits up to N seconds for a typed response
- Optionally pre-screens via a Claude verifier (auto-approve if total is under budget, escalate otherwise)
- Returns a typed Pydantic `Decision(approve: bool, reason: str | None)` back to the agent

The agent reads the decision via `ActionResult.extracted_content` and either submits or stops.

---

## 📁 What's in this repo

```
.
├── buy_usb_hub.py          ← Main demo — buys a Sauce Labs Backpack on saucedemo.com
├── job_application.py      ← Secondary demo: agent drafts an application, you approve the submit
├── docker-compose.yml      ← awaithumans server (the demo store is saucedemo.com — public, no Docker needed)
├── .env.example            ← Both channel configs side-by-side
├── pyproject.toml          ← Python deps
└── docs/
    └── images/             ← Screenshots
```

> **Filename note:** `buy_usb_hub.py` is named after the *conceptual* demo (an agent buying something tangible). The actual test target is saucedemo's `Sauce Labs Backpack` because that's the safe, public, login-included testing site. Point it at any store by editing the `task=` string + the `SAUCEDEMO_*` env vars.

### Why two examples?

The buy-USB-hub demo is the canonical case — every browser-agent framework markets purchasing as a top use case. The job-application demo is intentional: **[OpenAI Operator explicitly refuses](https://openai.com/index/introducing-operator/) high-stakes decisions like job applications.** OSS users have no choice but to add HITL. This repo shows how, in 30 extra lines.

---

## 🛠 Extending it

Add your own approval-gated action. Define a Pydantic payload, add a `@tools.action`, call `await_human()`:

```python
from awaithumans import await_human
from browser_use import ActionResult, Tools
from pydantic import BaseModel

class TransferApproval(BaseModel):
    from_account: str
    to_account: str
    amount_usd: float
    memo: str

class Decision(BaseModel):
    approve: bool
    reason: str | None = None

tools = Tools()

@tools.action(description="REQUIRED before any wire transfer. Asks a human.")
async def request_transfer_approval(
    from_account: str, to_account: str, amount_usd: float, memo: str
) -> ActionResult:
    decision: Decision = await await_human(
        task=f"Approve transfer — ${amount_usd:.2f} to {to_account}",
        payload=TransferApproval(...),
        response_schema=Decision,
        channel="slack",
        timeout_seconds=900,
    )
    return ActionResult(
        extracted_content="APPROVED" if decision.approve else f"REJECTED: {decision.reason}"
    )
```

Same shape works for any risky action: deploy, delete, send, post, suspend, refund, withdraw.

---

## 🧪 The demo store

The checkout demo points at **[saucedemo.com](https://www.saucedemo.com)** — the testing community's canonical practice site. **Public, free, login is intentionally publishable (`standard_user` / `secret_sauce`), no real money moves, no CAPTCHA, no rate limits.** This is the same site the Selenium/Playwright community uses for everyday automation demos.

Swap to any other store by changing `SAUCEDEMO_URL` + credentials in `.env`.

---

## 🔍 What awaithumans gives you over a hand-rolled Slack bot

This repo would be ~300 lines if you built the HITL layer yourself. With awaithumans it's 90:

- ✅ Durable: the agent's pending await survives worker restarts (Stripe-style idempotency keys)
- ✅ Typed: Pydantic on the way in, Pydantic on the way out — no string parsing
- ✅ Multi-channel: Slack, email, web dashboard, all from one config
- ✅ Audit trail: who approved what, when, from which channel
- ✅ AI verifier (optional): Claude pre-screens trivial cases so the human only sees the hard ones
- ✅ Resumable: kill the worker mid-await, restart, agent resumes from the exact pause

Full SDK docs: **[docs.awaithumans.dev](https://docs.awaithumans.dev)**

---

## 🤝 Related

- **[awaithumans](https://github.com/awaithumans/awaithumans)** — the HITL primitive itself (Python + TypeScript SDKs, Apache 2.0)
- **[browser-use](https://github.com/browser-use/browser-use)** — the browser-automation framework this demo is built on (MIT)
- **[Skyvern](https://github.com/Skyvern-AI/skyvern)** — alternative browser agent; same `Tools`-style integration would work
- **[Stagehand](https://github.com/browserbase/stagehand)** — TypeScript-native option (a TS version of this template is on the roadmap)

---

## 📜 License

MIT. Use it, fork it, ship it.

---

## 🗺 Roadmap

- [ ] Telegram channel — coming in awaithumans Week 3 release
- [ ] TypeScript version using Stagehand
- [ ] WhatsApp channel (via Twilio bridge)
- [ ] Companion demos: AI deploy approver, AI content moderator, AI travel booker

Have an idea? [Open an issue](https://github.com/awaithumans/awaithumans-browser-agent/issues) or DM [@awaithumans](https://x.com/awaithumans) on X.
