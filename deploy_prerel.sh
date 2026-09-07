#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

export CLOUDSDK_ACTIVE_CONFIG_NAME="${CLOUDSDK_ACTIVE_CONFIG_NAME:-r2c-tracker-pilot}"
export GCLOUD_PROJECT="${GCLOUD_PROJECT:-r2c-tracker-pilot}"
export REGION="${REGION:-us-west1}"
export SERVICE_NAME="${SERVICE_NAME:-r2c-tracker-prerel}"
export RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SERVICE_ACCOUNT:-r2c-tracker-prerel@r2c-tracker-pilot.iam.gserviceaccount.com}"
export CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-r2c-tracker-pilot:us-west1:r2c-prerel}"
export CLOUD_RUN_NETWORK="${CLOUD_RUN_NETWORK:-r2c-pilot-vpc}"
export CLOUD_RUN_SUBNET="${CLOUD_RUN_SUBNET:-r2c-pilot-us-west1}"
export CLOUD_RUN_VPC_EGRESS="${CLOUD_RUN_VPC_EGRESS:-private-ranges-only}"
export FLIGHTLOGS_BUCKET="${FLIGHTLOGS_BUCKET:-r2c-tracker-prerel-flightlogs}"
export CLOUD_RUN_MEMORY="${CLOUD_RUN_MEMORY:-1Gi}"
export ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-1}"
export DATABASE_URL_SECRET_NAME="${DATABASE_URL_SECRET_NAME:-r2c-prerel-tracker-database-url}"
export TRACKER_ADMIN_PASS_SECRET_NAME="${TRACKER_ADMIN_PASS_SECRET_NAME:-r2c-prerel-tracker-admin-password}"
export DEPLOYMENT_GATE_KEY_SECRET_NAME="${DEPLOYMENT_GATE_KEY_SECRET_NAME:-r2c-prerel-deployment-gate-key}"
export SECRET_KEY_SECRET_NAME="${SECRET_KEY_SECRET_NAME:-r2c-prerel-secret-key}"
export FAA_CLIENT_ID_SECRET_NAME="${FAA_CLIENT_ID_SECRET_NAME:-r2c-faa-notam-client-id}"
export FAA_CLIENT_SECRET_SECRET_NAME="${FAA_CLIENT_SECRET_SECRET_NAME:-r2c-faa-notam-client-secret}"
export CONTROL_PLANE_DATABASE_URL_SECRET_NAME="${CONTROL_PLANE_DATABASE_URL_SECRET_NAME:-r2c-prerel-control-plane-database-url}"
export CONTROL_PLANE_SIGNING_KEY_SECRET_NAME="${CONTROL_PLANE_SIGNING_KEY_SECRET_NAME:-r2c-prerel-control-plane-signing-key}"
export MANAGED_REQUEST_INGEST_KEY_SECRET_NAME="${MANAGED_REQUEST_INGEST_KEY_SECRET_NAME:-r2c-managed-request-ingest-key}"
export GOOGLE_OAUTH_CLIENT_ID_SECRET_NAME="${GOOGLE_OAUTH_CLIENT_ID_SECRET_NAME:-r2c-google-oauth-client-id}"
export GOOGLE_OAUTH_CLIENT_SECRET_SECRET_NAME="${GOOGLE_OAUTH_CLIENT_SECRET_SECRET_NAME:-r2c-google-oauth-client-secret}"
export MICROSOFT_OIDC_CLIENT_ID_SECRET_NAME="${MICROSOFT_OIDC_CLIENT_ID_SECRET_NAME:-}"
export MICROSOFT_OIDC_CLIENT_SECRET_SECRET_NAME="${MICROSOFT_OIDC_CLIENT_SECRET_SECRET_NAME:-}"
export MICROSOFT_OIDC_TENANT="${MICROSOFT_OIDC_TENANT:-organizations}"
export CLOUDFLARE_TURN_KEY_ID_SECRET_NAME="${CLOUDFLARE_TURN_KEY_ID_SECRET_NAME:-r2c-cloudflare-turn-key-id}"
export CLOUDFLARE_TURN_API_TOKEN_SECRET_NAME="${CLOUDFLARE_TURN_API_TOKEN_SECRET_NAME:-r2c-cloudflare-turn-api-token}"
export PLATFORM_EMAIL_SMTP_PASSWORD_SECRET_NAME="${PLATFORM_EMAIL_SMTP_PASSWORD_SECRET_NAME:-}"
export PLATFORM_EMAIL_SMTP_HOST="${PLATFORM_EMAIL_SMTP_HOST:-}"
export PLATFORM_EMAIL_SMTP_PORT="${PLATFORM_EMAIL_SMTP_PORT:-587}"
export PLATFORM_EMAIL_SMTP_USER="${PLATFORM_EMAIL_SMTP_USER:-}"
export PLATFORM_EMAIL_GMAIL_REFRESH_TOKEN_SECRET_NAME="${PLATFORM_EMAIL_GMAIL_REFRESH_TOKEN_SECRET_NAME:-r2c-platform-email-gmail-refresh-token}"
export PLATFORM_EMAIL_GMAIL_REFRESH_TOKEN_TARGET="${PLATFORM_EMAIL_GMAIL_REFRESH_TOKEN_TARGET-projects/r2c-tracker-pilot/secrets/r2c-platform-email-gmail-refresh-token}"
export PLATFORM_EMAIL_FROM="${PLATFORM_EMAIL_FROM:-kjt@uas4sar.com}"
export APP_STORE_CONNECT_WEBHOOK_SECRET_NAME="${APP_STORE_CONNECT_WEBHOOK_SECRET_NAME:-r2c-app-store-connect-webhook-secret}"
export TESTFLIGHT_FEEDBACK_EMAIL="${TESTFLIGHT_FEEDBACK_EMAIL:-kjt@uas4sar.com}"
export TESTFLIGHT_APP_NAME="${TESTFLIGHT_APP_NAME:-RID2Caltopo}"
export TESTFLIGHT_APP_APPLE_ID="${TESTFLIGHT_APP_APPLE_ID:-6792518823}"
export PLATFORM_BILLING_SOURCE="${PLATFORM_BILLING_SOURCE:-illustrative}"
export CONTROL_PLANE_MODE="${CONTROL_PLANE_MODE:-live}"
export RELEASE_STAGING_MODE="false"
export CONTROL_PLANE_PUBLIC_URL="${CONTROL_PLANE_PUBLIC_URL:-https://prerel.r2c-tracker.com}"
export CONTROL_PLANE_TRACKER_BASE_URL="${CONTROL_PLANE_TRACKER_BASE_URL:-https://prerel.r2c-tracker.com}"
export DEVICE_CREDENTIAL_ISSUANCE_ENABLED="${DEVICE_CREDENTIAL_ISSUANCE_ENABLED:-true}"
export SESSION_COOKIE_HTTPS_ONLY="${SESSION_COOKIE_HTTPS_ONLY:-true}"
if [ -z "${R2C_RECOMMENDED_IOS_APP_BUILD_NUMBER:-}" ] && [ "$#" -ge 1 ]; then
  export R2C_RECOMMENDED_IOS_APP_BUILD_NUMBER="$1"
