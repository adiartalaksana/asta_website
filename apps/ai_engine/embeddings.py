from decimal import Decimal

from pgvector.django import CosineDistance

from apps.core.constants import VECTOR_MATCH_THRESHOLD


def find_semantic_match(embedding, queryset):
    """Return the closest approved record only when it meets the confidence threshold."""
    candidate = (
        queryset.annotate(distance=CosineDistance("embedding", embedding))
        .order_by("distance")
        .first()
    )
    if candidate is None:
        return None
    candidate.similarity = Decimal(1) - Decimal(str(candidate.distance))
    return candidate if candidate.similarity >= VECTOR_MATCH_THRESHOLD else None
