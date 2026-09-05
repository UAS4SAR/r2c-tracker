"""Provider-neutral subscription catalog for R2C Tracker.

The catalog is intentionally independent of Stripe (or any future payment
provider).  Provider product and price identifiers belong in deployment
configuration and subscription records, not in the operational entitlement
definitions below.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


DEFAULT_TRIAL_DAYS = 45
ANNUAL_DISCOUNT_PERCENT = Decimal("10")


@dataclass(frozen=True)
class SubscriptionPlan:
    code: str
    name: str
    monthly_price_cents: Optional[int]
    annual_discount_percent: Decimal
    video_allowance_multiplier: Optional[Decimal]
    description: str

    @property
    def monthly_price(self) -> Optional[Decimal]:
        if self.monthly_price_cents is None:
            return None
        return Decimal(self.monthly_price_cents) / Decimal(100)

    @property
    def annual_price_cents(self) -> Optional[int]:
        if self.monthly_price_cents is None:
            return None
        undiscounted = Decimal(self.monthly_price_cents * 12)
        discounted = undiscounted * (
            Decimal(100) - self.annual_discount_percent
        ) / Decimal(100)
        return int(discounted.to_integral_exact())

    @property
    def annual_price(self) -> Optional[Decimal]:
        if self.annual_price_cents is None:
            return None
        return Decimal(self.annual_price_cents) / Decimal(100)


SUBSCRIPTION_PLANS = (
    SubscriptionPlan(
        code="team",
        name="Team",
        monthly_price_cents=2_500,
        annual_discount_percent=ANNUAL_DISCOUNT_PERCENT,
        video_allowance_multiplier=Decimal("1"),
        description="Base organizational service and video allowance.",
    ),
    SubscriptionPlan(
        code="operations",
        name="Operations",
        monthly_price_cents=5_000,
        annual_discount_percent=ANNUAL_DISCOUNT_PERCENT,
        video_allowance_multiplier=Decimal("3"),
        description="Expanded video capacity for regular operational use.",
    ),
    SubscriptionPlan(
        code="command",
        name="Command",
        monthly_price_cents=10_000,
        annual_discount_percent=ANNUAL_DISCOUNT_PERCENT,
        video_allowance_multiplier=Decimal("8"),
        description="High-capacity service for demanding organizations.",
    ),
    SubscriptionPlan(
        code="regional_enterprise",
        name="Regional / Enterprise",
        monthly_price_cents=None,
        annual_discount_percent=Decimal("0"),
        video_allowance_multiplier=None,
        description=(
            "Custom capacity, regional placement, and contracted support or "
            "service objectives."
        ),
    ),
)

SUBSCRIPTION_PLAN_BY_CODE = {plan.code: plan for plan in SUBSCRIPTION_PLANS}


def subscription_plan(code: str) -> SubscriptionPlan:
    normalized = code.strip().lower()
    try:
        return SUBSCRIPTION_PLAN_BY_CODE[normalized]
    except KeyError as exc:
        raise ValueError("Unknown subscription plan.") from exc
