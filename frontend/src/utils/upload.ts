// Multipart upload that works in BOTH runtimes. Use this for every file upload; never
// build the FormData part yourself.
//
//   import { uploadForm } from "@/src/utils/upload";
//   const res = await uploadForm(`${BACKEND_URL}/api/photos`, {
//     fields: { caption },
//     files: { image: { uri: asset.uri, name: "photo.jpg", mimeType: asset.mimeType } },
//   });
//
// Why: on a phone the global fetch is `expo/fetch` (SDK 54+), which rejects the old RN
// `{ uri, name, type }` part with "Unsupported FormDataPart implementation". The web
// preview accepts that shape, so the bug only shows on a device. Never set Content-Type:
// the runtime adds the multipart boundary. No extra dependency: `expo/fetch` reads
// `file://` on iOS and Android itself (React Native's own fetch cannot on Android).

import { Platform } from "react-native";
import { fetch as expoFetch } from "expo/fetch";

export type LocalFile = {
  uri: string; // asset.uri from expo-image-picker / expo-document-picker / expo-audio
  name?: string; // "photo.jpg"; last path segment of uri when omitted
  mimeType?: string; // asset.mimeType, e.g. "image/jpeg"
};

export type UploadOptions = {
  files: Record<string, LocalFile>; // form field name -> file
  fields?: Record<string, string>;
  method?: "POST" | "PUT";
  headers?: Record<string, string>; // auth only; never Content-Type
  signal?: AbortSignal;
  onProgress?: (fraction: number) => void; // 0..1 while the body is being sent (single-file uploads)
};

export function fileNameFor(file: LocalFile): string {
  return file.name || file.uri.split("?")[0].split("/").pop() || "file";
}

// Turn a local file into a part that the current runtime's fetch can serialise.
export async function toFormDataPart(file: LocalFile): Promise<Blob> {
  if (Platform.OS === "web") {
    // blob:/data: URL from the browser picker; the browser fetch reads it fine.
    const blob = await (await fetch(file.uri)).blob();
    const type = file.mimeType || blob.type;
    return type && type !== blob.type ? new Blob([blob], { type }) : blob;
  }
  // Native: read the local file through expo/fetch (never the RN fetch) and append the Blob.
  const blob = await (await expoFetch(file.uri)).blob();
  return file.mimeType && file.mimeType !== blob.type ? new Blob([blob], { type: file.mimeType }) : blob;
}

function asResponse(status: number, body: string): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => JSON.parse(body || "null"), text: async () => body } as unknown as Response;
}

// Same multipart request, but reports send progress. Native streams the file from disk (no full in-memory copy,
// which matters for large videos); web uses XHR, the only browser API with upload progress.
async function uploadWithProgress(url: string, opts: UploadOptions): Promise<Response> {
  const [field, file] = Object.entries(opts.files)[0];
  if (Platform.OS === "web") {
    const form = new FormData();
    for (const [k, v] of Object.entries(opts.fields ?? {})) form.append(k, v);
    form.append(field, await toFormDataPart(file), fileNameFor(file));
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(opts.method ?? "POST", url);
      for (const [k, v] of Object.entries(opts.headers ?? {})) xhr.setRequestHeader(k, v);
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => e.lengthComputable && opts.onProgress?.(e.loaded / e.total);
      xhr.onload = () => resolve(asResponse(xhr.status, xhr.responseText));
      xhr.onerror = () => reject(new TypeError("Network request failed"));
      xhr.onabort = () => reject(new TypeError("Upload aborted"));
      opts.signal?.addEventListener("abort", () => xhr.abort());
      xhr.send(form);
    });
  }
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const FS = require("expo-file-system/legacy");
  const task = FS.createUploadTask(
    url,
    file.uri,
    { httpMethod: opts.method ?? "POST", uploadType: FS.FileSystemUploadType.MULTIPART, fieldName: field, mimeType: file.mimeType, parameters: opts.fields, headers: opts.headers },
    (p: { totalBytesSent: number; totalBytesExpectedToSend: number }) => p.totalBytesExpectedToSend > 0 && opts.onProgress?.(p.totalBytesSent / p.totalBytesExpectedToSend),
  );
  opts.signal?.addEventListener("abort", () => task.cancelAsync());
  const r = await task.uploadAsync();
  if (!r) throw new TypeError("Upload cancelled");
  return asResponse(r.status, r.body);
}

export async function uploadForm(url: string, opts: UploadOptions): Promise<Response> {
  if (opts.onProgress && Object.keys(opts.files).length === 1) return uploadWithProgress(url, opts);
  const form = new FormData();
  for (const [k, v] of Object.entries(opts.fields ?? {})) form.append(k, v);
  for (const [field, file] of Object.entries(opts.files)) {
    form.append(field, await toFormDataPart(file), fileNameFor(file));
  }
  const init = {
    method: opts.method ?? "POST",
    body: form,
    headers: opts.headers, // the runtime adds the multipart boundary
    signal: opts.signal,
  };
  // Branch the call, not the function reference: the two fetches have different RequestInit types.
  return Platform.OS === "web" ? fetch(url, init) : expoFetch(url, init);
}
