from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()

    def validate_email(self, value):
        return value.casefold().strip()

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        validate_password(attrs["password"])
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        return value.casefold().strip()


class ProfileSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=120, trim_whitespace=True)
    profile_picture = serializers.ImageField(required=False, allow_null=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(required=False, allow_blank=True, choices=["female", "male", "non_binary", "prefer_not_to_say", ""])
    location = serializers.CharField(required=False, allow_blank=True, max_length=120)
    occupation = serializers.CharField(required=False, allow_blank=True, max_length=120)
    bio = serializers.CharField(required=False, allow_blank=True, max_length=800)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()


class FamilySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=1200)
    family_image = serializers.ImageField(required=False, allow_null=True)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()


class FamilyPatchSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=120, trim_whitespace=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=1200)
    family_image = serializers.ImageField(required=False, allow_null=True)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()


class JoinFamilySerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=32, trim_whitespace=True)

    def validate_invite_code(self, value):
        return "".join(value.upper().split())


class RelationshipSerializer(serializers.Serializer):
    family_id = serializers.CharField(max_length=24)
    person1_id = serializers.CharField(max_length=24)
    person2_id = serializers.CharField(max_length=24)
    relationship_type = serializers.ChoiceField(choices=["parent", "spouse", "sibling", "uncle", "aunt", "grandparent", "cousin", "nephew", "niece", "grandchild"])

    def validate(self, attrs):
        if attrs["person1_id"] == attrs["person2_id"]:
            raise serializers.ValidationError("A person cannot have a relationship with themselves.")
        return attrs


class RelationshipPatchSerializer(serializers.Serializer):
    person1_id = serializers.CharField(required=False, max_length=24)
    person2_id = serializers.CharField(required=False, max_length=24)
    relationship_type = serializers.ChoiceField(required=False, choices=["parent", "spouse", "sibling", "uncle", "aunt", "grandparent", "cousin", "nephew", "niece", "grandchild"])

    def validate(self, attrs):
        if "person1_id" in attrs and "person2_id" in attrs and attrs["person1_id"] == attrs["person2_id"]:
            raise serializers.ValidationError("A person cannot have a relationship with themselves.")
        return attrs


class RelationshipLookupSerializer(serializers.Serializer):
    person_id = serializers.CharField(max_length=24)


class HouseholdSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=800)
    member_ids = serializers.ListField(child=serializers.CharField(max_length=24), required=False, allow_empty=True)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()


class HouseholdMemberUpdateSerializer(serializers.Serializer):
    member_ids = serializers.ListField(child=serializers.CharField(max_length=24), allow_empty=False)


class HouseholdMessageSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True, max_length=2000, trim_whitespace=True)
    attachment_type = serializers.ChoiceField(required=False, allow_blank=True, choices=["image", "voice", ""])
    attachment = serializers.FileField(required=False, allow_null=True)
    duration_seconds = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=600)

    def validate(self, attrs):
        text = (attrs.get("text") or "").strip()
        attrs["text"] = text
        if not text and not attrs.get("attachment"):
            raise serializers.ValidationError("Message cannot be empty.")
        if attrs.get("attachment") and not attrs.get("attachment_type"):
            raise serializers.ValidationError({"attachment_type": "Specify the attachment type."})
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    otp = serializers.CharField(required=False, allow_blank=True, max_length=6, trim_whitespace=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class RequestPasswordOtpSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)


class VerifyOtpSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    otp = serializers.CharField(max_length=6, min_length=6, trim_whitespace=True)

    def validate_email(self, value):
        return value.casefold().strip()

    def validate_otp(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Enter the 6-digit code.")
        return value


class ResendOtpSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        return value.casefold().strip()


class ChangeEmailSerializer(serializers.Serializer):
    new_email = serializers.EmailField(max_length=254)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_new_email(self, value):
        return value.casefold().strip()


class DeactivateAccountSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class UserSettingsSerializer(serializers.Serializer):
    activity_status_enabled = serializers.BooleanField(required=False)
    show_profile_details = serializers.BooleanField(required=False)
    notifications_enabled = serializers.BooleanField(required=False)
    notify_messages = serializers.BooleanField(required=False)
    notify_household_reminders = serializers.BooleanField(required=False)
    notify_family_activity = serializers.BooleanField(required=False)
    reduce_motion = serializers.BooleanField(required=False)


class GroceryItemSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160, trim_whitespace=True)
    quantity = serializers.CharField(required=False, allow_blank=True, max_length=80)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=800)
    assigned_to = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=24)
    reminder_enabled = serializers.BooleanField(required=False, default=False)
    reminder_interval_minutes = serializers.IntegerField(required=False, min_value=5, max_value=43200, default=1440)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()


class GroceryItemPatchSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=160, trim_whitespace=True)
    quantity = serializers.CharField(required=False, allow_blank=True, max_length=80)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=800)
    assigned_to = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=24)
    status = serializers.ChoiceField(required=False, choices=["pending", "purchased", "cancelled"])
    reminder_enabled = serializers.BooleanField(required=False)
    reminder_interval_minutes = serializers.IntegerField(required=False, min_value=5, max_value=43200)

    def validate_name(self, value):
        if len(value.strip()) < 2:
            raise serializers.ValidationError("Enter at least two characters.")
        return value.strip()


class GroceryStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["pending", "purchased", "cancelled"])


class ReminderSnoozeSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(required=False, min_value=5, max_value=10080, default=60)


class MemorySerializer(serializers.Serializer):
    media = serializers.FileField()
    media_type = serializers.ChoiceField(choices=["photo", "video"])
    caption = serializers.CharField(required=False, allow_blank=True, max_length=1000, trim_whitespace=True)
    memory_date = serializers.DateField()
    tagged_user_ids = serializers.ListField(child=serializers.CharField(max_length=24), required=False, allow_empty=True)
    visibility = serializers.ChoiceField(choices=["family", "household", "selected", "private"])
    household_id = serializers.CharField(required=False, allow_blank=True, max_length=24)
    selected_user_ids = serializers.ListField(child=serializers.CharField(max_length=24), required=False, allow_empty=True)

    def validate(self, attrs):
        if attrs["visibility"] == "household" and not attrs.get("household_id"):
            raise serializers.ValidationError({"household_id": "Choose which household can see this memory."})
        if attrs["visibility"] == "selected" and not attrs.get("selected_user_ids"):
            raise serializers.ValidationError({"selected_user_ids": "Choose at least one family member."})
        return attrs


class MemoryPatchSerializer(serializers.Serializer):
    caption = serializers.CharField(required=False, allow_blank=True, max_length=1000, trim_whitespace=True)
    memory_date = serializers.DateField(required=False)
    tagged_user_ids = serializers.ListField(child=serializers.CharField(max_length=24), required=False, allow_empty=True)
    visibility = serializers.ChoiceField(required=False, choices=["family", "household", "selected", "private"])
    household_id = serializers.CharField(required=False, allow_blank=True, max_length=24)
    selected_user_ids = serializers.ListField(child=serializers.CharField(max_length=24), required=False, allow_empty=True)

    def validate(self, attrs):
        if attrs.get("visibility") == "household" and not attrs.get("household_id"):
            raise serializers.ValidationError({"household_id": "Choose which household can see this memory."})
        if attrs.get("visibility") == "selected" and not attrs.get("selected_user_ids"):
            raise serializers.ValidationError({"selected_user_ids": "Choose at least one family member."})
        return attrs
