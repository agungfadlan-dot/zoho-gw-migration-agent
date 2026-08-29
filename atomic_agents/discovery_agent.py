"""
Atomic Agent: Discovery & Assessment Agent.
Scans tenant topology, estimates mailbox storage, and recommends optimal pilot cohorts.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from atomic_agents.base import AtomicAgent
from connectors.zoho_client import ZohoAdminClient
from connectors.base import ZohoUser
from engine.discovery import DiscoveryEngine, OrganizationAssessmentReport, UserAssessment


@dataclass
class DiscoveryRequest:
    """Input payload for DiscoveryAssessmentAgent."""
    zoho_client: ZohoAdminClient
    sample_items: bool = True
    pilot_candidate_count: int = 5


@dataclass
class DiscoveryResult:
    """Output payload from DiscoveryAssessmentAgent."""
    report: OrganizationAssessmentReport
    all_users: List[ZohoUser]
    recommended_pilot_cohort: List[ZohoUser] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report": self.report.to_dict(),
            "recommended_pilot_emails": [u.email for u in self.recommended_pilot_cohort]
        }


class DiscoveryAssessmentAgent(AtomicAgent[DiscoveryRequest, DiscoveryResult]):
    """
    Atomic Agent responsible strictly for organization discovery,
    data volume estimation, and pilot cohort recommendation.
    """

    def __init__(self):
        super().__init__(
            name="DiscoveryAssessmentAgent",
            description="Scans directory topology, calculates mailbox sizes, and suggests pilot cohorts."
        )

    def execute(self, input_data: DiscoveryRequest) -> DiscoveryResult:
        discovery_engine = DiscoveryEngine(input_data.zoho_client)
        report = discovery_engine.run_assessment(sample_items=input_data.sample_items)
        all_users = input_data.zoho_client.list_organization_users()

        # Heuristic for recommending pilot cohort:
        # 1. Active users with mailboxes
        # 2. Prefer smaller/medium mailboxes first (lower blast radius for initial test)
        # 3. Non-admin users preferred for first pilot, or designated test accounts
        active_users = [u for u in all_users if u.is_active and u.mailbox_account_id]

        def pilot_score(user: ZohoUser) -> float:
            score = 0.0
            # Lower storage = lower risk for pilot run
            score += min(user.storage_used_bytes / (1024 * 1024), 500)
            # Deprioritize super-admins in the first pilot
            if user.role.lower() == "admin":
                score += 50
            return score

        sorted_candidates = sorted(active_users, key=pilot_score)
        recommended_pilot = sorted_candidates[:input_data.pilot_candidate_count]

        return DiscoveryResult(
            report=report,
            all_users=all_users,
            recommended_pilot_cohort=recommended_pilot
        )
