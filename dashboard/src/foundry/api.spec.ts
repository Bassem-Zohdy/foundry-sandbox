import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { ApiShapeError, ApiStatusError, parseResponse } from './api';

const user = z.object({ id: z.number(), email: z.string(), tags: z.array(z.object({ name: z.string() })) });

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

async function failure(promise: Promise<unknown>): Promise<Error> {
  const error = await promise.then(() => null, (caught: unknown) => caught);
  expect(error).toBeInstanceOf(Error);
  return error as Error;
}

describe('parseResponse', () => {
  it('returns the parsed body when it matches the schema', async () => {
    const body = { id: 1, email: 'ada@example.com', tags: [{ name: 'a' }] };
    await expect(parseResponse(user, json(body))).resolves.toEqual(body);
  });

  it('names every path that differs, nested ones with their index', async () => {
    const error = await failure(parseResponse(user, json({ id: '7', email: 3, tags: [{ name: 'a' }, {}] })));
    expect(error).toBeInstanceOf(ApiShapeError);
    expect((error as ApiShapeError).paths).toEqual(['id', 'email', 'tags.1.name']);
    expect(error.message).toContain('unexpected response at id, email, tags.1.name');
  });

  it('never puts a value from the payload in the error', async () => {
    const secret = 'person@example.com-do-not-log';
    const error = await failure(parseResponse(user, json({ id: secret, email: secret, tags: secret })));
    expect(error.message).not.toContain(secret);
    expect(JSON.stringify(error)).not.toContain(secret);
  });

  it('reports a body that is not JSON as a shape error', async () => {
    const error = await failure(parseResponse(user, new Response('<html>oops</html>')));
    expect((error as ApiShapeError).paths).toEqual(['(body is not JSON)']);
  });
});

describe('parseResponse status', () => {
  it('throws ApiStatusError for a status outside 2xx without reading the body', async () => {
    const response = json({ detail: 'leaked-detail' }, 503);
    const error = await failure(parseResponse(user, response));
    expect(error).toBeInstanceOf(ApiStatusError);
    expect((error as ApiStatusError).status).toBe(503);
    expect(error.message).not.toContain('leaked-detail');
    expect(response.bodyUsed).toBe(false);
  });

  it('keeps the query string out of the message', async () => {
    const response = json({}, 404);
    Object.defineProperty(response, 'url', { value: 'https://api.example.com/users?token=abc123#x' });
    const error = await failure(parseResponse(user, response));
    expect(error.message).toBe('https://api.example.com/users answered 404');
  });
});
