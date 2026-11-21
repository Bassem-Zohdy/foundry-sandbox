/// <reference types="vite/client" />
import type { ZodType } from 'zod';

/** A setting is missing or malformed. The message names the settings, never their values. */
export class EnvError extends Error {
  readonly names: readonly string[];

  constructor(names: readonly string[]) {
    super(`invalid or missing settings: ${names.join(', ')} (see .env.example)`);
    this.name = 'EnvError';
    this.names = names;
  }
}

/**
 * Validates the app's settings once, at startup, so a wrong build fails on its first page
 * instead of at the first request that needs a setting. `import.meta.env` holds the VITE_
 * variables of the build (plus MODE, DEV, PROD, BASE_URL and SSR): every one of them is public.
 *
 *   export const env = readEnv(z.object({ VITE_API_URL: z.url() }));
 */
export function readEnv<T>(schema: ZodType<T>, source: Record<string, unknown> = import.meta.env): T {
  const result = schema.safeParse(source);
  if (result.success) return result.data;
  const names = result.error.issues.map((issue) => issue.path.map(String).join('.') || '(root)');
  throw new EnvError([...new Set(names)]);
}
