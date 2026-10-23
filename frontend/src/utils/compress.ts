// On-device photo compression before upload (WhatsApp-style): longest edge <= 2048 px, JPEG 82 %.
// Keeps fixings, insulation edges and measurements legible; the original stays on the device until the server confirms.
import { Image } from "react-native";
import { manipulateAsync, SaveFormat } from "expo-image-manipulator";

const MAX_EDGE = 2048;
const QUALITY = 0.82;

function size(uri: string): Promise<{ w: number; h: number }> {
  return new Promise((res, rej) => Image.getSize(uri, (w, h) => res({ w, h }), rej));
}

/** Returns a compressed temporary JPEG uri, or null when compression isn't worthwhile/possible (upload the original). */
export async function compressPhoto(uri: string): Promise<string | null> {
  try {
    const { w, h } = await size(uri);
    if (Math.max(w, h) <= 1280) return null; // already small: re-encoding would only lose detail
    const resize = Math.max(w, h) > MAX_EDGE ? [w >= h ? { resize: { width: MAX_EDGE } } : { resize: { height: MAX_EDGE } }] : [];
    const r = await manipulateAsync(uri, resize, { compress: QUALITY, format: SaveFormat.JPEG });
    return r.uri;
  } catch {
    return null;
  }
}
