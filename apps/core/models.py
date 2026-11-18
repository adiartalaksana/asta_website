import uuid

from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ImmutableModel(TimeStampedModel):
    """Prevents ORM writes after an immutable record is sealed."""

    is_immutable = models.BooleanField(default=False, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and self.is_immutable:
            raise ValueError("Immutable records cannot be updated.")
        return super().save(*args, **kwargs)


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True
