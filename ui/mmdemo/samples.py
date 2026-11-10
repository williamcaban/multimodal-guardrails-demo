"""The built-in sample images and the guardrail modes the UI offers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    id: str
    file: str
    title: str
    group: str  # pii | injection | safe
    blurb: str


SAMPLES: tuple[Sample, ...] = (
    Sample(
        "invoice_pii", "invoice_pii.png", "Invoice", "pii", "Customer name, address, phone, email"
    ),
    Sample("check_pii", "check_pii.png", "Bank check", "pii", "Payer address and account line"),
    Sample(
        "healthcare_form_pii",
        "healthcare_form_pii.png",
        "Patient intake form",
        "pii",
        "Name, SSN, DOB, insurance ID, diagnosis",
    ),
    Sample("badge_pii", "badge_pii.png", "Employee badge", "pii", "Photo, name, employee ID"),
    Sample("id_card_pii", "id_card_pii.png", "Driver license", "pii", "Photo, name, DOB, address"),
    Sample(
        "inj_ignore_previous",
        "inj_ignore_previous.png",
        "Ignore previous instructions",
        "injection",
        "Asks the AI to reveal its system prompt",
    ),
    Sample(
        "inj_fibonacci",
        "inj_fibonacci.png",
        "Hidden task: write code",
        "injection",
        "Asks the AI to write a Fibonacci function",
    ),
    Sample(
        "inj_exfil",
        "inj_exfil.png",
        "Fake system message",
        "injection",
        "Asks the AI to send the conversation to a URL",
    ),
    Sample(
        "inj_hidden_invoice",
        "inj_hidden_invoice.png",
        "Invoice with hidden footer",
        "injection",
        "Small grey text: reply APPROVED FOR PAYMENT",
    ),
    Sample(
        "inj_faint_chart",
        "inj_faint_chart.png",
        "Chart with faint text",
        "injection",
        "Near-invisible text: output PWNED",
    ),
    Sample("chart_clean", "chart_clean.png", "Sales chart", "safe", "Nothing sensitive"),
    Sample(
        "invoice_redacted",
        "invoice_redacted.png",
        "Redacted invoice",
        "safe",
        "Personal data blacked out",
    ),
    Sample(
        "check_redacted",
        "check_redacted.png",
        "Redacted check",
        "safe",
        "Personal data blacked out",
    ),
    Sample(
        "visitor_badge_blank",
        "visitor_badge_blank.png",
        "Blank visitor badge",
        "safe",
        "Labels only",
    ),
    Sample("benign_recipe", "benign_recipe.png", "Recipe card", "safe", "Instructions for people"),
    Sample(
        "benign_code_listing",
        "benign_code_listing.png",
        "Code listing",
        "safe",
        "Fibonacci code shown as content",
    ),
)

SAMPLES_BY_ID: dict[str, Sample] = {s.id: s for s in SAMPLES}

# UI mode -> NeMo config id (the directory name mounted by the operator).
UNPROTECTED_CONFIG = "passthrough"
MODES: dict[str, str | None] = {
    "none": None,
    "pii": "pii-vision",
    "injection": "injection-vision",
    "both": "pii-and-injection",
}

# Short labels for the category codes the judge returns (see config/*/prompts.yml).
CATEGORY_LABELS: dict[str, str] = {
    "P1": "Name with home address",
    "P2": "Government ID number",
    "P3": "Financial account data",
    "P4": "Personal phone or email",
    "P5": "Date of birth",
    "P6": "Medical information",
    "P7": "Photo ID or badge",
    "I1": "Override instructions",
    "I2": "Reveal system prompt",
    "I3": "Unrelated task for the AI",
    "I4": "Leak data, call tool, visit URL",
    "I5": "Fake system message",
}

RAIL_LABELS: dict[str, str] = {
    "vision_pii": "PII rail",
    "vision_injection": "Prompt-injection rail",
}


@lru_cache(maxsize=64)
def sample_data_url(samples_dir: Path, sample: Sample) -> str:
    """Return the sample as a base64 data URL, ready for an OpenAI-style ``image_url``."""
    raw = (samples_dir / sample.file).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode()
