"""PlaybookService — match emails to playbooks and generate auto-responses."""
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PlaybookService:
    """Matches incoming emails against company playbooks."""

    MIN_CONFIDENCE = 0.7  # Reject matches below this threshold

    def __init__(self, db, llm):
        self.db = db
        self.llm = llm

    async def match(self, account_id: int, email_body: str, email_subject: str) -> Optional[Dict[str, Any]]:
        """Check if any playbook matches the email.

        Returns dict with playbook_id, template, auto_respond, company info — or None.
        """
        company = await self.db.get_company_profile(account_id)
        if not company:
            return None

        playbooks = await self.db.get_playbooks(company["id"])
        if not playbooks:
            return None

        match_result = await self.llm.match_playbook(email_body, email_subject, playbooks)
        if not match_result:
            return None

        matched_id = match_result.get("matched_id")
        try:
            confidence = float(match_result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        if not matched_id:
            return None

        if confidence < self.MIN_CONFIDENCE:
            logger.info(
                f"Playbook #{matched_id} matched but confidence {confidence:.2f} < {self.MIN_CONFIDENCE} — skipping"
            )
            return None

        matched = next((p for p in playbooks if p["id"] == matched_id), None)
        if not matched:
            return None

        return {
            "playbook_id": matched["id"],
            "template": matched["response_template"],
            "trigger": matched["trigger_description"],
            "auto_respond": matched.get("auto_respond", True),
            "confidence": confidence,
            "company": company,
        }

    async def generate_response(
        self, template: str, company: Dict, contact_name: str, email_body: str
    ) -> Optional[str]:
        """Generate a response based on template, company tone, and context."""
        try:
            response = await self.llm.generate_playbook_response(
                template=template,
                company_name=company.get("company_name", ""),
                tone=company.get("tone", "formal"),
                signature=company.get("signature", ""),
                contact_name=contact_name,
                email_body=email_body,
            )
            return response
        except Exception as e:
            logger.error(f"Error generating playbook response: {e}")
            return None
