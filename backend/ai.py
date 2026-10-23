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
from typing import List, Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent, TextDelta, StreamDone

log = logging.getLogger("ai")
OUTCOMES = {"ai_verified", "more_evidence_required", "manual_review_required", "potential_noncompliance"}


@dataclass
class AssessmentInput:
    nc_number: str
    description: str
    required_work: str
    safety_critical: bool
    reference_images: List[bytes] = field(default_factory=list)  # TrustMark inspection photos
    evidence_images: List[bytes] = field(default_factory=list)   # photos + video frames
    original_text: str = ""
    labels: List[str] = field(default_factory=list)  # one label per evidence image (PRE-/MID-/FINAL-REMEDIATION photo, VIDEO FRAME)
    installer_note: str = ""
    requires_stages: bool = False


@dataclass
class AssessmentResult:
    outcome: str
    confidence: float
    feedback: str
    concerns: List[str]
    model: str
    risk: str = "standard"
    method_demonstrated: Optional[bool] = None
    requests: List[str] = field(default_factory=list)
    works_description: str = ""
    best: dict = field(default_factory=dict)  # stage -> evidence image index

SYSTEM = """You are a practical, competent remediation evidence assessor for TrustMark non-compliances (NCs).
You receive the NC wording, the required remedial work, ORIGINAL inspection photos (REFERENCE, may be absent), the installer's
note, and labelled EVIDENCE images: PRE-REMEDIATION (original defect), MID-REMEDIATION (the corrective method, incl. concealed work),
FINAL-REMEDIATION (completed work) photos and VIDEO FRAMES from the NC's remediation video.
Verify the METHOD of remediation, not just the finished appearance: a finished surface does not prove what is behind it
(e.g. a rendered wall does not prove insulation behind a bracket - a mid-remediation photo must show it).
Rules:
- ai_verified: the evidence is relevant to THIS NC and convincingly shows the required corrective method and the completed result.
  Reasonable visual judgement and approximate measurements are allowed ("estimated"). Do not demand tools/measurements unless essential.
- more_evidence_required: evidence is missing, unclear, unrelated, or the method is not visible. List precise requests for the
  installer, e.g. "Please upload a mid-remediation photograph showing insulation fitted behind the bracket." Max 3 requests.
- potential_noncompliance: the evidence suggests the work was done incorrectly or does not meet the required work.
- manual_review_required: genuinely ambiguous, technically complex, or completion depends on hidden/tested items you cannot see.
- Never claim ratings, testing or regulatory compliance are verified.
- A generic or unrelated video does not count as evidence for this NC.
- risk: "safety" for gas, electrical, fire, structural, combustion ventilation / flues / CO; "low" for simple visually verifiable
  work (sealing, filling, lagging, vents, trims, draught strips, making good, cleaning); otherwise "standard".
- works_description: 20-40 words, one or two professional sentences stating what was corrected and how, using ONLY what the NC,
  the installer note and the visible evidence establish. Never invent materials or methods. Empty string if it cannot be established.
- best: for each stage present, the index (0-based, among EVIDENCE images only) of the clearest, most technically useful photo.
Respond with ONLY JSON: {"outcome": "ai_verified|more_evidence_required|potential_noncompliance|manual_review_required",
"risk": "low|standard|safety", "confidence": 0.0-1.0, "method_demonstrated": true|false,
"feedback": "<= 2 short plain-English sentences for the installer", "concerns": ["short items"],
"requests": ["precise evidence requests"], "works_description": "...", "best": {"pre": 0, "mid": 3, "final": 5}}"""


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
                f"Installer note: {data.installer_note or '(none)'}\n"
                f"The first {len(data.reference_images)} image(s) are REFERENCE inspection photos; "
                f"the remaining {len(data.evidence_images)} are EVIDENCE, in order (index: label):\n"
                + "\n".join(f"{i}: {data.labels[i] if i < len(data.labels) else 'EVIDENCE photo'}" for i in range(len(data.evidence_images))))
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
            md = j.get("method_demonstrated")
            return AssessmentResult(outcome, float(j.get("confidence", 0)), str(j.get("feedback", ""))[:300],
                                    [str(c)[:120] for c in j.get("concerns", [])][:5], self.model_name, risk,
                                    md if isinstance(md, bool) else None,
                                    [str(r)[:200] for r in (j.get("requests") or []) if r][:3],
                                    " ".join(str(j.get("works_description") or "").split()[:60]),
                                    {k: v for k, v in (j.get("best") or {}).items() if isinstance(v, int)})
        except Exception:
            log.warning("Unparseable AI output: %s", raw[:300])
            return AssessmentResult("manual_review_required", None, "Sent to an employee for review.",
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


PHOTO_SYSTEM = """You arrange installer remediation photos for a TrustMark evidence PDF for ONE non-compliance (NC).
You receive the NC wording, the installer note, any existing remedial-works text, and numbered installer photos (0-based, in upload
order with capture times). Upload order alone does NOT prove the stage - judge by content.
Choose at most ONE photo per stage:
- pre: the original defect / condition before corrective work
- mid: the corrective method or work in progress (concealed work before it was covered)
- final: the completed remedial work
Use null for a stage when no photo clearly shows it - never guess a label. Exclude duplicates, blurred and irrelevant photos.
"extra": up to 2 further indexes ONLY if technically necessary (e.g. a second defect area or a measurement).
"uncertain": true if any chosen stage is doubtful or a needed stage is missing.
"description": 20-40 words, one or two professional sentences stating what was corrected and how, using ONLY what the NC, installer
note and photos establish. Do not repeat the NC wording. Never invent materials or methods. Empty string if it cannot be established.
Respond with ONLY JSON: {"pre": 0|null, "mid": 2|null, "final": 5|null, "extra": [], "uncertain": false, "description": "..."}"""


async def arrange_photos(nc_text: str, required: str, installer_note: str, works_text: str, photos: List[bytes], times: List[str]) -> Optional[dict]:
    """Gemini picks/labels the PDF photo sequence and drafts the works description. None when AI is unavailable."""
    if not photos:
        return None
    try:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"pdf-photos-{uuid.uuid4()}",
                       system_message=PHOTO_SYSTEM).with_model(os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        listing = "\n".join(f"{i}: captured {t or 'unknown'}" for i, t in enumerate(times))
        msg = UserMessage(text=f"NC: {nc_text[:1500]}\nRequired work: {required[:600]}\nInstaller note: {installer_note or '(none)'}\n"
                               f"Existing remedial works text: {works_text or '(none)'}\nPhotos:\n{listing}",
                          file_contents=[ImageContent(image_base64=base64.b64encode(b).decode()) for b in photos])
        out = await chat.send_message(msg)
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        j = json.loads(m.group(0)) if m else {}
        n = len(photos)
        pick = lambda v: v if isinstance(v, int) and 0 <= v < n else None
        return {"pre": pick(j.get("pre")), "mid": pick(j.get("mid")), "final": pick(j.get("final")),
                "extra": [x for x in (j.get("extra") or []) if pick(x) is not None][:2], "uncertain": bool(j.get("uncertain")),
                "description": " ".join(str(j.get("description") or "").split()[:60])}
    except Exception as e:
        log.warning("photo arrangement failed: %s", type(e).__name__)
        return None
