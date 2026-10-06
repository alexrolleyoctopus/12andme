from django.contrib import admin
from django.urls import path, include
from budget import views
urlpatterns = [path("entry/<int:pk>/edit/", views.edit_entry), path("entry/<int:pk>/match/", views.match_entry), path("admin/", admin.site.urls), path("accounts/", include("django.contrib.auth.urls")), path("", views.dashboard, name="dashboard"), path("add/<str:kind>/", views.add, name="add"), path("entry/<int:pk>/settle/", views.settle, name="settle"), path("import/", views.import_csv, name="import")]
