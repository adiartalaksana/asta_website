from rest_framework import serializers

from .models import JournalHeader, JournalLine


class JournalLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = JournalLine
        fields = ("account", "narration", "debit", "credit")


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True)

    class Meta:
        model = JournalHeader
        fields = ("reference", "narration", "posting_date", "lines")

    def validate_lines(self, lines):
        debits = sum((line["debit"] for line in lines), 0)
        credits = sum((line["credit"] for line in lines), 0)
        if debits != credits:
            raise serializers.ValidationError("Debits must equal credits.")
        return lines
