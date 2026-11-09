"""Where a bake stands: its current and next steps, and progress. Pure functions of the schedule."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from .models import Bake, BakeStep


def next_step(bake: Bake) -> BakeStep | None:
    """The first step that isn't done (checked off or skipped): the one to do next."""
    return next((step for step in bake.steps if not step.done), None)


def current_step(bake: Bake) -> BakeStep | None:
    """The last step checked off, whose wait is running. None before the first check-off."""
    checked_off = [step for step in bake.steps if step.completed_at is not None and not step.skipped]
    return checked_off[-1] if checked_off else None


def step_after(bake: Bake, step: BakeStep) -> BakeStep | None:
    """The next step after `step` that isn't skipped."""
    return next((later for later in bake.steps if later.position > step.position and not later.skipped), None)


def progress(bake: Bake) -> tuple[int, int]:
    """(steps checked off, steps in the bake), leaving out skipped steps."""
    steps = [step for step in bake.steps if not step.skipped]
    return sum(step.completed_at is not None for step in steps), len(steps)


def with_check_off(bake: Bake, position: int, completed_at: datetime) -> Bake:
    """
    The bake with one step checked off and the schedule re-projected, the way breadsheet's `Bake.project()` does
    it: each later step is due at the previous step's check-off (or due time, if not checked off) plus that
    previous step's chosen wait. Steps before this one keep their times. Unchanged if the step is already done.
    """
    steps = list(bake.steps)
    index = next((i for i, step in enumerate(steps) if step.position == position), None)
    if index is None or steps[index].done:
        return bake
    steps[index] = replace(steps[index], completed_at=completed_at)
    time = completed_at + timedelta(seconds=steps[index].wait_chosen)
    for i in range(index + 1, len(steps)):
        step = steps[i]
        if step.skipped:
            continue
        steps[i] = replace(step, scheduled_at=time)
        time = (step.completed_at or time) + timedelta(seconds=step.wait_chosen)
    return replace(bake, steps=tuple(steps), projected_end=time, status="in_progress")
