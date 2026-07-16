from pydantic import BaseModel


class OnboardingOut(BaseModel):
    completed: bool
