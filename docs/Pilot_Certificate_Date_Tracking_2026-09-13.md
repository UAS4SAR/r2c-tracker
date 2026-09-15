# Certificate date accepted for pilot tracking

At the user's request, a pilot's certificate issue date is sufficient for organization qualifications tracking. Initial knowledge test/training and recurrent-training dates are optional and appear in a collapsed section of the member form.

Both `pilot_valid_until` (live roster) and `qualified_on` (flight history) use the certificate date when no initial knowledge date is recorded. An explicitly entered initial date is retained and used; optional recurrent training may extend the period. The calendar-month boundary, active pilot/member requirements, certificate identity fields, and organization isolation are unchanged. No actual test date is inferred or stored, and original flight submissions are not rewritten. This is an application tracking convention.

The mobile missing-training-date warning has been removed on both Apple and Android. Existing certificate-only profiles need no migration or re-entry; a roster refresh after Tracker deployment applies the new calculation.

Validation: 20 readiness/flight-history tests passed, including certificate-only roster eligibility, current/historical boundaries, optional training, future-event exclusion, inactive accounts, organization scope, and preservation of original submissions. Source changes only; no deployment performed.
