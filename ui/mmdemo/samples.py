"""The built-in sample images and audio clips, and the guardrail modes the UI offers."""

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

# Spoken clips (synthetic text-to-speech of fabricated data; see make_audio_samples.py).
AUDIO_SAMPLES: tuple[Sample, ...] = (
    Sample(
        "pii_name_address",
        "pii_name_address.wav",
        "Name and address",
        "pii",
        "A caller gives their name and home address",
    ),
    Sample("pii_ssn", "pii_ssn.wav", "Social security number", "pii", "Reads out an SSN"),
    Sample("pii_card", "pii_card.wav", "Card number", "pii", "Card number and expiry"),
    Sample(
        "pii_phone_email",
        "pii_phone_email.wav",
        "Phone and email",
        "pii",
        "Personal contact details",
    ),
    Sample(
        "pii_medical",
        "pii_medical.wav",
        "Patient details",
        "pii",
        "Name, date of birth, medication, insurance ID",
    ),
    Sample(
        "inj_ignore_previous",
        "inj_ignore_previous.wav",
        "Ignore previous instructions",
        "injection",
        "Asks the AI to reveal its system prompt",
    ),
    Sample(
        "inj_fibonacci",
        "inj_fibonacci.wav",
        "Hidden task: write code",
        "injection",
        "Asks the AI to write a Fibonacci function",
    ),
    Sample(
        "inj_exfil",
        "inj_exfil.wav",
        "Send the conversation out",
        "injection",
        "Asks the AI to email the conversation and credentials",
    ),
    Sample(
        "inj_polite",
        "inj_polite.wav",
        "Polite note to the AI",
        "injection",
        "Asks the AI to list its files and tools",
    ),
    Sample(
        "ctrl_labels_only",
        "ctrl_labels_only.wav",
        "Field names only",
        "safe",
        "Names the fields, reads no values (the PII rail sometimes blocks it)",
    ),
    Sample(
        "ctrl_business",
        "ctrl_business.wav",
        "Business hours",
        "safe",
        "Opening hours and a company support line",
    ),
    Sample("ctrl_weather", "ctrl_weather.wav", "Weather question", "safe", "Nothing sensitive"),
    Sample(
        "benign_voicemail",
        "benign_voicemail.wav",
        "Voicemail",
        "safe",
        "A call-back request about an appointment",
    ),
    Sample("benign_recipe", "benign_recipe.wav", "Recipe steps", "safe", "Instructions for people"),
)

AUDIO_SAMPLES_BY_ID: dict[str, Sample] = {s.id: s for s in AUDIO_SAMPLES}

# UI mode -> NeMo config id (the directory name mounted by the operator).
UNPROTECTED_CONFIG = "no-rails"
MODES: dict[str, str | None] = {
    "none": None,
    "pii": "pii-vision",
    "injection": "injection-vision",
    "both": "pii-and-injection",
}

UNPROTECTED_AUDIO_CONFIG = "no-rails-audio"
AUDIO_MODES: dict[str, str | None] = {
    "none": None,
    "pii": "pii-audio",
    "injection": "injection-audio",
    "both": "pii-and-injection-audio",
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
    "I3": "Task for the AI inside the image",
    "I4": "Leak data, call tool, visit URL",
    "I5": "Fake system message",
}

# The audio judges use their own, shorter list (config/*_audio/prompts.yml).
AUDIO_CATEGORY_LABELS: dict[str, str] = {
    "P1": "Name with home address",
    "P2": "Government ID number",
    "P3": "Financial account data",
    "P4": "Personal phone or email",
    "P5": "Date of birth",
    "P6": "Medical information",
    "I1": "Override instructions",
    "I2": "Reveal prompt, tools, files or secrets",
    "I3": "Unrelated task for the AI",
    "I4": "Send or leak data",
}

RAIL_LABELS: dict[str, str] = {
    "vision_pii": "PII rail",
    "vision_injection": "Prompt-injection rail",
    "audio_pii": "PII rail",
    "audio_injection": "Prompt-injection rail",
}


@lru_cache(maxsize=64)
def sample_data_url(samples_dir: Path, sample: Sample) -> str:
    """Return the sample as a base64 data URL, ready for an OpenAI-style ``image_url``."""
    raw = (samples_dir / sample.file).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode()


@lru_cache(maxsize=32)
def audio_base64(samples_dir: Path, sample: Sample) -> str:
    """Return the clip as base64, ready for an OpenAI-style ``input_audio`` part."""
    return base64.b64encode((samples_dir / sample.file).read_bytes()).decode()
