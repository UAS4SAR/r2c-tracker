import unittest
from decimal import Decimal

from subscription_plans import (
    ANNUAL_DISCOUNT_PERCENT,
    DEFAULT_TRIAL_DAYS,
    SUBSCRIPTION_PLANS,
    subscription_plan,
)


class SubscriptionPlanCatalogTest(unittest.TestCase):
    def test_working_catalog_prices_discount_and_capacity_ratios(self):
        self.assertEqual(45, DEFAULT_TRIAL_DAYS)
        self.assertEqual(Decimal("10"), ANNUAL_DISCOUNT_PERCENT)

        team = subscription_plan("TEAM")
        operations = subscription_plan("operations")
        command = subscription_plan("command")
        enterprise = subscription_plan("regional_enterprise")

        self.assertEqual(Decimal("25"), team.monthly_price)
        self.assertEqual(Decimal("270"), team.annual_price)
        self.assertEqual(Decimal("50"), operations.monthly_price)
        self.assertEqual(Decimal("540"), operations.annual_price)
        self.assertEqual(Decimal("100"), command.monthly_price)
        self.assertEqual(Decimal("1080"), command.annual_price)
        self.assertEqual(
            [Decimal("1"), Decimal("3"), Decimal("8")],
            [
                team.video_allowance_multiplier,
                operations.video_allowance_multiplier,
                command.video_allowance_multiplier,
            ],
        )
        self.assertIsNone(enterprise.monthly_price)
        self.assertIsNone(enterprise.video_allowance_multiplier)
        self.assertEqual(4, len(SUBSCRIPTION_PLANS))

    def test_unknown_plan_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown subscription plan"):
            subscription_plan("unlimited")


if __name__ == "__main__":
    unittest.main()
