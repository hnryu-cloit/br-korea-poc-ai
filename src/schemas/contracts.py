from pydantic import BaseModel


class GenerationInput(BaseModel):
    product: str
    summary: str
    goals: list[str]
    features: list[str]


class BrandProfile(BaseModel):
    hospital_name: str
    target_audience: str
    doctor_philosophy: str
    signature_procedures: list[str]
    brand_tone: list[str]
    banned_terms: list[str]


class GenerationOutput(BaseModel):
    draft: str
    review_notes: list[str]
    channels: dict[str, dict[str, str]]
