"""Privacy-preserving subscription-plan normalization for native meter reads.

The app-server exposes an account plan and one or more quota-bucket plans as
separate observations.  This module keeps those observations separate and
never invents a tier-to-quota multiplier: the native protocol does not expose
the claimed 5x/20x mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Values copied from the live v2 ``GetAccountResponse``/rate-limit schemas.
# ``unknown`` is a protocol sentinel, not a reportable subscription tier.
PLAN_TYPES = frozenset(
    {
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "team",
        "self_serve_business_prolite",
        "self_serve_business_usage_based",
        "business",
        "ent26",
        "enterprise_cbp_automation",
        "enterprise_cbp_usage_based",
        "enterprise",
        "edu",
        "edu_plus",
        "edu_pro",
    }
)
AUTH_TYPES = frozenset({"apiKey", "chatgpt", "amazonBedrock"})

_LIMITATIONS = (
    "Native schema does not verify a 5x/20x tier multiplier mapping.",
    "Plan observations are point-in-time and do not rewrite historical snapshots.",
    "Quota percentages and token usage remain separate measurements.",
)


def plan_type(value: Any) -> str | None:
    """Return a schema-allowlisted plan type, or ``None`` for unknown input."""

    # Do not normalize arbitrary strings (or stringify non-strings): callers
    # must never echo an untrusted backend value into a snapshot or diagnostic.
    if not isinstance(value, str):
        return None
    return value if value in PLAN_TYPES else None


def _account_payload(account_response: Any) -> Mapping[str, Any] | None:
    """Extract an account object from a native response without retaining it."""

    if not isinstance(account_response, Mapping):
        return None
    nested = account_response.get("account")
    if isinstance(nested, Mapping):
        return nested
    # Supporting an account-shaped mapping keeps this helper useful for tests
    # and callers that already extracted ``response["account"]``.  It still
    # reads only the two allowlisted fields below.
    if "type" in account_response or "planType" in account_response:
        return account_response
    return None


def _quota_plan_types(limits: Any) -> list[str]:
    """Collect only valid normalized bucket plan types in deterministic order."""

    if not isinstance(limits, (list, tuple)):
        return []
    values: set[str] = set()
    for bucket in limits:
        if not isinstance(bucket, Mapping):
            continue
        value = plan_type(bucket.get("plan_type"))
        if value is not None:
            values.add(value)
    return sorted(values)


def normalize_subscription(account_response: Any, limits: Any) -> dict[str, Any]:
    """Normalize native account and quota-bucket subscription observations.

    ``limits`` is the already-normalized list produced by ``meter``.  A valid
    native plan is authoritative for the current observation, but a differing
    bucket plan marks the result ``conflicted`` instead of silently selecting a
    value.  If native plan data is unavailable, exactly one allowlisted bucket
    plan can be exposed as a clearly-labelled ``fallback``; multiple bucket
    plans remain unknown.
    """

    account = _account_payload(account_response)
    auth_value = account.get("type") if account is not None else None
    auth_type = auth_value if isinstance(auth_value, str) and auth_value in AUTH_TYPES else None
    native_plan = (
        plan_type(account.get("planType"))
        if account is not None and auth_type not in {"apiKey", "amazonBedrock"}
        else None
    )
    quota_plans = _quota_plan_types(limits)
    account_reported = auth_type is not None or native_plan is not None

    route_without_subscription = auth_type in {"apiKey", "amazonBedrock"}

    if native_plan is not None:
        status = "reported"
        if quota_plans and set(quota_plans) != {native_plan}:
            status = "conflicted"
        source = "account/read"
        selected_plan = native_plan
    elif route_without_subscription:
        # API-key and Bedrock routes do not expose a ChatGPT subscription tier.
        # Never promote a quota bucket's plan into a subscription claim for
        # these routes; a simultaneous bucket observation is explicitly a
        # route/plan conflict instead.
        status = "conflicted" if quota_plans else "reported"
        source = "account/read"
        selected_plan = None
    elif quota_plans:
        # More than one bucket plan cannot identify a single current tier.
        # Preserve the allowlisted evidence while refusing to choose one.
        status = "conflicted" if len(quota_plans) > 1 else "fallback"
        source = "quota_buckets"
        selected_plan = quota_plans[0] if len(quota_plans) == 1 else None
    elif account_reported:
        status = "reported"
        source = "account/read"
        selected_plan = None
    else:
        status = "unavailable"
        source = "none"
        selected_plan = None

    return {
        "auth_type": auth_type,
        "plan_type": selected_plan,
        "status": status,
        "source": source,
        "quota_plan_types": quota_plans,
        "tier_multiplier": None,
        "limitations": list(_LIMITATIONS),
    }


__all__ = ["AUTH_TYPES", "PLAN_TYPES", "normalize_subscription", "plan_type"]
