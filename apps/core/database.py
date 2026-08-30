"""Thin MongoDB access layer. MongoDB is the source of application data."""
from __future__ import annotations

from functools import lru_cache
from threading import Lock

import certifi
from django.conf import settings
from pymongo import ASCENDING, MongoClient
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
                database.grocery_items.create_index([("household_id", ASCENDING), ("status", ASCENDING)], name="grocery_household_status")
                database.grocery_items.create_index([("assigned_to", ASCENDING), ("status", ASCENDING)], name="grocery_assignment_status")
                database.reminders.create_index(
                    [("household_id", ASCENDING), ("assigned_to", ASCENDING), ("active", ASCENDING), ("next_due_at", ASCENDING)],
                    name="reminder_due_lookup",
                )
                database.reminders.create_index([("grocery_item_id", ASCENDING)], unique=True, name="unique_grocery_reminder")
                _indexes_created = True
    return database
