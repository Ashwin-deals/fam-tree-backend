from __future__ import annotations

from collections import deque
from datetime import timedelta

from bson import ObjectId
from django.contrib.auth.hashers import check_password, make_password
from django.db.models import Q
from pymongo.errors import DuplicateKeyError
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.database import get_database
from apps.core.security import create_access_token
from apps.core.services import (
    calculate_relationship,
    create_invite_code,
    create_session,
    family_member_ids,
    find_household,
    find_family,
    household_member_ids,
    household_membership_for,
    membership_for,
    now,
    object_id,
    require_admin,
    require_household_manager,
    require_household_member,
    require_member,
    save_uploaded_attachment,
    save_uploaded_image,
    serialize_family,
    serialize_grocery_item,
    serialize_household,
    serialize_household_member,
    serialize_household_message,
    serialize_relationship,
    serialize_reminder,
    serialize_reminder_notification,
    serialize_session,
    serialize_user,
    serialize_user_settings,
)
from .serializers import (
    ChangeEmailSerializer,
    ChangePasswordSerializer,
    DeactivateAccountSerializer,
    FamilyPatchSerializer,
    FamilySerializer,
    GroceryItemPatchSerializer,
    GroceryItemSerializer,
    GroceryStatusSerializer,
    HouseholdMemberUpdateSerializer,
    HouseholdMessageSerializer,
    HouseholdSerializer,
    JoinFamilySerializer,
    LoginSerializer,
    ProfileSerializer,
    RegisterSerializer,
    RelationshipLookupSerializer,
    RelationshipPatchSerializer,
    RelationshipSerializer,
    ReminderSnoozeSerializer,
    UserSettingsSerializer,
)
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError


class RelationshipInviteSerializer(serializers.Serializer):
    invited_name = serializers.CharField(max_length=100)
    relationship_type = serializers.ChoiceField(choices=["parent", "spouse", "sibling", "uncle", "aunt", "grandparent", "cousin", "nephew", "niece", "grandchild"])


def auth_payload(user: dict, request) -> dict:
    session_id = create_session(user["_id"], request)
    return {"access_token": create_access_token(user, session_id), "user": serialize_user(user)}


def canonical_relationship(data: dict) -> dict:
    first, second = object_id(data["person1_id"], "person1_id"), object_id(data["person2_id"], "person2_id")
    if first == second:
        from rest_framework.exceptions import ValidationError
        raise ValidationError("A person cannot have a relationship with themselves.")
    # Parent direction is meaningful. Other undirected edges are stored exactly once.
    if data["relationship_type"] in {"spouse", "sibling", "cousin"} and str(first) > str(second):
        first, second = second, first
    return {"person1_id": first, "person2_id": second, "relationship_type": data["relationship_type"]}


def require_people_in_family(family: dict, first: ObjectId, second: ObjectId) -> None:
    member_ids = family_member_ids(family)
    if first not in member_ids or second not in member_ids:
        from rest_framework.exceptions import ValidationError
        raise ValidationError("Both people must be members of this family.")


def unique_object_ids(values: list[str], field: str = "member_ids") -> list[ObjectId]:
    seen: set[ObjectId] = set()
    result: list[ObjectId] = []
    for value in values:
        item = object_id(value, field)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_optional_user_id(value: str | None, field: str = "assigned_to") -> ObjectId | None:
    if value in {None, ""}:
        return None
    return object_id(str(value), field)


def require_users_in_family(family: dict, user_ids: set[ObjectId]) -> None:
    outside = user_ids - family_member_ids(family)
    if outside:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({"member_ids": "Every household member must belong to this family."})


def require_user_in_household(household: dict, user_id: ObjectId | None, field: str = "assigned_to") -> None:
    if user_id is None:
        return
    if user_id not in household_member_ids(household):
        from rest_framework.exceptions import ValidationError
        raise ValidationError({field: "Choose a member of this household."})


def users_by_id(user_ids: set[ObjectId]) -> dict[ObjectId, dict]:
    if not user_ids:
        return {}
    return {user["_id"]: user for user in get_database().users.find({"_id": {"$in": list(user_ids)}})}


def sync_grocery_reminder(item: dict, reset_next_due: bool = False) -> dict | None:
    database = get_database()
    current = database.reminders.find_one({"grocery_item_id": item["_id"]})
    interval = int(item.get("reminder_interval_minutes") or 1440)
    active = bool(item.get("reminder_enabled") and item.get("assigned_to") and item.get("status") == "pending")
    timestamp = now()
    if not active:
        if current:
            database.reminders.update_one(
                {"_id": current["_id"]},
                {"$set": {"active": False, "next_due_at": None, "updated_at": timestamp, "stopped_at": timestamp}},
            )
            current.update({"active": False, "next_due_at": None, "updated_at": timestamp, "stopped_at": timestamp})
            return current
        return None
    next_due_at = current.get("next_due_at") if current and not reset_next_due else timestamp + timedelta(minutes=interval)
    reminder = {
        "household_id": item["household_id"],
        "grocery_item_id": item["_id"],
        "assigned_to": item["assigned_to"],
        "interval_minutes": interval,
        "active": True,
        "next_due_at": next_due_at,
        "updated_at": timestamp,
    }
    database.reminders.update_one(
        {"grocery_item_id": item["_id"]},
        {"$set": reminder, "$setOnInsert": {"created_at": timestamp, "last_sent_at": None, "snoozed_until": None}},
        upsert=True,
    )
    return database.reminders.find_one({"grocery_item_id": item["_id"]})


