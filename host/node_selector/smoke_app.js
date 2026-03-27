const fs = require("fs");
const http = require("http");
const path = require("path");

const LOG_PATH = path.join(__dirname, "smoke-runtime.log");

function log(message) {
  const line = `${new Date().toISOString()} ${message}\n`;
  try {
    fs.appendFileSync(LOG_PATH, line, "utf8");
  } catch (_error) {
    // Best-effort logging only.
  }
}

const server = http.createServer((req, res) => {
  log(`request method=${req.method} url=${req.url}`);
  const body = Buffer.from(JSON.stringify({
    ok: true,
    pid: process.pid,
    url: req.url,
    uptime_seconds: Math.round(process.uptime()),
  }));
  res.writeHead(200, {
    "Content-Type": "application/json",
    "Content-Length": String(body.length),
    "Cache-Control": "no-store",
  });
  res.end(body);
});

server.listen(process.env.PORT || 3000, () => {
  log(`listening pid=${process.pid} port=${process.env.PORT || 3000}`);
});
