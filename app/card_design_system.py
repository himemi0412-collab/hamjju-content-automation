"""Isolated design contract for future card-news layout work.

This module is intentionally not imported by the production pipeline. It bounds
AI-authored design choices and provides deterministic, per-card QA retry plans;
the existing content, QA, handoff, and Naver paths remain unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping


SCHEMA_VERSION = "hamjju-card-design-v1"
MAX_TARGETED_RETRIES = 2
CARD_ROLES = ("cover", "flow", "comparison", "checklist", "decision")

# Immutable renderer-owned rules. The production renderer currently outputs
# 1080px cards; this isolated contract uses those observed production bounds.
# Values are normalized later so a rendering adapter can choose pixel density.
FIXED_RULES: dict[str, Any] = {
    "canvas": {"width": 1080, "height": 1080, "ratio": "1:1"},
    "safe_margin_px": 32,
    "scene_and_type_are_separate_layers": True,
    "korean_text_is_composited_after_visual_generation": True,
    "text_must_stay_inside_selected_type_zone": True,
    "font_policy": {
        "title_family": "approved_korean_display_font",
        "body_family": "approved_korean_readable_font",
        "title_size_px": {"min": 32, "max": 54},
        "body_size_px": {"min": 20, "max": 27},
    },
    "role_order": list(CARD_ROLES),
    "forbidden": [
        "generated text in the scene image",
        "logo or watermark",
        "unsupported factual or numeric claims",
        "text outside the safe margin",
        "overlapping text zones",
        "unapproved colors or layout geometry",
        "public or scheduled publishing",
    ],
}

# Every layout is renderer-owned geometry. The model may choose an ID, never
# coordinates. Rectangles are [left, top, right, bottom] in the fixed canvas.
# Text regions are disjoint and contained by the panel in each approved layout.
LAYOUTS: dict[str, dict[str, dict[str, tuple[int, int, int, int]]]] = {
    "cover": {
        "lower_left_story": {
            "panel": (42, 510, 770, 1040), "title": (70, 584, 730, 682),
            "copy": (70, 700, 730, 770), "items": (70, 800, 730, 1020),
        },
        "lower_right_story": {
            "panel": (310, 510, 1038, 1040), "title": (350, 584, 998, 682),
            "copy": (350, 700, 998, 770), "items": (350, 800, 998, 1020),
        },
    },
    "flow": {
        "left_process_rail": {
            "panel": (36, 82, 462, 998), "title": (68, 168, 430, 302),
            "copy": (68, 326, 430, 426), "items": (68, 468, 430, 962),
        },
        "right_process_rail": {
            "panel": (618, 82, 1044, 998), "title": (650, 168, 1012, 302),
            "copy": (650, 326, 1012, 426), "items": (650, 468, 1012, 962),
        },
    },
    "comparison": {
        "upper_comparison_band": {
            "panel": (72, 42, 1008, 490), "title": (104, 116, 976, 205),
            "copy": (104, 218, 976, 275), "items": (104, 310, 976, 470),
        },
        "lower_comparison_band": {
            "panel": (72, 590, 1008, 1038), "title": (104, 664, 976, 753),
            "copy": (104, 766, 976, 823), "items": (104, 858, 976, 1018),
        },
    },
    "checklist": {
        "right_action_column": {
            "panel": (532, 102, 1042, 1008), "title": (562, 182, 1002, 304),
            "copy": (562, 322, 1002, 430), "items": (562, 464, 1002, 974),
        },
        "left_action_column": {
            "panel": (38, 102, 548, 1008), "title": (68, 182, 508, 304),
            "copy": (68, 322, 508, 430), "items": (68, 464, 508, 974),
        },
    },
    "decision": {
        "lower_right_verdict": {
            "panel": (350, 530, 1038, 1038), "title": (380, 608, 1000, 702),
            "copy": (380, 714, 1000, 775), "items": (380, 800, 1000, 1010),
        },
        "lower_left_verdict": {
            "panel": (42, 530, 730, 1038), "title": (72, 608, 700, 702),
            "copy": (72, 714, 700, 775), "items": (72, 800, 700, 1010),
        },
    },
}

PALETTES: dict[str, tuple[str, ...]] = {
    "cool_editorial": ("#FFFFFF", "#F4F5F7", "#C9B8E8", "#A7DDCE", "#F7A6A6"),
    "blue_mint": ("#FFFFFF", "#EFF6FA", "#3D78B7", "#8EC2B5", "#D7E8F2"),
    "mono_coral": ("#FFFFFF", "#F2F2F2", "#202329", "#D84B45", "#D7D7D7"),
    "rose_sage": ("#FFFFFF", "#F5F6F7", "#C85B70", "#B7C9B0", "#AFC5DA"),
}
MEDIA = frozenset({"photo", "illustration", "diagram"})
CROPS = frozenset({"subject_left", "subject_center", "subject_right", "wide_scene", "close_detail"})
EMPHASIS = frozenset({"key_object", "hand_action", "comparison", "sequence", "decision_point"})


class DesignContractError(ValueError):
    """Raised when a design choice attempts to escape the fixed contract."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