def find_grocery_item(household: dict, grocery_id: str) -> dict:
    item = get_database().grocery_items.find_one({"_id": object_id(grocery_id, "grocery_id"), "household_id": household["_id"]})
    if not item:
        from rest_framework.exceptions import NotFound
        raise NotFound("Grocery item not found.")
    return item


def serialize_grocery_response(item: dict) -> dict:
    ids = {item["added_by"]}
    if item.get("assigned_to"):
        ids.add(item["assigned_to"])
    reminder = get_database().reminders.find_one({"grocery_item_id": item["_id"]})
    return serialize_grocery_item(item, users_by_id(ids), reminder)


def replace_household_members(household: dict, family: dict, member_ids: set[ObjectId]) -> None:
    database = get_database()
    member_ids.add(household["created_by"])
    require_users_in_family(family, member_ids)
    current_ids = household_member_ids(household)
    created = now()
    additions = member_ids - current_ids
    removals = current_ids - member_ids
    if additions:
        database.household_members.insert_many([
            {
                "household_id": household["_id"],
                "user_id": user_id,
                "role": "creator" if user_id == household["created_by"] else "member",
                "joined_at": created,
            }
            for user_id in additions
        ])
    if removals:
        database.household_members.delete_many({"household_id": household["_id"], "user_id": {"$in": list(removals)}})
        database.grocery_items.update_many(
            {"household_id": household["_id"], "assigned_to": {"$in": list(removals)}, "status": "pending"},
            {"$set": {"assigned_to": None, "updated_at": created}},
        )
        database.reminders.update_many(
            {"household_id": household["_id"], "assigned_to": {"$in": list(removals)}},
            {"$set": {"active": False, "next_due_at": None, "updated_at": created, "stopped_at": created}},
        )
    database.households.update_one({"_id": household["_id"]}, {"$set": {"updated_at": created}})


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = {
            "name": data["name"],
            "email": data["email"],
            "password_hash": make_password(data["password"]),
            "profile_picture": None,
            "date_of_birth": None,
            "gender": "",
            "location": "",
            "occupation": "",
            "bio": "",
            "token_version": 0,
            "is_active": True,
            "deactivated_at": None,
            "last_active_at": None,
            "privacy": {"activity_status_enabled": True, "show_profile_details": True},
            "notifications": {"enabled": True, "messages": True, "household_reminders": True, "family_activity": True},
            "appearance": {"reduce_motion": False},
            "created_at": now(),
            "updated_at": now(),
        }
        try:
            user["_id"] = get_database().users.insert_one(user).inserted_id
        except DuplicateKeyError:
            return Response({"error": {"code": "duplicate_email", "message": "An account with this email already exists.", "details": {"email": ["This email is already in use."]}}}, status=status.HTTP_409_CONFLICT)
        return Response(auth_payload(user, request), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = get_database().users.find_one({"email": data["email"]})
        if not user or not check_password(data["password"], user.get("password_hash", "")):
            return Response({"error": {"code": "invalid_credentials", "message": "Email or password is incorrect.", "details": {}}}, status=status.HTTP_401_UNAUTHORIZED)
        if not user.get("is_active", True):
            return Response({"error": {"code": "account_deactivated", "message": "This account has been deactivated.", "details": {}}}, status=status.HTTP_403_FORBIDDEN)
        return Response(auth_payload(user, request))


class LogoutView(APIView):
    def post(self, request):
        database = get_database()
        if request.user.session_id:
            # Sign out only this device; other active sessions are untouched.
            database.sessions.update_one(
                {"session_id": request.user.session_id, "user_id": request.user.document["_id"]},
                {"$set": {"revoked": True}},
            )
        else:
            # A legacy token with no session id can only be revoked the old blanket way.
            database.users.update_one({"_id": request.user.document["_id"]}, {"$inc": {"token_version": 1}, "$set": {"updated_at": now()}})
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyProfileView(APIView):
    def get(self, request):
        user = get_database().users.find_one({"_id": request.user.document["_id"]})
        return Response({"user": serialize_user(user)})

    def patch(self, request):
        serializer = ProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updates = dict(serializer.validated_data)
        image = updates.pop("profile_picture", None)
        if image:
            updates["profile_picture"] = save_uploaded_image(image, "profiles")
        elif "profile_picture" in serializer.validated_data:
            updates["profile_picture"] = None
        if not updates:
            return Response({"user": serialize_user(request.user.document)})
        if updates.get("date_of_birth"):
            # MongoDB stores a stable ISO date string for date-only profile information.
            updates["date_of_birth"] = updates["date_of_birth"].isoformat()
        updates["updated_at"] = now()
        get_database().users.update_one({"_id": request.user.document["_id"]}, {"$set": updates})
        user = get_database().users.find_one({"_id": request.user.document["_id"]})
        return Response({"user": serialize_user(user)})


class UserDetailView(APIView):
    def get(self, request, user_id: str):
        target_id = object_id(user_id, "user_id")
        user = get_database().users.find_one({"_id": target_id})
        if not user:
            raise NotFound("User not found.")
        is_self = target_id == request.user.document["_id"]
        shared_family = get_database().families.find_one({
            "members.user_id": {"$all": [request.user.document["_id"], target_id]}
        })
        if not shared_family and not is_self:
            raise PermissionDenied("You may only view profiles in a shared family.")
        privacy = user.get("privacy") or {}
        full = is_self or privacy.get("show_profile_details", True)
        return Response({"user": serialize_user(user, full=full)})


class UserSettingsView(APIView):
    def get(self, request):
        return Response({"settings": serialize_user_settings(request.user.document)})

    def patch(self, request):
        serializer = UserSettingsSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        field_map = {
            "activity_status_enabled": "privacy.activity_status_enabled",
            "show_profile_details": "privacy.show_profile_details",
            "notifications_enabled": "notifications.enabled",
            "notify_messages": "notifications.messages",
            "notify_household_reminders": "notifications.household_reminders",
            "notify_family_activity": "notifications.family_activity",
            "reduce_motion": "appearance.reduce_motion",
        }
        updates = {field_map[key]: value for key, value in data.items() if key in field_map}
        if updates:
            updates["updated_at"] = now()
            get_database().users.update_one({"_id": request.user.document["_id"]}, {"$set": updates})
        user = get_database().users.find_one({"_id": request.user.document["_id"]})
        return Response({"settings": serialize_user_settings(user)})


class ChangePasswordView(APIView):
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not check_password(data["current_password"], request.user.document.get("password_hash", "")):
            raise ValidationError({"current_password": "Current password is incorrect."})
        database = get_database()
        database.users.update_one(
            {"_id": request.user.document["_id"]},
            {"$set": {"password_hash": make_password(data["new_password"]), "updated_at": now()}, "$inc": {"token_version": 1}},
        )
        # A password change is a security event: every other device is signed out, and this
        # one is re-issued a fresh token/session so the current user isn't logged out too.
        database.sessions.update_many({"user_id": request.user.document["_id"]}, {"$set": {"revoked": True}})
        user = database.users.find_one({"_id": request.user.document["_id"]})
        return Response(auth_payload(user, request))


class ChangeEmailView(APIView):
    def post(self, request):
        serializer = ChangeEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not check_password(data["current_password"], request.user.document.get("password_hash", "")):
            raise ValidationError({"current_password": "Current password is incorrect."})
        database = get_database()
        try:
            database.users.update_one({"_id": request.user.document["_id"]}, {"$set": {"email": data["new_email"], "updated_at": now()}})
        except DuplicateKeyError:
            raise ValidationError({"new_email": "This email is already in use."})
        user = database.users.find_one({"_id": request.user.document["_id"]})
        return Response({"user": serialize_user(user)})


class UserSessionsView(APIView):
    def get(self, request):
        sessions = list(get_database().sessions.find({"user_id": request.user.document["_id"], "revoked": False}).sort("last_seen_at", -1))
        return Response({"sessions": [serialize_session(session, current=session["session_id"] == request.user.session_id) for session in sessions]})


class RevokeOtherSessionsView(APIView):
    def post(self, request):
        get_database().sessions.update_many(
            {"user_id": request.user.document["_id"], "session_id": {"$ne": request.user.session_id}},
            {"$set": {"revoked": True}},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserSessionDetailView(APIView):
    def delete(self, request, session_id: str):
        database = get_database()
        session = database.sessions.find_one({"session_id": session_id, "user_id": request.user.document["_id"]})
        if not session:
            raise NotFound("Session not found.")
        database.sessions.update_one({"_id": session["_id"]}, {"$set": {"revoked": True}})
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeactivateAccountView(APIView):
    def post(self, request):
        serializer = DeactivateAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not check_password(serializer.validated_data["password"], request.user.document.get("password_hash", "")):
            raise ValidationError({"password": "Your password is incorrect."})
        database = get_database()
        database.users.update_one(
            {"_id": request.user.document["_id"]},
            {"$set": {"is_active": False, "deactivated_at": now(), "updated_at": now()}, "$inc": {"token_version": 1}},
        )
        database.sessions.update_many({"user_id": request.user.document["_id"]}, {"$set": {"revoked": True}})
        return Response(status=status.HTTP_204_NO_CONTENT)


class FamiliesView(APIView):
    def get(self, request):
        families = list(get_database().families.find({"members.user_id": request.user.document["_id"]}).sort("updated_at", -1))
        return Response({"families": [serialize_family(family, membership_for(family, request.user.document["_id"])) for family in families]})

    def post(self, request):
        serializer = FamilySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        image = data.pop("family_image", None)
        created = now()
        family = {
            "name": data["name"],
            "description": data.get("description", ""),
            "family_image": save_uploaded_image(image, "families") if image else None,
            "created_by": request.user.document["_id"],
            "members": [{"user_id": request.user.document["_id"], "role": "admin", "joined_at": created}],
            "invite_code": create_invite_code(),
            "created_at": created,
            "updated_at": created,
        }
        try:
            family["_id"] = get_database().families.insert_one(family).inserted_id
        except DuplicateKeyError:
            # The chance of a collision is negligible; retry once to retain the unique guarantee.
            family["invite_code"] = create_invite_code()
            family["_id"] = get_database().families.insert_one(family).inserted_id
        return Response({"family": serialize_family(family, family["members"][0])}, status=status.HTTP_201_CREATED)


class JoinFamilyView(APIView):
    def post(self, request):
        serializer = JoinFamilySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["invite_code"]
        
        family = get_database().families.find_one({"invite_code": code})
        if family:
            if membership_for(family, request.user.document["_id"]):
                return Response({"family": serialize_family(family, membership_for(family, request.user.document["_id"])), "already_member": True})
            membership = {"user_id": request.user.document["_id"], "role": "member", "joined_at": now()}
            get_database().families.update_one({"_id": family["_id"]}, {"$push": {"members": membership}, "$set": {"updated_at": now()}})
            family["members"].append(membership)
            return Response({"family": serialize_family(family, membership)}, status=status.HTTP_201_CREATED)
            
        invite = get_database().family_invites.find_one({"code": code})
        if not invite:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"invite_code": "That invitation code is not valid."})
            
        family = find_family(str(invite["family_id"]))
        user_id = request.user.document["_id"]
        if membership_for(family, user_id):
            return Response({"family": serialize_family(family, membership_for(family, user_id)), "already_member": True})
            
        pending_id = invite["pending_id"]
        
        get_database().families.update_one(
            {"_id": family["_id"], "members.pending_id": pending_id},
            {"$set": {
                "members.$.user_id": user_id,
                "members.$.is_pending": False,
                "updated_at": now()
            }}
        )
        
        get_database().relationships.update_many(
            {"person1_id": str(pending_id)},
            {"$set": {"person1_id": str(user_id)}}
        )
        get_database().relationships.update_many(
            {"person2_id": str(pending_id)},
            {"$set": {"person2_id": str(user_id)}}
        )
        
        get_database().family_invites.delete_one({"_id": invite["_id"]})
        
        family = find_family(str(family["_id"]))
        membership = membership_for(family, user_id)
        return Response({"family": serialize_family(family, membership)}, status=status.HTTP_201_CREATED)


