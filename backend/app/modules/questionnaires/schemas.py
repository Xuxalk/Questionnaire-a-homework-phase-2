from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SurveyCreateRequest(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    anonymous: bool
    allow_multiple_submissions: bool

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SurveyUpdateRequest(StrictInput):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    anonymous: Optional[bool] = None
    allow_multiple_submissions: Optional[bool] = None

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("title")
    @classmethod
    def title_cannot_be_null(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            raise ValueError("title cannot be null")
        return value


class DeadlineRequest(StrictInput):
    deadline: Optional[datetime]

    @field_validator("deadline")
    @classmethod
    def require_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline must include a timezone")
        return value.astimezone(timezone.utc)


class LibraryQuestionReferenceRequest(StrictInput):
    source_question_id: str = Field(min_length=1, max_length=64)
    source_version_id: str = Field(min_length=1, max_length=64)


class OptionInput(StrictInput):
    option_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=300)

    @field_validator("label", mode="before")
    @classmethod
    def trim_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class JumpRuleInput(StrictInput):
    priority: int = Field(ge=1)
    condition: Dict[str, Any]
    target_question_id: str = Field(min_length=1, max_length=64)


class MultiChoiceValidation(StrictInput):
    min_selected: int = Field(ge=0)
    max_selected: int = Field(ge=1)


class TextValidation(StrictInput):
    min_length: Optional[int] = Field(default=None, ge=0)
    max_length: Optional[int] = Field(default=None, ge=0)


class NumberValidation(StrictInput):
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    integer_only: bool = False

    @field_validator("min_value", "max_value", mode="before")
    @classmethod
    def reject_boolean_bounds(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("number bounds must be numeric")
        return value

    @field_validator("min_value", "max_value")
    @classmethod
    def reject_non_finite_bounds(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("number bounds must be finite")
        return value


class QuestionBase(StrictInput):
    prompt: str = Field(min_length=1, max_length=1000)
    required: bool
    jump_rules: List[JumpRuleInput] = Field(default_factory=list, max_length=100)

    @field_validator("prompt", mode="before")
    @classmethod
    def trim_prompt(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SingleChoiceQuestionInput(QuestionBase):
    type: Literal["single_choice"]
    options: List[OptionInput] = Field(min_length=1, max_length=100)


class MultiChoiceQuestionInput(QuestionBase):
    type: Literal["multi_choice"]
    options: List[OptionInput] = Field(min_length=1, max_length=100)
    validation: MultiChoiceValidation


class TextQuestionInput(QuestionBase):
    type: Literal["text"]
    validation: TextValidation = Field(default_factory=TextValidation)


class NumberQuestionInput(QuestionBase):
    type: Literal["number"]
    validation: NumberValidation = Field(default_factory=NumberValidation)


QuestionInput = Union[
    SingleChoiceQuestionInput,
    MultiChoiceQuestionInput,
    TextQuestionInput,
    NumberQuestionInput,
]
