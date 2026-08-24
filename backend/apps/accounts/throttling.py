import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AuthenticationThrottle


@dataclass(frozen=True)
class ThrottlePolicy:
    max_events: int
    window: timedelta
    block_for: timedelta


def login_ip_policy():
    return ThrottlePolicy(
        max_events=settings.AUTH_LOGIN_IP_MAX_FAILURES,
        window=timedelta(seconds=settings.AUTH_LOGIN_IP_WINDOW_SECONDS),
        block_for=timedelta(seconds=settings.AUTH_LOGIN_IP_BLOCK_SECONDS),
    )


def login_account_policy():
    return ThrottlePolicy(
        max_events=settings.AUTH_LOGIN_ACCOUNT_MAX_FAILURES,
        window=timedelta(seconds=settings.AUTH_LOGIN_ACCOUNT_WINDOW_SECONDS),
        block_for=timedelta(seconds=settings.AUTH_LOGIN_ACCOUNT_BLOCK_SECONDS),
    )


def password_reset_ip_policy():
    return ThrottlePolicy(
        max_events=settings.AUTH_RESET_IP_MAX_REQUESTS,
        window=timedelta(seconds=settings.AUTH_RESET_IP_WINDOW_SECONDS),
        block_for=timedelta(seconds=settings.AUTH_RESET_IP_BLOCK_SECONDS),
    )


def password_reset_account_policy():
    return ThrottlePolicy(
        max_events=settings.AUTH_RESET_ACCOUNT_MAX_REQUESTS,
        window=timedelta(seconds=settings.AUTH_RESET_ACCOUNT_WINDOW_SECONDS),
        block_for=timedelta(seconds=settings.AUTH_RESET_ACCOUNT_BLOCK_SECONDS),
    )


def get_client_ip(request):
    """Trust nginx's real-IP header only for a request from the local proxy."""

    remote_addr = str(request.META.get("REMOTE_ADDR", "")).strip()
    if remote_addr in {"127.0.0.1", "::1"}:
        proxied_addr = str(request.META.get("HTTP_X_REAL_IP", "")).strip()
        if proxied_addr:
            return proxied_addr
    return remote_addr or "unknown"


def retry_after(scope, identifier, policy):
    key_hash = _hash_identifier(scope, identifier)
    throttle = AuthenticationThrottle.objects.filter(
        scope=scope,
        key_hash=key_hash,
    ).first()
    if throttle is None or throttle.blocked_until is None:
        return 0

    remaining = (throttle.blocked_until - timezone.now()).total_seconds()
    return max(0, math.ceil(remaining))


@transaction.atomic
def record_event(scope, identifier, policy):
    now = timezone.now()
    key_hash = _hash_identifier(scope, identifier)
    throttle, _created = AuthenticationThrottle.objects.select_for_update().get_or_create(
        scope=scope,
        key_hash=key_hash,
        defaults={"window_started_at": now},
    )

    block_has_expired = throttle.blocked_until and throttle.blocked_until <= now
    window_has_expired = throttle.window_started_at + policy.window <= now
    if block_has_expired or window_has_expired:
        throttle.event_count = 0
        throttle.window_started_at = now
        throttle.blocked_until = None

    if throttle.blocked_until and throttle.blocked_until > now:
        return math.ceil((throttle.blocked_until - now).total_seconds())

    throttle.event_count += 1
    if throttle.event_count >= policy.max_events:
        throttle.blocked_until = now + policy.block_for
    throttle.save()
    return 0


def consume_event(scope, identifier, policy):
    remaining = retry_after(scope, identifier, policy)
    if remaining:
        return remaining
    record_event(scope, identifier, policy)
    return 0


def clear_events(scope, identifier):
    AuthenticationThrottle.objects.filter(
        scope=scope,
        key_hash=_hash_identifier(scope, identifier),
    ).delete()


def _hash_identifier(scope, identifier):
    normalized = str(identifier or "").strip().casefold()
    message = f"{scope}:{normalized}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()