FIXED_RULES_SHA256 = hashlib.sha256(_canonical_json(FIXED_RULES)).hexdigest()


def validate_layout_library() -> None:
    """Check renderer-owned geometry once; unsafe presets fail closed."""
    required_zones = ("title", "copy", "items")
    for role, layouts in LAYOUTS.items():
        if not layouts:
            raise DesignContractError(f"{role}: no approved layouts")
        for layout_name, zones in layouts.items():
            panel = zones.get("panel")
            if set(zones) != {"panel", *required_zones} or not _valid_rect(panel):
                raise DesignContractError(f"{role}/{layout_name}: invalid panel or text zones")
            px1, py1, px2, py2 = panel
            if min(px1, py1, 1080 - px2, 1080 - py2) < FIXED_RULES["safe_margin_px"]:
                raise DesignContractError(f"{role}/{layout_name}: panel violates safe margin")
            rects: list[tuple[str, tuple[int, int, int, int]]] = []
            for name in required_zones:
                rect = zones[name]
                if not _valid_rect(rect):
                    raise DesignContractError(f"{role}/{layout_name}: invalid {name} rectangle")
                x1, y1, x2, y2 = rect
                if x1 < px1 or y1 < py1 or x2 > px2 or y2 > py2:
                    raise DesignContractError(f"{role}/{layout_name}: {name} outside panel")
                rects.append((name, rect))
            for index, (name, rect) in enumerate(rects):
                for other_name, other in rects[index + 1:]:
                    if _rectangles_overlap(rect, other):
                        raise DesignContractError(
                            f"{role}/{layout_name}: {name} overlaps {other_name}"
                        )


def _valid_rect(rect: Any) -> bool:
    return (
        isinstance(rect, tuple) and len(rect) == 4
        and all(isinstance(value, int) for value in rect)
        and rect[0] < rect[2] and rect[1] < rect[3]
    )


def _rectangles_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]


validate_layout_library()


@dataclass(frozen=True)
class CardChoice:
    role: str
    layout: str
    medium: str
    crop: str
    emphasis: str


@dataclass(frozen=True)
class DesignManifest:
    schema_version: str
    fixed_rules_sha256: str
    palette: str
    cards: tuple[CardChoice, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fixed_rules_sha256": self.fixed_rules_sha256,
            "palette": self.palette,
            "cards": [asdict(card) for card in self.cards],
        }


