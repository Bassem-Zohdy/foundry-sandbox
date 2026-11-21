import type { ZodType } from 'zod';

const MAX_PATHS = 10;

/** The URL without its query and fragment, which may carry tokens or personal data. */
function where(response: Response): string {
  return response.url.split(/[?#]/)[0] || '(no url)';
}

/** The API answered with a status outside 200-299. The body is not read into the message. */
export class ApiStatusError extends Error {
  readonly status: number;

  constructor(response: Response) {
    super(`${where(response)} answered ${response.status}`);
    this.name = 'ApiStatusError';
    this.status = response.status;
  }
}

/**
 * The response body is not the shape the app expects. The message and `paths` name where it
 * differs (`items.2.price`), never the values: a payload may hold personal data, and an error
 * message ends up in logs and error trackers.
 */
export class ApiShapeError extends Error {
  readonly status: number;
  readonly paths: readonly string[];

  constructor(response: Response, paths: readonly string[]) {
    const shown = paths.slice(0, MAX_PATHS).join(', ');
    const more = paths.length > MAX_PATHS ? ` and ${paths.length - MAX_PATHS} more` : '';
    super(`${where(response)} (${response.status}): unexpected response at ${shown}${more}`);
    this.name = 'ApiShapeError';
    this.status = response.status;
    this.paths = paths;
  }
}

function pathOf(path: readonly PropertyKey[]): string {
  return path.length === 0 ? '(root)' : path.map(String).join('.');
}

/**
 * Checks an API response at the app's trust boundary: a 2xx status, a JSON body and the shape
 * `schema` describes. Returns the parsed body; throws ApiStatusError or ApiShapeError.
 *
 *   const user = await parseResponse(userSchema, await fetch('/api/me'));
 */
export async function parseResponse<T>(schema: ZodType<T>, response: Response): Promise<T> {
  if (!response.ok) throw new ApiStatusError(response);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiShapeError(response, ['(body is not JSON)']);
  }
  const result = schema.safeParse(body);
  if (result.success) return result.data;
  const paths = [...new Set(result.error.issues.map((issue) => pathOf(issue.path)))];
  throw new ApiShapeError(response, paths);
}
