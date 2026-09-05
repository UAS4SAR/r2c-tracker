# UAS4SAR cloud ownership migration

Status: Google project placement, permanent UAS4SAR administration, and billing cutover completed on 2026-09-04. The former billing account has no attached projects; the required 72-hour late-usage observation period is in progress. Cloudflare account administration, billing contacts, deployment authentication, the production RID2Caltopo Worker, and TURN operation are under UAS4SAR control. Old-principal removal and the separate `r2c-tracker.com` registrar transfer remain open.

## Progress on 2026-09-04

- Added and accepted both `kjt@uas4sar.com` and `ken@uas4sar.com` as Cloudflare **Super Administrator - All Privileges**. Both UAS4SAR users have two-factor authentication enabled. Renamed the existing Cloudflare account in place from `Kjtsar@kjt.us's Account` to `UAS4SAR LLC`; its account ID and attached resources did not change. The old member remains temporarily for recovery while billing and deployment authentication are cut over.
- Changed the Cloudflare billing email and account abuse-contact email to `kjt@uas4sar.com`. Left the existing primary PayPal payment method unchanged. Revoked the local Wrangler OAuth session associated with `kjtsar@kjt.us` and replaced it with an OAuth session associated with `kjt@uas4sar.com`, limited to the `UAS4SAR LLC` account and the `user:read`, `account:read`, `workers_scripts:write`, `workers_routes:write`, and `zone:read` scopes plus background access. The replacement identity can read and deploy `rid2caltopo-site`. The website build passed all 9 tests and the approved bundle was deployed as version `ffe9923d-560c-46a2-8c25-83e17471954c`, authored by `kjt@uas4sar.com`. The version retains `MANAGED_REQUEST_INGEST_KEY` and binds email to `info@uas4sar.com`. Both apex domains returned HTTP 200, `www.rid2caltopo.com` redirected to the apex and then returned 200, and all three rendered responses contained the UAS4SAR mailbox and updated tutorial with no `kjtsar@kjt.us` occurrence.
- Verified that production revision `r2c-tracker-pilot-00204-qux` successfully generated usage-attributed Cloudflare TURN credentials twice on September 4, including at `2026-09-04T19:43:32Z`. The TURN key-ID and API-token secrets each have one enabled version, and production logs contain no TURN credential-generation failure in the last 24 hours.
- Moved `r2c-tracker-platform` and `shaped-splicer-482602-v1` through dedicated export/import folders into UAS4SAR organization `722735295275`. Both projects now have parent folder `950131083631` (`R2C Import from KJT`).
- Relinked `r2c-tracker-platform`, `shaped-splicer-482602-v1`, and `rid2caltopo` to UAS4SAR billing account `01B1DE-5A235D-3DA7F7`. Together with `r2c-tracker-pilot`, all scoped service and mobile projects now use that account. The former account has no attached projects.
- Reconfirmed after the Cloudflare cutover that all four scoped projects remain billing-enabled on `01B1DE-5A235D-3DA7F7`, while former account `013BAC-12404A-395D0E` has no attached projects. The first same-day export observation still shows `$0.0496` gross and `$0.0016` net database-project usage in the former export for September 4, with a latest export time of `2026-09-04T21:26:46Z`; the UAS4SAR export had not yet populated September 4 rows. Treat this as preliminary posting lag and continue the 72-hour observation rather than as a failed relink.
- Preserved the existing billing tables in `r2c-tracker-platform.r2c_billing_export` and enabled the UAS4SAR billing account's standard and detailed usage-cost exports into that dataset. Pricing export remains disabled after the Google Console setup form failed to complete; the temporary BigQuery Admin and Project Editor grants used for setup were removed.
- Granted permanent Owner access to `kjt@uas4sar.com` on `shaped-splicer-482602-v1`, `r2c-tracker-platform`, and `rid2caltopo`. Removed the redundant temporary Project IAM Admin, Project Billing Manager, Browser, and Service Usage Viewer grants; retain the old owners for the 72-hour observation period.
- Re-audited direct project IAM after cutover. `kjtsar@kjt.us` remains Owner on all four scoped projects and additionally retains Compute OS Admin Login and IAP Tunnel Resource Accessor on `shaped-splicer-482602-v1`. `kjt@uas4sar.com` remains Owner on all four projects and has the required database VM administration roles; `ken@uas4sar.com` is also a direct Owner on `r2c-tracker-pilot` and remains the UAS4SAR Organization Administrator. Remove the enumerated old bindings only after the observation gate passes.
- Granted `kjt@uas4sar.com` Billing Account Viewer on the former billing account and Billing Account User and Viewer on the UAS4SAR billing account. Temporary Organization Policy Admin was granted for the move and removed afterward; the destination already grants Project Creator to the UAS4SAR domain.
- Verified that `ken@uas4sar.com` is the UAS4SAR Organization Administrator and destination Billing Account Administrator.
- Created `uas4sar-migration-preflight-20260904`, a ready 30 GB incremental snapshot of the production database disk in the `us` storage location.
- Verified production `v1.4.83` liveness and readiness, including both tracker and control-plane database checks.
- Confirmed that the source VM's daily snapshot policy exists but is not attached; its prior newest snapshot was dated August 30. Reattaching that recurring policy is a separate cost/recovery decision.
- Used folder-scoped source-export and destination-import policies for the migration, then restored the source organization, destination organization, source folder, and destination folder policies to `DENY`. Removed temporary source-organization and folder Project Mover roles, destination Folder Creator, and destination Organization Policy Admin. No cross-organization migration window remains open.
- Verified after project placement and billing reassignment that the database VM remains running and deletion-protected and production `v1.4.83` remains live and ready with both databases healthy.
- Preflighted `rid2caltopo`: it has no App Engine application, enabled Firestore API, Cloud Storage buckets, Compute/Cloud Run/Cloud SQL surface, or resource liens. Moved project number `702342854979` through the same scoped folders into UAS4SAR folder `950131083631`, preserved its Firebase service identities, granted `kjt@uas4sar.com` Owner, and restored both folder policies to `DENY`.
- The first `rid2caltopo` billing-link attempt reached the Free Trial account's five-project limit. A read-only Cloud Asset inventory of same-day default project `black-pier-507600-m0` (`My First Project`) found only its project record, enabled baseline services, and billing link—no service accounts, storage buckets, or other resources. After explicit approval, disabled billing on that empty project without deleting it and used the freed slot to link `rid2caltopo`. Temporary audit access was removed.

