from django.urls import path

from shop import views

urlpatterns = [
    path("<str:version>/<str:case>/", views.case_view),
]
