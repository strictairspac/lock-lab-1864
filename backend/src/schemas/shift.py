from pydantic import BaseModel, Field


class ShiftSign(BaseModel):
    shift_id: int = Field(gt=0)
    next_code: str = Field(default='', max_length=80)
