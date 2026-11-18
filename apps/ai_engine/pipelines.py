from dataclasses import dataclass
from decimal import Decimal

from apps.core.constants import AUTO_STAGE_CONFIDENCE


@dataclass(frozen=True)
class CategorizationResult:
    account_id: str | None
    confidence: Decimal
    tier: int
    requires_review: bool


def categorize_transaction(raw_record) -> CategorizationResult:
    """Run deterministic, vector, then private LLM inference in that order."""
    result = CategorizationResult(None, Decimal("0"), 3, True)
    return result


def should_auto_stage(result: CategorizationResult) -> bool:
    return result.confidence >= AUTO_STAGE_CONFIDENCE and not result.requires_review
