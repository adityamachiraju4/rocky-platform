import { PushNotifications, type Token } from "@capacitor/push-notifications";
import { isNativeApp } from "./native";

export type PushPermissionState =
  | "granted"
  | "denied"
  | "prompt"
  | "prompt-with-rationale"
  | "unavailable";

export async function requestPushPermission(): Promise<PushPermissionState> {
  if (!isNativeApp()) return "unavailable";
  const current = await PushNotifications.checkPermissions();
  const permission = current.receive === "prompt"
    ? await PushNotifications.requestPermissions()
    : current;
  if (permission.receive !== "granted") return permission.receive;
  await PushNotifications.register();
  return "granted";
}

export async function onPushRegistration(
  callback: (token: string) => void,
): Promise<() => Promise<void>> {
  if (!isNativeApp()) return async () => {};
  const handle = await PushNotifications.addListener("registration", (token: Token) => {
    callback(token.value);
  });
  return () => handle.remove();
}
