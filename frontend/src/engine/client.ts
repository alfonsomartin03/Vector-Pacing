// Main-thread entry to the browser engine. Calculations run in a Web Worker (one per request, terminated
// when done or cancelled), so the UI stays responsive. Where Worker is unavailable (unit tests), the same
// code runs in-thread. Nothing here talks to a backend: all data is bundled or read from local files.
import type { DraftingResponse, DraftingSettings } from '../drafting/types';
import type { RequestParams } from '../params';
import type { OptimizeResponse, SimulateResponse } from '../types';
import { parseGpx, type CourseModel } from './course';
import type { DraftingScenarioInput, EngineRequest } from './handle';
import type { CourseSource } from './pacing';
import { EngineError } from './util';

export { EngineError };

const abortError = () => new DOMException('The operation was aborted.', 'AbortError');

function runEngine<T>(req: EngineRequest, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) return Promise.reject(abortError());
  if (typeof Worker === 'undefined') {
    return import('./handle').then(async ({ handle }) => {
      const result = await handle(req);
      if (signal?.aborted) throw abortError();
      return result as T;
    });
  }
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'vector-pacing-engine' });
    const finish = () => {
      worker.terminate();
      signal?.removeEventListener('abort', onAbort);
    };
    const onAbort = () => {
      finish();
      reject(abortError());
    };
    signal?.addEventListener('abort', onAbort);
    worker.onmessage = (e: MessageEvent<{ ok: true; result: T } | { ok: false; message: string }>) => {
      finish();
      if (e.data.ok) resolve(e.data.result);
      else reject(new EngineError(e.data.message));
    };
    worker.onerror = (e) => {
      finish();
      reject(new EngineError(`The calculation worker failed${e.message ? `: ${e.message}` : ''}. Reload the page and try again.`));
    };
    worker.postMessage(req);
  });
}

export function runPacing(
  kind: 'simulate' | 'optimize',
  course: CourseModel,
  source: CourseSource,
  params: RequestParams,
  signal?: AbortSignal,
): Promise<SimulateResponse | OptimizeResponse> {
  return runEngine({ type: kind, course, source, params }, signal);
}

export function runDrafting(scenario: DraftingScenarioInput, settings: DraftingSettings, signal?: AbortSignal): Promise<DraftingResponse> {
  return runEngine({ type: 'drafting', scenario, settings }, signal);
}

/** The bundled demo course (lazy-loaded chunk, parsed in the browser). */
export async function loadDemoCourse(): Promise<CourseModel> {
  const { default: text } = await import('../data/demo_course.gpx?raw');
  return parseGpx(text, 'demo');
}

/** Parse a user-selected GPX file locally; the file never leaves the browser. */
export async function loadCourseFile(file: File): Promise<CourseModel> {
  return parseGpx(await file.text(), file.name.slice(0, 200));
}
