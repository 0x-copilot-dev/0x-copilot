import { crashReporter } from "electron";

// Phase 1 stub. Phase 8 fills in submitURL and flips uploadToServer to
// true when the crash endpoint is provisioned (PRD §10 distribution,
// architecture spec §9).
export function startCrashReporter(): void {
  crashReporter.start({
    companyName: "Atlas",
    productName: "Atlas Desktop",
    submitURL: "",
    uploadToServer: false,
    ignoreSystemCrashHandler: false,
    compress: true,
  });
}
