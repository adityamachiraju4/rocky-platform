const DEFAULT_API_BASE = "/api";
export const PRODUCTION_API_BASE = "https://rocky-platform-production.up.railway.app";
const CAPACITOR_LOCAL_ORIGINS = new Set(["http://localhost", "https://localhost"]);

interface ApiBaseOptions {
  native?: boolean;
  mode?: string;
  prod?: boolean;
}

type CapacitorGlobal = {
  isNativePlatform?: () => boolean;
};

type ViteImportMetaEnv = {
  MODE?: string;
  PROD?: boolean;
  VITE_API_BASE_URL?: string;
};

type ImportMetaWithEnv = ImportMeta & {
  env?: ViteImportMetaEnv;
};

function trimTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.replace(/\/+$/, "") : value;
}

export function apiBaseUrl(
  envValue = configuredApiBaseUrl(),
  options: ApiBaseOptions = {},
): string {
  const configured = envValue?.trim();
  const native = options.native ?? isRuntimeNative();
  const mode = options.mode ?? buildMode();
  const prod = options.prod ?? isProductionBuild();
  if (!configured) {
    if (native) {
      if (mode === "native-release") return PRODUCTION_API_BASE;
      throw new Error("Native API base requires VITE_API_BASE_URL with an absolute Rocky backend URL.");
    }
    if (prod) return PRODUCTION_API_BASE;
    return DEFAULT_API_BASE;
  }

  const trimmed = trimTrailingSlash(configured);
  if (!native) return trimmed;

  if (!isAbsoluteUrl(trimmed)) {
    throw new Error("Native API base must be an absolute Rocky backend URL.");
  }

  const origin = new URL(trimmed).origin;
  if (CAPACITOR_LOCAL_ORIGINS.has(origin)) {
    throw new Error("Native API base must not use Capacitor's local https://localhost WebView origin.");
  }

  return trimmed;
}

export function apiUrl(path: string, base = apiBaseUrl()): string {
  const endpoint = path.startsWith("/") ? path : `/${path}`;
  if (!isAbsoluteUrl(base)) return `${base}${endpoint}`;

  const baseUrl = new URL(base);
  const basePath = trimTrailingSlash(baseUrl.pathname);
  const endpointUrl = new URL(endpoint, "https://rocky.local");
  if (
    (endpointUrl.pathname === "/api" || endpointUrl.pathname.startsWith("/api/"))
    && basePath.endsWith("/api")
  ) {
    throw new Error("API endpoint path must not include /api when the API base already includes /api.");
  }

  baseUrl.pathname = `${basePath}${endpointUrl.pathname}`;
  baseUrl.search = endpointUrl.search;
  baseUrl.hash = endpointUrl.hash;
  return baseUrl.toString();
}

function configuredApiBaseUrl(): string | undefined {
  return (import.meta as ImportMetaWithEnv).env?.VITE_API_BASE_URL;
}

function buildMode(): string | undefined {
  return (import.meta as ImportMetaWithEnv).env?.MODE;
}

function isProductionBuild(): boolean {
  return (import.meta as ImportMetaWithEnv).env?.PROD === true;
}

function isRuntimeNative(): boolean {
  const capacitor = (globalThis as { Capacitor?: CapacitorGlobal }).Capacitor;
  try {
    return capacitor?.isNativePlatform?.() === true;
  } catch {
    return false;
  }
}

function isAbsoluteUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}
