import { z } from "zod";
import { readEnv } from "./foundry/env";

export const env = readEnv(z.object({ VITE_API_URL: z.string().default("") }));
