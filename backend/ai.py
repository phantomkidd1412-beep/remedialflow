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
    original_text: str = ""


@dataclass
class AssessmentResult:
    outcome: str
    confidence: float
    feedback: str
    concerns: List[str]
    model: str
    risk: str = "standard"

SYSTEM = """You are a practical remediation evidence assessor for TrustMark non-compliances (NCs) — efficient, not an over-strict inspector.
You receive: the NC description (and original TrustMark wording), the required remedial work, ORIGINAL inspection photos
(labelled REFERENCE, may be absent) and installer EVIDENCE photos/video frames. Compare the evidence with the NC and required work.
Rules:
- If the photos convincingly show the required remedial work completed (e.g. a vent perimeter visibly sealed, pipes visibly lagged, holes visibly filled), outcome = ai_verified.
- Reasonable visual judgement and approximate measurements are allowed when supported by the evidence; say "estimated" for estimates. Do not demand a measuring tape unless an exact measurement is essential to the NC.
- Never claim concealed work, ratings, testing or regulatory compliance is verified. If completion genuinely depends on something not visible, outcome = manual_review_required.
- If photos are blurry, unrelated, or do not show the area/completion, outcome = more_evidence_required (say exactly what photo is missing).
- If genuinely uncertain or technically complex, outcome = manual_review_required.
- risk: "safety" for gas, electrical, fire, structural, combustion ventilation / flues / CO; "low" for simple visually verifiable work (sealing, filling, lagging, vents, trims, draught strips, making good, cleaning, insulation top-up); otherwise "standard".
Respond with ONLY JSON: {"outcome": "ai_verified|more_evidence_required|manual_review_required", "risk": "low|standard|safety",
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
        text = (f"NC {data.nc_number}: {data.description}\nOriginal TrustMark wording: {data.original_text or data.description}\n"
                f"Required remedial work: {data.required_work}\n"
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
            risk = j.get("risk") if j.get("risk") in ("low", "standard", "safety") else "standard"
            return AssessmentResult(outcome, float(j.get("confidence", 0)), str(j.get("feedback", ""))[:300],
                                    [str(c)[:120] for c in j.get("concerns", [])][:5], self.model_name, risk)
        except Exception:
            log.warning("Unparseable AI output: %s", raw[:300])
            return AssessmentResult("manual_review_required", 0, "Sent to an employee for review.",
                                    ["AI response could not be read"], self.model_name)


PDF_SYSTEM = """You extract structured data from TrustMark / retrofit inspection reports (PDF).
Return ONLY JSON:
{"ref": "project/job reference or null", "address": "full property address without postcode or null",
 "postcode": "UK postcode or null", "nc_count_stated": <integer or null>,
 "ncs": [{"original_text": "the COMPLETE NC observation copied verbatim, unchanged",
          "description": "short plain summary (max 15 words)", "required_work": "remedial work required, copied verbatim",
          "safety_critical": true|false, "pages": [1-based page numbers where this NC and its photos appear]}],
 "confidence": 0.0-1.0, "notes": "short note about anything unclear"}
Rules: copy values exactly as written; never invent references or addresses (use null).
If the report only states a number of NCs without details, create that many entries with description "NC <n> – details not in report" and required_work "See inspection report".
safety_critical = electrical, gas, fire, structural, combustion ventilation or carbon monoxide related."""


class PdfExtractor:
    def __init__(self, provider: str, model: str):
        self.provider, self.model_name = provider, model

    async def extract(self, pdf_path: str, text_hint: str) -> dict:
        from emergentintegrations.llm.chat import FileContentWithMimeType
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"pdf-{uuid.uuid4()}",
                       system_message=PDF_SYSTEM).with_model(self.provider, self.model_name)
        msg = UserMessage(text=f"Extract the project details and every NC from this report.\n\nExtracted text (may be partial):\n{text_hint[:20000]}",
                          file_contents=[FileContentWithMimeType(mime_type="application/pdf", file_path=pdf_path)])
        out = ""
        async for ev in chat.stream_message(msg):
            if isinstance(ev, TextDelta):
                out += ev.content
            elif isinstance(ev, StreamDone):
                break
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            raise ValueError("AI returned no data")
        j = json.loads(m.group(0))
        ncs = []
        for n in j.get("ncs") or []:
            if not isinstance(n, dict):
                continue
            ncs.append({"description": str(n.get("description") or "").strip()[:500],
                        "original_text": str(n.get("original_text") or n.get("description") or "").strip(),
                        "required_work": str(n.get("required_work") or "").strip(),
                        "safety_critical": bool(n.get("safety_critical")),
                        "pages": [int(p) for p in (n.get("pages") or []) if str(p).isdigit()][:6]})
        return {"ref": j.get("ref"), "address": j.get("address"), "postcode": j.get("postcode"),
                "nc_count_stated": j.get("nc_count_stated"), "ncs": ncs,
                "confidence": float(j.get("confidence") or 0), "notes": str(j.get("notes") or "")[:300]}


def get_pdf_extractor() -> PdfExtractor:
    return PdfExtractor(os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_DOC_MODEL", "gemini-3-flash-preview"))


def get_assessor() -> Assessor:
    return LlmAssessor(os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
