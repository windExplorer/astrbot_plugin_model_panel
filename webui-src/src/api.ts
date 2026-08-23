// API 封装：通过 AstrBot 官方插件 Page 桥接（AstrBotPluginPage）调用插件后端 Web API。
// 桥接会正确处理 /api/plug/ 路由 + asset_token 鉴权，不能用裸 fetch 相对路径（会 CORS 失败）。

const PAGE_PLUGIN_NAME = "astrbot_plugin_model_panel";

interface Bridge {
  apiGet(endpoint: string, params?: Record<string, any>): Promise<any>;
  apiPost(endpoint: string, body?: Record<string, any>): Promise<any>;
}

function getBridge(): Bridge | null {
  const w = window as any;
  if (w.AstrBotPluginPage) return w.AstrBotPluginPage;
  try {
    if (w.parent && w.parent !== w && w.parent.AstrBotPluginPage) {
      return w.parent.AstrBotPluginPage;
    }
  } catch {
    return null;
  }
  return null;
}

function isUsable(b: Bridge | null | undefined): b is Bridge {
  return Boolean(b && typeof b.apiGet === "function" && typeof b.apiPost === "function");
}

async function getPageBridge(timeoutMs = 2500): Promise<Bridge> {
  const start = Date.now();
  while (true) {
    const b = getBridge();
    if (isUsable(b)) return b;
    if (Date.now() - start > timeoutMs) {
      throw new Error("未检测到 AstrBot 插件 Page 桥接，请从 AstrBot 后台的插件拓展页打开");
    }
    await new Promise((r) => setTimeout(r, 100));
  }
}

function endpointForStyle(style: string, routePath: string): string {
  const clean = routePath.replace(/^\/+/, "");
  switch (style) {
    case "bare": return clean;
    case "slash": return "/" + clean;
    case "full": return PAGE_PLUGIN_NAME + "/" + clean;
    case "fullSlash": return "/" + PAGE_PLUGIN_NAME + "/" + clean;
    default: return "";
  }
}

function bridgeEndpointCandidates(routePath: string): string[] {
  const clean = routePath.replace(/^\/+/, "");
  const candidates = [
    endpointForStyle("bare", clean),
    endpointForStyle("slash", clean),
    endpointForStyle("full", clean),
    endpointForStyle("fullSlash", clean),
  ].map((s) => String(s || "").replace(/\/+/g, "/"));
  return [...new Set(candidates.filter(Boolean))];
}

function isRouteMissingPayload(payload: any): boolean {
  if (!payload || typeof payload !== "object") return false;
  const text = String((payload.error || "") + " " + (payload.message || "") + " " + (payload.detail || "")).toLowerCase();
  return /未找到.*路由|route.*not.*found|not.*found.*route|404/.test(text);
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + " 超时（" + ms / 1000 + "s 无响应）")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

async function bridgeRequest(br: Bridge, path: string, method: string, body?: unknown): Promise<any> {
  const url = new URL(path, "https://astrbot-plugin-page.local/");
  const routePath = url.pathname.replace(/^\/+/, "");
  const candidates = bridgeEndpointCandidates(routePath);
  const errors: string[] = [];

  if (method === "GET") {
    const params = Object.fromEntries(url.searchParams.entries());
    for (const c of candidates) {
      try {
        const p = await withTimeout(br.apiGet(c, Object.keys(params).length ? params : undefined), 6000, "GET " + c);
        if (isRouteMissingPayload(p)) {
          errors.push(p.message || p.error || "未找到该路由");
          continue;
        }
        return p;
      } catch (e: any) {
        errors.push(e && e.message ? e.message : String(e));
      }
    }
    throw new Error(errors[0] || "未找到可用的页面 API 路由");
  }

  let payload = body || {};
  try {
    payload = JSON.parse(JSON.stringify(payload));
  } catch {
    /* ignore */
  }
  for (const c of candidates) {
    try {
      const r = await withTimeout(br.apiPost(c, payload), 6000, "POST " + c);
      if (isRouteMissingPayload(r)) {
        errors.push(r.message || r.error || "未找到该路由");
        continue;
      }
      return r;
    } catch (e: any) {
      errors.push(e && e.message ? e.message : String(e));
    }
  }
  throw new Error(errors[0] || "未找到可用的页面 API 路由");
}

function normalize(payload: any): any {
  if (payload && typeof payload === "object") {
    if (Object.prototype.hasOwnProperty.call(payload, "success")) {
      if (payload.success === false) {
        throw new Error(payload.error || "请求失败");
      }
      return payload.data !== undefined ? payload.data : payload;
    }
  }
  return payload;
}

async function apiRaw(path: string, method: string, body?: unknown): Promise<any> {
  const br = await getPageBridge();
  const payload = await bridgeRequest(br, path, method, body);
  return normalize(payload);
}

