# R2C Tracker Subscription Model

## Current status

The subscription model is present as a dormant control-plane capability. The
application does not create payment-provider Checkout sessions, accept payments,
process billing webhooks, enforce paid tiers, or charge overages. Existing
organizations remain on the extended beta until a separately reviewed migration
is approved.

The initial catalog is a planning baseline:

| Plan | Monthly | Annual | Relative streaming allowance |
| --- | ---: | ---: | ---: |
| Team | $25 | $270 | 1x |
| Operations | $50 | $540 | 3x |
| Command | $100 | $1,080 | 8x |
| Regional / Enterprise | Contract | Contract | Contract |

Annual prices reflect a 10% discount. New trials are intended to last 45 days.
Absolute viewer-hour allowances remain unset until measured operating data can
support them; they must not be inferred from the relative multipliers.

## Customer-visible utilization

Organization management pages do not receive or display infrastructure costs,
provider rates, internal allocations, or profit margin. They display:

- plan and billing interval;
- viewer-hours used month to date for monthly plans or year to date for annual
  plans;
- the subscription-period boundary; and
- estimated viewer-hours remaining when an allowance has been assigned.

A viewer-hour is one hour delivered to one viewer. Two simultaneous viewers for
one hour therefore consume two viewer-hours. Completed and active managed-video
requests are clipped to the displayed period before their durations are summed.
The remaining-hours value is an estimate, floored at zero, and is deliberately
unavailable while a plan's absolute allowance is still undecided.

Actual Google Cloud and service-provider costs remain available only to the
platform administrator for capacity planning and internal reconciliation. They
are not customer entitlements and are not written into the customer-facing
utilization view.

## Provider-neutral subscription record

The control plane can retain an authoritative subscription snapshot containing:

- internal plan code and monthly or annual billing interval;
- lifecycle state and collection method;
- provider, customer, subscription, and price references;
- current period start and end;
- cancellation-at-period-end state;
- the provider snapshot timestamp; and
- the contracted viewer-hour allowance, when one has been approved.

Provider references remain server-side and are excluded from customer pages and
audit-event details. An older provider snapshot cannot overwrite a newer one,
and a provider cannot be changed implicitly. Synchronizing a payment-provider
record does not itself grant, revoke, or interrupt operational access. That
separation is intentional so delayed, duplicated, or out-of-order billing events
cannot terminate an active incident stream.

## Future Stripe connection

Connecting the UAS4SAR Stripe account is a separate deployment project. Before
live mode is enabled:

1. Create one Stripe Product and explicit monthly and annual Prices for each
   public plan in Stripe test mode. Keep the resulting Price IDs in deployment
   configuration, mapped through an allowlist to internal plan codes, intervals,
   and approved viewer-hour allowances.
2. Store the restricted Stripe secret key and the webhook endpoint signing
   secret in Secret Manager. Do not store either in the database or logs.
3. Create Checkout sessions only from an authenticated organization-owner flow.
   The server, not the browser, selects an allowlisted Price ID and attaches an
   opaque internal organization reference.
4. Verify every webhook against the unmodified request body before parsing it,
   and claim the provider event ID through the existing idempotency record before
   applying it.
5. Treat webhook delivery as asynchronous and unordered. On a relevant event,
   retrieve the current Stripe Subscription and synchronize that authoritative
   snapshot instead of deriving state from event order.
6. Define and test an incident-safe entitlement policy separately. It should
   include a grace path and must not cut off an active stream merely because a
   payment event arrives.
7. Exercise checkout, renewal, failed payment, plan change, cancellation, replay,
   and out-of-order-event cases in Stripe test mode. Moving to live credentials
   requires an explicit production approval and rollback plan.

No Stripe SDK or credential is required by the dormant model. This keeps the
current deployment free of payment-provider access until the commercial and
operational policies are ready.
