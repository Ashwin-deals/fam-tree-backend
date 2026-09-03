"""Request validation for the Play API. Mirrors apps/api/serializers.py's role."""
from __future__ import annotations

from rest_framework import serializers

from .catalog import GAMES_BY_ID


class BotSerializer(serializers.Serializer):
    difficulty = serializers.ChoiceField(choices=["easy", "medium", "hard"], default="medium")


class CreateRoomSerializer(serializers.Serializer):
    game_id = serializers.ChoiceField(choices=sorted(GAMES_BY_ID))
    visibility = serializers.ChoiceField(choices=["private", "family", "public"], default="private")
    family_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    settings = serializers.DictField(required=False, default=dict)
    bots = BotSerializer(many=True, required=False, default=list)


class JoinRoomSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16)


class ReadySerializer(serializers.Serializer):
    ready = serializers.BooleanField()


class AddBotSerializer(serializers.Serializer):
    difficulty = serializers.ChoiceField(choices=["easy", "medium", "hard"], default="medium")


class ActionSerializer(serializers.Serializer):
    # Game-specific payloads are validated by the engine, which is the only place that
    # knows the rules; this just enforces the envelope.
    action = serializers.DictField()
    expected_version = serializers.IntegerField(required=False, allow_null=True)

    def validate_action(self, value):
        if not value.get("type"):
            raise serializers.ValidationError("Every action needs a type.")
        if any(key.startswith("_") for key in value):
            raise serializers.ValidationError("Unsupported action field.")
        return value


class InviteFamilySerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.CharField(), allow_empty=False, max_length=12)


class InvitationResponseSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