## Objective

Place every production resource and paid dependency used by r2c-tracker under UAS4SAR LLC control, make `kjt@uas4sar.com` the operating identity, remove operational dependence on `kjtsar@kjt.us`, and account for the service's total cost of ownership. The former Google Cloud billing account must receive no new r2c-tracker usage after cutover.

## Verified inventory

| Component | Current ownership or billing | State | Required action |
|---|---|---|---|
| Google Cloud project `r2c-tracker-pilot` | UAS4SAR organization and billing account | Organization placement, billing, and standard/detailed UAS4SAR billing export coverage are complete. `kjt@uas4sar.com` and `ken@uas4sar.com` are project owners; `kjtsar@kjt.us` is still an owner. | Observe both billing exports for late usage, then remove the old owner after the cutover observation period. |
| Google Cloud project `shaped-splicer-482602-v1` | UAS4SAR organization and billing account | Placement, billing, and permanent UAS4SAR Owner access are complete. The live PostgreSQL VM, disk, snapshots, buckets, secret, service accounts, and artifact repository moved intact; application database checks pass. | Decide the recurring snapshot policy and remove old IAM after the observation period. |
| Google Cloud project `r2c-tracker-platform` | UAS4SAR organization and billing account | Placement, billing, historical-table retention, permanent UAS4SAR Owner access, and standard/detailed billing exports are complete. Pricing export remains disabled. | Diagnose or retry pricing export without retaining temporary administrator access. |
| Google Cloud project `rid2caltopo` | UAS4SAR organization and billing account | Placement, billing, and permanent UAS4SAR Owner access are complete. The Firebase-labeled project has no active App Engine application, Firestore API, Storage buckets, Compute, Cloud Run, Cloud SQL, or liens. | Observe both billing exports for late usage, then remove old IAM after the 72-hour observation period. |
| Cloudflare account `UAS4SAR LLC` | `kjt@uas4sar.com`, `ken@uas4sar.com`, and `kjtsar@kjt.us` are accepted Super Administrators. Both UAS4SAR users have two-factor authentication enabled; the old user does not. | The account name and all attached resources remain on account ID `05075bb52ae729d966cfcc8106e92cc4`. It hosts six locked, auto-renewing Registrar domains, Worker `rid2caltopo-site`, and TURN key `r2c-tracker-pilot`. Billing and abuse-contact email now use `kjt@uas4sar.com`; the primary payment method remains PayPal associated with `kjt@kjt.us`. There are no user API tokens on the old user and no account-owned API tokens. The old local Wrangler OAuth session has been revoked. Its UAS4SAR replacement is active with deployment-specific scopes. Production version `ffe9923d-560c-46a2-8c25-83e17471954c` is authored by `kjt@uas4sar.com`, retains the ingest secret, binds email to `info@uas4sar.com`, and serves the three configured hostnames without exposing `kjtsar@kjt.us`. Production TURN credential generation succeeds with usage attribution, both Google secret versions are enabled, and no recent generation failure was found. Account-wide two-factor enforcement is off. | Verify an end-to-end managed-request email, confirm the PayPal recovery path is acceptable, enable account-wide two-factor enforcement, then remove the old member. |
| `r2c-tracker.com` registrar and DNS | GoDaddy nameservers; not present in the inspected Cloudflare account | The registration expires July 28, 2029 and is locked against transfer, update, renewal, and deletion changes. The registrant is privacy-protected. Apex A records point to Google, `www` aliases to `ghs.googlehosted.com`, and both hostnames return HTTPS 200 from Google Frontend. Registrar account ownership, recovery contacts, payment, and cost are not yet verified. | Put the existing GoDaddy account or domain access, registrant/contact data, renewal notices, and payment under UAS4SAR control. Keep DNS unchanged while doing so; unlock only if an inter-registrar transfer is deliberately chosen. |
| Legacy `tracker.kjt.us` hostname | Public DNS still aliases to Google hosting; the HTTPS check did not complete successfully | The hostname is not a healthy service, but its stale DNS remains under the old domain. | Identify the DNS-zone owner, confirm there is no intended redirect or rollback use, then remove the record after cutover. |
| Runtime credentials | Google Secret Manager in `r2c-tracker-pilot` | Includes database, Google OAuth, Gmail, Cloudflare TURN, FAA, App Store webhook, SMTP, signing, and administrative credentials. Values were not read. | Establish the owner of each upstream account and rotate credentials issued to the old identity after the platform cutover. |

