"""
AI Transcription and Analysis Service

Uses:
- faster-whisper for fast, accurate speech-to-text transcription
- Ollama with Llama 3.1 8B for call analysis

Supported languages: Hindi, Arabic, Malayalam, English (and 90+ others)
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Staff extension mapping - maps extensions to staff details
STAFF_EXTENSION_MAP = {
    # Call Centre Team
    "201": {"name": "Jijina", "department": "Call Centre", "role": "Call Centre Agent"},
    "202": {"name": "Joanna", "department": "Call Centre", "role": "Call Centre Agent"},
    "203": {"name": "Ramshad", "department": "Call Centre", "role": "Call Centre Agent"},
    # Sales Team
    "207": {"name": "Saumil", "department": "Sales", "role": "Sales Agent"},
    "208": {"name": "Pranay", "department": "Sales", "role": "Sales Agent"},
    "209": {"name": "Sai", "department": "Sales", "role": "Sales Agent"},
}

def get_staff_from_extension(extension: str) -> dict:
    """Get staff details from extension number."""
    if extension:
        # Clean extension - remove any prefix
        ext_clean = str(extension).strip()
        if ext_clean in STAFF_EXTENSION_MAP:
            return STAFF_EXTENSION_MAP[ext_clean]
    return {"name": None, "department": "Unknown", "role": "Unknown"}


def is_valid_transcript_for_analysis(transcript: str) -> tuple[bool, str]:
    """
    Check if transcript has enough meaningful content for AI analysis.

    Returns:
        tuple: (is_valid, reason) - is_valid is True if transcript should be analyzed,
               reason explains why it's invalid if False
    """
    import re

    if not transcript:
        return False, "Empty transcript"

    cleaned = transcript.strip()

    # Check minimum character length
    if len(cleaned) < 20:
        return False, "Transcript too short"

    # Remove speaker labels and timestamps for word counting
    text_only = re.sub(r'\[SPEAKER_\d+\]:', '', cleaned)
    text_only = re.sub(r'\[\d+:\d+\]', '', text_only)
    text_only = re.sub(r'\[.*?\]', '', text_only)  # Remove any bracketed content
    text_only = text_only.strip()

    # Count meaningful words (at least 2 characters, not just punctuation)
    words = [w for w in text_only.split() if len(w) >= 2 and re.search(r'[a-zA-Z]', w)]
    word_count = len(words)

    # Need at least 5 meaningful words for analysis
    if word_count < 5:
        return False, f"Insufficient content ({word_count} words)"

    # Check for noise patterns - transcriptions that are just ring tones, music, or silence
    noise_patterns = [
        r'^[\s\.\,\!\?\-]+$',  # Only punctuation
        r'^(ring|ringing|beep|tone|music|silence|noise|static|hum|buzz|click)+[\s\,\.]*$',
        r'^(uh|um|hmm|ah|oh|eh|er)+[\s\,\.]*$',  # Only filler sounds
        r'^(hello|hi|hey|bye|goodbye|thank you|thanks|okay|ok|yes|no|yeah|yep|nope)[\s\,\.!?]*$',  # Only single greeting/farewell
    ]

    text_lower = text_only.lower().strip()
    for pattern in noise_patterns:
        if re.match(pattern, text_lower, re.IGNORECASE):
            return False, "Transcript contains only noise or minimal interaction"

    # Check if transcript is just repeated words (e.g., "hello hello hello" from ring back tone)
    unique_words = set(w.lower() for w in words if len(w) >= 3)
    if len(unique_words) < 3 and word_count >= 5:
        return False, "Transcript contains repetitive non-conversational content"

    # Check for actual conversational indicators
    conversational_indicators = [
        r'\b(what|how|when|where|why|who|can|could|would|should|is|are|do|does|have|has)\b',  # Question words
        r'\b(please|need|want|help|service|visa|id|emirates|company|license|document|appointment)\b',  # Service-related
        r'\b(yes|no|okay|sure|right|correct|exactly|understand)\b',  # Conversational responses
        r'\b(sir|madam|mam|mr|mrs|miss)\b',  # Polite address
        r'\b(call|calling|phone|number|contact|reach)\b',  # Call-related
        r'\b(thank|thanks|welcome|sorry|excuse)\b',  # Politeness markers
    ]

    has_conversation = False
    for pattern in conversational_indicators:
        if re.search(pattern, text_lower):
            has_conversation = True
            break

    if not has_conversation and word_count < 15:
        return False, "Transcript lacks conversational content"

    return True, "Valid transcript"

# Summary prompt template - detailed business-specific analysis with speaker identification
SUMMARY_PROMPT_TEMPLATE = """Analyze this phone call transcript between a STAFF member and a CUSTOMER.

═══════════════════════════════════════════════════════════════════════════════
COMPANY CONTEXT - OUR BUSINESSES:
═══════════════════════════════════════════════════════════════════════════════

