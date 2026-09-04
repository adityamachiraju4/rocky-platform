import { Geolocation } from "@capacitor/geolocation";
import { isNativeApp } from "./native";
export { needsDeviceLocation } from "./locationIntent";
import type { DeviceLocationContext } from "./types";

export type LocationPermissionState =
  | "not_determined"
  | "granted"
  | "denied"
  | "restricted"
  | "unavailable";

export interface LocationContextResult {
  status: LocationPermissionState;
  context: DeviceLocationContext | null;
}

export async function requestDeviceLocationContext(): Promise<LocationContextResult> {
  if (isNativeApp()) return requestNativeLocationContext();
  return requestBrowserLocationContext();
}

async function requestNativeLocationContext(): Promise<LocationContextResult> {
  try {
    const current = await Geolocation.checkPermissions();
    if (current.location === "denied") return { status: "denied", context: null };

    const requested = current.location === "granted"
      ? current
      : await Geolocation.requestPermissions({ permissions: ["coarseLocation"] });
    if (requested.location !== "granted") {
      return {
        status: requested.location === "denied" ? "denied" : "restricted",
        context: null,
      };
    }

    const position = await Geolocation.getCurrentPosition({
      enableHighAccuracy: false,
      timeout: 10_000,
      maximumAge: 10 * 60_000,
    });

    return {
      status: "granted",
      context: {
        latitude: coarseCoordinate(position.coords.latitude),
        longitude: coarseCoordinate(position.coords.longitude),
        accuracy_meters: Math.round(position.coords.accuracy),
        source: "native",
      },
    };
  } catch {
    return { status: "unavailable", context: null };
  }
}

async function requestBrowserLocationContext(): Promise<LocationContextResult> {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return { status: "unavailable", context: null };
  }

  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({
        status: "granted",
        context: {
          latitude: coarseCoordinate(position.coords.latitude),
          longitude: coarseCoordinate(position.coords.longitude),
          accuracy_meters: Math.round(position.coords.accuracy),
          source: "web",
        },
      }),
      (error) => resolve({
        status: error.code === error.PERMISSION_DENIED ? "denied" : "unavailable",
        context: null,
      }),
      {
        enableHighAccuracy: false,
        timeout: 10_000,
        maximumAge: 10 * 60_000,
      },
    );
  });
}

function coarseCoordinate(value: number): number {
  return Number(value.toFixed(3));
}
