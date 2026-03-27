const http = require("http");

function normalizedPath(req) {
  const rawUrl = new URL(req.url, "http://localhost");
  const baseUri = process.env.TWOMAN_BASE_URI || "";
  if (baseUri && rawUrl.pathname.startsWith(baseUri)) {
    const suffix = rawUrl.pathname.slice(baseUri.length);
    return suffix || "/";
  }
  return rawUrl.pathname || "/";
}

const server = http.createServer((req, res) => {
  const path = normalizedPath(req);

  if (path === "/health") {
    const body = JSON.stringify({
      ok: true,
      pid: process.pid,
      version: process.version,
      time: Date.now() / 1000,
      env: {
        TWOMAN_BASE_URI: process.env.TWOMAN_BASE_URI || "",
      },
    });
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
    });
    res.end(body);
    return;
  }

  if (path === "/pid") {
    const body = JSON.stringify({ pid: process.pid });
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
    });
    res.end(body);
    return;
  }

  if (path === "/stream") {
    res.writeHead(200, { "Content-Type": "text/plain" });
    let count = 0;
    const timer = setInterval(() => {
      count += 1;
      res.write(`tick=${count} pid=${process.pid} ts=${Date.now() / 1000}\n`);
      if (count >= 60) {
        clearInterval(timer);
        res.end();
      }
    }, 200);
    req.on("close", () => clearInterval(timer));
    return;
  }

  if (path === "/upload_probe") {
    let body = Buffer.alloc(0);
    req.on("data", (chunk) => {
      body = Buffer.concat([body, chunk]);
    });
    req.on("end", () => {
      const response = JSON.stringify({
        ok: true,
        pid: process.pid,
        bytes: body.length,
        time: Date.now() / 1000,
      });
      res.writeHead(200, {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(response),
      });
      res.end(response);
    });
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not found", path }));
});

server.listen(process.env.PORT || 3000);
