import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getParseModel, setParseModel } from "./parse-model";

function stubWindow() {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    },
  });
  return store;
}

describe("parse-model", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is SSR-safe", () => {
    expect(getParseModel()).toBe("haiku");
    expect(() => setParseModel("jev")).not.toThrow();
  });

  describe("in a browser", () => {
    let store: Map<string, string>;
    beforeEach(() => {
      store = stubWindow();
    });

    it("defaults to haiku when unset", () => {
      expect(getParseModel()).toBe("haiku");
    });

    it("persists jev under topfit.parseModel", () => {
      setParseModel("jev");
      expect(store.get("topfit.parseModel")).toBe("jev");
      expect(getParseModel()).toBe("jev");
    });

    it("falls back to haiku when the value is not allowed", () => {
      store.set("topfit.parseModel", "jev");
      expect(getParseModel(["haiku"])).toBe("haiku");
      store.set("topfit.parseModel", "garbage");
      expect(getParseModel()).toBe("haiku");
    });
  });
});
