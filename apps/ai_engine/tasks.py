from celery import shared_task

from .pipelines import categorize_transaction


@shared_task
def categorize_batch(record_ids: list[str]) -> int:
    # Fetching and persistence are intentionally performed by the ingestion boundary.
    return len([categorize_transaction(record_id) for record_id in record_ids])
