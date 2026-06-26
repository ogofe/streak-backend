from django.urls import path

from .consumers import CourierRealtimeConsumer, OrganizationRealtimeConsumer


websocket_urlpatterns = [
    path("ws/organization/", OrganizationRealtimeConsumer.as_asgi()),
    path("ws/courier/", CourierRealtimeConsumer.as_asgi()),
]
