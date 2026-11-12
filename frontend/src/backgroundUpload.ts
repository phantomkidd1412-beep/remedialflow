// OS-scheduled background runs of the evidence queue (iOS BGTaskScheduler / Android WorkManager via expo-background-task).
// The OS decides when this runs (minimum ~15 min, needs network, not guaranteed). The task must be defined at module
// scope, so app/_layout.tsx imports this file. Native only; the web preview never registers it.
import { Platform } from "react-native";

const TASK = "evidence-upload-resume";
let runner: (() => Promise<void>) | null = null;

/** QueueProvider registers its processor; the background task just calls it. */
export function setBackgroundRunner(fn: () => Promise<void>) {
  runner = fn;
}

if (Platform.OS !== "web") {
  try {
    /* eslint-disable @typescript-eslint/no-require-imports */
    const TaskManager = require("expo-task-manager");
    const BackgroundTask = require("expo-background-task");
    /* eslint-enable @typescript-eslint/no-require-imports */
    TaskManager.defineTask(TASK, async () => {
      try {
        await runner?.();
        return BackgroundTask.BackgroundTaskResult.Success;
      } catch {
        return BackgroundTask.BackgroundTaskResult.Failed;
      }
    });
  } catch {
    // module missing in this binary (e.g. an older build): uploads still resume when the app is opened
  }
}

export async function registerBackgroundUploads() {
  if (Platform.OS === "web") return;
  try {
    /* eslint-disable @typescript-eslint/no-require-imports */
    const TaskManager = require("expo-task-manager");
    const BackgroundTask = require("expo-background-task");
    /* eslint-enable @typescript-eslint/no-require-imports */
    if (!(await TaskManager.isTaskRegisteredAsync(TASK))) await BackgroundTask.registerTaskAsync(TASK, { minimumInterval: 15 });
  } catch {}
}