1. AMER ALQUOZ GTC (Government Transaction Centre)
   Location: Al Barsha Mall, Al Barsha, Dubai, UAE
   Brand Names: Amer Centre, Amer Alquoz, Amer Al Barsha Mall
   Services:
   - Emirates ID services (new application, renewal, replacement, lost ID, status check, biometric update)
   - Visa services (tourist visa, visit visa, residence visa, Golden Visa, Green Visa, work permit, visa renewal, visa cancellation)
   - Attestation services (certificate attestation, document legalization, MOFA attestation, embassy attestation)
   - Typing services (application forms, government documents, legal typing)
   - Medical fitness test coordination and appointment booking
   - Entry permits and visa stamping (inside/outside country change status)
   - ICP (Federal Authority for Identity & Citizenship) related services
   - PRO services (Public Relations Officer services)
   - Labor card and work permit services
   - Family visa sponsorship and dependent visa
   - Tasheel services
   - GDRFA (General Directorate of Residency and Foreigners Affairs) services

2. NEXTURE CORPORATE SERVICES LLC
   Location: I-Rise Tower, Tecom, Barsha Heights, Dubai, UAE
   Brand Names: Nexture, Nexture Corporate, Nexture Business Setup
   Services:
   - Company formation (Mainland LLC, Free Zone, Offshore, Branch office)
   - Trade license services (new license, renewal, amendment, activity addition)
   - Business registration and DED licensing
   - Corporate bank account opening assistance
   - Office space solutions (Flexi-desk, virtual office, co-working space)
   - Corporate PRO services
   - Investor visa and partner visa processing
   - Business consultation and advisory
   - Company liquidation and deregistration
   - VAT registration, filing, and compliance
   - Corporate tax registration and compliance
   - Corporate tie-ups and partnerships
   - Real estate license services (broker, developer)
   - Import/export code registration
   - Ejari and tenancy contract services

═══════════════════════════════════════════════════════════════════════════════
STAFF EXTENSION DIRECTORY:
═══════════════════════════════════════════════════════════════════════════════
Call Centre Team:
- Extension 201: Jijina (Call Centre Agent)
- Extension 202: Joanna (Call Centre Agent)
- Extension 203: Ramshad (Call Centre Agent)

Sales Team:
- Extension 207: Saumil (Sales Agent)
- Extension 208: Pranay (Sales Agent)
- Extension 209: Sai (Sales Agent)

Use this mapping to identify staff if extension is mentioned or visible in recording filename.
The recording filename format includes the extension: e.g., "20251211-201-Outbound.wav" means extension 201 (Jijina).

═══════════════════════════════════════════════════════════════════════════════
SPEAKER IDENTIFICATION RULES:
═══════════════════════════════════════════════════════════════════════════════
- If transcript has speaker labels (SPEAKER_00, SPEAKER_01, etc.), SPEAKER_00 is typically the person who answered (STAFF)
- If someone greets with "Good morning/afternoon, Amer Centre" or company name, they are STAFF
- If someone asks about visa/Emirates ID/company setup services, they are CUSTOMER
- The person providing information/solutions is STAFF
- The person asking questions/requesting services is CUSTOMER
- Match staff names from the extension directory if identifiable

═══════════════════════════════════════════════════════════════════════════════
TRANSCRIPT:
═══════════════════════════════════════════════════════════════════════════════
{transcript}

═══════════════════════════════════════════════════════════════════════════════
RECORDING CONTEXT (if available):
═══════════════════════════════════════════════════════════════════════════════
{recording_context}

