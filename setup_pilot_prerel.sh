#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${PYTHON:-${SCRIPT_DIR}/.venv/bin/python}"
CONFIG="${CLOUDSDK_ACTIVE_CONFIG_NAME:-r2c-tracker-pilot}"
PROJECT="${GCLOUD_PROJECT:-r2c-tracker-pilot}"
REGION="${REGION:-us-west1}"
PREREL_INSTANCE="r2c-prerel"
PREREL_SERVICE="r2c-tracker-prerel"
PREREL_SERVICE_ACCOUNT="r2c-tracker-prerel@${PROJECT}.iam.gserviceaccount.com"
PREREL_BUCKET="r2c-tracker-prerel-flightlogs"
TRACKER_ROLE="r2c_prerel_tracker_user"
CONTROL_ROLE="r2c_prerel_control_user"
TRACKER_DATABASE="r2c_prerel_tracker"
CONTROL_DATABASE="r2c_prerel_control_plane"
REUSE_EXISTING="0"

if [ "${1:-}" = "--reuse-existing" ]; then
  REUSE_EXISTING="1"
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "Usage: $0 [--reuse-existing]" >&2
  exit 2
fi

if [ "${PROJECT}" != "r2c-tracker-pilot" ]; then
  echo "Refusing prerel setup in unexpected project ${PROJECT}." >&2
  exit 1
fi

pilot_gcloud() {
  gcloud --configuration="${CONFIG}" --quiet "$@"
}

for command in gcloud "${PYTHON}"; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Required command is unavailable: ${command}" >&2
    exit 1
  fi
done

service_exists="0"
instance_exists="0"
if pilot_gcloud run services describe "${PREREL_SERVICE}" \
    --project "${PROJECT}" --region "${REGION}" >/dev/null 2>&1; then
  service_exists="1"
fi
if pilot_gcloud sql instances describe "${PREREL_INSTANCE}" \
    --project "${PROJECT}" >/dev/null 2>&1; then
  instance_exists="1"
fi

if [ "${REUSE_EXISTING}" = "1" ] && [ "${instance_exists}" = "1" ]; then
  instance_version="$(pilot_gcloud sql instances describe "${PREREL_INSTANCE}" \
    --project "${PROJECT}" --format='value(databaseVersion)')"
  instance_region="$(pilot_gcloud sql instances describe "${PREREL_INSTANCE}" \
    --project "${PROJECT}" --format='value(region)')"
  instance_tier="$(pilot_gcloud sql instances describe "${PREREL_INSTANCE}" \
    --project "${PROJECT}" --format='value(settings.tier)')"
  instance_state="$(pilot_gcloud sql instances describe "${PREREL_INSTANCE}" \
    --project "${PROJECT}" --format='value(state)')"
  if [ "${instance_version}" != "POSTGRES_15" ] \
      || [ "${instance_region}" != "${REGION}" ] \
      || [ "${instance_tier}" != "db-f1-micro" ] \
      || [ "${instance_state}" != "RUNNABLE" ]; then
    echo "Refusing to reuse prerel Cloud SQL with unexpected configuration: version=${instance_version} region=${instance_region} tier=${instance_tier} state=${instance_state}." >&2
    exit 1
  fi
  for database_name in "${TRACKER_DATABASE}" "${CONTROL_DATABASE}"; do
    if [ "$(pilot_gcloud sql databases list --instance "${PREREL_INSTANCE}" \
        --project "${PROJECT}" --filter="name=${database_name}" --format='value(name)')" != "${database_name}" ]; then
      echo "Refusing to reuse prerel: database ${database_name} is missing." >&2
      exit 1
    fi
  done
  for role_name in "${TRACKER_ROLE}" "${CONTROL_ROLE}"; do
    if [ "$(pilot_gcloud sql users list --instance "${PREREL_INSTANCE}" \
        --project "${PROJECT}" --filter="name=${role_name}" --format='value(name)')" != "${role_name}" ]; then
      echo "Refusing to reuse prerel: database role ${role_name} is missing." >&2
      exit 1
    fi
  done
  for secret_name in \
    r2c-prerel-tracker-database-url \
    r2c-prerel-control-plane-database-url \
    r2c-prerel-tracker-admin-password \
    r2c-prerel-deployment-gate-key \
    r2c-prerel-secret-key \
    r2c-prerel-control-plane-signing-key; do
    if ! pilot_gcloud secrets describe "${secret_name}" --project "${PROJECT}" >/dev/null 2>&1; then
      echo "Refusing to reuse prerel: secret ${secret_name} is missing." >&2
      exit 1
    fi
  done
  if ! pilot_gcloud iam service-accounts describe "${PREREL_SERVICE_ACCOUNT}" \
      --project "${PROJECT}" >/dev/null 2>&1; then
    echo "Refusing to reuse prerel: service account ${PREREL_SERVICE_ACCOUNT} is missing." >&2
    exit 1
  fi
  if ! pilot_gcloud storage buckets describe "gs://${PREREL_BUCKET}" \
      --project "${PROJECT}" >/dev/null 2>&1; then
    echo "Refusing to reuse prerel: bucket gs://${PREREL_BUCKET} is missing." >&2
    exit 1
  fi
  echo "Validated reusable prerel resources."
  if [ "${service_exists}" = "1" ]; then
    echo "Cloud Run service ${PREREL_SERVICE} already exists; redeploy with ./deploy_prerel.sh when ready."
  fi
  echo "Run ./scripts/refresh_prerel_databases.sh before the first deploy (or to refresh clones)."
  exit 0