class FamilyDetailView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        membership = require_member(family, request.user.document["_id"])
        return Response({"family": serialize_family(family, membership)})

    def patch(self, request, family_id: str):
        family = find_family(family_id)
        require_admin(family, request.user.document["_id"])
        serializer = FamilyPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updates = dict(serializer.validated_data)
        image = updates.pop("family_image", None)
        if image:
            updates["family_image"] = save_uploaded_image(image, "families")
        elif "family_image" in serializer.validated_data:
            updates["family_image"] = None
        updates["updated_at"] = now()
        get_database().families.update_one({"_id": family["_id"]}, {"$set": updates})
        family.update(updates)
        return Response({"family": serialize_family(family, membership_for(family, request.user.document["_id"]))})


class FamilyMembersView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        members_by_id = {user["_id"]: user for user in get_database().users.find({"_id": {"$in": list(family_member_ids(family))}})}
        members = []
        for membership in family.get("members", []):
            # Pending (invited-but-not-yet-joined) entries have a pending_id, not a user_id.
            if not membership.get("user_id"):
                continue
            user = members_by_id.get(membership["user_id"])
            if user:
                value = serialize_user(user)
                value["role"] = membership["role"]
                value["joined_at"] = membership.get("joined_at")
                members.append(value)
        return Response({"members": members})


