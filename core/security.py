import hashlib
import base64
import hmac
import struct
import secrets
import time
from datetime import timedelta
from dataclasses import dataclass
from urllib.parse import quote

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.utils import timezone

from .models import APIKey, Courier, ImpersonationSession, LoginAttempt, LoginSecurityState, Organization, OrganizationUser, PlatformUser, RefreshToken


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(raw_password: str) -> str:
    return make_password(raw_password)


def verify_password(raw_password: str, encoded: str) -> bool:
    return check_password(raw_password, encoded)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def sign_totp_secret(secret: str) -> str:
    return signing.Signer(salt="streak.mfa").sign(secret)


def unsign_totp_secret(signed_secret: str) -> str:
    return signing.Signer(salt="streak.mfa").unsign(signed_secret)


def totp_code(secret: str, timestamp: int | None = None, step: int = 30, digits: int = 6) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    padded_secret = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded_secret, casefold=True)
    counter = int(timestamp / step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    normalized = "".join(ch for ch in str(code) if ch.isdigit())
    if len(normalized) != 6:
        return False
    now = int(time.time())
    return any(hmac.compare_digest(totp_code(secret, now + offset * 30), normalized) for offset in range(-window, window + 1))


def create_mfa_setup(user: OrganizationUser | PlatformUser | object) -> dict:
    target = _mfa_target(user)
    secret = generate_totp_secret()
    target.mfa_secret = sign_totp_secret(secret)
    target.mfa_enabled = False
    target.mfa_confirmed_at = None
    target.save(update_fields=["mfa_secret", "mfa_enabled", "mfa_confirmed_at", "updated_at"])
    label = quote(f"{settings.TOTP_ISSUER}:{_user_email(user)}")
    issuer = quote(settings.TOTP_ISSUER)
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "provisioning_uri": uri}


def verify_user_totp(user: OrganizationUser | PlatformUser | object, code: str) -> bool:
    target = _mfa_target(user)
    if not target.mfa_secret:
        return False
    try:
        secret = unsign_totp_secret(target.mfa_secret)
    except signing.BadSignature:
        return False
    return verify_totp(secret, code)


def confirm_mfa(user: OrganizationUser | PlatformUser | object, code: str) -> bool:
    if not verify_user_totp(user, code):
        return False
    target = _mfa_target(user)
    target.mfa_enabled = True
    target.mfa_confirmed_at = timezone.now()
    target.save(update_fields=["mfa_enabled", "mfa_confirmed_at", "updated_at"])
    return True


def disable_mfa(user: OrganizationUser | PlatformUser | object) -> None:
    target = _mfa_target(user)
    target.mfa_enabled = False
    target.mfa_secret = ""
    target.mfa_confirmed_at = None
    target.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_confirmed_at", "updated_at"])


def mfa_required_for_user(user: OrganizationUser | PlatformUser | object) -> bool:
    return _mfa_target(user).mfa_enabled


@dataclass(frozen=True)
class TokenPair:
    access: str
    refresh: str
    refresh_record: RefreshToken


def _base_claims(subject: str, subject_type: str) -> dict:
    now = timezone.now()
    return {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "sub": subject,
        "typ": subject_type,
    }


def create_access_token_for_org_user(user: OrganizationUser) -> str:
    now = timezone.now()
    claims = _base_claims(str(user.id), "organization")
    claims.update(
        {
            "exp": int((now + settings.JWT_ACCESS_TTL).timestamp()),
            "organization_id": str(user.organization_id),
            "role": user.role.key,
        }
    )
    return jwt.encode(claims, settings.SECRET_KEY, algorithm="HS256")


def create_access_token_for_platform_user(user) -> str:
    now = timezone.now()
    claims = _base_claims(str(user.id), "platform")
    profile = platform_profile_for_user(user)
    claims.update(
        {
            "exp": int((now + settings.JWT_ACCESS_TTL).timestamp()),
            "role": profile.role.key,
        }
    )
    return jwt.encode(claims, settings.SECRET_KEY, algorithm="HS256")


def create_access_token_for_courier(courier: Courier) -> str:
    now = timezone.now()
    claims = _base_claims(str(courier.id), "courier")
    claims.update(
        {
            "exp": int((now + settings.JWT_ACCESS_TTL).timestamp()),
            "organization_id": str(courier.organization_id),
            "branch_id": str(courier.branch_id) if courier.branch_id else None,
        }
    )
    return jwt.encode(claims, settings.SECRET_KEY, algorithm="HS256")


def create_impersonation_access_token(session: ImpersonationSession) -> str:
    now = timezone.now()
    claims = _base_claims(str(session.platform_user.user_id), "impersonation")
    expires_at = min(now + settings.JWT_ACCESS_TTL, session.expires_at)
    claims.update(
        {
            "exp": int(expires_at.timestamp()),
            "organization_id": str(session.organization_id),
            "impersonation_session_id": str(session.id),
            "permissions": session.allowed_permissions,
        }
    )
    return jwt.encode(claims, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=["HS256"],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
    )


def create_refresh_token(
    *,
    user,
    subject_type: str,
    ip_address: str | None = None,
    user_agent: str = "",
    device_name: str = "",
) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(48)
    kwargs = {
        "subject_type": subject_type,
        "token_hash": hash_secret(raw),
        "expires_at": timezone.now() + settings.JWT_REFRESH_TTL,
        "ip_address": ip_address,
        "user_agent": user_agent[:2000],
        "device_name": device_name[:120],
    }
    if subject_type == RefreshToken.SubjectType.ORGANIZATION:
        kwargs["organization_user"] = user
    elif subject_type == RefreshToken.SubjectType.PLATFORM:
        kwargs["platform_user"] = platform_profile_for_user(user)
    else:
        kwargs["courier"] = user
    return raw, RefreshToken.objects.create(**kwargs)


