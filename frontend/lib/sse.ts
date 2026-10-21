// Fetch-based Server-Sent Events reader.
//
// EventSource cannot send custom headers (e.g. Authorization), so we open the
// stream with fetch() and parse the `event:`/`data:` frames ourselves.
//
// Usage:
//   const stop = openSse(url, {
//     token: getToken(),
//     onEvent: (type, data) => { ... },
//     onDone: () => { ... },   // stream closed (terminal event or server close)
//     onError: (err) => { ... },
//   });
//   // later: stop();

export interface SseEvent {
  type: string;
  data: unknown;
  raw: string;
}

export interface OpenSseOptions {
  token?: string | null;
  onEvent: (event: SseEvent) => void;
  onDone?: (reason: "closed" | "aborted") => void;
  onError?: (error: Error) => void;
  signal?: AbortSignal;
}

interface Frame {
  event: string;
  data: string[];
}

/** Split an SSE byte stream into complete frames separated by blank lines. */
function parseSseChunk(buffer: string): { frames: Frame[]; rest: string } {
  const frames: Frame[] = [];
  let rest = buffer;
  // Frames are separated by \n\n (or \r\n\r\n).
  let idx: number;
  while ((idx = rest.search(/\n\n|\r\n\r\n/)) !== -1) {
    const block = rest.slice(0, idx);
    rest = rest.slice(idx).replace(/^\r?\n\r?\n/, "");
    const frame: Frame = { event: "message", data: [] };
    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue; // blank or comment (keepalive)
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") frame.event = value;
      else if (field === "data") frame.data.push(value);
      // "id" / "retry" are not used by the backend; ignore.
    }
    if (frame.data.length > 0 || frame.event !== "message") frames.push(frame);
  }
  return { frames, rest };
}

export function openSse(url: string, opts: OpenSseOptions): () => void {
  const controller = new AbortController();
  const onOuterAbort = () => controller.abort();
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener("abort", onOuterAbort, { once: true });
  }

  let finished = false;
  const finish = (reason: "closed" | "aborted") => {
    if (finished) return;
    finished = true;
    controller.abort();
    opts.onDone?.(reason);
  };

  (async () => {
    let res: Response;
    try {
      res = await fetch(url, {
        method: "GET",
        headers: {
          Accept: "text/event-stream",
          ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
        },
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finish("aborted");
        return;
      }
      opts.onError?.(err instanceof Error ? err : new Error(String(err)));
      return;
    }

    if (!res.ok || !res.body) {
      let message = `SSE stream failed (${res.status})`;
      try {
        const body = (await res.json()) as { error?: { message?: string } };
        if (body?.error?.message) message = body.error.message;
      } catch {
        // ignore
      }
      opts.onError?.(new Error(message));
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = parseSseChunk(buffer);
        buffer = rest;
        for (const frame of frames) {
          const raw = frame.data.join("\n");
          let data: unknown = raw;
          if (raw) {
            try {
              data = JSON.parse(raw);
            } catch {
              data = raw;
            }
          }
          try {
            opts.onEvent({ type: frame.event, data, raw });
          } catch (err) {
            // A handler bug must not kill the stream silently.
            opts.onError?.(err instanceof Error ? err : new Error(String(err)));
          }
        }
      }
      finish("closed");
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finish("aborted");
        return;
      }
      opts.onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  })();

  return () => finish("aborted");
}