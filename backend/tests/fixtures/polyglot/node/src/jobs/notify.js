// Nightly notification run, started by scripts/nightly_digital.sh
const { Notifier } = require("../notifier");

function main() {
  const notifier = new Notifier();
  return notifier.send("ALL", ["Your statement is ready"]);
}

main();