Google's project move is a resource-hierarchy metadata change: project IDs and project-owned resources remain in place, direct project IAM remains, and active resources are expected to stay online. Inherited IAM, organization policies, quotas, and security controls change immediately. The destination UAS4SAR organization currently restricts IAM members to the UAS4SAR Cloud Identity customer, so all required UAS4SAR principals and service accounts must be validated before moving either project.

Moving a project does not move its billing. Billing must be relinked separately after the hierarchy move.

## Measured cost baseline

The former Google Cloud detailed billing export currently reports:

| Period | Project | Gross | Credits | Net |
|---|---|---:|---:|---:|
| August 2026 | `r2c-tracker-pilot` | $19.22 | -$4.98 | $14.24 |
| August 2026 | `shaped-splicer-482602-v1` | $5.34 | -$4.78 | $0.56 |
| August 2026 | `r2c-tracker-platform` | $0.00 | $0.00 | $0.00 |
| September 1-4, 2026 | `r2c-tracker-pilot` | $0.75 | -$0.71 | $0.04 |
| September 1-4, 2026 | `shaped-splicer-482602-v1` | $0.51 | -$0.49 | $0.02 |

The September export still contains usage for both production projects. Google can post usage and adjustments after the billing association changes, so the former account is not considered clear merely because a project disappears from its project list.

Cloudflare showed $0 observed and projected usage for September 1-4, but six recent paid invoices total $66.32. Those invoices must be classified from their line items before being assigned among domains or products. The `r2c-tracker.com` registrar charge remains unknown.

## Required authority

Use the same `@uas4sar.com` principal for the cross-organization move. Grant only for the migration window and revoke temporary roles afterward:

- Project IAM Admin on each source project.
- Project Mover on the KJT source organization.
- Project Creator on the UAS4SAR destination organization.
- Organization Policy Admin on both organizations, to temporarily allow the exact source/destination organization pair.
- For billing reassignment, Project Billing Manager, Project Browser, and Service Usage Viewer on each project; Billing Account Viewer on the former account; and Billing Account User plus Billing Account Viewer on the UAS4SAR account. Billing Account Administrator is broader than necessary for merely relinking projects.

The temporary hierarchy-migration roles, policy exceptions, project-scoped migration roles, and billing-export setup roles were removed after use. Permanent UAS4SAR Owner access remains on the migrated projects.

Cloudflare requires an existing verified Super Administrator to invite the new Super Administrator. This grants full account, billing, membership, purchase, and account-token authority and therefore requires explicit approval immediately before the invitation is sent.

## Cutover sequence

