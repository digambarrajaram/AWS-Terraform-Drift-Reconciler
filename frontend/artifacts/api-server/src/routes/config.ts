import { Router } from "express";

const router = Router();

/**
 * GET /api/config
 *
 * Returns Supabase connection details to the frontend.
 * Set SUPABASE_URL and SUPABASE_ANON_KEY in the environment (Replit Secrets
 * or .env). Without them the response is 503 and all Supabase queries in the
 * frontend stay disabled.
 *
 * In production this endpoint can alternatively be served by serve.py — the
 * frontend only needs the JSON shape: { supabaseUrl, supabaseAnonKey }.
 */
router.get("/config", (_req, res) => {
  const supabaseUrl     = process.env.SUPABASE_URL;
  const supabaseAnonKey = process.env.SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseAnonKey) {
    res.status(503).json({
      error: "Backend not configured — set SUPABASE_URL and SUPABASE_ANON_KEY",
    });
    return;
  }

  // GITHUB_REPO is optional — PR links simply won't render if it's unset.
  const githubRepo = (process.env.GITHUB_REPO || "").trim() || undefined;

  res.json({
    supabaseUrl,
    supabaseAnonKey,
    ...(githubRepo ? { githubRepo } : {}),
  });
});

export default router;
