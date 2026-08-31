"""
Atomic Agent: User Provisioning Agent.
Handles idempotent user creation in Google Workspace, password generation, and alias assignment.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import time
import os
import csv
from atomic_agents.base import AtomicAgent
from connectors.google_client import GoogleWorkspaceAdminClient
from connectors.base import ZohoUser
from engine.checkpoint import CheckpointStore


@dataclass
class ProvisioningRequest:
    """Input payload for UserProvisioningAgent."""
    users: List[ZohoUser]
    google_client: GoogleWorkspaceAdminClient
    checkpoint_store: CheckpointStore
    dry_run: bool = False
    export_csv: bool = True
    pause_controller: Optional[Any] = None


@dataclass
class ProvisioningSummary:
    """Output payload from UserProvisioningAgent."""
    total_requested: int
    created_count: int = 0
    existing_count: int = 0
    failed_count: int = 0
    results: List[Dict[str, Any]] = field(default_factory=list)
    credentials_csv_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requested": self.total_requested,
            "created": self.created_count,
            "existing": self.existing_count,
            "failed": self.failed_count,
            "credentials_csv_path": self.credentials_csv_path
        }


class UserProvisioningAgent(AtomicAgent[ProvisioningRequest, ProvisioningSummary]):
    """
    Atomic Agent responsible strictly for user account provisioning,
    alias synchronization, and secure temporary password generation.
    """

    def __init__(self):
        super().__init__(
            name="UserProvisioningAgent",
            description="Provisions Google Workspace users with aliases and secure passwords."
        )

    def execute(self, input_data: ProvisioningRequest) -> ProvisioningSummary:
        results = []
        created_count = 0
        existing_count = 0
        failed_count = 0

        for user in input_data.users:
            if input_data.pause_controller:
                input_data.pause_controller.wait_if_paused()
            input_data.checkpoint_store.register_user(
                zuid=user.zuid,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                aliases=user.aliases
            )

            if input_data.dry_run:
                self.logger.info(f"[DRY-RUN] Would provision Google Workspace user: {user.email}")
                res = {"status": "SIMULATED_CREATED", "email": user.email, "temp_password": "[SIMULATED_SECURE_PASS]"}
                results.append(res)
                created_count += 1
                input_data.checkpoint_store.update_user_status(user.email, "SIMULATED_CREATED")
                continue

            try:
                res = input_data.google_client.provision_user(
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    aliases=user.aliases
                )
                status = res.get("status")
                if status == "CREATED":
                    created_count += 1
                    input_data.checkpoint_store.update_user_status(user.email, "CREATED")
                elif status in ("EXISTING", "EXISTS"):
                    existing_count += 1
                    input_data.checkpoint_store.update_user_status(user.email, "EXISTING")
                else:
                    existing_count += 1
                    input_data.checkpoint_store.update_user_status(user.email, "EXISTING")
                results.append(res)
            except Exception as e:
                error_str = str(e)
                self.logger.error(f"Failed to provision {user.email}: {error_str}")
                # Check if the user already exists in Google Workspace despite creation error
                try:
                    existing_user = input_data.google_client.get_user(user.email)
                    if existing_user and existing_user.get("primaryEmail"):
                        self.logger.info(f"User {user.email} confirmed existing in Google Workspace. Proceeding as EXISTING.")
                        existing_count += 1
                        input_data.checkpoint_store.update_user_status(user.email, "EXISTING")
                        results.append({"status": "EXISTING", "email": user.email, "warning": error_str})
                        continue
                except Exception:
                    pass

                failed_count += 1
                input_data.checkpoint_store.update_user_status(user.email, "FAILED", error_msg=error_str)
                results.append({"status": "FAILED", "email": user.email, "error": error_str})

        # Secure One-time CSV Export for newly created accounts
        csv_path = None
        newly_created = [r for r in results if r.get("status") == "CREATED" and "temp_password" in r]
        if newly_created and not input_data.dry_run and input_data.export_csv:
            csv_path = f"provisioned_credentials_{int(time.time())}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Email", "Temporary_Password", "ChangePasswordAtNextLogin"])
                for u in newly_created:
                    writer.writerow([u["email"], u["temp_password"], "TRUE"])
            self.logger.info(f"One-time temporary credentials exported to {csv_path}")

        return ProvisioningSummary(
            total_requested=len(input_data.users),
            created_count=created_count,
            existing_count=existing_count,
            failed_count=failed_count,
            results=results,
            credentials_csv_path=csv_path
        )
