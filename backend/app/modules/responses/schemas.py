from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class StepSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    answer: Optional[Dict[str, Any]] = None
    skip: bool = False


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
