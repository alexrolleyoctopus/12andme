from django.contrib import admin
from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from budget import views

urlpatterns = [
    path("assign/", views.assign_categories, name="assign"),
    path("entry/<int:pk>/edit/", views.edit_entry),
    path("entry/<int:pk>/match/", views.match_entry),
    path("admin/", admin.site.urls),
    # Expose only the account pages that this app actually implements.
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("add/<str:kind>/", views.add, name="add"),
    path("entry/<int:pk>/settle/", views.settle, name="settle"),
    path("import/", views.import_csv, name="import"),
]
