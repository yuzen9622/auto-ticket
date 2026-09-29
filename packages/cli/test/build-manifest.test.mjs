import { afterEach, describe, expect, it, vi } from "vitest";

import { readReleaseAssets } from "../../../scripts/release/build_manifest.mjs";

afterEach(() => vi.unstubAllGlobals());

describe("readReleaseAssets", () => {
  it("reads draft checksums through the authenticated asset API", async () => {
    const version = "0.6.2";
    const repo = "owner/project";
    const assets = ["darwin-arm64", "win32-x64"].flatMap((target, index) => {
      const name = `auto-ticket-runtime-${target}-${version}.tar.gz`;
      return [
        { id: index * 2 + 1, name, size: 100 + index, downloadUrl: "https://example.invalid/draft" },
        { id: index * 2 + 2, name: `${name}.sha256`, size: 112, downloadUrl: "https://example.invalid/draft" },
      ];
    });
    const checksum = "a".repeat(64);
    const publicDownload = vi.fn(async () => ({ ok: false, status: 404 }));
    vi.stubGlobal("fetch", publicDownload);
    const ghRequest = vi.fn(async (args) => {
      if (args[1] === "graphql") {
        return JSON.stringify({ data: { repository: { release: { databaseId: 42, releaseAssets: { nodes: assets } } } } });
      }
      if (args[1] === `repos/${repo}/releases/42/assets?per_page=100`) return JSON.stringify(assets);
      if ([2, 4].some((id) => args[1] === `repos/${repo}/releases/assets/${id}`)) {
        expect(args).toContain("Accept: application/octet-stream");
        return `${checksum}  artifact.tar.gz\n`;
      }
      throw new Error(`Unexpected request: ${args.join(" ")}`);
    });

    await expect(readReleaseAssets({ tag: `v${version}`, repo, version }, ghRequest)).resolves.toEqual({
      "darwin-arm64": { sha256: checksum, size: 100 },
      "win32-x64": { sha256: checksum, size: 101 },
    });
    expect(publicDownload).not.toHaveBeenCalled();
  });

  it("rejects a missing release before downloading assets", async () => {
    const ghRequest = vi.fn(async () => JSON.stringify({ data: { repository: { release: null } } }));
    await expect(readReleaseAssets({ tag: "v0.6.2", repo: "owner/project", version: "0.6.2" }, ghRequest))
      .rejects.toThrow("找不到 Release v0.6.2");
    expect(ghRequest).toHaveBeenCalledTimes(1);
  });
});