/** GET 请求，endpoint 形如 "overview"、"providers"（不带插件名、不带 /api/plug）。 */
export async function apiGet<T = any>(endpoint: string, params?: Record<string, any>): Promise<T> {
  let path = endpoint;
  if (params && Object.keys(params).length) {
    const qs = new URLSearchParams();
    Object.keys(params).forEach((k) => {
      const v = params[k];
      if (v === undefined || v === null || v === "") return;
      qs.set(k, String(v));
    });
    const q = qs.toString();
    if (q) path = endpoint + "?" + q;
  }
  return apiRaw(path, "GET") as Promise<T>;
}

/** POST 请求，body 作为 JSON 负载发送。 */
export async function apiPost<T = any>(endpoint: string, body?: unknown): Promise<T> {
  return apiRaw(endpoint, "POST", body || {}) as Promise<T>;
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
  /** 归一化错误码：timeout/connect/refused/auth/rate_limit/not_found/server/unknown/skipped */
  error_code?: string;
  /** 短错误文本（≤80 字符），前端不要直接把堆栈展示给用户 */
  error: string | null;
  /** 实际重试次数，0 表示一次就过 */
  retry_count?: number;
  /** 检测时间戳（秒） */
  checked_at?: number;
  skipped?: boolean;
}

export interface CompanionProviderItem {
  key: string;
  value: string;
  configured?: boolean;
}

export interface OverviewHistory {
  sessions_total: number;
  results_total: number;
  results_ok: number;
  results_fail: number;
  latest_session?: {
    ok_count: number;
    fail_count: number;
    skip_count: number;
    total: number;
    alive_rate: number;
  } | null;
}

export interface Overview {
  total: number;
  default_provider_id: string;
  default_set: boolean;
  companion_loaded: boolean;
  companion_provider_count: number;
  history?: OverviewHistory;
  latest_results?: Record<string, TestResult>;
}

export interface CompanionProvidersResponse {
  loaded: boolean;
  items: CompanionProviderItem[];
  config_mode?: string;
  configured_count?: number;
  total_keys?: number;
}

export interface TestAllStreamItemEvent {
  type: "item";
  index: number;
  total: number;
  item: TestResult;
}

export interface TestAllStreamStartEvent {
  type: "start";
  session_id: number | null;
  total: number;
  ts: number;
}

export interface TestAllStreamDoneEvent {
  type: "done";
  session_id: number | null;
  ok_count: number;
  fail_count: number;
  skip_count: number;
  total: number;
}

export interface TestAllStreamErrorEvent {
  type: "error";
  message: string;
}

export type TestAllStreamEvent =
  | TestAllStreamStartEvent
  | TestAllStreamItemEvent
  | TestAllStreamDoneEvent
  | TestAllStreamErrorEvent;

/**
 * 异步一键检测：先 POST 启动任务拿到 session_id，再轮询 /session/{id} 拿进度，
 * 每次进度有更新就把新增 item 推给 handlers.onItem()。
 * 这种"启动 + 轮询"模式适配 AstrBot Page 桥接层只能同步 JSON 的限制，
 * 同时能实现"边跑边显示"。
 */
export interface TestAllStreamHandlers {
  onStart?: (e: TestAllStreamStartEvent) => void;
  onItem?: (e: TestAllStreamItemEvent) => void;
  onDone?: (e: TestAllStreamDoneEvent) => void;
  onError?: (msg: string) => void;
}

export async function startTestAllStream(
  body: { skip?: string[]; timeout?: number },
  handlers: TestAllStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const { onStart, onItem, onDone, onError } = handlers;
  try {
    const r = await apiPost<{ ok: boolean; session_id: number | null; total: number; error?: string }>(
      "/panel/providers/test_all_stream",
      body,
    );
    if (!r.ok || r.session_id == null) {
      onError?.(r.error || "启动检测任务失败");
      return;
    }
    onStart?.({ type: "start", session_id: r.session_id, total: r.total, ts: Math.floor(Date.now() / 1000) });
    const seen = new Set<string>();
    const startTime = Date.now();
    const maxWaitMs = 1000 * 60 * 10; // 最多等 10 分钟
    while (!signal?.aborted) {
      if (Date.now() - startTime > maxWaitMs) {
        onError?.("检测超时未完成");
        return;
      }
      const snap = await apiGet<{
        session_id: number;
        total: number;
        done: boolean;
        items: TestResult[];
        ok_count: number;
        fail_count: number;
        skip_count: number;
        error?: string | null;
      }>(`/panel/providers/session/${r.session_id}`);
      if (snap && Array.isArray(snap.items)) {
        for (let i = 0; i < snap.items.length; i++) {
          const it = snap.items[i];
          if (seen.has(it.id + "@" + i)) continue;
          seen.add(it.id + "@" + i);
          onItem?.({ type: "item", index: i + 1, total: snap.total, item: it });
        }
      }
      if (snap?.done) {
        onDone?.({
          type: "done",
          session_id: snap.session_id,
          ok_count: snap.ok_count || 0,
          fail_count: snap.fail_count || 0,
          skip_count: snap.skip_count || 0,
          total: snap.total || 0,
        });
        if (snap.error) onError?.(snap.error);
        return;
      }
      await new Promise((res) => setTimeout(res, 600));
    }
  } catch (e: any) {
    onError?.(e?.message || String(e));
  }
}