fi

if [ "${REUSE_EXISTING}" = "1" ]; then
  echo "Refusing prerel reuse because ${PREREL_INSTANCE} does not exist; run without --reuse-existing first." >&2
  exit 1
fi

if [ "${instance_exists}" = "1" ]; then
  echo "Prerel Cloud SQL instance already exists; use --reuse-existing or delete it deliberately first." >&2
  exit 1
fi

tracker_password="$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(36))')"
control_password="$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(36))')"
root_password="$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(36))')"
temporary_dir="$(mktemp -d)"
instance_created="0"
setup_complete="0"
cleanup() {
  rm -rf "${temporary_dir}"
  if [ "${instance_created}" = "1" ] && [ "${setup_complete}" != "1" ]; then
    pilot_gcloud sql instances delete "${PREREL_INSTANCE}" \
      --project "${PROJECT}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT HUP INT TERM
export R2C_PREREL_ROOT_PASSWORD="${root_password}"
export R2C_PREREL_TRACKER_PASSWORD="${tracker_password}"
export R2C_PREREL_CONTROL_PASSWORD="${control_password}"
export R2C_PREREL_FLAGS_DIR="${temporary_dir}"
${PYTHON} - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["R2C_PREREL_FLAGS_DIR"])
for filename, key, value in (
    ("root.yaml", "--root-password", os.environ["R2C_PREREL_ROOT_PASSWORD"]),
    ("tracker.yaml", "--password", os.environ["R2C_PREREL_TRACKER_PASSWORD"]),
    ("control.yaml", "--password", os.environ["R2C_PREREL_CONTROL_PASSWORD"]),
):
    path = root / filename
    path.write_text(f"{key}: {value}\n")
    path.chmod(0o600)
PY

echo "Creating Cloud SQL instance ${PREREL_INSTANCE} (this can take several minutes)..."
pilot_gcloud sql instances create "${PREREL_INSTANCE}" \
  --project "${PROJECT}" \
  --database-version POSTGRES_15 \
  --tier db-f1-micro \
  --region "${REGION}" \
  --availability-type zonal \
  --storage-type SSD \
  --storage-size 10 \
  --no-storage-auto-increase \
  --no-deletion-protection \
  --flags-file "${temporary_dir}/root.yaml"
instance_created="1"

pilot_gcloud sql databases create "${TRACKER_DATABASE}" \
  --instance "${PREREL_INSTANCE}" --project "${PROJECT}"
pilot_gcloud sql databases create "${CONTROL_DATABASE}" \
  --instance "${PREREL_INSTANCE}" --project "${PROJECT}"
pilot_gcloud sql users create "${TRACKER_ROLE}" \
  --instance "${PREREL_INSTANCE}" --project "${PROJECT}" \
  --flags-file "${temporary_dir}/tracker.yaml"
pilot_gcloud sql users create "${CONTROL_ROLE}" \
  --instance "${PREREL_INSTANCE}" --project "${PROJECT}" \
  --flags-file "${temporary_dir}/control.yaml"