═══════════════════════════════════════════════════════════════════════════════
COMPREHENSIVE ANALYSIS - Return JSON:
═══════════════════════════════════════════════════════════════════════════════
{{
    "call_type": "visa_inquiry|emirates_id|attestation|company_setup|trade_license|golden_visa|green_visa|follow_up|complaint|consultation|support|general_inquiry|otp_verification|appointment_booking|status_check|document_collection|payment_inquiry|callback_request|internal|spam|wrong_number|other",
    "service_category": "Amer Centre Services|Nexture Corporate Services|Both|Unknown",
    "service_subcategory": "Specific service like 'Golden Visa', 'Trade License Renewal', 'Emirates ID Status', etc.",
    "summary": "2-3 sentence summary: What did CUSTOMER want? How did STAFF help? What was the outcome?",

    "staff_name": "Name of staff member (use extension directory if identifiable), otherwise null",
    "staff_extension": "Extension number if identifiable from recording or conversation, otherwise null",
    "staff_department": "Call Centre|Sales|Unknown based on extension directory",
    "customer_name": "Name of customer if mentioned, otherwise null",
    "customer_phone": "Customer's phone number if mentioned, formatted as +971-XX-XXX-XXXX",
    "company_name": "Customer's company name if mentioned (for corporate clients), otherwise null",

    "topics_discussed": ["List specific topics: e.g., 'Golden Visa eligibility', 'Trade license amendment process', 'Emirates ID renewal documents required'"],
    "customer_requests": ["Specific requests: e.g., 'Check visa status for application #12345', 'Get quote for mainland company setup', 'Schedule appointment for biometrics'"],
    "staff_responses": ["How staff addressed each request with specific details provided"],
    "action_items": ["Follow-up actions with owner: e.g., 'Customer to send passport copy on WhatsApp', 'Staff to email quotation', 'Customer to visit branch on Monday'"],
    "commitments_made": ["Promises made by staff: e.g., 'Will call back within 2 hours', 'Will send documents by email today'"],

    "resolution_status": "resolved|pending|escalated|requires_followup|transferred|callback_scheduled|unclear",

    "key_details": {{
        "application_numbers": ["Any application/reference/file numbers mentioned"],
        "transaction_ids": ["Any transaction or receipt numbers"],
        "phone_numbers": ["Format ALL as +971-XX-XXX-XXXX for UAE numbers"],
        "email_addresses": ["Any email addresses mentioned"],
        "amounts_mentioned": ["Any fees/costs with currency (e.g., 'AED 500', '1000 dirhams')"],
        "dates_deadlines": ["Any dates, deadlines, or timeframes mentioned"],
        "document_types": ["Documents mentioned: passport, Emirates ID, visa copy, trade license, MOA, etc."],
        "locations": ["Locations mentioned: branches, offices, government departments, free zones"],
        "passport_numbers": ["Any passport numbers mentioned (partial is fine)"],
        "emirates_id_numbers": ["Any Emirates ID numbers mentioned"],
        "company_license_numbers": ["Any trade license or company registration numbers"],
        "visa_file_numbers": ["Any visa file numbers or permit numbers"],
        "other_details": ["Any other critical information"]
    }},

    "call_classification": {{
        "is_sales_opportunity": true/false,
        "lead_quality": "hot|warm|cold|not_applicable",
        "lead_source": "new_inquiry|referral|repeat_customer|marketing_campaign|unknown",
        "estimated_deal_value": "Amount in AED if discussed, otherwise null",
        "conversion_likelihood": "high|medium|low|not_applicable",
        "competitor_mentioned": "Name of any competitor mentioned, otherwise null",
        "urgency_level": "immediate|within_week|within_month|no_urgency|unclear",
        "decision_maker": true/false (is the caller the decision maker?),
        "follow_up_required": true/false,
        "follow_up_date": "Specific date if mentioned, otherwise null",
        "lost_reason": "If opportunity lost, reason why (e.g., 'price too high', 'chose competitor', 'not ready')"
    }},

    "customer_profile": {{
        "customer_type": "individual|corporate|government|vip|repeat_customer|new_customer",
        "nationality": "Nationality if mentioned or identifiable",
        "language_preference": "Language used: English|Arabic|Hindi|Malayalam|Urdu|Other",
        "communication_channel_preference": "WhatsApp|Email|Phone|In-person|None mentioned",
        "special_requirements": ["Any special needs or requests"],
        "relationship_history": "first_contact|returning_customer|regular_client|vip|unknown"
    }},

    "mood_sentiment_analysis": {{
        "overall_sentiment": "positive|neutral|negative|mixed",
        "customer_mood": {{
            "initial": "calm|anxious|frustrated|angry|confused|happy|neutral|impatient|worried",
            "final": "satisfied|relieved|still_frustrated|angry|neutral|happy|unclear|appreciative|disappointed",
            "mood_change": "improved|worsened|unchanged|fluctuated"
        }},
        "staff_mood": {{
            "tone": "professional|friendly|helpful|indifferent|rushed|irritated|warm|empathetic",
            "patience_level": "excellent|good|adequate|low",
            "energy_level": "high|moderate|low"
        }},
        "call_atmosphere": "cordial|tense|rushed|collaborative|confrontational|neutral|warm|frustrating",
        "frustration_indicators": ["List any signs: raised voice, repeated questions, complaints, interruptions, etc."],
        "satisfaction_indicators": ["List any signs: thanks, appreciation, positive acknowledgment, willingness to proceed, etc."],
        "trust_indicators": ["Signs of trust: agreement to send documents, providing personal info, booking appointment, etc."]
    }},

    "employee_performance": {{
        "greeting_quality": "excellent|professional|casual|poor|none",
        "introduction": "Did staff introduce themselves and company? yes|partial|no",
        "active_listening": "excellent|good|adequate|poor",
        "knowledge_displayed": "excellent|good|adequate|poor",
        "problem_resolution": "resolved|partially_resolved|not_resolved|escalated|not_applicable",
        "communication_clarity": "excellent|clear|mostly_clear|unclear|confusing",
        "customer_handling": "excellent|good|needs_improvement|poor",
        "empathy_shown": "high|moderate|low|none",
        "response_time_perception": "prompt|acceptable|slow|very_slow",
        "proactive_suggestions": "Did staff offer additional helpful information? yes|partial|no",
        "upselling_attempted": "Did staff suggest additional services? yes|no|not_applicable",
        "closing_quality": "excellent|good|adequate|poor|abrupt",
        "follow_up_commitment": "yes_with_timeline|yes_vague|no|not_applicable",
        "hold_time_handling": "Was customer put on hold appropriately? yes|no|excessive|not_applicable",
        "professionalism_score": "1-10 rating based on overall conduct",
        "knowledge_score": "1-10 rating based on service knowledge",
        "communication_score": "1-10 rating based on communication skills",
        "empathy_score": "1-10 rating based on emotional intelligence",
        "overall_performance_score": "1-10 overall performance rating",
        "areas_for_improvement": ["Specific actionable suggestions"],
        "positive_highlights": ["What the employee did well"],
        "coaching_notes": ["Notes for manager/supervisor to discuss with employee"]
    }},

    "compliance_check": {{
        "data_protection": "Did staff handle personal data appropriately? yes|no|not_applicable",
        "service_accuracy": "Was information provided accurate? yes|partially|no|cannot_verify",
        "pricing_transparency": "Was pricing clearly communicated? yes|no|not_applicable",
        "terms_explained": "Were terms and conditions mentioned? yes|no|not_applicable",
        "inappropriate_promises": "Any inappropriate commitments made? none|list_if_any",
        "escalation_protocol": "Was escalation handled correctly? yes|no|not_applicable"
    }},

    "call_quality_metrics": {{
        "call_duration_assessment": "appropriate|too_short|too_long",
        "first_call_resolution": true/false,
        "transfer_required": true/false,
        "callback_required": true/false,
        "information_complete": "Was all required information gathered? yes|partial|no",
        "customer_effort_score": "low|medium|high (how much effort did customer need to expend?)",
        "likely_to_recommend": "likely|neutral|unlikely|cannot_assess"
    }}
}}

