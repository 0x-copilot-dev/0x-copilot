import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useMutableRecord, useRecord } from "./useResource";

describe("useRecord", () => {
  it("loads on mount", async () => {
    const fetcher = vi.fn().mockResolvedValue({ id: "x" });
    const { result } = renderHook(() => useRecord(fetcher, "fallback"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ id: "x" });
    expect(result.current.error).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("captures fetch errors with the fallback string", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useRecord(fetcher, "could not load"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("boom");
    expect(result.current.data).toBeNull();
  });

  it("falls back when the thrown value is not an Error", async () => {
    const fetcher = vi.fn().mockRejectedValue("not an error");
    const { result } = renderHook(() => useRecord(fetcher, "could not load"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("could not load");
  });

  it("refresh re-fetches and replaces data", async () => {
    let value = 1;
    const fetcher = vi.fn(() => Promise.resolve({ n: value }));
    const { result } = renderHook(() => useRecord(fetcher, "fallback"));

    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    value = 2;
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.data).toEqual({ n: 2 });
  });

  it("setData allows external mutation of the cached snapshot", async () => {
    const fetcher = vi.fn().mockResolvedValue({ count: 1 });
    const { result } = renderHook(() => useRecord(fetcher, "fallback"));

    await waitFor(() => expect(result.current.data).toEqual({ count: 1 }));
    act(() => {
      result.current.setData({ count: 99 });
    });
    expect(result.current.data).toEqual({ count: 99 });
  });
});

describe("useMutableRecord", () => {
  it("save replaces the local snapshot with the server's response", async () => {
    const fetcher = vi.fn().mockResolvedValue({ value: "a" });
    const saver = vi.fn().mockResolvedValue({ value: "b" });
    const { result } = renderHook(() =>
      useMutableRecord(fetcher, saver, {
        load: "load fail",
        save: "save fail",
      }),
    );

    await waitFor(() => expect(result.current.data).toEqual({ value: "a" }));
    await act(async () => {
      await result.current.save({ value: "b" });
    });
    expect(result.current.data).toEqual({ value: "b" });
    expect(result.current.error).toBeNull();
  });

  it("save surfaces error and rethrows; load error remains intact", async () => {
    const fetcher = vi.fn().mockResolvedValue({ value: "a" });
    const saver = vi.fn().mockRejectedValue(new Error("save boom"));
    const { result } = renderHook(() =>
      useMutableRecord(fetcher, saver, {
        load: "load fail",
        save: "save fail",
      }),
    );

    await waitFor(() => expect(result.current.data).toEqual({ value: "a" }));
    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.save({ value: "b" });
      } catch (err) {
        caught = err;
      }
    });
    expect((caught as Error).message).toBe("save boom");
    expect(result.current.error).toBe("save boom");
  });

  it("save fallback string is used when thrown value is not an Error", async () => {
    const fetcher = vi.fn().mockResolvedValue({});
    const saver = vi.fn().mockRejectedValue("string");
    const { result } = renderHook(() =>
      useMutableRecord(fetcher, saver, {
        load: "load fail",
        save: "save fail",
      }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.save({});
      } catch (err) {
        caught = err;
      }
    });
    expect(caught).toBeDefined();
    expect(result.current.error).toBe("save fail");
  });
});
