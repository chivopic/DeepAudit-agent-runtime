// Local build helpers.
const { exec } = require("child_process");

function buildBranch(branch, cb) {
  exec("git checkout " + branch + " && npm run build", cb);
}

module.exports = { buildBranch };
