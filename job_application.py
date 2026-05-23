"""
Secondary demo: agent drafts a job application; human approves the submit.

Why this exists in the same repo as buy_usb_hub.py: OpenAI Operator
explicitly REFUSES job-application submission as a "high-stakes decision."
OSS browser-agent users have no choice but to add a HITL layer. awaithumans
is that layer.

Run:
    python job_application.py
"""

from __future__ import annotations

import asyncio
import os

from browser_use import ActionResult, Agent, ChatAnthropic, ChatOpenAI, Tools
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from awaithumans import await_human

load_dotenv()


class ApplicationDraft(BaseModel):
    """What the human reviews before the agent hits Submit."""

    job_title: str
    company: str
    job_url: str
    cover_letter: str = Field(description="The full cover letter the agent drafted")
    resume_filename: str
    answered_questions: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form Q&A the application asked (e.g., 'Why do you want this role?')",
    )


class Decision(BaseModel):
    approve: bool
    reason: str | None = Field(
        default=None,
        description="Reason for rejection. Agent reads this and revises the draft.",
    )


tools = Tools()


@tools.action(
    description=(
        "REQUIRED before clicking 'Submit Application' or 'Apply Now'. "
        "Sends the full application draft (cover letter + answered questions) "
        "to a human for review. Returns 'APPROVED' or 'REJECTED: <reason>'."
    )
)
async def request_application_approval(
    job_title: str,
    company: str,
    job_url: str,
    cover_letter: str,
    resume_filename: str,
) -> ActionResult:
    payload = ApplicationDraft(
        job_title=job_title,
        company=company,
        job_url=job_url,
        cover_letter=cover_letter,
        resume_filename=resume_filename,
    )

    channel = os.environ.get("AWAITHUMANS_DEMO_CHANNEL", "slack")
    notify_id = (
        os.environ["DEMO_SLACK_NOTIFY_ID"]
        if channel == "slack"
        else os.environ["DEMO_EMAIL_NOTIFY"]
    )

    decision: Decision = await await_human(
        task=f"Approve application — {job_title} at {company}",
        payload=payload,
        response_schema=Decision,
        channel=channel,
        notify=[f"{channel}:{notify_id}"],
        timeout_seconds=1800,  # 30 min — applications need real review time
    )

    if decision.approve:
        return ActionResult(extracted_content="HUMAN APPROVED. Submit the application now.")
    return ActionResult(
        extracted_content=(
            f"HUMAN REJECTED. Reason: {decision.reason or '(no reason given)'}. "
            "Revise the cover letter or answers and ask for approval again."
        )
    )


async def main() -> None:
    job_url = os.environ.get(
        "DEMO_JOB_URL",
        "https://boards.greenhouse.io/example-company/jobs/123456",
    )

    task = (
        f"Open {job_url}. Read the job description. Draft a tailored cover "
        "letter (3 short paragraphs, no clichés, reference one specific "
        "responsibility from the job description). Fill in the application "
        "form using these details:\n"
        "  - Name: Demo User\n"
        "  - Email: demo@example.com\n"
        "  - Resume: resume.pdf (assume already uploaded)\n"
        "BEFORE clicking 'Submit Application' you MUST call "
        "request_application_approval with the full draft. If the human "
        "approves, submit. If rejected, revise and ask again."
    )

    if os.environ.get("OPENAI_API_KEY"):
        llm = ChatOpenAI(model="gpt-4o-mini")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        llm = ChatAnthropic(model="claude-haiku-4-5")
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
