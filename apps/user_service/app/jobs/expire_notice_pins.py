"""Expire timed notice banner pins (re-export)."""

from apps.user_service.app.jobs.publish_scheduled_notices import expire_notice_pins

__all__ = ["expire_notice_pins"]
