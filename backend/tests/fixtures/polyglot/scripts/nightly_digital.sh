#!/bin/bash
# Nightly digital channel batch: build statements, notify customers, reconcile payments.
set -euo pipefail

RUN_DATE=$(date +%Y-%m-%d)

python python/statements/load_statements.py --date "$RUN_DATE" > /var/log/bank/statements.log
node node/src/jobs/notify.js
java -cp app.jar com.bank.digital.ReconcileJob < /data/in/ledger_extract.csv
./scripts/cleanup.sh
