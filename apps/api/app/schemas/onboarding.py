from typing import Literal

from pydantic import BaseModel


class OnboardingStepOut(BaseModel):
    key: str
    label: str
    description: str
    kind: Literal["main", "recommended", "milestone"]
    completed: bool
    action_label: str
    action_href: str


class OnboardingOut(BaseModel):
    completed: bool
    main_configuration_complete: bool
    completed_steps: int
    total_steps: int
    steps: list[OnboardingStepOut]