def rotate_refresh_token(raw_refresh_token: str) -> TokenPair:
    token_hash = hash_secret(raw_refresh_token)
    record = RefreshToken.objects.select_related(
        "organization_user__organization",
        "organization_user__role",
        "platform_user__user",
        "platform_user__role",
        "courier__organization",
        "courier__branch",
    ).get(token_hash=token_hash)
    if not record.is_active:
        raise ValueError("Refresh token is expired or revoked.")

    record.revoked_at = timezone.now()
    if record.subject_type == RefreshToken.SubjectType.ORGANIZATION:
        user = record.organization_user
        access = create_access_token_for_org_user(user)
    elif record.subject_type == RefreshToken.SubjectType.PLATFORM:
        user = record.platform_user.user
        access = create_access_token_for_platform_user(user)
    else:
        user = record.courier
        access = create_access_token_for_courier(user)
    refresh, new_record = create_refresh_token(
        user=user,
        subject_type=record.subject_type,
        ip_address=record.ip_address,
        user_agent=record.user_agent,
        device_name=record.device_name,
    )
    record.rotated_to = new_record
    record.save(update_fields=["revoked_at", "rotated_to", "updated_at"])
    return TokenPair(access=access, refresh=refresh, refresh_record=new_record)


def issue_org_user_pair(user: OrganizationUser, request=None) -> TokenPair:
    access = create_access_token_for_org_user(user)
    refresh, record = create_refresh_token(
        user=user,
        subject_type=RefreshToken.SubjectType.ORGANIZATION,
        ip_address=_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )
    return TokenPair(access=access, refresh=refresh, refresh_record=record)


def issue_platform_user_pair(user, request=None) -> TokenPair:
    access = create_access_token_for_platform_user(user)
    refresh, record = create_refresh_token(
        user=user,
        subject_type=RefreshToken.SubjectType.PLATFORM,
        ip_address=_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )
    return TokenPair(access=access, refresh=refresh, refresh_record=record)


def platform_profile_for_user(user) -> PlatformUser:
    try:
        profile = user.platform_profile
    except PlatformUser.DoesNotExist as exc:
        raise ValueError("Django user is not configured as a platform user.") from exc
    if not profile.is_active:
        raise ValueError("Platform user is inactive.")
    return profile


def issue_courier_pair(courier: Courier, request=None) -> TokenPair:
    access = create_access_token_for_courier(courier)
    refresh, record = create_refresh_token(
        user=courier,
        subject_type=RefreshToken.SubjectType.COURIER,
        ip_address=_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )
    return TokenPair(access=access, refresh=refresh, refresh_record=record)


def validate_api_key(raw_key: str) -> APIKey | None:
    if not raw_key.startswith("sk_"):
        return None
    prefix = raw_key[:16]
    key = APIKey.objects.select_related("organization").filter(
        prefix=prefix,
        key_hash=hash_secret(raw_key),
        revoked_at__isnull=True,
    ).first()
    if key:
        key.last_used_at = timezone.now()
        key.save(update_fields=["last_used_at", "updated_at"])
    return key


def make_api_key() -> tuple[str, str, str]:
    raw = f"sk_live_{secrets.token_urlsafe(32)}"
    return raw, raw[:16], hash_secret(raw)


def login_lock_state(
    *,
    subject_type: str,
    email: str,
    organization: Organization | None = None,
    ip_address: str | None = None,
) -> LoginSecurityState:
    state, _ = LoginSecurityState.objects.get_or_create(
        subject_type=subject_type,
        organization=organization,
        email=email.lower(),
        ip_address=ip_address,
        defaults={},
    )
    return state


def is_login_locked(*, subject_type: str, email: str, organization: Organization | None = None, request=None) -> bool:
    state = login_lock_state(
        subject_type=subject_type,
        email=email,
        organization=organization,
        ip_address=_ip(request),
    )
    return state.is_locked


def record_login_attempt(
    *,
    subject_type: str,
    email: str,
    organization: Organization | None = None,
    request=None,
    success: bool,
    mfa_required: bool = False,
    failure_reason: str = "",
    count_for_lockout: bool = True,
) -> LoginAttempt:
    ip_address = _ip(request)
    attempt = LoginAttempt.objects.create(
        subject_type=subject_type,
        organization=organization,
        email=email.lower(),
        ip_address=ip_address,
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
        success=success,
        mfa_required=mfa_required,
        failure_reason=failure_reason,
    )
    state = login_lock_state(
        subject_type=subject_type,
        organization=organization,
        email=email,
        ip_address=ip_address,
    )
    if success:
        state.failure_count = 0
        state.locked_until = None
        state.last_failure_at = None
    elif not count_for_lockout:
        state.save(update_fields=["updated_at"])
        return attempt
    else:
        state.failure_count += 1
        state.last_failure_at = timezone.now()
        if state.failure_count >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            state.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    state.save(update_fields=["failure_count", "locked_until", "last_failure_at", "updated_at"])
    return attempt


def _ip(request) -> str | None:
    if not request:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _mfa_target(user):
    if isinstance(user, OrganizationUser):
        return user
    if isinstance(user, PlatformUser):
        return user
    if isinstance(user, get_user_model()):
        return platform_profile_for_user(user)
    raise TypeError("MFA is only available for user accounts.")


def _user_email(user) -> str:
    if isinstance(user, PlatformUser):
        return user.display_email
    return getattr(user, "email", "")