═══════════════════════════════════════════════════════════════════════════════
CRITICAL RULES:
═══════════════════════════════════════════════════════════════════════════════
1. ONLY include information ACTUALLY said in the transcript - do not assume or fabricate
2. If something wasn't mentioned, use null or empty array []
3. PHONE NUMBER FORMAT:
   - UAE numbers: +971-50-XXX-XXXX, +971-55-XXX-XXXX, +971-4-XXX-XXXX
   - If caller says "050-1234567", format as "+971-50-123-4567"
   - International numbers: include country code
4. DEDUPLICATION: If a number/name is repeated for confirmation, count it ONCE only
5. OTP CALLS: If the call is primarily about OTP verification, mark call_type as "otp_verification"
6. Be SPECIFIC about services: "Golden Visa inquiry for property investment" not just "visa inquiry"
7. MOOD ANALYSIS: Base mood assessment on actual tone indicators (urgency words, politeness, complaints, thanks)
8. EMPLOYEE PERFORMANCE: Be objective and constructive - provide actionable feedback
9. SALES OPPORTUNITIES: Identify potential business opportunities and qualify leads
10. STAFF IDENTIFICATION: Use extension directory to identify staff when possible
11. COMPLIANCE: Note any compliance concerns or data handling issues
12. COACHING: Provide specific coaching notes that would help improve performance

