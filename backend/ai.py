"""AI assessment layer. Callers use `get_assessor()`; swap model/provider with AI_PROVIDER / AI_MODEL
env vars or add another `Assessor` subclass."""
import os
import json
import re
import uuid
import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent, TextDelta, StreamDone

log = logging.getLogger("ai")
OUTCOMES = {"ai_verified", "more_evidence_required", "manual_review_required"}


@dataclass
class AssessmentInput:
    nc_number: str
    description: str
    required_work: str
    safety_critical: bool
    reference_images: List[bytes] = field(default_factory=list)  # TrustMark inspection photos
    evidence_images: List[bytes] = field(default_factory=list)   # photos + video frames


@dataclass
class AssessmentResult:
    outcome: str
    confidence: float
    feedback: str
    concerns: List[str]
    model: str


SYSTEM = """You assess photographic evidence of remedial building work for TrustMark non-compliances (NCs).
You receive: the NC description, the required remedial work, ORIGINAL inspection photos (labelled REFERENCE) and
installer EVIDENCE photos/video frames. Compare evidence against the NC and reference photos.
Strict rules:
- Judge only what is clearly visible. NEVER infer concealed work, measurements, fixings, ratings or technical/regulatory compliance from appearance.
- If the required work cannot be fully confirmed visually (concealed, needs measurement, needs testing), outcome = manual_review_required.
- If photos are blurry, unrelated, missing the area, or do not show completion, outcome = more_evidence_required.
- If anything is ambiguous or safety-critical (electrical, gas, structural, fire, ventilation of combustion appliances), outcome = manual_review_required.
- Only use ai_verified when the completed remedial work is unambiguously and fully visible.
Respond with ONLY JSON: {"outcome": "ai_verified|more_evidence_required|manual_review_required",
"confidence": 0.0-1.0, "feedback": "<= 2 short plain-English sentences for the installer", "concerns": ["short items"]}"""


class Assessor(ABC):
    model_name = ""

    @abstractmethod
    async def assess(self, data: AssessmentInput) -> AssessmentResult: ...


class LlmAssessor(Assessor):
    def __init__(self, provider: str, model: str):
        self.provider, self.model_name = provider, model

    async def assess(self, data: AssessmentInput) -> AssessmentResult:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"assess-{uuid.uuid4()}",
                       system_message=SYSTEM).with_model(self.provider, self.model_name)
        imgs = [ImageContent(image_base64=base64.b64encode(b).decode()) for b in data.reference_images + data.evidence_images]
        text = (f"NC {data.nc_number}: {data.description}\nRequired remedial work: {data.required_work}\n"
                f"Safety-critical flag: {data.safety_critical}\n"
                f"The first {len(data.reference_images)} image(s) are REFERENCE inspection photos; "
                f"the remaining {len(data.evidence_images)} are EVIDENCE.")
        out = ""
        async for ev in chat.stream_message(UserMessage(text=text, file_contents=imgs)):
            if isinstance(ev, TextDelta):
                out += ev.content
            elif isinstance(ev, StreamDone):
                break
        return self._parse(out)

    def _parse(self, raw: str) -> AssessmentResult:
        try:
            m = re.search(r"\{.*\}", raw, re.S)
            j = json.loads(m.group(0))
            outcome = j.get("outcome") if j.get("outcome") in OUTCOMES else "manual_review_required"
            return AssessmentResult(outcome, float(j.get("confidence", 0)), str(j.get("feedback", ""))[:300],
                                    [str(c)[:120] for c in j.get("concerns", [])][:5], self.model_name)
        except Exception:
            log.warning("Unparseable AI output: %s", raw[:300])
            return AssessmentResult("manual_review_required", 0, "Sent to an employee for review.",
                                    ["AI response could not be read"], self.model_name)


def get_assessor() -> Assessor:
    return LlmAssessor(os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
