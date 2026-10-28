from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BankCreateRequest(StrictInput):
    name: str = Field(min_length=1, max_length=120)


class BankItemRequest(StrictInput):
    family_id: str = Field(min_length=1, max_length=64)
    version_id: str = Field(min_length=1, max_length=64)


class BankItemVersionRequest(StrictInput):
    version_id: str = Field(min_length=1, max_length=64)


class SurveyQuestionImportRequest(StrictInput):
    bank_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ShareQuestionVersionRequest(StrictInput):
    recipient_username: str = Field(min_length=1, max_length=64)

    @field_validator("recipient_username", mode="before")
    @classmethod
    def trim_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


