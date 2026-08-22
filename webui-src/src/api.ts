// API 封装：调用插件后端注册的 Web API。
// AstrBot 插件页面在跳转时会在 URL 上带上 asset_token，这里把它带上鉴权。

function getAssetToken(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("asset_token") || "";
}

async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAssetToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(path, { ...options, headers });
  if (!resp.ok) {
    let text = "";
    try {
      text = await resp.text();
    } catch {
      /* ignore */
    }
    throw new Error(`${path} ${resp.status}: ${text.slice(0, 200)}`);
  }
  return (await resp.json()) as T;
}

export function apiGet<T = any>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

export function apiPost<T = any>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

// ---------- 类型 ----------
export interface ProviderItem {
  id: string;
  name: string;
  type: string;
  model: string;
  is_default?: boolean;
}

export interface TestResult {
  id: string;
  name: string;
  model: string;
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  skipped?: boolean;
}

export interface CompanionProviderItem {
  key: string;
  value: string;
}

export interface Overview {
  total: number;
  default_provider_id: string;
  default_set: boolean;
  companion_loaded: boolean;
  companion_provider_count: number;
}
