from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import OrganizationUser, Courier
from .security import decode_access_token
from django.utils import timezone


class OrganizationRealtimeConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.organization_id = None
        token = parse_qs(self.scope.get("query_string", b"").decode()).get("token", [None])[0]
        actor = await self._authenticate(token)
        if not actor:
            await self.close(code=4401)
            return
        self.organization_id = str(actor.organization_id)
        self.group_name = f"org.{self.organization_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "organization_id", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def organization_event(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _authenticate(self, token):
        if not token:
            return None
        try:
            claims = decode_access_token(token)
        except Exception:
            return None
        if claims.get("typ") == "courier":
            return Courier.objects.select_related("organization").filter(
                id=claims["sub"],
                organization_id=claims.get("organization_id"),
            ).exclude(status=Courier.Status.INACTIVE).first()
        if claims.get("typ") == "organization":
            authenticated_user = OrganizationUser.objects.select_related("organization").filter(
                id=claims["sub"],
                status=OrganizationUser.Status.ACTIVE,
            ).first()
            if not authenticated_user:
                return None
            authenticated_user.last_active_at = timezone.now()
            authenticated_user.save(update_fields=["last_active_at", "updated_at"])
            return authenticated_user
        return None


class CourierRealtimeConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.organization_id = None
        token = parse_qs(self.scope.get("query_string", b"").decode()).get("token", [None])[0]
        courier = await self._authenticate(token)
        if not courier:
            await self.close(code=4401)
            return
        self.organization_id = str(courier.organization_id)
        self.group_name = f"org.{self.organization_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "organization_id", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def organization_event(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _authenticate(self, token):
        if not token:
            return None
        try:
            claims = decode_access_token(token)
        except Exception:
            return None
        if claims.get("typ") != "courier":
            return None
        courier = Courier.objects.select_related("organization", "branch").filter(
            id=claims["sub"],
            organization_id=claims.get("organization_id"),
        ).exclude(status=Courier.Status.INACTIVE).first()
        return courier