class FamilyMemberDetailView(APIView):
    def delete(self, request, family_id: str, user_id: str):
        family = find_family(family_id)
        require_admin(family, request.user.document["_id"])
        target = object_id(user_id, "user_id")
        if target == request.user.document["_id"] or target == family["created_by"]:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("The family creator cannot be removed.")
        if not membership_for(family, target):
            from rest_framework.exceptions import NotFound
            raise NotFound("Family member not found.")
        get_database().families.update_one({"_id": family["_id"]}, {"$pull": {"members": {"user_id": target}}, "$set": {"updated_at": now()}})
        # Clean only initial-phase data associated with this family.
        get_database().relationships.delete_many({"family_id": family["_id"], "$or": [{"person1_id": target}, {"person2_id": target}]})
        return Response(status=status.HTTP_204_NO_CONTENT)


class FamilyInviteView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        require_admin(family, request.user.document["_id"])
        return Response({"invite_code": family["invite_code"]})

    def post(self, request, family_id: str):
        family = find_family(family_id)
        require_admin(family, request.user.document["_id"])
        code = create_invite_code()
        get_database().families.update_one({"_id": family["_id"]}, {"$set": {"invite_code": code, "updated_at": now()}})
        return Response({"invite_code": code})

class FamilyInvitesView(APIView):
    def post(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        serializer = RelationshipInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        pending_id = ObjectId()
        code = create_invite_code()
        
        pending_member = {
            "pending_id": pending_id,
            "name": serializer.validated_data["invited_name"],
            "is_pending": True,
            "joined_at": now(),
            "role": "member",
            "invited_by": request.user.document["_id"]
        }
        
        get_database().families.update_one({"_id": family["_id"]}, {"$push": {"members": pending_member}})
        
        edge = canonical_relationship({
            "person1_id": str(request.user.document["_id"]),
            "person2_id": str(pending_id),
            "relationship_type": serializer.validated_data["relationship_type"]
        })
        edge["family_id"] = family["_id"]
        edge["created_at"] = now()
        get_database().relationships.insert_one(edge)
        
        get_database().family_invites.insert_one({
            "family_id": family["_id"],
            "code": code,
            "pending_id": pending_id,
            "created_by": request.user.document["_id"],
            "created_at": now()
        })
        
        return Response({"invite_code": code, "pending_id": str(pending_id)}, status=status.HTTP_201_CREATED)


class FamilyTreeView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        member_ids = list(family_member_ids(family))
        users = {user["_id"]: serialize_user(user) for user in get_database().users.find({"_id": {"$in": member_ids}})}
        
        serialized_members = []
        for member in family.get("members", []):
            if member.get("is_pending"):
                serialized_members.append({
                    "id": str(member["pending_id"]),
                    "name": member.get("name", "Pending member"),
                    "is_pending": True,
                    "profile_picture": None
                })
            elif member.get("user_id") and member["user_id"] in users:
                serialized_members.append(users[member["user_id"]])
                
        relationships = list(get_database().relationships.find({"family_id": family["_id"]}))
        return Response({
            "family": serialize_family(family, membership_for(family, request.user.document["_id"])),
            "members": serialized_members,
            "relationships": [serialize_relationship(edge) for edge in relationships],
        })


class FamilyRelationshipLookupView(APIView):
    def post(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        serializer = RelationshipLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = object_id(serializer.validated_data["person_id"], "person_id")
        if target not in family_member_ids(family):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"person_id": "Choose a member of this family."})
        relationships = list(get_database().relationships.find({"family_id": family["_id"]}))
        result = calculate_relationship(str(request.user.document["_id"]), str(target), relationships)
        return Response(result)