Return ONLY valid JSON, no other text."""


# ============== Transformers Whisper ASR Engine with Speaker Diarization ==============

class WhisperEngine:
    """Whisper ASR engine with speaker diarization for Blackwell GPU support."""

    def __init__(self):
        self._pipe = None
        self._diarization_pipe = None
        self._model_loaded = False
        self._diarization_loaded = False
        self._loading = False
        self._lock = asyncio.Lock()
        self._device = None
        self._hf_token = os.environ.get("HF_TOKEN")  # HuggingFace token for pyannote

    async def _load_model(self):
        """Lazy load the whisper model with GPU using transformers."""
        if self._model_loaded or self._loading:
            return

        async with self._lock:
            if self._model_loaded:
                return

            self._loading = True
            try:
                # Set TRITON_PTXAS_PATH for Blackwell GPU compatibility
                os.environ["TRITON_PTXAS_PATH"] = "/usr/local/cuda/bin/ptxas"

                # Ensure ffmpeg is in PATH (required for audio loading)
                current_path = os.environ.get("PATH", "")
                if "/usr/bin" not in current_path:
                    os.environ["PATH"] = f"/usr/bin:/usr/local/bin:{current_path}"

                import torch
                from transformers import pipeline

                # Check GPU availability
                if torch.cuda.is_available():
                    self._device = "cuda:0"
                    gpu_name = torch.cuda.get_device_name(0)
                    logger.info(f"Using GPU: {gpu_name}")
                else:
                    self._device = "cpu"
                    logger.info("Using CPU (no GPU available)")

                # Use whisper-large-v3-turbo for fast inference
                model_id = "openai/whisper-large-v3-turbo"
                torch_dtype = torch.float16 if "cuda" in self._device else torch.float32

                logger.info(f"Loading Whisper model: {model_id} on {self._device}")

                # Run in thread pool to avoid blocking
                loop = asyncio.get_event_loop()

                def _load():
                    return pipeline(
                        "automatic-speech-recognition",
                        model=model_id,
                        torch_dtype=torch_dtype,
                        device=self._device,
                    )

                self._pipe = await loop.run_in_executor(None, _load)
                self._model_loaded = True
                logger.info(f"Whisper model loaded successfully on {self._device}")

                # Try to load speaker diarization model
                await self._load_diarization()

            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                raise
            finally:
                self._loading = False

    async def _load_diarization(self):
        """Load pyannote speaker diarization model."""
        if self._diarization_loaded or not self._hf_token:
            if not self._hf_token:
                logger.warning("No HF_TOKEN set - speaker diarization disabled. "
                             "Set HF_TOKEN env var with your HuggingFace token to enable.")
            return

        try:
            logger.info("Loading speaker diarization model (pyannote)...")
            loop = asyncio.get_event_loop()

            def _load_diarization():
                from pyannote.audio import Pipeline
                diarization = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self._hf_token
                )
                if "cuda" in self._device:
                    import torch
                    diarization.to(torch.device(self._device))
                return diarization

            self._diarization_pipe = await loop.run_in_executor(None, _load_diarization)
            self._diarization_loaded = True
            logger.info("Speaker diarization model loaded successfully")

        except ImportError:
            logger.warning("pyannote-audio not installed - speaker diarization disabled. "
                         "Install with: pip install pyannote-audio")
        except Exception as e:
            logger.warning(f"Failed to load diarization model: {e} - continuing without diarization")

    def _assign_speakers_to_segments(self, segments: List[Dict], diarization) -> List[Dict]:
        """Assign speaker labels to transcript segments based on diarization."""
        if not diarization:
            return segments

        # Convert diarization to list of (start, end, speaker)
        speaker_segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            speaker_segments.append({
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker
            })

        # Assign speakers to transcript segments
        for segment in segments:
            seg_start = segment.get("start", 0)
            seg_end = segment.get("end", 0)
            seg_mid = (seg_start + seg_end) / 2

            # Find the speaker active at the midpoint of this segment
            best_speaker = None
            best_overlap = 0

            for sp in speaker_segments:
                # Calculate overlap between transcript segment and speaker segment
                overlap_start = max(seg_start, sp["start"])
                overlap_end = min(seg_end, sp["end"])
                overlap = max(0, overlap_end - overlap_start)

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = sp["speaker"]

            segment["speaker"] = best_speaker or "UNKNOWN"

        return segments

    def _format_transcript_with_speakers(self, segments: List[Dict]) -> str:
        """Format transcript with speaker labels."""
        if not segments:
            return ""

        lines = []
        current_speaker = None

        for segment in segments:
            speaker = segment.get("speaker", "UNKNOWN")
            text = segment.get("text", "").strip()

            if not text:
                continue

            if speaker != current_speaker:
                current_speaker = speaker
                lines.append(f"\n[{speaker}]: {text}")
            else:
                lines.append(text)

        return " ".join(lines).strip()

    async def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        """Transcribe audio with speaker diarization using Whisper + pyannote."""
        if not self._model_loaded:
            await self._load_model()

        try:
            logger.info(f"Transcribing with Whisper ({self._device}): {audio_path}")

            # If using CUDA, try and fall back to CPU on kernel errors
            if "cuda" in str(self._device):
                try:
                    return await self._do_transcribe(audio_path, language)
                except RuntimeError as e:
                    if "no kernel image" in str(e) or "CUDA" in str(e):
                        logger.warning(f"CUDA error, falling back to CPU: {e}")
                        self._device = "cpu"
                        # Reload model on CPU
                        self._model_loaded = False
                        await self._load_model()
                        return await self._do_transcribe(audio_path, language)
                    raise
            else:
                return await self._do_transcribe(audio_path, language)

        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _do_transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        """Internal transcription method."""
        try:

            loop = asyncio.get_event_loop()

            def _do_transcribe():
                generate_kwargs = {}
                if language:
                    generate_kwargs["language"] = language

                result = self._pipe(
                    audio_path,
                    chunk_length_s=30,
                    batch_size=24,  # Increase for GPU
                    return_timestamps=True,
                    generate_kwargs=generate_kwargs,
                )

                # Process chunks/timestamps if available
                segment_list = []
                if "chunks" in result:
                    for chunk in result["chunks"]:
                        segment_list.append({
                            "start": chunk.get("timestamp", [0, 0])[0] or 0,
                            "end": chunk.get("timestamp", [0, 0])[1] or 0,
                            "text": chunk.get("text", "").strip()
                        })

                return {
                    "raw_transcript": result.get("text", "").strip(),
                    "segments": segment_list,
                    "duration": segment_list[-1]["end"] if segment_list else 0,
                }

            whisper_result = await loop.run_in_executor(None, _do_transcribe)

            # Perform speaker diarization if available
            diarization_result = None
            has_diarization = False

            if self._diarization_loaded and self._diarization_pipe:
                try:
                    logger.info("Running speaker diarization...")

                    def _do_diarization():
                        return self._diarization_pipe(audio_path)

                    diarization_result = await loop.run_in_executor(None, _do_diarization)
                    has_diarization = True
                    logger.info("Speaker diarization complete")

                except Exception as e:
                    logger.warning(f"Diarization failed: {e} - using transcript without speakers")

            # Assign speakers to segments
            segments = whisper_result["segments"]
            if has_diarization:
                segments = self._assign_speakers_to_segments(segments, diarization_result)
                transcript = self._format_transcript_with_speakers(segments)
            else:
                transcript = whisper_result["raw_transcript"]

            return {
                "success": True,
                "transcript": transcript,
                "raw_transcript": whisper_result["raw_transcript"],
                "language": language or "auto",
                "language_probability": 1.0,
                "segments": segments,
                "duration": whisper_result["duration"],
                "has_speaker_diarization": has_diarization,
            }

        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def is_ready(self) -> bool:
        """Check if Whisper is ready."""
        try:
            if not self._model_loaded:
                await self._load_model()
            return self._model_loaded
        except:
            return False

    @property
    def has_diarization(self) -> bool:
        """Check if diarization is available."""
        return self._diarization_loaded


# ============== LLM Analysis Service (vLLM or Ollama) ==============

class LLMAnalysisService:
    """Service for analyzing transcripts using Llama 3.1 8B via vLLM or Ollama."""

    def __init__(self):
        # Try vLLM first (OpenAI-compatible API), fall back to Ollama
        self.vllm_url = os.environ.get("VLLM_URL", "http://localhost:8080/v1")
        # Check OLLAMA_HOST env var (for Docker), fall back to settings
        ollama_host = os.environ.get("OLLAMA_HOST")
        self.ollama_url = ollama_host if ollama_host else settings.ollama_url
        self.model = os.environ.get("VLLM_MODEL", "nvidia/Llama-3.1-8B-Instruct-FP4")
        self.ollama_model = settings.ollama_model
        self._use_vllm = None  # Will be determined on first call

    async def _check_vllm(self) -> bool:
        """Check if vLLM is available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.vllm_url}/models")
                return response.status_code == 200
        except:
            return False

    async def analyze_transcript(self, transcript: str, recording_context: str = "") -> Dict[str, Any]:
        """Analyze call transcript and extract structured information."""

        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            transcript=transcript,
            recording_context=recording_context if recording_context else "No additional context available."
        )

        # Determine which backend to use
        if self._use_vllm is None:
            self._use_vllm = await self._check_vllm()
            if self._use_vllm:
                logger.info("Using vLLM backend for LLM analysis")
            else:
                logger.info("Using Ollama backend for LLM analysis")

        if self._use_vllm:
            return await self._analyze_with_vllm(prompt)
        else:
            return await self._analyze_with_ollama(prompt)

    async def _analyze_with_vllm(self, prompt: str) -> Dict[str, Any]:
        """Analyze using vLLM's OpenAI-compatible API."""
        try:
            async with httpx.AsyncClient(timeout=settings.processing_timeout_seconds) as client:
                response = await client.post(
                    f"{self.vllm_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "You are an AI assistant that analyzes phone call transcripts and returns structured JSON."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    }
                )
                response.raise_for_status()
                result = response.json()

                # Extract response from OpenAI format
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return self._parse_llm_response(content)

        except httpx.ConnectError:
            logger.warning("vLLM not available, falling back to Ollama")
            self._use_vllm = False
            return await self._analyze_with_ollama(prompt)
        except Exception as e:
            logger.error(f"vLLM analysis failed: {e}")
            return {"success": False, "error": str(e)}

    async def _analyze_with_ollama(self, prompt: str) -> Dict[str, Any]:
        """Analyze using Ollama API."""
        try:
            async with httpx.AsyncClient(timeout=settings.processing_timeout_seconds) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 2000,
                            "num_ctx": settings.ollama_context_length,
                        }
                    }
                )
                response.raise_for_status()
                result = response.json()

                return self._parse_llm_response(result.get("response", ""))

        except httpx.ConnectError:
            logger.error(f"Cannot connect to Ollama server at {self.ollama_url}")
            return {
                "success": False,
                "error": f"Neither vLLM nor Ollama available. Start vLLM or Ollama."
            }
        except httpx.ReadTimeout:
            logger.error("Ollama request timed out")
            return {
                "success": False,
                "error": "Request timed out. The model may be loading or the transcript is too long."
            }
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """Parse and validate LLM JSON response."""
        import json
        import re

        try:
            # Find JSON in response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response_text[start:end]
                # Clean common issues
                json_str = re.sub(r',\s*([\]}])', r'\1', json_str)
                # Remove control characters
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', json_str)

                data = json.loads(json_str)
                return {"success": True, "data": data}
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed: {e}")

        # Fallback: manual extraction
        return {
            "success": True,
            "data": self._extract_fields_manually(response_text)
        }

    def _extract_fields_manually(self, response_text: str) -> Dict[str, Any]:
        """Extract fields when JSON parsing fails."""
        import re
        result = {}

        # Extract call_type
        call_type_match = re.search(r'"call_type"\s*:\s*"([^"]*)"', response_text)
        if call_type_match:
            result["call_type"] = call_type_match.group(1)

        # Extract summary
        summary_match = re.search(r'"summary"\s*:\s*"([^"]*(?:[^"\\]|\\.)*)"', response_text)
        if summary_match:
            result["summary"] = summary_match.group(1).replace('\\"', '"').replace('\\n', ' ')

        # Extract sentiment
        sentiment_match = re.search(r'"sentiment"\s*:\s*"([^"]*)"', response_text)
        if sentiment_match:
            result["sentiment"] = sentiment_match.group(1)

        # Extract staff_name
        staff_match = re.search(r'"staff_name"\s*:\s*"([^"]*)"', response_text)
        if staff_match and staff_match.group(1).lower() not in ['null', 'none']:
            result["staff_name"] = staff_match.group(1)

        # Extract customer_name
        customer_match = re.search(r'"customer_name"\s*:\s*"([^"]*)"', response_text)
        if customer_match and customer_match.group(1).lower() not in ['null', 'none']:
            result["customer_name"] = customer_match.group(1)

        # Extract arrays
        for field in ["topics_discussed", "action_items", "customer_requests", "staff_responses"]:
            array_match = re.search(rf'"{field}"\s*:\s*\[(.*?)\]', response_text, re.DOTALL)
            if array_match:
                items = re.findall(r'"([^"]*)"', array_match.group(1))
                if items:
                    result[field] = items

        # Extract key_details
        key_details = {}
        for detail_field in ["names_mentioned", "numbers_mentioned", "dates_mentioned", "other_details"]:
            detail_match = re.search(rf'"{detail_field}"\s*:\s*(?:"([^"]*)"|(\[[^\]]*\]))', response_text)
            if detail_match:
                value = detail_match.group(1) or detail_match.group(2)
                if value and value.lower() not in ['null', 'none', '[]']:
                    if value.startswith('['):
                        items = re.findall(r'"([^"]*)"', value)
                        if items:
                            key_details[detail_field] = ', '.join(items)
                    else:
                        key_details[detail_field] = value

        if key_details:
            result["key_details"] = key_details

        if "summary" not in result:
            result["summary"] = "Summary could not be parsed from AI response"
            result["error"] = "JSON parsing failed - fields extracted manually"

        return result

    async def check_status(self) -> Dict[str, Any]:
        """Check LLM backend status (vLLM or Ollama)."""
        # Check vLLM first
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.vllm_url}/models")
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("id", m.get("name", "unknown")) for m in data.get("data", [])]
                    return {
                        "status": "running",
                        "backend": "vLLM",
                        "models_available": models,
                        "target_model": self.model,
                        "model_ready": True,
                    }
        except:
            pass

        # Fall back to Ollama
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.ollama_url}/api/tags")
                response.raise_for_status()
                data = response.json()

                models = [m["name"] for m in data.get("models", [])]
                has_target = self.ollama_model in models or any(self.ollama_model.split(":")[0] in m for m in models)

                return {
                    "status": "running",
                    "backend": "Ollama",
                    "models_available": models,
                    "target_model": self.ollama_model,
                    "model_ready": has_target,
                }
        except httpx.ConnectError:
            return {
                "status": "not_running",
                "error": "Neither vLLM nor Ollama running"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }


