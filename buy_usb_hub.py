"""
Browser agent that asks before it buys.

Demo: a browser-use agent shops for a USB-C hub on the demo store, but stops
to ask a human before clicking 'Place order'. The human reviews the cart
(item, total, shipping address, page screenshot) in Slack or via an email
magic-link, then approves or rejects. The agent resumes accordingly.

Why this matters: OpenAI Operator's own design guideline is that an agent
"must ask approval before finalizing any significant action, such as
submitting an order." OSS browser agents don't ship this primitive.
awaithumans does.

Run:
    cp .env.example .env  # then fill in
    docker compose up -d  # awaithumans server + demo store
    python buy_usb_hub.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

from browser_use import ActionResult, Agent, ChatAnthropic, ChatOpenAI, Tools
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from awaithumans import await_human
from awaithumans.verifiers.claude import claude_verifier

load_dotenv()

# One unique key per script invocation. Within a single run the agent
# might retry the same approval (e.g., transient network error) — those
# retries collapse onto the same task via the server's idempotency check.
# A fresh `python buy_usb_hub.py` gets a fresh key so it never hits the
# cached response from a previous run. Without this, repeated demos
# silently return the FIRST run's approval forever.
RUN_ID = uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Typed payload: what the human sees + what they respond with.
# ---------------------------------------------------------------------------


class CartApproval(BaseModel):
    """What the human sees on Slack / email when the agent reaches checkout."""

    action: str = Field(description="One-line summary of what the agent is about to do")
    checkout_url: str = Field(description="The page the agent is currently on")
    item_name: str = Field(description="Name of the item in the cart")
    quantity: int = Field(description="How many units")
    total_usd: float = Field(description="Total cost in USD, including tax + shipping")
    shipping_address: str = Field(description="Where the order is shipping to")
    eta: str | None = Field(default=None, description="Estimated delivery date if shown")


class Decision(BaseModel):
    """What the agent reads back after the human responds."""

    approve: bool
    reason: str | None = Field(
        default=None,
        description="Why rejected (empty if approved). Agent uses this to revise.",
    )


# ---------------------------------------------------------------------------
# The custom action the agent's LLM can call.
#
# browser-use's Tools pattern lets us register actions the agent decides to
# invoke based on the description. We tell the LLM: "before any checkout,
# call request_human_approval first."
# ---------------------------------------------------------------------------

tools = Tools()


@tools.action(
    description=(
        "REQUIRED before clicking 'Place order', 'Submit', 'Pay', or any "
        "irreversible action. Sends the cart details to a human for "
        "approval. Pass the current page URL as `checkout_url`. Returns "
        "'APPROVED' or 'REJECTED: <reason>'. If rejected, stop and revise."
    )
)
async def request_human_approval(
    item_name: str,
    quantity: int,
    total_usd: float,
    shipping_address: str,
    checkout_url: str,
    action: str = "Submit order",
    eta: str | None = None,
) -> ActionResult:
    """Pause the agent and ask a human via Slack or email."""

    payload = CartApproval(
        action=action,
        checkout_url=checkout_url,
        item_name=item_name,
        quantity=quantity,
        total_usd=total_usd,
        shipping_address=shipping_address,
        eta=eta,
    )

    # Optional AI verifier: Claude reviews the human's response before
    # resuming the agent. Catches accidental approvals, mismatched data,
    # etc. Server-side only — runs on the awaithumans server, the SDK
    # just passes config.
    #
    # OFF by default because the published Docker image (:latest /
    # :v0.1.6) doesn't ship the [verifier-claude] extra — turning this
    # on against the default image causes the response submission to
    # fail with: "Verifier provider 'claude' requires the
    # [verifier-claude] extra. Install with:
    # pip install \"awaithumans[verifier-claude]\"".
    #
    # To enable, build a custom image (or run a local server) with the
    # extra installed, then set DEMO_USE_VERIFIER=true in .env.
    verifier = None
    if os.environ.get("DEMO_USE_VERIFIER", "false").lower() == "true":
        verifier = claude_verifier(
            instructions=(
                "You're a second-pass check on a checkout approval. "
                "PASS if the human's decision is consistent with the cart "
                "payload — total under $40, URL matches the demo store, "
                "items look like a reasonable purchase. FAIL otherwise so "
                "the agent re-asks the human."
            ),
        )

    # Channel is inferred from the prefix on each notify target.
    # 'slack:U01234567' routes to a Slack DM; 'email:you@example.com'
    # routes to an email magic-link. The await_human SDK doesn't take
    # a separate channel kwarg.
    channel = os.environ.get("AWAITHUMANS_DEMO_CHANNEL", "slack")
    notify_id = (
        os.environ["DEMO_SLACK_NOTIFY_ID"]
        if channel == "slack"
        else os.environ["DEMO_EMAIL_NOTIFY"]
    )

    # assign_to is OWNERSHIP (only this user can submit without claiming),
    # while notify is just a heads-up. Setting both means the dashboard
    # opens straight into the response form — no 'Claim it' step. Default
    # to the operator email you signed up with; override if multiple
    # humans share the queue.
    operator_email = os.environ.get("DEMO_OPERATOR_EMAIL")

    decision: Decision = await await_human(
        task=f"Approve checkout — {item_name} (${total_usd:.2f})",
        payload_schema=CartApproval,
        payload=payload,
        response_schema=Decision,
        assign_to=operator_email,
        notify=[f"{channel}:{notify_id}"],
        verifier=verifier,
        timeout_seconds=600,
        idempotency_key=f"checkout-{RUN_ID}",
    )

    if decision.approve:
        return ActionResult(
            extracted_content=(
                "HUMAN APPROVED. Proceed to click 'Place order' now."
            )
        )
    else:
        return ActionResult(
            extracted_content=(
                f"HUMAN REJECTED. Reason: {decision.reason or '(no reason given)'}. "
                "Stop. Do NOT submit. Report back with what you tried."
            )
        )


# ---------------------------------------------------------------------------
# The main demo.
# ---------------------------------------------------------------------------


async def main() -> None:
    # We use saucedemo.com — the testing community's canonical practice site.
    # Login is publicly documented (standard_user / secret_sauce); no real
    # money moves; no CAPTCHAs. Override via env if you want to point at a
    # different store.
    store_url = os.environ.get("SAUCEDEMO_URL", "https://www.saucedemo.com")
    username = os.environ.get("SAUCEDEMO_USERNAME", "standard_user")
    password = os.environ.get("SAUCEDEMO_PASSWORD", "secret_sauce")

    task = (
        f"Go to {store_url}. Log in with username '{username}' and password "
        f"'{password}'. Once logged in you'll see the products page.\n"
        "\n"
        "Find the 'Sauce Labs Backpack' (it should be priced at $29.99). "
        "Click 'Add to cart'. Click the cart icon (top right). Click "
        "'Checkout'. Fill in the checkout form:\n"
        "  - First Name: Demo\n"
        "  - Last Name: User\n"
        "  - Zip: 94000\n"
        "Click 'Continue'. You'll reach the 'Checkout: Overview' page showing "
        "the cart total.\n"
        "\n"
        "STOP HERE. BEFORE clicking the final 'Finish' button you MUST call "
        "request_human_approval with the cart total, item name, quantity (1), "
        "shipping address ('Demo User, 94000'), and the current URL. If the "
        "human approves, click 'Finish' and report the confirmation page text. "
        "If rejected, do NOT click Finish; report what happened."
    )

    # Pick whichever LLM key you set. Auto-falls-back to Anthropic if
    # OPENAI_API_KEY isn't present.
    #
    # Model picks are deliberate: the agent has to fluently call our
    # CUSTOM `request_human_approval` tool, which has 5+ typed
    # parameters. Smaller models (gpt-4o-mini, claude-haiku) intermittently
    # stuff the action JSON into the `thinking` field instead of the
    # `action` field that browser-use's validator requires — wasting
    # tokens and hitting the consecutive-failure limit. Sonnet is the
    # sweet spot of cost vs. structured-output reliability for this
    # task; bump to Opus only if you see Sonnet struggling.
    if os.environ.get("OPENAI_API_KEY"):
        llm = ChatOpenAI(model="gpt-4o")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        llm = ChatAnthropic(model="claude-sonnet-4-5")
    else:
        raise SystemExit(
            "Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env before running."
        )

    agent = Agent(
        task=task,
        llm=llm,
        tools=tools,
    )

    result = await agent.run()
    print("\n=== Agent finished ===")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
