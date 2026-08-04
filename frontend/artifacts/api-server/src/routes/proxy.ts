import { Router, type Request, type Response } from "express";
import http from "node:http";
import { logger } from "../lib/logger";

const PYTHON_BACKEND =
  (process.env.PYTHON_BACKEND_URL || "http://localhost:8080").replace(
    /\/+$/,
    "",
  );

const PROXY_TIMEOUT_MS = Number(process.env.PROXY_TIMEOUT_MS || 300_000); // 5 min — scans are long-running

const router = Router();

/**
 * Catch-all proxy: forwards any /api/* request that isn't handled by the
 * health or config routes to the Python serve.py backend.
 *
 * The frontend's apiFetch() sends everything to /api/* on the same origin.
 * This Express server handles /api/config directly (Supabase credentials)
 * and /api/healthz directly (health check).  Everything else — scan triggers,
 * rollback previews, exception CRUD, notification settings, etc. — is the
 * Python backend's domain and gets proxied as-is.
 */
router.all("/{*path}", (req: Request, res: Response) => {
  const targetPath = `/api${req.path.startsWith("/") ? req.path : `/${req.path}`}`;

  // Preserve query string if present
  const query = new URL(req.url || "/", "http://localhost").search;
  const fullPath = targetPath + query;

  logger.info({ method: req.method, path: fullPath }, "Proxying to Python backend");

  const options: http.RequestOptions = {
    hostname: new URL(PYTHON_BACKEND).hostname,
    port:     new URL(PYTHON_BACKEND).port,
    path:     fullPath,
    method:   req.method,
    headers:  { ...req.headers },
    timeout:  PROXY_TIMEOUT_MS,
  };

  // Remove hop-by-hop headers that shouldn't be forwarded
  delete options.headers?.host;
  delete options.headers?.connection;

  // Express's json() middleware already consumed the request stream and
  // stored the parsed body in req.body.  We can't pipe the original stream
  // (it's drained), so write the JSON-serialised body instead.
  const hasBody = req.method !== "GET" && req.method !== "HEAD" && req.method !== "OPTIONS";

  if (hasBody && req.body !== undefined && req.body !== null) {
    const bodyStr = JSON.stringify(req.body);
    options.headers = options.headers || {};
    options.headers["content-length"] = String(Buffer.byteLength(bodyStr));
    // Content-Type is already set by apiFetch's 'Content-Type: application/json'
  }

  const proxyReq = http.request(options, (proxyRes) => {
    // Forward status and headers
    res.status(proxyRes.statusCode ?? 502);

    // Copy response headers (skip hop-by-hop)
    if (proxyRes.headers) {
      Object.entries(proxyRes.headers).forEach(([key, value]) => {
        if (key && value && !["transfer-encoding", "connection", "keep-alive"].includes(key.toLowerCase())) {
          res.setHeader(key, value);
        }
      });
    }

    // Pipe the response body
    proxyRes.pipe(res);
  });

  proxyReq.on("error", (err: NodeJS.ErrnoException) => {
    logger.error({ err, path: fullPath }, "Proxy request failed");
    if (err.code === "ECONNREFUSED") {
      res.status(503).json({
        error: "Python backend is not running — start serve.py on port " +
               (new URL(PYTHON_BACKEND).port || "8080"),
      });
    } else if (err.code === "ECONNRESET") {
      res.status(502).json({ error: "Python backend connection reset" });
    } else {
      res.status(502).json({ error: `Proxy error: ${err.message}` });
    }
  });

  proxyReq.on("timeout", () => {
    proxyReq.destroy();
    res.status(504).json({ error: "Python backend request timed out" });
  });

  // Write the serialised body (if any) and end the request.
  if (hasBody && req.body !== undefined && req.body !== null) {
    proxyReq.write(JSON.stringify(req.body));
  }
  proxyReq.end();
});

export default router;
