import { z } from "zod";
import { parseResponse } from "./foundry/api";
import { env } from "./config.js";

const health = z.object({ status: z.string() });

export const fetchHealth = async () => parseResponse(health, await fetch(`${env.VITE_API_URL}/healthz`));
