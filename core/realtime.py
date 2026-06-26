from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_organization_event(organization_id, event_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    async_to_sync(channel_layer.group_send)(
        f"org.{organization_id}",
        {
            "type": "organization.event",
            "payload": {"type": event_type, **payload},
        },
    )
