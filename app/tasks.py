import uuid
import httpx
from .celery_app import celery_app
from .database import SessionLocal
from .models import WebhookEvent, EventStatus


@celery_app.task(name="deliver_webhook")
def deliver_webhook(event_id: str):
    db = SessionLocal()
    try:
        event = db.query(WebhookEvent).filter(
            WebhookEvent.id == uuid.UUID(event_id)
        ).first()

        if not event:
            print(f"WARNING: Event {event_id} not found")
            return

        try:
            response = httpx.post(
                event.target_url,
                json=event.payload,
                headers={"X-Relay-Event-Id": event_id},
                timeout=10.0,
            )

            if 200 <= response.status_code < 300:
                event.status = EventStatus.SUCCESS
            elif 400 <= response.status_code < 500:
                event.status = EventStatus.FAILED
            else:
                event.status = EventStatus.FAILED

        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
            print(f"Delivery failed for event {event_id}: {e}")
            event.status = EventStatus.FAILED

        event.attempts += 1
        db.commit()

    finally:
        db.close()
