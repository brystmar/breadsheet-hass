"""Where a bake stands: its current and next steps, and progress. Pure functions of the schedule."""

from __future__ import annotations

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
