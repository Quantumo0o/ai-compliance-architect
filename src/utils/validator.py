from pydantic import BaseModel, Field
from typing import List, Optional

class Requirement(BaseModel):
    req_id: Optional[str] = Field(None, description="Unique ID for the requirement (e.g., REQ-001)")
    text: str = Field(..., description="The full text of the requirement")
    section_id: Optional[str] = Field(None, description="The section ID (e.g., 4.2.1)")
    section_title: Optional[str] = Field(None, description="The section title")
    page_number: Optional[int] = Field(None, description="The page number where the requirement was found")
    obligation_level: str = Field(..., description="Mandatory, Optional, etc.")
    category: str = Field(..., description="Operational, Security, Financial, etc.")

class RequirementList(BaseModel):
    requirements: List[Requirement]

class Adjudication(BaseModel):
    compliance_status: str = Field(..., description="Fully Compliant, Partially Compliant, Non-Compliant")
    confidence_score: float = Field(..., description="Confidence score from 0 to 1")
    evidence_summary: str = Field(..., description="Summary of the evidence found")
    source_document: str = Field(..., description="The source document used for evidence")
    exact_quote: str = Field(..., description="Exact quote from the evidence")
    gap_analysis: str = Field(..., description="Comparison between requirement and our capability")
