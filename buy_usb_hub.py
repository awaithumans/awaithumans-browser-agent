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

from browser_use import ActionResult, Agent, Tools
from browser_use.llm import ChatOpenAI
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from awaithumans import await_human
from awaithumans.verifier_claude import ClaudeVerifier

load_dotenv()


# ---------------------------------------------------------------------------
# Typed payload: what the human sees + what they respond with.
# ---------------------------------------------------------------------------


class CartApproval(BaseModel):
    """What the human sees on Slack / email when the agent reaches checkout."""

    action: str = Field(description="One-line summary of what the agent is about to do")
    page_url: str = Field(description="The page the agent is currently on")
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
        "irreversible action. Sends the cart details to a human for approval. "
        "Returns 'APPROVED' or 'REJECTED: <reason>'. If rejected, stop and revise."
    )
)
async def request_human_approval(
    item_name: str,
    quantity: int,
    total_usd: float,
    shipping_address: str,
    page_url: str,
    action: str = "Submit order",
    eta: str | None = None,
) -> ActionResult:
    """Pause the agent and ask a human via Slack or email."""

    payload = CartApproval(
        action=action,
        page_url=page_url,
        item_name=item_name,
        quantity=quantity,
        total_usd=total_usd,
        shipping_address=shipping_address,
        eta=eta,
    )

    # Optional AI verifier: Claude pre-screens the request before paging the
    # human. If the request is obviously fine (e.g., total within budget,
    # URL matches the demo store), the verifier returns auto-approve and
    # the human never sees a notification. If anything looks off, the
    # verifier flags it for the human's attention.
    verifier = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        verifier = ClaudeVerifier(
            instructions=(
                "You are pre-screening a checkout approval request. "
                "AUTO-APPROVE if: total <= $40, URL contains 'localhost:8080' "
                "or the configured demo store domain, item description matches "
                "a reasonable USB-C product, address is the user's known address. "
                "ESCALATE TO HUMAN otherwise."
            ),
        )

    channel = os.environ.get("AWAITHUMANS_DEMO_CHANNEL", "slack")
    notify_id = (
        os.environ["DEMO_SLACK_NOTIFY_ID"]
        if channel == "slack"
        else os.environ["DEMO_EMAIL_NOTIFY"]
    )

    decision: Decision = await await_human(
        task=f"Approve checkout — {item_name} (${total_usd:.2f})",
        payload=payload,
        response_schema=Decision,
        channel=channel,
        notify=[f"{channel}:{notify_id}"],
        verifier=verifier,
        timeout_seconds=600,
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
    store_url = os.environ.get("DEMO_STORE_URL", "http://localhost:8080")

    task = (
        f"Go to {store_url}. Find a USB-C hub priced under $40 with at least "
        "4-star reviews. Add it to the cart. Proceed to checkout. Fill the "
        "shipping form using these details:\n"
        "  - Name: Demo User\n"
        "  - Address: 123 Test Lane, Test City, CA 94000, USA\n"
        "  - Email: demo@example.com\n"
        "Reach the order-review page. BEFORE clicking 'Place order' you MUST "
        "call request_human_approval with the cart total, item name, quantity, "
        "shipping address, and current URL. If the human approves, click "
        "'Place order' and confirm the order ID. If rejected, do NOT submit; "
        "report what happened."
    )

    agent = Agent(
        task=task,
        llm=ChatOpenAI(model="gpt-4o-mini"),
        tools=tools,
    )

    result = await agent.run()
    print("\n=== Agent finished ===")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