create_secret() {
  secret_name="$1"
  secret_value="$2"
  if pilot_gcloud secrets describe "${secret_name}" --project "${PROJECT}" >/dev/null 2>&1; then
    printf %s "${secret_value}" | pilot_gcloud secrets versions add "${secret_name}" \
      --project "${PROJECT}" --data-file=- >/dev/null
  else
    pilot_gcloud secrets create "${secret_name}" --project "${PROJECT}" \
      --replication-policy=automatic >/dev/null
    printf %s "${secret_value}" | pilot_gcloud secrets versions add "${secret_name}" \
      --project "${PROJECT}" --data-file=- >/dev/null
  fi
}

connection_name="${PROJECT}:${REGION}:${PREREL_INSTANCE}"
create_secret "r2c-prerel-tracker-database-url" \
  "postgresql+asyncpg://${TRACKER_ROLE}:${tracker_password}@/${TRACKER_DATABASE}?host=/cloudsql/${connection_name}"
create_secret "r2c-prerel-control-plane-database-url" \
  "postgresql+asyncpg://${CONTROL_ROLE}:${control_password}@/${CONTROL_DATABASE}?host=/cloudsql/${connection_name}"
create_secret "r2c-prerel-tracker-admin-password" "$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(36))')"
create_secret "r2c-prerel-deployment-gate-key" "$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(48))')"
create_secret "r2c-prerel-secret-key" "$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(48))')"
create_secret "r2c-prerel-control-plane-signing-key" "$(${PYTHON} -c 'import secrets; print(secrets.token_urlsafe(48))')"

if ! pilot_gcloud iam service-accounts describe "${PREREL_SERVICE_ACCOUNT}" \
    --project "${PROJECT}" >/dev/null 2>&1; then
  pilot_gcloud iam service-accounts create r2c-tracker-prerel \
    --project "${PROJECT}" --display-name "R2C Tracker prerel soak runtime"
fi
pilot_gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member "serviceAccount:${PREREL_SERVICE_ACCOUNT}" \
  --role roles/cloudsql.client >/dev/null

if ! pilot_gcloud storage buckets describe "gs://${PREREL_BUCKET}" \
    --project "${PROJECT}" >/dev/null 2>&1; then
  pilot_gcloud storage buckets create "gs://${PREREL_BUCKET}" \
    --project "${PROJECT}" --location "${REGION}" --uniform-bucket-level-access
fi
pilot_gcloud storage buckets add-iam-policy-binding "gs://${PREREL_BUCKET}" \
  --member "serviceAccount:${PREREL_SERVICE_ACCOUNT}" \
  --role roles/storage.objectAdmin >/dev/null

for secret_name in \
  r2c-prerel-tracker-database-url \
  r2c-prerel-control-plane-database-url \
  r2c-prerel-tracker-admin-password \
  r2c-prerel-deployment-gate-key \
  r2c-prerel-secret-key \
  r2c-prerel-control-plane-signing-key \
  r2c-faa-notam-client-id \
  r2c-faa-notam-client-secret \
  r2c-google-oauth-client-id \
  r2c-google-oauth-client-secret \
  r2c-managed-request-ingest-key \
  r2c-cloudflare-turn-key-id \
  r2c-cloudflare-turn-api-token \
  r2c-platform-email-gmail-refresh-token \
  r2c-app-store-connect-webhook-secret; do
  if pilot_gcloud secrets describe "${secret_name}" --project "${PROJECT}" >/dev/null 2>&1; then
    pilot_gcloud secrets add-iam-policy-binding "${secret_name}" \
      --project "${PROJECT}" \
      --member "serviceAccount:${PREREL_SERVICE_ACCOUNT}" \
      --role roles/secretmanager.secretAccessor >/dev/null || true
  fi
done

setup_complete="1"
unset tracker_password control_password root_password
unset R2C_PREREL_ROOT_PASSWORD R2C_PREREL_TRACKER_PASSWORD R2C_PREREL_CONTROL_PASSWORD
echo "Prerel Cloud SQL instance, secrets, service account, and bucket are ready."
echo "Next: ./scripts/refresh_prerel_databases.sh then ./deploy_prerel.sh APP_VERSION_CODE"
