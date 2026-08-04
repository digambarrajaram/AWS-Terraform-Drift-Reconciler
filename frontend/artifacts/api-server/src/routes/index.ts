import { Router, type IRouter } from "express";
import healthRouter from "./health";
import configRouter from "./config";
import proxyRouter from "./proxy";

const router: IRouter = Router();

// Direct routes — these are handled by the Express server itself.
router.use(healthRouter);
router.use(configRouter);

// Catch-all proxy to the Python serve.py backend.
// MUST be registered last so it only catches requests not handled above.
router.use(proxyRouter);

export default router;
