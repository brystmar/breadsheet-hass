"""Tests for parsing breadsheet's bake objects."""

from datetime import datetime, timezone

import pytest

from custom_components.breadsheet.models import Bake, parse_bakes

from .conftest import BAKE_ID, bake_json, step_json


def test_a_real_bake_parses() -> None:
    raw = bake_json()
    raw["steps"][0] = step_json(1, "2026-11-09T18:00:00+00:00", 3600, completed_at="2026-11-09T18:02:00-08:00")
    raw["steps"][1] = step_json(2, None, 25200, skipped=True)
    bake = Bake.from_api(raw)
    assert bake.projected_end == datetime(2026, 11, 10, 2, tzinfo=timezone.utc)
    assert bake.steps[0].completed_at == datetime(2026, 11, 10, 2, 2, tzinfo=timezone.utc)  # stored in UTC
    assert [step.done for step in bake.steps] == [True, True, False]
    assert bake.steps[1].scheduled_at is None


def test_unknown_fields_are_ignored() -> None:
    raw = bake_json()
    raw["something_new"] = {"from": "a later milestone"}
    raw["steps"][0]["also_new"] = 1
    assert list(parse_bakes([raw])) == [BAKE_ID]


@pytest.mark.parametrize(("change", "where"), [
    (lambda b: b.update(projected_end="tomorrow"), "projected_end"),
    (lambda b: b.update(projected_end="2026-11-10T02:00:00"), "no UTC offset"),
    (lambda b: b.update(name=None), "name"),
    (lambda b: b.update(steps=[]), "steps"),
    (lambda b: b["steps"][0].update(wait_chosen="3600"), "wait_chosen"),
    (lambda b: b["steps"][0].update(position=True), "position"),
    (lambda b: b["steps"][0].pop("hands_on"), "hands_on"),
    (lambda b: b.update(id=""), "no id"),
])
def test_malformed_bakes_are_refused(change, where: str) -> None:
    raw = bake_json()
    change(raw)
    with pytest.raises(ValueError):
        parse_bakes([raw])
