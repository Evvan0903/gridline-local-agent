import type { Bootstrap } from "./types";
let csrf = "";
export async function bootstrap(): Promise<Bootstrap> {
  const response = await fetch("/api/bootstrap", {
    credentials: "same-origin",
  });
  if (!response.ok)
    throw new Error(
      "The local backend is unavailable. Start it with ./start-web.sh.",
    );
  const body = (await response.json()) as Bootstrap;
  csrf = body.csrf_token;
  return body;
}
export async function api<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", csrf);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api" + path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 401 && retry) {
    await bootstrap();
    return api(path, init, false);
  }
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: "Local request failed" }));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}
export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export async function download(id: string, name: string) {
  const response = await fetch(
    `/api/artifacts/${encodeURIComponent(id)}/download`,
    { credentials: "same-origin" },
  );
  if (!response.ok) {
    const value = await response
      .json()
      .catch(() => ({ detail: "Download failed" }));
    throw new Error(value.detail);
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}
