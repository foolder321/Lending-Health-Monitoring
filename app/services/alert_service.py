"""Alert service for threshold-crossing notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Optional


@dataclass
class AlertDecision:
    should_alert: bool
    direction: Optional[str] = None   # "up" | "down" | "critical_repeat"
    crossed_level: Optional[str] = None
    previous_bucket: Optional[str] = None
    current_bucket: Optional[str] = None
    is_critical_repeat: bool = False


class AlertService:
    """Determines whether an alert should be sent for a position."""

    def __init__(self, repository, repeat_minutes: int = 10) -> None:
        self.repository = repository
        self.repeat_minutes = repeat_minutes

    @staticmethod
    def get_hf_bucket(hf: Optional[float]) -> str:
        """Convert health factor into a discrete alert bucket."""
        if hf is None:
            return "unknown"
        if hf < 1.1:
            return "lt_1.1"
        if hf < 1.2:
            return "1.1"
        if hf < 1.25:
            return "1.2"
        if hf < 1.3:
            return "1.25"
        if hf < 1.4:
            return "1.3"
        if hf < 1.5:
            return "1.4"
        if hf < 1.6:
            return "1.5"
        if hf < 1.7:
            return "1.6"
        if hf < 1.8:
            return "1.7"
        if hf < 1.9:
            return "1.8"
        if hf < 2.0:
            return "1.9"
        return "2.0+"

    @staticmethod
    def _bucket_rank(bucket: str) -> int:
        order = {
            "unknown": -1,
            "lt_1.1": 0,
            "1.1": 1,
            "1.2": 2,
            "1.25": 3,
            "1.3": 4,
            "1.4": 5,
            "1.5": 6,
            "1.6": 7,
            "1.7": 8,
            "1.8": 9,
            "1.9": 10,
            "2.0+": 11,
        }
        return order.get(bucket, -1)

    async def evaluate(self, session, address: str, health_factor: Optional[float]) -> AlertDecision:
        """
        Return alert decision when:
        1. HF bucket changes, or
        2. HF is in critical zone (< 1.1) and repeat interval elapsed.
        """
        current_bucket = self.get_hf_bucket(health_factor)
        last_state = await self.repository.fetch_last_alert_state(session, address)
        previous_bucket = last_state["hf_bucket"] if last_state else None

        now = datetime.now(UTC)

        # First observation: save state, no alert
        if previous_bucket is None:
            await self.repository.upsert_alert_state(
                session=session,
                address=address,
                health_factor=health_factor,
                hf_bucket=current_bucket,
                last_alert_sent_at=None,
            )
            return AlertDecision(
                should_alert=False,
                previous_bucket=None,
                current_bucket=current_bucket,
            )

        # Critical repeat mode
        if current_bucket == "lt_1.1":
            last_sent_at_raw = last_state.get("last_alert_sent_at")
            should_repeat = False

            if last_sent_at_raw is None:
                should_repeat = True
            else:
                if isinstance(last_sent_at_raw, str):
                    last_sent_at = datetime.fromisoformat(last_sent_at_raw)
                else:
                    last_sent_at = last_sent_at_raw

                if last_sent_at.tzinfo is None:
                    last_sent_at = last_sent_at.replace(tzinfo=UTC)

                should_repeat = now - last_sent_at >= timedelta(minutes=self.repeat_minutes)

            await self.repository.upsert_alert_state(
                session=session,
                address=address,
                health_factor=health_factor,
                hf_bucket=current_bucket,
                last_alert_sent_at=now if should_repeat else last_state.get("last_alert_sent_at"),
            )

            if should_repeat:
                return AlertDecision(
                    should_alert=True,
                    direction="critical_repeat",
                    crossed_level="1.1",
                    previous_bucket=previous_bucket,
                    current_bucket=current_bucket,
                    is_critical_repeat=True,
                )

            return AlertDecision(
                should_alert=False,
                previous_bucket=previous_bucket,
                current_bucket=current_bucket,
            )

        # Normal bucket-crossing mode
        if previous_bucket == current_bucket:
            await self.repository.upsert_alert_state(
                session=session,
                address=address,
                health_factor=health_factor,
                hf_bucket=current_bucket,
                last_alert_sent_at=last_state.get("last_alert_sent_at"),
            )
            return AlertDecision(
                should_alert=False,
                previous_bucket=previous_bucket,
                current_bucket=current_bucket,
            )

        prev_rank = self._bucket_rank(previous_bucket)
        curr_rank = self._bucket_rank(current_bucket)
        direction = "up" if curr_rank > prev_rank else "down"
        crossed_level = current_bucket if direction == "up" else previous_bucket

        await self.repository.upsert_alert_state(
            session=session,
            address=address,
            health_factor=health_factor,
            hf_bucket=current_bucket,
            last_alert_sent_at=now,
        )

        return AlertDecision(
            should_alert=True,
            direction=direction,
            crossed_level=crossed_level,
            previous_bucket=previous_bucket,
            current_bucket=current_bucket,
        )
