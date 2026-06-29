from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def _send(group: str, event_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    async_to_sync(channel_layer.group_send)(
        group,
        {
            "type": "organization.event",
            "payload": {"type": event_type, **payload},
        },
    )


def broadcast_organization_event(organization_id, event_type: str, payload: dict) -> None:
    """Operational realtime stream (deliveries, couriers, metrics)."""
    _send(f"org.{organization_id}", event_type, payload)


def broadcast_chat_event(organization_id, event_type: str, payload: dict) -> None:
    """Dispatch <-> courier chat stream, kept separate from operational traffic."""
    _send(f"org.{organization_id}.chat", event_type, payload)