1. **Establish UAS4SAR authority and recovery.** Confirm two UAS4SAR-controlled administrators, hardware-backed MFA, recovery contacts, the destination billing account, payment profile, and a break-glass process. Grant the temporary least-privilege Google roles listed above.
2. **Prepare complete cost collection.** Move `r2c-tracker-platform` first. Preserve the former billing tables and configure standard, detailed, and pricing exports for the UAS4SAR billing account into the same dataset. Add budgets and alerts for the tracker projects.
3. **Preflight the database project.** Export IAM and effective organization policies; check Shared VPC, VPC Service Controls, custom roles, liens, quotas, Marketplace products, service-account domain restrictions, and public-IP behavior. Test application-to-database connectivity and record a fresh recoverable snapshot.
4. **Move the database project intact.** Temporarily allow only the KJT-to-UAS4SAR organization migration, move `shaped-splicer-482602-v1`, verify the VM, disk, snapshots, bucket, secret, service accounts, firewall, IAP, and application database traffic, then remove the temporary import/export policies.
5. **Relink Google billing.** Change `shaped-splicer-482602-v1` and, if necessary after reconciliation, `r2c-tracker-pilot` and `r2c-tracker-platform` to the confirmed UAS4SAR billing account. Lock the final billing associations after verification.
6. **Transfer Cloudflare administration.** Invite and accept `kjt@uas4sar.com` as Super Administrator; update billing identity and payment; create UAS4SAR-owned API and TURN credentials; deploy and validate the Worker, DNS, email delivery, video relay, and renewal settings; then revoke old credentials and membership.
7. **Transfer the registrar.** Put `r2c-tracker.com` registration, payment, notices, and recovery under UAS4SAR while preserving its Google custom-domain DNS records. Verify both apex and `www` TLS/HTTP behavior.
8. **Rotate remaining upstream credentials.** Replace any Gmail/OAuth, SMTP, FAA, App Store, database, webhook, signing, and administrative secret whose issuing account or recovery path is tied to the old identity. Validate before disabling each predecessor.
9. **Remove old access.** After at least 72 hours of stable health and cost reconciliation, remove `@kjt.us` users, tokens, and recovery contacts from tracker-scoped Google, Cloudflare, registrar, and upstream systems.

## Zero-old-cost acceptance criteria

The migration is complete only when all of the following are true:

- The former Google billing account has no r2c-tracker projects attached.
- Its detailed export shows no new r2c-tracker usage start times for at least 72 hours, and all late adjustments are classified.
- The next former-account statement contains no tracker usage other than documented lagged adjustments or credits.
- The UAS4SAR billing export contains every tracker project and reconciles to the UAS4SAR invoice.
- `shaped-splicer-482602-v1`, `r2c-tracker-platform`, and `r2c-tracker-pilot` are in the UAS4SAR organization and have no old-domain IAM binding required for operation.
- Cloudflare has a verified UAS4SAR Super Administrator, UAS4SAR billing/recovery data, UAS4SAR-owned deployment and TURN credentials, and no operational dependency on the old member.
- `r2c-tracker.com` renewal and recovery are owned and paid by UAS4SAR.
- Production health, database writes, login, email, managed video/TURN, flight-log storage, custom domains, TLS, and billing-export freshness all pass after cutover.

## Total-cost-of-ownership ledger

Record invoices and usage under these categories, even when a current month is zero:

- Google Cloud: Cloud Run request/CPU/memory, Compute Engine VM/disk/snapshots, network egress, Cloud Storage, Secret Manager, Artifact Registry, logging/monitoring, BigQuery billing export, release-staging resources, support, taxes, credits, and committed-use costs.
- Cloudflare: domain registrations and renewals, Workers, Realtime/TURN, DNS/add-ons, email routing or sending, taxes, and overages.
- Registrar/DNS: `r2c-tracker.com` registration, privacy, transfer, DNS, and certificate add-ons if any.
- Identity and communications: Google Workspace mailboxes, aliases, SMTP/email provider, recovery/security keys, and administrative seats attributable to the service.
- Commerce and distribution: Stripe subscription and dispute fees, Apple/Google developer or webhook-related fees allocated to r2c-tracker, and any marketplace subscriptions.
- Operations: monitoring/on-call services, backups and recovery tests, security scanning, support, accounting, insurance, and labor or contractor time needed to operate the service.

Allocate shared annual charges monthly and record both gross cost and credits. Keep entitlement metering based on timely first-party streaming usage; use provider invoices and exports for internal reconciliation and margin planning.