# ============== Main AI Service ==============

class AITranscriptionService:
    """Main service coordinating ASR and LLM analysis."""

    def __init__(self):
        self._asr_engine = WhisperEngine()  # Use GPU-accelerated OpenAI Whisper
        self._llm_service = LLMAnalysisService()

    async def transcribe_audio(
        self,
        audio_path: str,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe audio file using faster-whisper."""
        return await self._asr_engine.transcribe(audio_path, language)

    async def summarize_transcript(
        self,
        transcript: str,
        model: str = None,
        recording_context: str = "",
    ) -> Dict[str, Any]:
        """Analyze transcript using Llama 3.1 8B."""
        return await self._llm_service.analyze_transcript(transcript, recording_context)

    async def process_recording(
        self,
        audio_path: str,
        language_hint: Optional[str] = None,
        recording_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Full pipeline: Transcribe and analyze."""
        import re
        start_time = datetime.now()

        # Extract extension from recording filename if available
        extension = None
        call_direction = None
        recording_context = ""

        filename = recording_file or os.path.basename(audio_path)
        if filename:
            # Pattern: 20251211160749-1765454864.23722-201-0556195159-Outbound.wav
            # or: 20251211-201-Inbound-0501234567.wav
            ext_match = re.search(r'-(\d{3})-', filename)
            if ext_match:
                extension = ext_match.group(1)
                staff_info = get_staff_from_extension(extension)
                if staff_info["name"]:
                    recording_context = f"Extension: {extension}\nStaff Member: {staff_info['name']}\nDepartment: {staff_info['department']}\nRole: {staff_info['role']}"

            # Extract direction
            if "Outbound" in filename or "outbound" in filename:
                call_direction = "outbound"
                recording_context += f"\nCall Direction: Outbound (Staff initiated the call)"
            elif "Inbound" in filename or "inbound" in filename:
                call_direction = "inbound"
                recording_context += f"\nCall Direction: Inbound (Customer called in)"
            elif "Internal" in filename or "internal" in filename:
                call_direction = "internal"
                recording_context += f"\nCall Direction: Internal (Call between staff members)"

        # Step 1: Transcribe
        logger.info(f"Starting transcription for: {audio_path}")
        transcription = await self.transcribe_audio(audio_path, language_hint)

        if not transcription.get("success"):
            return {
                "success": False,
                "error": transcription.get("error", "Transcription failed"),
                "stage": "transcription"
            }

        transcript = transcription["transcript"]

        if not transcript or len(transcript.strip()) < 10:
            return {
                "success": False,
                "error": "Transcript too short or empty",
                "stage": "transcription"
            }

        # Validate if transcript has meaningful content for analysis
        is_valid, validation_reason = is_valid_transcript_for_analysis(transcript)

        if not is_valid:
            logger.info(f"Transcript not valid for analysis: {validation_reason}")
            # Get staff info for the response
            staff_info_for_response = get_staff_from_extension(extension) if extension else {}
            # Return success but with a special "insufficient data" response
            return {
                "success": True,
                "transcript_preview": transcript[:500] + "..." if len(transcript) > 500 else transcript,
                "full_transcript": transcript,
                "language_detected": transcription.get("language"),
                "summary": {
                    "call_type": "insufficient_data",
                    "summary": "Not enough data to generate analysis. The call may contain only ringing, background noise, or minimal interaction.",
                    "service_category": "Unknown",
                    "resolution_status": "unclear",
                    "mood_sentiment_analysis": {
                        "overall_sentiment": "neutral"
                    },
                    "insufficient_data_reason": validation_reason,
                },
                "summary_error": None,
                "processing_time_seconds": round((datetime.now() - start_time).total_seconds(), 2),
                "model_used": "none - insufficient data",
                "asr_engine": "transformers-whisper-turbo",
                "staff_extension": extension,
                "staff_name": staff_info_for_response.get("name") if extension else None,
                "staff_department": staff_info_for_response.get("department") if extension else None,
                "call_direction": call_direction,
                "analysis_skipped": True,
                "analysis_skip_reason": validation_reason,
            }

        logger.info(f"Transcription complete, analyzing with {settings.ollama_model}...")

        # Step 2: Analyze with recording context
        analysis = await self._llm_service.analyze_transcript(transcript, recording_context)

        processing_time = (datetime.now() - start_time).total_seconds()

        # Get staff info from extension for the result
        staff_info = get_staff_from_extension(extension) if extension else {}

        # If analysis succeeded, enrich with extension-based staff info
        summary_data = analysis.get("data") if analysis.get("success") else None
        if summary_data and extension:
            # Set staff info from extension if not already in analysis
            if not summary_data.get("staff_extension"):
                summary_data["staff_extension"] = extension
            if not summary_data.get("staff_name") and staff_info.get("name"):
                summary_data["staff_name"] = staff_info["name"]
            if not summary_data.get("staff_department"):
                summary_data["staff_department"] = staff_info.get("department", "Unknown")

        return {
            "success": True,
            "transcript_preview": transcript[:500] + "..." if len(transcript) > 500 else transcript,
            "full_transcript": transcript,
            "language_detected": transcription.get("language"),
            "summary": summary_data,
            "summary_error": analysis.get("error") if not analysis.get("success") else None,
            "processing_time_seconds": round(processing_time, 2),
            "model_used": self._llm_service.model if self._llm_service._use_vllm else self._llm_service.ollama_model,
            "asr_engine": "transformers-whisper-turbo",
            "staff_extension": extension,
            "staff_name": staff_info.get("name") if staff_info else None,
            "staff_department": staff_info.get("department") if staff_info else None,
            "call_direction": call_direction,
        }

    async def check_status(self) -> Dict[str, Any]:
        """Check status of all AI services."""
        asr_ready = await self._asr_engine.is_ready()
        llm_status = await self._llm_service.check_status()

        return {
            "asr_engine": "transformers-whisper",
            "asr_model": "openai/whisper-large-v3-turbo",
            "asr_device": self._asr_engine._device if asr_ready else None,
            "asr_ready": asr_ready,
            "speaker_diarization": {
                "enabled": self._asr_engine.has_diarization,
                "model": "pyannote/speaker-diarization-3.1" if self._asr_engine.has_diarization else None,
                "note": "Set HF_TOKEN env var to enable" if not self._asr_engine.has_diarization else "Active"
            },
            "llm": llm_status,
            "ready": asr_ready and llm_status.get("status") == "running",
        }

    # Backward compatibility alias
    async def check_ollama_status(self) -> Dict[str, Any]:
        """Backward compatible status check."""
        return await self._llm_service.check_status()


# ============== Global Instance ==============

_ai_service: Optional[AITranscriptionService] = None


def get_ai_service() -> AITranscriptionService:
    """Get or create AI transcription service instance."""
    global _ai_service
    if _ai_service is None:
        _ai_service = AITranscriptionService()
    return _ai_service
