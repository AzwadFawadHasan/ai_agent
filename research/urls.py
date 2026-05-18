from django.urls import path

from . import views

urlpatterns = [
    path("repositories/", views.RepositoryListView.as_view(), name="repository-list"),
    path("repositories", views.RepositoryListView.as_view(), name="repository-list-noslash"),
    path(
        "repositories/sessions/",
        views.RepositorySessionsView.as_view(),
        name="repository-sessions",
    ),
    path(
        "repositories/sessions",
        views.RepositorySessionsView.as_view(),
        name="repository-sessions-noslash",
    ),
    path(
        "research-sessions/",
        views.ResearchSessionListCreateView.as_view(),
        name="research-session-list-create",
    ),
    path(
        "research-sessions",
        views.ResearchSessionListCreateView.as_view(),
        name="research-session-list-create-noslash",
    ),
    path(
        "research-sessions/<int:pk>/",
        views.ResearchSessionDetailView.as_view(),
        name="research-session-detail",
    ),
    path(
        "research-sessions/<int:pk>",
        views.ResearchSessionDetailView.as_view(),
        name="research-session-detail-noslash",
    ),
]
