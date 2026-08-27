from django.urls import path

from routing.views import RouteOptimizeAPIView

urlpatterns = [
    path(
        "optimize/",
        RouteOptimizeAPIView.as_view(),
        name="route-optimize",
    ),
]
