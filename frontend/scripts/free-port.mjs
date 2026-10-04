import { execSync } from "node:child_process";

const port = process.env.PORT || "3000";
const isWindows = process.platform === "win32";

function run(cmd) {
  return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function pidsOnWindows() {
  const out = run("netstat -ano -p tcp");
  const pids = new Set();
  for (const line of out.split(/\r?\n/)) {
    if (!new RegExp(`:${port}\\s`).test(line)) continue;
    if (!/LISTENING/i.test(line)) continue;
    const cols = line.trim().split(/\s+/);
    const pid = cols[cols.length - 1];
    if (pid && pid !== "0") pids.add(pid);
  }
  return [...pids];
}

function pidsOnUnix() {
  const out = run(`lsof -ti tcp:${port} -sTCP:LISTEN || true`);
  return out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

let pids;
try {
  pids = isWindows ? pidsOnWindows() : pidsOnUnix();
} catch {
  console.log(`could not inspect port ${port}; assuming free`);
  process.exit(0);
}

if (pids.length === 0) {
  console.log(`port ${port} is free`);
  process.exit(0);
}

for (const pid of pids) {
  const label = pids.length > 1 ? "processes" : "process";
  console.log(`freeing port ${port}: killing ${label} ${pid}`);
  try {
    if (isWindows) run(`taskkill /PID ${pid} /T /F`);
    else run(`kill -9 ${pid}`);
  } catch {
    console.log(`  pid ${pid} already gone or not killable`);
  }
}

console.log(`port ${port} freed`);