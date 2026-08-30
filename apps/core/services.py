"""Domain services for Mongo-backed families and deterministic kinship."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import secrets
from typing import Iterable

from bson import ObjectId
from django.core.files.storage import default_storage
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .database import get_database


def now() -> datetime:
    return datetime.now(timezone.utc)


def object_id(value: str, field: str = "id") -> ObjectId:
    if not ObjectId.is_valid(value):
        raise ValidationError({field: "Invalid identifier."})
    return ObjectId(value)


def identifier(document: dict) -> dict:
    document = dict(document)
    document["id"] = str(document.pop("_id"))
    return document


def serialize_user(user: dict, include_email: bool = True) -> dict:
    result = {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "profile_picture": user.get("profile_picture"),
        "date_of_birth": user.get("date_of_birth"),
        "gender": user.get("gender"),
        "location": user.get("location", ""),
        "occupation": user.get("occupation", ""),
        "bio": user.get("bio", ""),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }
    if include_email:
        result["email"] = user.get("email", "")
    return result


def serialize_family(family: dict, membership: dict | None = None) -> dict:
    return {
        "id": str(family["_id"]),
        "name": family["name"],
        "description": family.get("description", ""),
        "family_image": family.get("family_image"),
        "created_by": str(family["created_by"]),
        "created_at": family.get("created_at"),
        "updated_at": family.get("updated_at"),
        "member_count": len(family.get("members", [])),
        "my_role": membership.get("role") if membership else None,
    }


def serialize_relationship(relationship: dict) -> dict:
    return {
        "id": str(relationship["_id"]),
        "family_id": str(relationship["family_id"]),
        "person1_id": str(relationship["person1_id"]),
        "person2_id": str(relationship["person2_id"]),
        "relationship_type": relationship["relationship_type"],
        "created_at": relationship.get("created_at"),
    }


def serialize_household(household: dict, membership: dict | None = None, member_count: int | None = None) -> dict:
    if member_count is None:
        member_count = get_database().household_members.count_documents({"household_id": household["_id"]})
    return {
        "id": str(household["_id"]),
        "family_id": str(household["family_id"]),
        "name": household["name"],
        "description": household.get("description", ""),
        "created_by": str(household["created_by"]),
        "created_at": household.get("created_at"),
        "updated_at": household.get("updated_at"),
        "member_count": member_count,
        "my_role": membership.get("role") if membership else None,
    }


def serialize_household_member(membership: dict, user: dict | None = None) -> dict:
    return {
        "id": str(membership["_id"]),
        "household_id": str(membership["household_id"]),
        "user_id": str(membership["user_id"]),
        "role": membership.get("role", "member"),
        "joined_at": membership.get("joined_at"),
        "user": serialize_user(user) if user else None,
    }


def serialize_household_message(message: dict, sender: dict | None = None) -> dict:
    return {
        "id": str(message["_id"]),
        "household_id": str(message["household_id"]),
        "sender_id": str(message["sender_id"]),
        "sender": serialize_user(sender) if sender else None,
        "text": message.get("text", ""),
        "created_at": message.get("created_at"),
    }


def serialize_grocery_item(item: dict, users_by_id: dict[ObjectId, dict] | None = None, reminder: dict | None = None) -> dict:
    users_by_id = users_by_id or {}
    added_by = users_by_id.get(item["added_by"])
    assigned_to = users_by_id.get(item.get("assigned_to")) if item.get("assigned_to") else None
    return {
        "id": str(item["_id"]),
        "household_id": str(item["household_id"]),
        "name": item["name"],
        "quantity": item.get("quantity", ""),
        "notes": item.get("notes", ""),
        "added_by": str(item["added_by"]),
        "added_by_user": serialize_user(added_by) if added_by else None,
        "assigned_to": str(item["assigned_to"]) if item.get("assigned_to") else None,
        "assigned_to_user": serialize_user(assigned_to) if assigned_to else None,
        "status": item.get("status", "pending"),
        "reminder_enabled": bool(item.get("reminder_enabled")),
        "reminder_interval_minutes": item.get("reminder_interval_minutes"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "purchased_at": item.get("purchased_at"),
        "cancelled_at": item.get("cancelled_at"),
        "reminder": serialize_reminder(reminder) if reminder else None,
    }


def serialize_reminder(reminder: dict) -> dict:
    return {
        "id": str(reminder["_id"]),
        "household_id": str(reminder["household_id"]),
        "grocery_item_id": str(reminder["grocery_item_id"]),
        "assigned_to": str(reminder["assigned_to"]) if reminder.get("assigned_to") else None,
        "active": bool(reminder.get("active")),
        "interval_minutes": reminder.get("interval_minutes"),
        "next_due_at": reminder.get("next_due_at"),
        "last_sent_at": reminder.get("last_sent_at"),
        "snoozed_until": reminder.get("snoozed_until"),
    }


def serialize_reminder_notification(reminder: dict, item: dict, assigned_by: dict | None = None) -> dict:
    assigner_name = assigned_by.get("name") if assigned_by else "a household member"
    return {
        "id": str(reminder["_id"]),
        "household_id": str(reminder["household_id"]),
        "grocery_item_id": str(item["_id"]),
        "item_name": item["name"],
        "message": f"Don't forget to buy {item['name']} \u2014 assigned by {assigner_name}.",
        "next_due_at": reminder.get("next_due_at"),
        "grocery_item": serialize_grocery_item(item, {item["added_by"]: assigned_by} if assigned_by else {}, reminder),
    }


def find_family(family_id: str) -> dict:
    family = get_database().families.find_one({"_id": object_id(family_id, "family_id")})
    if not family:
        raise NotFound("Family not found.")
    return family


def membership_for(family: dict, user_id: str | ObjectId) -> dict | None:
    key = ObjectId(user_id) if isinstance(user_id, str) else user_id
    return next((member for member in family.get("members", []) if member["user_id"] == key), None)


def require_member(family: dict, user_id: str | ObjectId) -> dict:
    membership = membership_for(family, user_id)
    if not membership:
        raise PermissionDenied("You are not a member of this family.")
    return membership


def require_admin(family: dict, user_id: str | ObjectId) -> dict:
    membership = require_member(family, user_id)
    if membership.get("role") != "admin":
        raise PermissionDenied("Family administrator permission is required.")
    return membership


def find_household(household_id: str) -> dict:
    household = get_database().households.find_one({"_id": object_id(household_id, "household_id")})
    if not household:
        raise NotFound("Household not found.")
    return household


def household_membership_for(household: dict, user_id: str | ObjectId) -> dict | None:
    key = ObjectId(user_id) if isinstance(user_id, str) else user_id
    return get_database().household_members.find_one({"household_id": household["_id"], "user_id": key})


def require_household_member(household: dict, user_id: str | ObjectId) -> dict:
    membership = household_membership_for(household, user_id)
    if not membership:
        raise PermissionDenied("You are not a member of this household.")
    return membership


def require_household_manager(household: dict, user_id: str | ObjectId) -> None:
    key = ObjectId(user_id) if isinstance(user_id, str) else user_id
    if household["created_by"] == key:
        return
    family = find_family(str(household["family_id"]))
    membership = membership_for(family, key)
    if membership and membership.get("role") == "admin":
        return
    raise PermissionDenied("Household creator or family administrator permission is required.")


def household_member_ids(household: dict) -> set[ObjectId]:
    memberships = get_database().household_members.find({"household_id": household["_id"]}, {"user_id": 1})
    return {member["user_id"] for member in memberships}


def create_invite_code() -> str:
    return secrets.token_urlsafe(8).replace("-", "").replace("_", "").upper()[:10]


def save_uploaded_image(upload, prefix: str) -> str:
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError({"image": "Image must be 5 MB or smaller."})
    if upload.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError({"image": "Use a JPEG, PNG, or WebP image."})
    extension = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else "jpg"
    path = default_storage.save(f"{prefix}/{secrets.token_urlsafe(12)}.{extension}", upload)
    return default_storage.url(path)


def family_member_ids(family: dict) -> set[ObjectId]:
    return {member["user_id"] for member in family.get("members", [])}


def relationship_graph(relationships: Iterable[dict]) -> dict[str, list[tuple[str, str]]]:
    """An undirected adjacency graph annotated with the relationship from node to neighbour."""
    graph: dict[str, list[tuple[str, str]]] = {}
    for relationship in relationships:
        a, b = str(relationship["person1_id"]), str(relationship["person2_id"])
        kind = relationship["relationship_type"]
        if kind == "parent":
            graph.setdefault(a, []).append((b, "child"))
            graph.setdefault(b, []).append((a, "parent"))
        else:
            graph.setdefault(a, []).append((b, kind))
            graph.setdefault(b, []).append((a, kind))
    return graph


def ancestor_distances(person_id: str, parent_to_children: dict[str, list[str]]) -> dict[str, int]:
    child_to_parents: dict[str, list[str]] = {}
    for parent, children in parent_to_children.items():
        for child in children:
            child_to_parents.setdefault(child, []).append(parent)
    distances = {person_id: 0}
    queue = deque([person_id])
    while queue:
        current = queue.popleft()
        for parent in child_to_parents.get(current, []):
            if parent not in distances:
                distances[parent] = distances[current] + 1
                queue.append(parent)
    return distances


def plural_ancestor(base: str, distance: int) -> str:
    if distance == 1:
        return base
    if distance == 2:
        return f"grand{base}"
    return f"{'great-' * (distance - 2)}grand{base}"


def calculate_relationship(source_id: str, target_id: str, relationships: list[dict]) -> dict:
    if source_id == target_id:
        return {"relationship": "You", "path": [source_id], "confidence": "exact"}

    graph = relationship_graph(relationships)
    # BFS is retained for an explainable graph path even where ancestry gives a richer label.
    predecessor = {source_id: None}
    queue = deque([source_id])
    while queue:
        current = queue.popleft()
        for neighbor, _ in graph.get(current, []):
            if neighbor not in predecessor:
                predecessor[neighbor] = current
                queue.append(neighbor)
    if target_id not in predecessor:
        return {"relationship": "No known relationship", "path": [], "confidence": "unknown"}

    path = []
    node: str | None = target_id
    while node is not None:
        path.append(node)
        node = predecessor[node]
    path.reverse()

    parent_to_children: dict[str, list[str]] = {}
    for edge in relationships:
        if edge["relationship_type"] == "parent":
            parent_to_children.setdefault(str(edge["person1_id"]), []).append(str(edge["person2_id"]))
    source_ancestors = ancestor_distances(source_id, parent_to_children)
    target_ancestors = ancestor_distances(target_id, parent_to_children)

    if target_id in source_ancestors:
        return {"relationship": plural_ancestor("parent", source_ancestors[target_id]), "path": path, "confidence": "exact"}
    if source_id in target_ancestors:
        return {"relationship": plural_ancestor("child", target_ancestors[source_id]), "path": path, "confidence": "exact"}

    common = set(source_ancestors) & set(target_ancestors)
    common.discard(source_id)
    common.discard(target_id)
    if common:
        ancestor = min(common, key=lambda item: source_ancestors[item] + target_ancestors[item])
        source_distance, target_distance = source_ancestors[ancestor], target_ancestors[ancestor]
        if source_distance == target_distance == 1:
            label = "sibling"
        elif source_distance == 1 and target_distance == 2:
            label = "niece / nephew"
        elif source_distance == 2 and target_distance == 1:
            label = "aunt / uncle"
        else:
            degree = min(source_distance, target_distance) - 1
            removed = abs(source_distance - target_distance)
            label = f"{degree}{'st' if degree == 1 else 'nd' if degree == 2 else 'rd' if degree == 3 else 'th'} cousin"
            if removed:
                label += f", {removed} time{'s' if removed > 1 else ''} removed"
        return {"relationship": label, "path": path, "confidence": "exact"}

    # Direct spouse/sibling relationships are useful even without parent edges.
    if len(path) == 2:
        direct = next(kind for neighbor, kind in graph[source_id] if neighbor == target_id)
        return {"relationship": direct, "path": path, "confidence": "exact"}
    return {"relationship": "Related through the family graph", "path": path, "confidence": "graph"}
