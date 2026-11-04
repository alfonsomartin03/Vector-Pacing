// Web Worker entry: runs pacing and drafting calculations off the main thread.
import { handle, type EngineRequest } from './handle';
import { EngineError } from './util';

self.onmessage = async (e: MessageEvent<EngineRequest>) => {
  try {
    self.postMessage({ ok: true, result: await handle(e.data) });
  } catch (err) {
    const message = err instanceof EngineError ? err.message : `Unexpected calculation error: ${err instanceof Error ? err.message : String(err)}`;
    self.postMessage({ ok: false, message });
  }
};
