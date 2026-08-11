"""
backend/api/agents/form_understanding.py
==========================================
FormUnderstandingAgent

Input  : Raw HTML string from the browser extension (one application page)
Output : FormUnderstandingResult  — list of DetectedField with canonical mappings

Pipeline
--------
1. Pre-process the HTML:
   a. Strip script/style tags (reduce token usage)
   b. Extract <input>, <textarea>, <select> elements with their attributes
   c. Walk the DOM tree to pair inputs with their nearby <label> text
   d. Produce a compact "field manifest" (JSON list) — much cheaper to send
      to the LLM than the full HTML
2. Detect the ATS platform from the page URL / HTML markers
3. Classify each field with the LLM:
   - What is this field asking for?
   - What canonical template key does it map to?
   - Should it be filled by the AI (open question) or deterministically?
4. Validate and score the result
5. Return FormUnderstandingResult

Two-phase design
----------------
Phase 1 (heuristic, no LLM):  Handle well-known ATS platforms by matching
                               HTML name/id patterns against a platform registry.
Phase 2 (LLM):                Send unresolved or ambiguous fields to the LLM
                               for semantic mapping.

This minimises LLM calls — known platforms resolve ~80 % of fields in phase 1.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from bs4 import BeautifulSoup, Tag
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core import Settings as LlamaSettings

from backend.api.agents.base import (
    DetectedField,
    FormUnderstandingResult,
    configure_llama_settings,
)
from backend.api.core.config import settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Canonical field key registry
# Maps template keys → common HTML name/id/label patterns (lowercase regex)
# ---------------------------------------------------------------------------

CANONICAL_FIELD_MAP: dict[str, dict[str, Any]] = {
    # Personal info — deterministic (no AI needed)
    "first_name":         {"patterns": [r"first.?name", r"fname", r"given.?name"], "type": "text", "ai": False},
    "last_name":          {"patterns": [r"last.?name", r"lname", r"surname", r"family.?name"], "type": "text", "ai": False},
    "full_name":          {"patterns": [r"full.?name", r"^name$", r"candidate.?name"], "type": "text", "ai": False},
    "email":              {"patterns": [r"e.?mail", r"email.?address"], "type": "email", "ai": False},
    "phone":              {"patterns": [r"phone", r"mobile", r"tel(?:ephone)?", r"cell"], "type": "phone", "ai": False},
    "address":            {"patterns": [r"address", r"street"], "type": "text", "ai": False},
    "city":               {"patterns": [r"^city$", r"city.?name"], "type": "text", "ai": False},
    "state":              {"patterns": [r"^state$", r"province", r"region"], "type": "text", "ai": False},
    "zip_code":           {"patterns": [r"zip", r"postal", r"postcode"], "type": "text", "ai": False},
    "country":            {"patterns": [r"^country$", r"country.?of.?residence"], "type": "select", "ai": False},
    "location":           {"patterns": [r"location", r"current.?location"], "type": "text", "ai": False},
    # Professional links
    "linkedin_url":       {"patterns": [r"linkedin"], "type": "url", "ai": False},
    "github_url":         {"patterns": [r"github", r"gitlab", r"bitbucket"], "type": "url", "ai": False},
    "portfolio_url":      {"patterns": [r"portfolio", r"personal.?site", r"website", r"personal.?url"], "type": "url", "ai": False},
    # Work status
    "current_employer":   {"patterns": [r"current.?employer", r"current.?company", r"present.?employer"], "type": "text", "ai": False},
    "current_title":      {"patterns": [r"current.?title", r"current.?role", r"current.?position"], "type": "text", "ai": False},
    "years_experience":   {"patterns": [r"years?.?of.?exp", r"total.?exp", r"experience.?years?"], "type": "text", "ai": False},
    "salary_expectation": {"patterns": [r"salary.?expect", r"desired.?salary", r"compensation.?expect", r"expected.?ctc"], "type": "text", "ai": False},
    "notice_period":      {"patterns": [r"notice.?period", r"joining.?time", r"start.?date", r"available.?from"], "type": "text", "ai": False},
    "willing_to_relocate":{"patterns": [r"relocat", r"willing.?to.?move"], "type": "radio", "ai": False},
    "visa_sponsorship":   {"patterns": [r"visa.?sponsor", r"work.?authoriz", r"authoriz.*work", r"right.?to.?work", r"require.*sponsor"], "type": "radio", "ai": False},
    # Resume / cover letter uploads
    "resume_file":        {"patterns": [r"resume", r"cv", r"curriculum"], "type": "file", "ai": False},
    "cover_letter_file":  {"patterns": [r"cover.?letter.?file", r"cover.?letter.?upload"], "type": "file", "ai": False},
    # Open-text (AI-generated)
    "cover_letter":       {"patterns": [r"cover.?letter(?!.?file|.?upload)", r"motivation.?letter", r"letter.?of.?interest"], "type": "textarea", "ai": True},
    "why_us":             {"patterns": [r"why.*(us|company|role|position|interest)", r"interest.*role", r"motivation"], "type": "textarea", "ai": True},
    "about_yourself":     {"patterns": [r"about.?your(self)?", r"tell.?us.?about", r"introduce.?yourself"], "type": "textarea", "ai": True},
    "strengths":          {"patterns": [r"strength", r"best.?qualit", r"key.?skill"], "type": "textarea", "ai": True},
    "greatest_achievement":{"patterns": [r"achievement", r"accomplishment", r"proud.?of", r"significant.?project"], "type": "textarea", "ai": True},
    "challenges":         {"patterns": [r"challenge", r"difficult.?situation", r"overcome"], "type": "textarea", "ai": True},
    "diversity_statement":{"patterns": [r"diversity", r"inclusion", r"equit"], "type": "textarea", "ai": True},
    "additional_info":    {"patterns": [r"additional.?info", r"anything.?else", r"other.?comment"], "type": "textarea", "ai": True},
    "referral":           {"patterns": [r"referr", r"hear.?about", r"how.?did.?you.?find"], "type": "select", "ai": False},
    "gender":             {"patterns": [r"^gender$", r"sex$"], "type": "select", "ai": False},
    "ethnicity":          {"patterns": [r"ethnic", r"race$", r"racial"], "type": "select", "ai": False},
    "veteran_status":     {"patterns": [r"veteran", r"military"], "type": "select", "ai": False},
    "disability_status":  {"patterns": [r"disabilit"], "type": "select", "ai": False},
}

# Compile all patterns once at import time for performance
_COMPILED_PATTERNS: dict[str, list[re.Pattern]] = {
    key: [re.compile(p, re.IGNORECASE) for p in meta["patterns"]]
    for key, meta in CANONICAL_FIELD_MAP.items()
}


# ---------------------------------------------------------------------------
# Heuristic field mapper (Phase 1 — no LLM)
# ---------------------------------------------------------------------------

def _heuristic_map(
    label: str,
    html_name: str,
    html_id: str,
    placeholder: str,
    aria_label: str,
) -> tuple[str, bool, float] | None:
    """
    Try to map a field to a canonical key using regex patterns.

    Returns (canonical_key, requires_ai, confidence) or None if no match.
    Confidence reflects how specific the match was.
    """
    # Combine all identifying strings for matching
    haystack = " ".join(filter(None, [label, html_name, html_id, placeholder, aria_label])).lower()

    best_key: str | None = None
    best_score = 0.0

    for key, patterns in _COMPILED_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(haystack):
                # Prefer matches on label/aria_label (high confidence) over name/id
                if pattern.search(label.lower() if label else "") or pattern.search(aria_label.lower() if aria_label else ""):
                    score = 0.92
                elif pattern.search(html_name.lower() if html_name else ""):
                    score = 0.85
                else:
                    score = 0.72
                if score > best_score:
                    best_key = key
                    best_score = score
                break  # first matching pattern per key is enough

    if best_key:
        meta = CANONICAL_FIELD_MAP[best_key]
        return best_key, meta["ai"], best_score
    return None


# ---------------------------------------------------------------------------
# HTML pre-processor
# ---------------------------------------------------------------------------

def _extract_field_manifest(html: str) -> list[dict[str, Any]]:
    """
    Parse raw HTML and extract a compact list of field descriptors.
    This avoids sending the full HTML (potentially 100 KB+) to the LLM.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove noisy tags
    for tag in soup.find_all(["script", "style", "svg", "noscript", "meta", "link"]):
        tag.decompose()

    fields: list[dict[str, Any]] = []
    order = 0

    # Collect all interactive form elements
    input_tags = soup.find_all(
        ["input", "textarea", "select"],
        attrs={"type": lambda t: t not in ("hidden", "submit", "button", "reset", "image", None) or t is None},
    )

    for el in input_tags:
        if not isinstance(el, Tag):
            continue

        tag_name = el.name.lower()
        input_type = (el.get("type") or "text").lower() if tag_name == "input" else tag_name

        # Skip truly invisible / decorative inputs
        if input_type in ("hidden", "submit", "button", "reset", "image"):
            continue

        html_name = el.get("name") or ""
        html_id = el.get("id") or ""
        placeholder = el.get("placeholder") or ""
        aria_label = el.get("aria-label") or ""
        aria_required = el.get("aria-required", "false").lower() == "true"
        required_attr = el.has_attr("required")
        is_required = required_attr or aria_required

        # Find associated label text
        label_text = ""
        # Method 1: explicit <label for="id">
        if html_id:
            label_el = soup.find("label", attrs={"for": html_id})
            if label_el:
                label_text = label_el.get_text(separator=" ", strip=True)

        # Method 2: wrapping <label>
        if not label_text:
            parent = el.parent
            while parent and parent.name not in ("form", "body", "[document]"):
                if parent.name == "label":
                    label_text = parent.get_text(separator=" ", strip=True)
                    break
                parent = parent.parent

        # Method 3: nearest preceding sibling / parent text
        if not label_text:
            for sibling in el.find_previous_siblings(["label", "span", "div", "p", "td", "th"])[:3]:
                text = sibling.get_text(strip=True)
                if text and len(text) < 200:
                    label_text = text
                    break

        # Extract options for select / radio / checkbox
        options: list[str] = []
        if tag_name == "select":
            options = [
                opt.get_text(strip=True)
                for opt in el.find_all("option")
                if opt.get_text(strip=True) and opt.get("value", "") not in ("", "none", "select")
            ]
        elif input_type in ("radio", "checkbox"):
            # Radio groups: collect sibling inputs with same name
            if html_name:
                siblings = soup.find_all("input", attrs={"name": html_name})
                for sib in siblings:
                    val = sib.get("value") or sib.get("aria-label") or ""
                    lbl = ""
                    sib_id = sib.get("id")
                    if sib_id:
                        lbl_el = soup.find("label", attrs={"for": sib_id})
                        if lbl_el:
                            lbl = lbl_el.get_text(strip=True)
                    options.append(lbl or val)
                options = list(dict.fromkeys(filter(None, options)))  # dedup

        # Build XPath (simple attribute-based)
        xpath = None
        if html_id:
            xpath = f'//*[@id="{html_id}"]'
        elif html_name:
            xpath = f'//{tag_name}[@name="{html_name}"]'

        css_selector = None
        if html_id:
            css_selector = f"#{html_id}"
        elif html_name:
            css_selector = f'{tag_name}[name="{html_name}"]'

        fields.append({
            "label": label_text[:256],
            "html_name": html_name[:256],
            "html_id": html_id[:256],
            "html_placeholder": placeholder[:256],
            "aria_label": aria_label[:256],
            "field_type": input_type,
            "is_required": is_required,
            "options": options[:30],  # cap option list
            "xpath": xpath,
            "css_selector": css_selector,
            "display_order": order,
        })
        order += 1

    return fields


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_PLATFORM_SIGNALS: list[tuple[str, str]] = [
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("workday.com", "workday"),
    ("myworkdayjobs.com", "workday"),
    ("forms.google.com", "google_forms"),
    ("microsoft.com/forms", "microsoft_forms"),
    ("forms.office.com", "microsoft_forms"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("icims.com", "icims"),
    ("ashbyhq.com", "ashby"),
    ("ultipro.com", "ultipro"),
    ("bamboohr.com", "bamboohr"),
    ("workable.com", "workable"),
    ("keka.com", "keka"),
    ("successfactors.com", "sap_successfactors"),
    ("sensehq.com", "sensehq"),
    ("appdover.com", "appdover"),
    ("oracle.com", "oracle_cloud"),
    ("oraclecloud.com", "oracle_cloud"),
]


def _detect_platform(page_url: str, html: str) -> str:
    """Detect the ATS platform from the URL and HTML fingerprints."""
    combined = (page_url + " " + html[:2000]).lower()
    for signal, platform in _PLATFORM_SIGNALS:
        if signal in combined:
            return platform
    return "unknown"


# ---------------------------------------------------------------------------
# LLM Phase 2 — resolve ambiguous fields
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are a precise form field classifier for job application forms.

Your task: Given a list of form fields (with their HTML attributes and labels),
determine the canonical mapping for each field.

GROUND RULES:
1. Only use the information provided in the field attributes. Do not invent mappings.
2. If you cannot determine a confident mapping, use mapped_field = "unknown".
3. Mark requires_ai = true ONLY for open-text questions that require a personalised answer
   (e.g. "Why do you want to work here?", cover letters, personal essays).
   Simple factual fields (name, email, phone) are never AI-required.
4. Output ONLY a valid JSON array. No markdown, no explanations.

CANONICAL FIELD KEYS available:
first_name, last_name, full_name, email, phone, address, city, state, zip_code,
country, location, linkedin_url, github_url, portfolio_url, current_employer,
current_title, years_experience, salary_expectation, notice_period,
willing_to_relocate, visa_sponsorship, resume_file, cover_letter_file,
cover_letter, why_us, about_yourself, strengths, greatest_achievement,
challenges, diversity_statement, additional_info, referral,
gender, ethnicity, veteran_status, disability_status, unknown

OUTPUT FORMAT (JSON array):
[
  {
    "display_order": <integer from input>,
    "mapped_field": "<canonical key from the list above>",
    "requires_ai": <true|false>,
    "mapping_confidence": <float 0.0-1.0>
  }
]
"""

_LLM_USER_TEMPLATE = """\
Classify the following form fields:

{field_manifest_json}

Output the JSON array only.
"""


async def _llm_classify_fields(
    unresolved_fields: list[dict],
    llm,
) -> dict[int, tuple[str, bool, float]]:
    """
    Send unresolved fields to the LLM for semantic classification.

    Returns a dict mapping display_order → (mapped_field, requires_ai, confidence).
    """
    if not unresolved_fields:
        return {}

    # Only send the fields the heuristic couldn't resolve
    manifest = json.dumps(unresolved_fields, ensure_ascii=False, indent=2)

    # Truncate if the manifest is too large
    if len(manifest) > 12_000:
        manifest = manifest[:12_000] + "\n... (truncated)"

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=_LLM_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=_LLM_USER_TEMPLATE.format(field_manifest_json=manifest)),
    ]

    response = await llm.achat(messages)
    raw = (response.message.content or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    try:
        classifications: list[dict] = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("FormUnderstandingAgent LLM JSON parse failed", error=str(exc))
        return {}

    results: dict[int, tuple[str, bool, float]] = {}
    for item in classifications:
        order = item.get("display_order")
        if order is not None:
            results[int(order)] = (
                item.get("mapped_field", "unknown"),
                bool(item.get("requires_ai", False)),
                float(item.get("mapping_confidence", 0.5)),
            )
    return results


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class FormUnderstandingAgent:
    """
    Maps raw HTML from a job application page to canonical field definitions.

    Usage:
        agent = FormUnderstandingAgent()
        result: FormUnderstandingResult = await agent.understand(html, page_url)
    """

    def __init__(self, llm=None) -> None:
        configure_llama_settings()
        self._llm = llm or LlamaSettings.llm

    async def understand(
        self,
        html: str,
        page_url: str = "",
    ) -> FormUnderstandingResult:
        """
        Full form understanding pipeline.

        Args:
            html:      Raw HTML of the job application page.
            page_url:  URL of the page (used for platform detection).

        Returns:
            FormUnderstandingResult with populated DetectedField list.
        """
        t_start = time.monotonic()

        # 1. Detect platform
        platform = _detect_platform(page_url, html)

        # 2. Extract compact field manifest from HTML
        raw_fields = _extract_field_manifest(html)
        if not raw_fields:
            return FormUnderstandingResult(
                platform_detected=platform,
                warnings=["No interactive form fields found in HTML"],
            )

        # 3. Phase 1: Heuristic resolution
        detected: list[DetectedField] = []
        unresolved: list[dict] = []

        for raw in raw_fields:
            mapping = _heuristic_map(
                label=raw["label"],
                html_name=raw["html_name"],
                html_id=raw["html_id"],
                placeholder=raw["html_placeholder"],
                aria_label=raw["aria_label"],
            )

            if mapping and mapping[2] >= 0.72:
                # High-confidence heuristic match
                canonical_key, requires_ai, confidence = mapping
                detected.append(DetectedField(
                    label=raw["label"],
                    html_name=raw["html_name"] or None,
                    html_id=raw["html_id"] or None,
                    html_placeholder=raw["html_placeholder"] or None,
                    aria_label=raw["aria_label"] or None,
                    field_type=raw["field_type"],
                    mapped_field=canonical_key,
                    is_required=raw["is_required"],
                    options=raw["options"],
                    requires_ai=requires_ai,
                    mapping_confidence=confidence,
                    xpath=raw["xpath"],
                    css_selector=raw["css_selector"],
                    display_order=raw["display_order"],
                ))
            else:
                # Send to LLM for semantic resolution
                unresolved.append(raw)
                # Placeholder so display_order is preserved in detected list
                detected.append(None)  # type: ignore[arg-type]

        # 4. Phase 2: LLM resolution for unresolved fields
        if unresolved:
            logger.info(
                "FormUnderstandingAgent: sending unresolved fields to LLM",
                count=len(unresolved),
            )
            llm_results = await _llm_classify_fields(unresolved, self._llm)

            # Map LLM results back by display_order
            llm_by_order = {raw["display_order"]: (raw, llm_results.get(raw["display_order"])) for raw in unresolved}

            for i, field in enumerate(detected):
                if field is None:
                    # Find the corresponding unresolved raw field
                    raw_field = None
                    for raw in unresolved:
                        if raw["display_order"] == i:
                            raw_field = raw
                            break
                    if raw_field is None:
                        continue

                    llm_result = llm_results.get(raw_field["display_order"])
                    if llm_result:
                        canonical_key, requires_ai, confidence = llm_result
                    else:
                        canonical_key, requires_ai, confidence = "unknown", False, 0.3

                    detected[i] = DetectedField(
                        label=raw_field["label"],
                        html_name=raw_field["html_name"] or None,
                        html_id=raw_field["html_id"] or None,
                        html_placeholder=raw_field["html_placeholder"] or None,
                        aria_label=raw_field["aria_label"] or None,
                        field_type=raw_field["field_type"],
                        mapped_field=canonical_key,
                        is_required=raw_field["is_required"],
                        options=raw_field["options"],
                        requires_ai=requires_ai,
                        mapping_confidence=confidence,
                        xpath=raw_field["xpath"],
                        css_selector=raw_field["css_selector"],
                        display_order=raw_field["display_order"],
                    )

        # Remove any None placeholders that weren't filled
        detected = [f for f in detected if f is not None]

        # 5. Compute aggregate confidence
        if detected:
            avg_confidence = sum(f.mapping_confidence for f in detected) / len(detected)
        else:
            avg_confidence = 0.0

        ai_required = sum(1 for f in detected if f.requires_ai)
        latency_ms = int((time.monotonic() - t_start) * 1000)

        result = FormUnderstandingResult(
            fields=detected,
            platform_detected=platform,
            total_fields=len(detected),
            ai_required_count=ai_required,
            parsing_confidence=avg_confidence,
        )

        logger.info(
            "FormUnderstandingAgent complete",
            platform=platform,
            total_fields=len(detected),
            ai_required=ai_required,
            avg_confidence=avg_confidence,
            latency_ms=latency_ms,
        )
        return result