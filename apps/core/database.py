"""Thin MongoDB access layer. MongoDB is the source of application data."""
from __future__ import annotations

from functools import lru_cache
from threading import Lock

import certifi
from django.conf import settings
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

_index_lock = Lock()
_indexes_created = False


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    return MongoClient(
        settings.MONGODB_URI,
        serverSelectionTimeoutMS=5000,
        tlsCAFile=certifi.where(),
        tz_aware=True,
    )


def get_database() -> Database:
    global _indexes_created
    database = get_client()[settings.MONGODB_DATABASE]
    if not _indexes_created:
        with _index_lock:
            if not _indexes_created:
                database.users.create_index([("email", ASCENDING)], unique=True, name="unique_email")
                database.families.create_index([("invite_code", ASCENDING)], unique=True, sparse=True, name="invite_code")
                database.families.create_index([("members.user_id", ASCENDING)], name="family_member_lookup")
                database.relationships.create_index([("family_id", ASCENDING)], name="relationship_family")
                database.relationships.create_index(
                    [("family_id", ASCENDING), ("person1_id", ASCENDING), ("person2_id", ASCENDING), ("relationship_type", ASCENDING)],
                    unique=True,
                    name="canonical_relationship",
                )
                database.households.create_index([("family_id", ASCENDING)], name="household_family")
                database.households.create_index([("created_by", ASCENDING)], name="household_creator")
                database.household_members.create_index(
                    [("household_id", ASCENDING), ("user_id", ASCENDING)],
                    unique=True,
                    name="unique_household_member",
                )
                database.household_members.create_index([("user_id", ASCENDING)], name="household_member_user")
                database.messages.create_index([("household_id", ASCENDING), ("created_at", ASCENDING)], name="message_timeline")
                database.messages.create_index([("family_id", ASCENDING), ("created_at", ASCENDING)], name="family_message_timeline")
                database.sessions.create_index([("user_id", ASCENDING)], name="session_by_user")
                database.sessions.create_index([("session_id", ASCENDING)], unique=True, name="unique_session_id")
                database.otps.create_index([("user_id", ASCENDING), ("purpose", ASCENDING), ("created_at", ASCENDING)], name="otp_lookup")
                # TTL index: MongoDB automatically deletes an OTP document once its expires_at
                # time has passed — no separate cleanup job needed.
                database.otps.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="otp_ttl")
                database.grocery_items.create_index([("household_id", ASCENDING), ("status", ASCENDING)], name="grocery_household_status")
                database.grocery_items.create_index([("assigned_to", ASCENDING), ("status", ASCENDING)], name="grocery_assignment_status")
                database.reminders.create_index(
                    [("household_id", ASCENDING), ("assigned_to", ASCENDING), ("active", ASCENDING), ("next_due_at", ASCENDING)],
                    name="reminder_due_lookup",
                )
                database.reminders.create_index([("grocery_item_id", ASCENDING)], unique=True, name="unique_grocery_reminder")
                database.memories.create_index([("family_id", ASCENDING), ("_id", DESCENDING)], name="memory_family_timeline")
                database.memories.create_index([("owner_id", ASCENDING)], name="memory_owner")
                database.memories.create_index([("household_id", ASCENDING)], sparse=True, name="memory_household")
                database.memories.create_index([("selected_user_ids", ASCENDING)], sparse=True, name="memory_selected_viewers")
                database.memories.create_index([("tagged_user_ids", ASCENDING)], sparse=True, name="memory_tagged_members")
                # Play: game rooms, live state, invitations and finished-game history.
                database.game_rooms.create_index([("code", ASCENDING)], name="game_room_code")
                database.game_rooms.create_index([("status", ASCENDING), ("last_activity_at", DESCENDING)], name="game_room_open")
                database.game_rooms.create_index([("players.user_id", ASCENDING), ("status", ASCENDING)], name="game_room_participant")
                database.game_rooms.create_index([("family_id", ASCENDING), ("status", ASCENDING)], sparse=True, name="game_room_family")
                database.game_rooms.create_index([("game_id", ASCENDING), ("status", ASCENDING)], name="game_room_by_game")
                database.game_states.create_index([("room_id", ASCENDING)], unique=True, name="unique_game_state_room")
                database.game_invitations.create_index([("room_id", ASCENDING), ("to_user_id", ASCENDING)], unique=True, name="unique_game_invitation")
                database.game_invitations.create_index([("to_user_id", ASCENDING), ("status", ASCENDING)], name="game_invitation_inbox")
                # TTL: an unanswered invitation removes itself a day after it expires.
                database.game_invitations.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="game_invitation_ttl")
                database.game_results.create_index([("participant_ids", ASCENDING), ("finished_at", DESCENDING)], name="game_history_for_user")
                database.game_results.create_index([("room_id", ASCENDING)], unique=True, name="unique_game_result_room")
                _indexes_created = True
    return database
