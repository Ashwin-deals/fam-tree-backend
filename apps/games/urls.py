from django.urls import path

from .views import (
    GameCatalogView,
    GameHistoryView,
    GameRoomDetailView,
    GameRoomsView,
    InvitationDetailView,
    InvitationsView,
    JoinByCodeView,
    RoomActionView,
    RoomBotsView,
    RoomInviteView,
    RoomPlayerView,
    RoomReadyView,
    RoomRematchView,
    RoomStartView,
    RoomStateView,
)

urlpatterns = [
    path("", GameCatalogView.as_view()),
    path("rooms/", GameRoomsView.as_view()),
    path("rooms/join/", JoinByCodeView.as_view()),
    path("rooms/<str:room_id>/", GameRoomDetailView.as_view()),
    path("rooms/<str:room_id>/ready/", RoomReadyView.as_view()),
    path("rooms/<str:room_id>/bots/", RoomBotsView.as_view()),
    path("rooms/<str:room_id>/players/<int:seat>/", RoomPlayerView.as_view()),
    path("rooms/<str:room_id>/start/", RoomStartView.as_view()),
    path("rooms/<str:room_id>/state/", RoomStateView.as_view()),
    path("rooms/<str:room_id>/actions/", RoomActionView.as_view()),
    path("rooms/<str:room_id>/invite/", RoomInviteView.as_view()),
    path("rooms/<str:room_id>/rematch/", RoomRematchView.as_view()),
    path("invitations/", InvitationsView.as_view()),
    path("invitations/<str:invitation_id>/", InvitationDetailView.as_view()),
    path("history/", GameHistoryView.as_view()),
]
