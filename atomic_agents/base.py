"""
Atomic Agent Base Class & Lifecycle Contracts.
Provides strict typed execution, isolated error boundaries, and security hooks.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar, Any, Dict, Optional, List
import time
import traceback
from security.sanitizer import setup_secure_logger

logger = setup_secure_logger("atomic_agent_base")

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


@dataclass
class AgentResult(Generic[TOutput]):
    """Standardized result envelope for any Atomic Agent execution."""
    success: bool
    data: Optional[TOutput] = None
    error: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    execution_time_sec: float = 0.0
    agent_name: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data if isinstance(self.data, (dict, list, str, int, float, bool, type(None))) else str(self.data),
            "error": self.error,
            "error_details": self.error_details,
            "execution_time_sec": self.execution_time_sec,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
        }


class AtomicAgent(ABC, Generic[TInput, TOutput]):
    """
    Abstract Base Class for all Atomic Migration Agents.
    Enforces single-responsibility, typed boundaries, and error isolation.
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.logger = setup_secure_logger(f"agent.{name.lower().replace(' ', '_')}")

    def pre_execute(self, input_data: TInput) -> None:
        """Hook executed before running the atomic task. Validates preconditions."""
        pass

    @abstractmethod
    def execute(self, input_data: TInput) -> TOutput:
        """Core atomic logic to be implemented by specialized sub-agents."""
        pass

    def post_execute(self, input_data: TInput, result: TOutput) -> None:
        """Hook executed after successful completion. Performs state validation."""
        pass

    def run(self, input_data: TInput) -> AgentResult[TOutput]:
        """
        Executes the atomic agent with fault boundary protection,
        execution timing, and sanitized logging.
        """
        start_time = time.time()
        self.logger.info(f"Starting atomic agent [{self.name}] - {self.description}")

        try:
            # 1. Pre-execution guardrails
            self.pre_execute(input_data)

            # 2. Main execution
            output_data = self.execute(input_data)

            # 3. Post-execution verification
            self.post_execute(input_data, output_data)

            elapsed = round(time.time() - start_time, 3)
            self.logger.info(f"Completed atomic agent [{self.name}] successfully in {elapsed}s")

            return AgentResult(
                success=True,
                data=output_data,
                execution_time_sec=elapsed,
                agent_name=self.name
            )

        except Exception as e:
            elapsed = round(time.time() - start_time, 3)
            error_msg = str(e)
            self.logger.error(f"Atomic agent [{self.name}] failed after {elapsed}s: {error_msg}")

            return AgentResult(
                success=False,
                error=error_msg,
                error_details={"exception_type": type(e).__name__},
                execution_time_sec=elapsed,
                agent_name=self.name
            )
