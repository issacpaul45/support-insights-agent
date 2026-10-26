from rest_framework import serializers

from .models import Ticket


class TicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = [
            "id",
            "title",
            "description",
            "category",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AgentQuestionSerializer(serializers.Serializer):
    question = serializers.CharField(
        max_length=1000,
        trim_whitespace=True,
    )

    def validate_question(self, value):
        if not value:
            raise serializers.ValidationError(
                "Question cannot be empty."
            )
        return value
