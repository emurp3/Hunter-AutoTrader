"""
Capability-fit scoring.

Answers the question Hunter's scoring was never asking: "Can EMurph's
existing assets, skills, equipment, software, or relationships turn this
specific opportunity into money faster / cheaper / with less risk than a
cold-start opportunity would require?"

This is deliberately a lightweight keyword/category matcher against a
config of EMurph's real, known capabilities — not an LLM call, not a
fabricated probability. It exists to nudge ranking, not to replace human
judgment. A match means "there's a plausible existing-asset path here
worth a human or Claude CU agent taking a closer look" — nothing more.

CAPABILITY_TAGS should be edited directly as EMurph's real capabilities
change (new equipment, new licenses, new relationships, etc).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityFitResult:
    score: float  # 0..10
    matched_tags: list[str]


# Each tag maps to keywords that, if found in an opportunity's description,
# next_action, category, or origin_module, suggest EMurph already has a
# running start on it. Keep keywords specific enough to avoid false
# positives (e.g. "video" alone is too broad; "video editing" is not).
CAPABILITY_TAGS: dict[str, list[str]] = {
    "video_and_ai_content_production": [
        "video editing", "video production", "storyboard", "ai video",
        "explainer video", "promo video", "video content",
    ],
    "music_production_and_distribution": [
        "music production", "beat", "song", "album", "distrokid",
        "songtrust", "audio production", "jingle", "voiceover",
    ],
    "project_management_and_enterprise_ops": [
        "project management", "pmp", "process improvement", "enterprise software",
        "oracle", "erp", "scrum", "agile coach",
    ],
    "software_and_automation": [
        "automation", "workflow automation", "api integration", "web app",
        "chatbot", "scripting", "software development", "saas", "dashboard",
        "spreadsheet", "google sheets", "notion template",
    ],
    "ministry_media_and_teaching": [
        "church", "worship", "sermon", "bible study", "sunday school",
        "ministry", "faith-based", "gospel",
    ],
    "childrens_publishing": [
        "children's book", "picture book", "early literacy", "kids book",
    ],
    "ecommerce_and_product_launch": [
        "gumroad", "etsy", "shopify", "print on demand", "dropship",
        "product launch", "online store", "digital product",
    ],
    "ai_governance_and_research_writing": [
        "ai governance", "ai ethics", "facial recognition", "privacy policy",
        "research methodology", "grant writing", "white paper",
    ],
    "local_service_and_small_business_consulting": [
        "google business profile", "local seo", "small business consulting",
        "local listing",
    ],
}


def score_capability_fit(*, description: str, next_action: str, category: str, origin_module: str) -> CapabilityFitResult:
    haystack = " ".join(
        filter(None, [description or "", next_action or "", category or "", origin_module or ""])
    ).lower()

    matched: list[str] = []
    for tag, keywords in CAPABILITY_TAGS.items():
        if any(keyword in haystack for keyword in keywords):
            matched.append(tag)

    # Diminishing returns per additional match — first match matters most.
    if not matched:
        return CapabilityFitResult(score=0.0, matched_tags=[])
    score = min(10.0, 6.0 + (len(matched) - 1) * 2.0)
    return CapabilityFitResult(score=round(score, 2), matched_tags=matched)
