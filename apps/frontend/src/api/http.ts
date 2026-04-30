export async function assertOk(response: Response): Promise<void> {
  if (response.ok) {
    return;
  }
  const detail = await response.text();
  throw new Error(detail || `Request failed with ${response.status}`);
}

export function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json" };
}
