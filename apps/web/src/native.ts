import { Capacitor } from "@capacitor/core";
import { App as CapacitorApp } from "@capacitor/app";
import { Device } from "@capacitor/device";
import { Keyboard, KeyboardResize } from "@capacitor/keyboard";
import { Network } from "@capacitor/network";
import { SplashScreen } from "@capacitor/splash-screen";
import { StatusBar, Style } from "@capacitor/status-bar";

export function isNativeApp(): boolean {
  return Capacitor.isNativePlatform();
}

export async function nativeDeviceLoginMetadata(): Promise<{
  device_name?: string;
  device_type?: string;
  platform?: string;
}> {
  if (!isNativeApp()) return {};
  const [info, id] = await Promise.all([Device.getInfo(), Device.getId()]);
  return {
    device_name: info.name || id.identifier,
    device_type: info.model,
    platform: info.platform,
  };
}

export async function configureNativeShell(): Promise<void> {
  if (!isNativeApp()) return;

  await Promise.allSettled([
    StatusBar.setStyle({ style: Style.Dark }),
    StatusBar.setBackgroundColor({ color: "#0e0f11" }),
    SplashScreen.hide(),
    Keyboard.setResizeMode({ mode: KeyboardResize.Body }),
  ]);

  const emitNetwork = async () => {
    const status = await Network.getStatus();
    window.dispatchEvent(new CustomEvent("rocky:network-status", {
      detail: { online: status.connected, connectionType: status.connectionType },
    }));
  };

  await Promise.allSettled([
    AppCapacitorLifecycle(),
    Network.addListener("networkStatusChange", (status) => {
      window.dispatchEvent(new CustomEvent("rocky:network-status", {
        detail: { online: status.connected, connectionType: status.connectionType },
      }));
    }),
    emitNetwork(),
  ]);
}

async function AppCapacitorLifecycle(): Promise<void> {
  await CapacitorApp.addListener("appStateChange", ({ isActive }) => {
    window.dispatchEvent(new CustomEvent(
      isActive ? "rocky:native-foreground" : "rocky:native-background",
    ));
  });

  await CapacitorApp.addListener("resume", () => {
    window.dispatchEvent(new CustomEvent("rocky:native-foreground"));
  });

  await CapacitorApp.addListener("pause", () => {
    window.dispatchEvent(new CustomEvent("rocky:native-background"));
  });

  await CapacitorApp.addListener("backButton", ({ canGoBack }) => {
    const event = new CustomEvent("rocky:native-back", { cancelable: true });
    window.dispatchEvent(event);
    if (event.defaultPrevented) return;
    if (canGoBack) {
      window.history.back();
      return;
    }
    void CapacitorApp.exitApp();
  });
}