class FamilySummaryView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        relationships = list(get_database().relationships.find({"family_id": family["_id"]}))
        parent_edges = [edge for edge in relationships if edge["relationship_type"] == "parent"]
        children: dict[str, list[str]] = {}
        parents: set[str] = set()
        children_nodes: set[str] = set()
        for edge in parent_edges:
            parent, child = str(edge["person1_id"]), str(edge["person2_id"])
            children.setdefault(parent, []).append(child)
            parents.add(parent)
            children_nodes.add(child)
        roots = parents - children_nodes
        max_depth = 0
        queue = deque((root, 1) for root in roots)
        while queue:
            node, depth = queue.popleft()
            max_depth = max(max_depth, depth)
            queue.extend((child, depth + 1) for child in children.get(node, []))
        users = list(get_database().users.find({"_id": {"$in": list(family_member_ids(family))}}).sort("created_at", -1).limit(5))
        return Response({
            "member_count": len(family.get("members", [])),
            "generation_count": max_depth or (1 if family.get("members") else 0),
            "relationship_count": len(relationships),
            "recent_members": [serialize_user(user) for user in users],
        })


class FamilyHouseholdsView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        memberships = list(get_database().household_members.find({"user_id": request.user.document["_id"]}))
        membership_by_household = {membership["household_id"]: membership for membership in memberships}
        households = list(get_database().households.find({
            "_id": {"$in": list(membership_by_household)},
            "family_id": family["_id"],
        }).sort("updated_at", -1))
        return Response({
            "households": [
                serialize_household(household, membership_by_household.get(household["_id"]))
                for household in households
            ]
        })

    def post(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        serializer = HouseholdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        members = set(unique_object_ids(data.get("member_ids", [])))
        members.add(request.user.document["_id"])
        require_users_in_family(family, members)
        created = now()
        household = {
            "family_id": family["_id"],
            "name": data["name"],
            "description": data.get("description", ""),
            "created_by": request.user.document["_id"],
            "created_at": created,
            "updated_at": created,
        }
        household["_id"] = get_database().households.insert_one(household).inserted_id
        get_database().household_members.insert_many([
            {
                "household_id": household["_id"],
                "user_id": user_id,
                "role": "creator" if user_id == request.user.document["_id"] else "member",
                "joined_at": created,
            }
            for user_id in members
        ])
        membership = household_membership_for(household, request.user.document["_id"])
        return Response({"household": serialize_household(household, membership, len(members))}, status=status.HTTP_201_CREATED)


class HouseholdDetailView(APIView):
    def get(self, request, household_id: str):
        household = find_household(household_id)
        membership = require_household_member(household, request.user.document["_id"])
        return Response({"household": serialize_household(household, membership)})

    def patch(self, request, household_id: str):
        household = find_household(household_id)
        require_household_manager(household, request.user.document["_id"])
        family = find_family(str(household["family_id"]))
        serializer = HouseholdSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updates = {}
        if "name" in data:
            updates["name"] = data["name"]
        if "description" in data:
            updates["description"] = data.get("description", "")
        if updates:
            updates["updated_at"] = now()
            get_database().households.update_one({"_id": household["_id"]}, {"$set": updates})
            household.update(updates)
        if "member_ids" in data:
            replace_household_members(household, family, set(unique_object_ids(data["member_ids"])))
        membership = household_membership_for(household, request.user.document["_id"])
        return Response({"household": serialize_household(household, membership)})

    def delete(self, request, household_id: str):
        household = find_household(household_id)
        require_household_manager(household, request.user.document["_id"])
        database = get_database()
        database.reminders.delete_many({"household_id": household["_id"]})
        database.grocery_items.delete_many({"household_id": household["_id"]})
        database.messages.delete_many({"household_id": household["_id"]})
        database.household_members.delete_many({"household_id": household["_id"]})
        database.households.delete_one({"_id": household["_id"]})
        return Response(status=status.HTTP_204_NO_CONTENT)


class HouseholdMembersView(APIView):
    def get(self, request, household_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        memberships = list(get_database().household_members.find({"household_id": household["_id"]}).sort("joined_at", 1))
        users = users_by_id({membership["user_id"] for membership in memberships})
        return Response({"members": [serialize_household_member(membership, users.get(membership["user_id"])) for membership in memberships]})

    def patch(self, request, household_id: str):
        household = find_household(household_id)
        require_household_manager(household, request.user.document["_id"])
        family = find_family(str(household["family_id"]))
        serializer = HouseholdMemberUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        replace_household_members(household, family, set(unique_object_ids(serializer.validated_data["member_ids"])))
        memberships = list(get_database().household_members.find({"household_id": household["_id"]}).sort("joined_at", 1))
        users = users_by_id({membership["user_id"] for membership in memberships})
        return Response({"members": [serialize_household_member(membership, users.get(membership["user_id"])) for membership in memberships]})


class HouseholdMessagesView(APIView):
    def get(self, request, household_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        try:
            limit = min(max(int(request.query_params.get("limit", 60)), 1), 100)
        except ValueError:
            limit = 60
        messages = list(get_database().messages.find({"household_id": household["_id"]}).sort("created_at", -1).limit(limit))
        messages.reverse()
        users = users_by_id({message["sender_id"] for message in messages})
        return Response({"messages": [serialize_household_message(message, users.get(message["sender_id"])) for message in messages]})

    def post(self, request, household_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        serializer = HouseholdMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        attachment = data.get("attachment")
        attachment_type = data.get("attachment_type") or None
        message = {
            "household_id": household["_id"],
            "sender_id": request.user.document["_id"],
            "text": data["text"],
            "attachment_type": attachment_type,
            "attachment_url": save_uploaded_attachment(attachment, f"chat/{attachment_type}", attachment_type) if attachment and attachment_type else None,
            "duration_seconds": data.get("duration_seconds"),
            "created_at": now(),
        }
        message["_id"] = get_database().messages.insert_one(message).inserted_id
        return Response({"message": serialize_household_message(message, request.user.document)}, status=status.HTTP_201_CREATED)


class FamilyMessagesView(APIView):
    def get(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        try:
            limit = min(max(int(request.query_params.get("limit", 60)), 1), 100)
        except ValueError:
            limit = 60
        messages = list(get_database().messages.find({"family_id": family["_id"]}).sort("created_at", -1).limit(limit))
        messages.reverse()
        users = users_by_id({message["sender_id"] for message in messages})
        return Response({"messages": [serialize_household_message(message, users.get(message["sender_id"])) for message in messages]})

    def post(self, request, family_id: str):
        family = find_family(family_id)
        require_member(family, request.user.document["_id"])
        serializer = HouseholdMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        attachment = data.get("attachment")
        attachment_type = data.get("attachment_type") or None
        message = {
            "family_id": family["_id"],
            "sender_id": request.user.document["_id"],
            "text": data["text"],
            "attachment_type": attachment_type,
            "attachment_url": save_uploaded_attachment(attachment, f"chat/{attachment_type}", attachment_type) if attachment and attachment_type else None,
            "duration_seconds": data.get("duration_seconds"),
            "created_at": now(),
        }
        message["_id"] = get_database().messages.insert_one(message).inserted_id
        return Response({"message": serialize_household_message(message, request.user.document)}, status=status.HTTP_201_CREATED)


class HouseholdGroceriesView(APIView):
    def get(self, request, household_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        query = {"household_id": household["_id"]}
        if request.query_params.get("status") in {"pending", "purchased", "cancelled"}:
            query["status"] = request.query_params["status"]
        items = list(get_database().grocery_items.find(query).sort("created_at", -1))
        ids = {item["added_by"] for item in items}
        ids.update(item["assigned_to"] for item in items if item.get("assigned_to"))
        users = users_by_id(ids)
        reminders = {
            reminder["grocery_item_id"]: reminder
            for reminder in get_database().reminders.find({"grocery_item_id": {"$in": [item["_id"] for item in items]}})
        }
        return Response({"items": [serialize_grocery_item(item, users, reminders.get(item["_id"])) for item in items]})

    def post(self, request, household_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        serializer = GroceryItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assigned_to = normalize_optional_user_id(data.get("assigned_to"))
        require_user_in_household(household, assigned_to)
        timestamp = now()
        item = {
            "household_id": household["_id"],
            "name": data["name"],
            "quantity": data.get("quantity", ""),
            "notes": data.get("notes", ""),
            "assigned_to": assigned_to,
            "added_by": request.user.document["_id"],
            "status": "pending",
            "reminder_enabled": bool(data.get("reminder_enabled")),
            "reminder_interval_minutes": int(data.get("reminder_interval_minutes") or 1440),
            "created_at": timestamp,
            "updated_at": timestamp,
            "purchased_at": None,
            "cancelled_at": None,
        }
        item["_id"] = get_database().grocery_items.insert_one(item).inserted_id
        sync_grocery_reminder(item, reset_next_due=True)
        return Response({"item": serialize_grocery_response(item)}, status=status.HTTP_201_CREATED)


class HouseholdGroceryDetailView(APIView):
    def patch(self, request, household_id: str, grocery_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        item = find_grocery_item(household, grocery_id)
        serializer = GroceryItemPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updates = {}
        reset_reminder = False
        for field in ("name", "quantity", "notes", "reminder_enabled", "reminder_interval_minutes"):
            if field in data:
                updates[field] = data[field]
                if field in {"reminder_enabled", "reminder_interval_minutes"}:
                    reset_reminder = True
        if "assigned_to" in data:
            assigned_to = normalize_optional_user_id(data.get("assigned_to"))
            require_user_in_household(household, assigned_to)
            updates["assigned_to"] = assigned_to
            reset_reminder = True
        if "status" in data:
            status_value = data["status"]
            updates["status"] = status_value
            reset_reminder = True
            if status_value == "purchased":
                updates["purchased_at"] = now()
            elif status_value == "cancelled":
                updates["cancelled_at"] = now()
            elif status_value == "pending":
                updates["purchased_at"] = None
                updates["cancelled_at"] = None
        if updates:
            updates["updated_at"] = now()
            get_database().grocery_items.update_one({"_id": item["_id"]}, {"$set": updates})
            item.update(updates)
            sync_grocery_reminder(item, reset_next_due=reset_reminder)
        return Response({"item": serialize_grocery_response(item)})

    def delete(self, request, household_id: str, grocery_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        item = find_grocery_item(household, grocery_id)
        get_database().reminders.delete_one({"grocery_item_id": item["_id"]})
        get_database().grocery_items.delete_one({"_id": item["_id"]})
        return Response(status=status.HTTP_204_NO_CONTENT)


class HouseholdGroceryStatusView(APIView):
    def post(self, request, household_id: str, grocery_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        item = find_grocery_item(household, grocery_id)
        serializer = GroceryStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_value = serializer.validated_data["status"]
        timestamp = now()
        updates = {"status": status_value, "updated_at": timestamp}
        if status_value == "purchased":
            updates["purchased_at"] = timestamp
        elif status_value == "cancelled":
            updates["cancelled_at"] = timestamp
        else:
            updates["purchased_at"] = None
            updates["cancelled_at"] = None
        get_database().grocery_items.update_one({"_id": item["_id"]}, {"$set": updates})
        item.update(updates)
        sync_grocery_reminder(item, reset_next_due=True)
        return Response({"item": serialize_grocery_response(item)})


class HouseholdDueRemindersView(APIView):
    def get(self, request, household_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        notifications = request.user.document.get("notifications") or {}
        if not notifications.get("enabled", True) or not notifications.get("household_reminders", True):
            # Enforced server-side: a user who has turned reminders off never receives them,
            # regardless of what the frontend does.
            return Response({"reminders": []})
        timestamp = now()
        reminders = list(get_database().reminders.find({
            "household_id": household["_id"],
            "assigned_to": request.user.document["_id"],
            "active": True,
            "next_due_at": {"$lte": timestamp},
        }).sort("next_due_at", 1).limit(10))
        notifications = []
        for reminder in reminders:
            item = get_database().grocery_items.find_one({"_id": reminder["grocery_item_id"], "household_id": household["_id"]})
            if not item or item.get("status") != "pending":
                get_database().reminders.update_one(
                    {"_id": reminder["_id"]},
                    {"$set": {"active": False, "next_due_at": None, "updated_at": timestamp, "stopped_at": timestamp}},
                )
                continue
            assigned_by = get_database().users.find_one({"_id": item["added_by"]})
            interval = int(reminder.get("interval_minutes") or item.get("reminder_interval_minutes") or 1440)
            next_due_at = timestamp + timedelta(minutes=interval)
            get_database().reminders.update_one(
                {"_id": reminder["_id"]},
                {"$set": {"last_sent_at": timestamp, "next_due_at": next_due_at, "updated_at": timestamp, "snoozed_until": None}},
            )
            reminder.update({"last_sent_at": timestamp, "next_due_at": next_due_at, "updated_at": timestamp, "snoozed_until": None})
            notifications.append(serialize_reminder_notification(reminder, item, assigned_by))
        return Response({"reminders": notifications})


class HouseholdReminderSnoozeView(APIView):
    def post(self, request, household_id: str, reminder_id: str):
        household = find_household(household_id)
        require_household_member(household, request.user.document["_id"])
        serializer = ReminderSnoozeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reminder = get_database().reminders.find_one({"_id": object_id(reminder_id, "reminder_id"), "household_id": household["_id"]})
        if not reminder:
            from rest_framework.exceptions import NotFound
            raise NotFound("Reminder not found.")
        item = get_database().grocery_items.find_one({"_id": reminder["grocery_item_id"], "household_id": household["_id"]})
        if not item or item.get("status") != "pending" or not reminder.get("active"):
            from rest_framework.exceptions import ValidationError
            raise ValidationError("This reminder is no longer active.")
        snoozed_until = now() + timedelta(minutes=serializer.validated_data["minutes"])
        get_database().reminders.update_one(
            {"_id": reminder["_id"]},
            {"$set": {"next_due_at": snoozed_until, "snoozed_until": snoozed_until, "updated_at": now()}},
        )
        reminder.update({"next_due_at": snoozed_until, "snoozed_until": snoozed_until})
        return Response({"reminder": serialize_reminder(reminder)})


class RelationshipsView(APIView):
    def post(self, request):
        serializer = RelationshipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        family = find_family(data["family_id"])
        require_member(family, request.user.document["_id"])
        edge = canonical_relationship(data)
        require_people_in_family(family, edge["person1_id"], edge["person2_id"])
        edge.update({"family_id": family["_id"], "created_at": now(), "created_by": request.user.document["_id"]})
        try:
            edge["_id"] = get_database().relationships.insert_one(edge).inserted_id
        except DuplicateKeyError:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("This relationship already exists.")
        return Response({"relationship": serialize_relationship(edge)}, status=status.HTTP_201_CREATED)


class RelationshipDetailView(APIView):
    def patch(self, request, relationship_id: str):
        relationship = get_database().relationships.find_one({"_id": object_id(relationship_id, "relationship_id")})
        if not relationship:
            from rest_framework.exceptions import NotFound
            raise NotFound("Relationship not found.")
        family = find_family(str(relationship["family_id"]))
        require_admin(family, request.user.document["_id"])
        serializer = RelationshipPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        merged = {
            "person1_id": serializer.validated_data.get("person1_id", str(relationship["person1_id"])),
            "person2_id": serializer.validated_data.get("person2_id", str(relationship["person2_id"])),
            "relationship_type": serializer.validated_data.get("relationship_type", relationship["relationship_type"]),
        }
        edge = canonical_relationship(merged)
        require_people_in_family(family, edge["person1_id"], edge["person2_id"])
        try:
            get_database().relationships.update_one({"_id": relationship["_id"]}, {"$set": edge})
        except DuplicateKeyError:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("This relationship already exists.")
        relationship.update(edge)
        return Response({"relationship": serialize_relationship(relationship)})

    def delete(self, request, relationship_id: str):
        relationship = get_database().relationships.find_one({"_id": object_id(relationship_id, "relationship_id")})
        if not relationship:
            from rest_framework.exceptions import NotFound
            raise NotFound("Relationship not found.")
        family = find_family(str(relationship["family_id"]))
        require_admin(family, request.user.document["_id"])
        get_database().relationships.delete_one({"_id": relationship["_id"]})
        return Response(status=status.HTTP_204_NO_CONTENT)
