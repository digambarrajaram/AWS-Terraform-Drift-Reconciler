import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

let _loaded = false;

/**
 * Read the repo-root .env file and populate process.env (idempotent).
 *
 * Walks up from this module's directory until it finds a .env file, matching
 * the convention in drift_reconciler/env_loader.py.  Existing process.env
 * values are never overwritten — a variable set in the environment always
 * takes precedence over the .env file.
 */
export function loadEnv(): void {
  if (_loaded) return;
  _loaded = true;

  // Start from the monorepo root (frontend/) and walk up to the repo root.
  const candidates = [
    // Built output: artifacts/api-server/dist/lib/ → up 6 levels to repo root
    resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..", "..", ".env"),
    // Source: artifacts/api-server/src/lib/ → up 6 levels to repo root
    resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..", "..", ".env"),
    // Direct fallback: monorepo root (frontend/)
    resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", ".env"),
  ];

  let envPath: string | null = null;
  for (const p of candidates) {
    try {
      readFileSync(p, "utf-8");
      envPath = p;
      break;
    } catch {
      // file doesn't exist at this path — try next candidate
    }
  }

  if (!envPath) {
    console.warn(
      "[envLoader] Could not find .env file — SUPABASE_URL, SUPABASE_ANON_KEY, " +
        "and PYTHON_BACKEND_URL must be set in the environment.",
    );
    return;
  }

  const content = readFileSync(envPath, "utf-8");
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx === -1) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const val = trimmed.slice(eqIdx + 1).trim();
    if (key && !(key in process.env)) {
      process.env[key] = val;
    }
  }
}
