from django.urls import path

from . import control_views, views

app_name = "portal_users"

urlpatterns = [
    # Control center (Pusat Kendali)
    path("", control_views.overview, name="control"),
    path("activity/", control_views.activity, name="activity"),
    path("lock/", control_views.lock, name="lock"),
    # AI agents: one instruction per process
    path("agents/", control_views.agent_list, name="agent-list"),
    path("agents/preview/", control_views.agent_preview, name="agent-preview"),
    path("agents/<str:process>/", control_views.agent_edit, name="agent-edit"),
    path("agents/<str:process>/restore/<int:version>/", control_views.agent_restore, name="agent-restore"),
    # Portal accounts
    path("users/", views.user_list, name="list"),
    path("users/new/", views.user_create, name="create"),
    path("users/<int:user_id>/", views.user_edit, name="edit"),
    path("users/<int:user_id>/password/", views.user_reset_password, name="reset-password"),
    path("users/<int:user_id>/status/", views.user_toggle_active, name="toggle-active"),
    path("users/<int:user_id>/delete/", views.user_delete, name="delete"),
]
