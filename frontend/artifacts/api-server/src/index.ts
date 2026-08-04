import { loadEnv } from "./lib/envLoader";
loadEnv(); // Must run before any other import reads process.env

import app from "./app";
import { logger } from "./lib/logger";

// On Replit the PORT is injected by the platform.  For local dev, default to
// 3000 so the Vite proxy (which defaults to http://localhost:3000) works
// without any extra configuration.
const rawPort = process.env["PORT"] || "3000";
const port    = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
});
