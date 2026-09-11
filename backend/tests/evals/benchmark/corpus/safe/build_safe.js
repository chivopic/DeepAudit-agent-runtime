// Local build helpers.
const { execFile } = require("child_process");

const BRANCH_RE = /^[A-Za-z0-9._\/-]+$/;

function buildBranch(branch, cb) {
  if (!BRANCH_RE.test(branch)) return cb(new Error("invalid branch name"));
  execFile("git", ["checkout", branch], (err) => {
    if (err) return cb(err);
    execFile("npm", ["run", "build"], cb);
  });
}

module.exports = { buildBranch };