def build_manifest(selection: Mapping[str, Any]) -> DesignManifest:
    """Validate model-selected options; fixed geometry and QA rules stay local."""
    if set(selection) != {"palette", "cards"}:
        raise DesignContractError("selection must contain only palette and cards")
    palette = selection.get("palette")
    if palette not in PALETTES:
        raise DesignContractError("palette is not in the approved palette set")
    raw_cards = selection.get("cards")
    if not isinstance(raw_cards, list) or len(raw_cards) != len(CARD_ROLES):
        raise DesignContractError("exactly five role-ordered card selections are required")

    cards: list[CardChoice] = []
    fields = {"role", "layout", "medium", "crop", "emphasis"}
    for expected_role, raw in zip(CARD_ROLES, raw_cards, strict=True):
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise DesignContractError(f"{expected_role}: selection fields are incomplete or unsupported")
        role = raw.get("role")
        layout = raw.get("layout")
        medium = raw.get("medium")
        crop = raw.get("crop")
        emphasis = raw.get("emphasis")
        if role != expected_role:
            raise DesignContractError("cards must keep the fixed five-role order")
        if layout not in LAYOUTS[expected_role]:
            raise DesignContractError(f"{expected_role}: layout is not approved for this role")
        if medium not in MEDIA or crop not in CROPS or emphasis not in EMPHASIS:
            raise DesignContractError(f"{expected_role}: medium, crop, or emphasis is not approved")
        cards.append(CardChoice(role, layout, medium, crop, emphasis))

    return DesignManifest(SCHEMA_VERSION, FIXED_RULES_SHA256, palette, tuple(cards))


def validate_manifest(value: Mapping[str, Any]) -> DesignManifest:
    """Revalidate a persisted manifest and reject stale or altered fixed rules."""
    if set(value) != {"schema_version", "fixed_rules_sha256", "palette", "cards"}:
        raise DesignContractError("manifest fields are incomplete or unsupported")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise DesignContractError("design manifest schema version mismatch")
    if value.get("fixed_rules_sha256") != FIXED_RULES_SHA256:
        raise DesignContractError("fixed design rules changed; regenerate the manifest")
    return build_manifest({"palette": value.get("palette"), "cards": value.get("cards")})


@dataclass(frozen=True)
class CardQAResult:
    card: int
    passed: bool
    reason: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class RetryPlan:
    retry_cards: tuple[int, ...]
    exhausted_cards: tuple[int, ...]
    passed_cards: tuple[int, ...]
    all_passed: bool


def plan_targeted_retries(
    results: list[CardQAResult], attempts_by_card: Mapping[int, int],
) -> RetryPlan:
    """Retry only failed, retryable cards; never schedule a passed card."""
    if len(results) != 5 or {result.card for result in results} != set(range(1, 6)):
        raise DesignContractError("design QA must return exactly one result for cards 1 through 5")
    retry_cards: list[int] = []
    exhausted: list[int] = []
    passed: list[int] = []
    for result in sorted(results, key=lambda item: item.card):
        if result.passed:
            passed.append(result.card)
            continue
        if not result.reason.strip():
            raise DesignContractError(f"card {result.card}: failed QA requires a preserved reason")
        attempts = attempts_by_card.get(result.card, 0)
        if not isinstance(attempts, int) or attempts < 0:
            raise DesignContractError(f"card {result.card}: retry count is invalid")
        if result.retryable and attempts < MAX_TARGETED_RETRIES:
            retry_cards.append(result.card)
        else:
            exhausted.append(result.card)
    return RetryPlan(tuple(retry_cards), tuple(exhausted), tuple(passed), not exhausted and not retry_cards)


def verify_unchanged_cards(
    before_sha256: Mapping[int, str], after_sha256: Mapping[int, str], retry_cards: set[int],
) -> None:
    """Fail if a targeted repair changed any card outside its retry set."""
    if set(before_sha256) != set(range(1, 6)) or set(after_sha256) != set(range(1, 6)):
        raise DesignContractError("before/after hashes must cover all five cards")
    changed_unrequested = [
        index for index in range(1, 6)
        if index not in retry_cards and before_sha256[index] != after_sha256[index]
    ]
    if changed_unrequested:
        raise DesignContractError(f"targeted repair changed non-target cards: {changed_unrequested}")
