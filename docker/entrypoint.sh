# Entrypoint for the cron-driven daily report container.
#
#   docker run ... <image>                     -> start the cron scheduler
#   docker run ... <image> python main.py ...  -> run a one-off command
set -eu

if [ "$#" -gt 0 ] && [ "$1" != "scheduler" ]; then
    exec "$@"
fi

CRON_SCHEDULE="${CRON_SCHEDULE:-0 18 * * 1-5}"
CRON_TZ="${CRON_TZ:-Asia/Shanghai}"

# Cron does not inherit the container environment, so persist it for the job.
python - <<'PY' > /app/.cron-env.sh
import os
import shlex

for key, value in os.environ.items():
    print(f"export {key}={shlex.quote(value)}")
PY

cat > /etc/cron.d/daily-report <<EOF
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin
CRON_TZ=${CRON_TZ}
${CRON_SCHEDULE} root . /app/.cron-env.sh && cd /app && .venv/bin/python main.py >> /proc/1/fd/1 2>&1
EOF

chmod 0644 /etc/cron.d/daily-report
crontab /etc/cron.d/daily-report

echo "Daily report scheduler started (schedule='${CRON_SCHEDULE}', timezone='${CRON_TZ}')"
exec cron -f
