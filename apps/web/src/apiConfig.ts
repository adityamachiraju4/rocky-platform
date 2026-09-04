const DEFAULT_API_BASE = "/api";
export const PRODUCTION_API_BASE = "https://rocky-platform-production.up.railway.app";

interface ApiBaseOptions {
  prod?: boolean;
}

type ViteImportMetaEnv = {
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
  const prod = options.prod ?? isProductionBuild();
  if (!configured) return prod ? PRODUCTION_API_BASE : DEFAULT_API_BASE;
  return trimTrailingSlash(configured);
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

function isProductionBuild(): boolean {
  return (import.meta as ImportMetaWithEnv).env?.PROD === true;
}

function isAbsoluteUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}
