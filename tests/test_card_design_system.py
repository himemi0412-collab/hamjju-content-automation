from copy import deepcopy

import pytest

from app.card_design_system import (
    CARD_ROLES,
    FIXED_RULES,
    FIXED_RULES_SHA256,
    LAYOUTS,
    CardQAResult,
    DesignContractError,
    build_manifest,
    layout_geometry,
    plan_targeted_retries,
    validate_manifest,
    validate_layout_library,
    verify_unchanged_cards,
)


def selection():
    return {
        "palette": "cool_editorial",
        "cards": [
            {"role": role, "layout": next(iter(LAYOUTS[role])), "medium": "photo",
             "crop": "subject_right", "emphasis": "key_object"}
            for role in CARD_ROLES
        ],
    }


def test_manifest_binds_only_allowed_variants_to_fixed_rules():
    manifest = build_manifest(selection())
    data = manifest.to_dict()

    assert manifest.fixed_rules_sha256 == FIXED_RULES_SHA256
    assert tuple(card.role for card in manifest.cards) == CARD_ROLES
    assert validate_manifest(data) == manifest


def test_layout_geometry_is_deterministic_and_renderer_owned():
    manifest = build_manifest(selection())
    first = layout_geometry(manifest, 1)
    second = layout_geometry(manifest.to_dict(), 1)

    assert first == second
    assert first["panel"] == (42, 510, 770, 1040)
    assert set(first) == {"panel", "title", "copy", "items"}


def test_layout_geometry_rejects_invalid_card_number():
    manifest = build_manifest(selection())
    with pytest.raises(DesignContractError, match="between 1 and 5"):
        layout_geometry(manifest, 6)


def test_ai_selection_cannot_override_fixed_rules_or_geometry():
    unsafe = selection()
    unsafe["canvas"] = {"width": 999, "height": 999}
    with pytest.raises(DesignContractError, match="only palette and cards"):
        build_manifest(unsafe)

    unsafe = selection()
    unsafe["cards"][0]["coordinates"] = [0, 0, 1080, 1080]
    with pytest.raises(DesignContractError, match="fields are incomplete or unsupported"):
        build_manifest(unsafe)


@pytest.mark.parametrize("mutator, message", [
    (lambda value: value.update(palette="yellow_beige"), "palette"),
    (lambda value: value["cards"].pop(), "five role-ordered"),
    (lambda value: value["cards"][0].update(layout="freeform"), "layout"),
    (lambda value: value["cards"][2].update(role="flow"), "role order"),
    (lambda value: value["cards"][1].update(medium="text_generated"), "medium, crop"),
])
def test_invalid_design_choices_fail_closed(mutator, message):
    value = selection()
    mutator(value)
    with pytest.raises(DesignContractError, match=message):
        build_manifest(value)


def test_manifest_rejects_stale_fixed_rules_fingerprint():
    data = build_manifest(selection()).to_dict()
    data["fixed_rules_sha256"] = "0" * 64

    with pytest.raises(DesignContractError, match="fixed design rules changed"):
        validate_manifest(data)


def test_fixed_rules_are_independent_of_model_selection():
    original = deepcopy(FIXED_RULES)
    build_manifest(selection())
    assert FIXED_RULES == original
    assert FIXED_RULES["korean_text_is_composited_after_visual_generation"] is True
    assert FIXED_RULES["role_order"] == list(CARD_ROLES)


def test_layout_library_contains_safe_margins_and_nonoverlapping_text_regions():
    validate_layout_library()


def test_layout_library_rejects_overlapping_text_regions(monkeypatch):
    monkeypatch.setitem(LAYOUTS, "cover", {
        "unsafe": {
            "panel": (32, 32, 1048, 1048), "title": (100, 100, 600, 300),
            "copy": (200, 200, 700, 400), "items": (100, 500, 900, 900),
        },
    })
    with pytest.raises(DesignContractError, match="overlaps"):
        validate_layout_library()


def test_design_qa_retries_only_failed_cards_and_preserves_reason():
    results = [CardQAResult(i, True) for i in (1, 3, 4, 5)]
    results.insert(1, CardQAResult(2, False, "subject anatomy defect", retryable=True))

    plan = plan_targeted_retries(results, {2: 0})

    assert plan.retry_cards == (2,)
    assert plan.passed_cards == (1, 3, 4, 5)
    assert plan.exhausted_cards == ()
    assert plan.all_passed is False


def test_retry_limit_and_nonretryable_failures_stop_without_success():
    results = [CardQAResult(i, True) for i in (1, 2, 4, 5)]
    results.append(CardQAResult(3, False, "composition defect", retryable=True))

    exhausted = plan_targeted_retries(results, {3: 2})
    blocked = plan_targeted_retries(
        [CardQAResult(i, True) for i in (1, 2, 3, 5)] +
        [CardQAResult(4, False, "unparseable QA report", retryable=False)], {},
    )

    assert exhausted.retry_cards == ()
    assert exhausted.exhausted_cards == (3,)
    assert exhausted.all_passed is False
    assert blocked.retry_cards == ()
    assert blocked.exhausted_cards == (4,)


def test_retry_plan_rejects_missing_or_duplicate_card_qa():
    incomplete = [CardQAResult(i, True) for i in range(1, 5)]
    duplicate = [CardQAResult(i, True) for i in range(1, 5)] + [CardQAResult(4, True)]

    with pytest.raises(DesignContractError, match="exactly one result"):
        plan_targeted_retries(incomplete, {})
    with pytest.raises(DesignContractError, match="exactly one result"):
        plan_targeted_retries(duplicate, {})


def test_selective_repair_requires_non_target_card_hashes_to_stay_identical():
    before = {i: f"before-{i}" for i in range(1, 6)}
    after = dict(before)
    after[2] = "repaired-2"
    verify_unchanged_cards(before, after, {2})

    after[4] = "accidental-change-4"
    with pytest.raises(DesignContractError, match=r"non-target cards: \[4\]"):
        verify_unchanged_cards(before, after, {2})