fi
if [ -z "${VIDEO_ICE_SERVERS_JSON:-}" ]; then
  export VIDEO_ICE_SERVERS_JSON='[{"urls":["stun:stun.cloudflare.com:3478"]}]'
fi
export FAA_NOTAM_API_BASE_URL="${FAA_NOTAM_API_BASE_URL:-https://api-staging.cgifederal-aim.com/nmsapi}"
export FAA_NOTAM_TOKEN_URL="${FAA_NOTAM_TOKEN_URL:-https://api-staging.cgifederal-aim.com/v1/auth/token}"

if [ "${GCLOUD_PROJECT}" != "r2c-tracker-pilot" ]; then
  echo "Refusing prerel deployment to unexpected project ${GCLOUD_PROJECT}." >&2
  exit 1
fi
if [ "${SERVICE_NAME}" != "r2c-tracker-prerel" ]; then
  echo "Refusing prerel deployment to unexpected service ${SERVICE_NAME}." >&2
  exit 1
fi
if [ "${ALLOW_UNAUTHENTICATED}" != "1" ]; then
  echo "Prerel tablet clients require unauthenticated Cloud Run transport." >&2
  exit 1
fi
if [ "${CONTROL_PLANE_MODE}" != "live" ]; then
  echo "Refusing prerel deployment with CONTROL_PLANE_MODE=${CONTROL_PLANE_MODE}." >&2
  exit 1
fi
case "${CONTROL_PLANE_PUBLIC_URL}" in
  https://prerel.r2c-tracker.com|https://prerel.r2c-tracker.com/) ;;
  *)
    echo "Refusing prerel deploy with CONTROL_PLANE_PUBLIC_URL=${CONTROL_PLANE_PUBLIC_URL}." >&2
    exit 1
    ;;
esac

exec "${SCRIPT_DIR}/deploy.sh" "$@"
