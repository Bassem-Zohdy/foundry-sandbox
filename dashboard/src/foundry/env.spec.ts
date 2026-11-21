import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { EnvError, readEnv } from './env';

const schema = z.object({ VITE_API_URL: z.url(), VITE_RETRIES: z.coerce.number().int().min(0) });

describe('readEnv', () => {
  it('returns the parsed settings', () => {
    const env = readEnv(schema, { VITE_API_URL: 'https://api.example.com', VITE_RETRIES: '3', MODE: 'test' });
    expect(env).toEqual({ VITE_API_URL: 'https://api.example.com', VITE_RETRIES: 3 });
  });

  it('names the missing and malformed settings, never their values', () => {
    const bad = 'not a url with private-value';
    let caught: unknown;
    try {
      readEnv(schema, { VITE_API_URL: bad });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(EnvError);
    expect((caught as EnvError).names).toEqual(['VITE_API_URL', 'VITE_RETRIES']);
    expect((caught as EnvError).message).not.toContain(bad);
  });

  it('reads import.meta.env when no source is given', () => {
    const env = readEnv(z.object({ MODE: z.string() }));
    expect(env.MODE).toBe('test');
  });
});
