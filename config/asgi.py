"""ASGI entry point.

Plain Django by default. When Django Channels is installed the same application also
serves the game-room websocket, so a deployment can opt into realtime by installing the
extra without any other change (see apps/games/realtime.py for the fallback).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()
application = django_application

try:
    from channels.routing import ProtocolTypeRouter, URLRouter

    from apps.games.routing import websocket_urlpatterns

    application = ProtocolTypeRouter({
        "http": django_application,
        "websocket": URLRouter(websocket_urlpatterns),
    })
except Exception:  # Channels not installed — HTTP only, clients poll for game state.
    pass
