import demoGpx from '../data/demo_course.gpx?raw';
import { EngineError, runDrafting, runPacing } from '../engine/client';
import { parseGpx } from '../engine/course';
import { handle, type EngineRequest } from '../engine/handle';
import { PRESETS } from '../drafting/presets';
import { defaultForm, validate } from '../params';

/** Minimal stand-in for a module Web Worker that runs the real handler asynchronously. */
class FakeWorker {
  static instances: FakeWorker[] = [];
  static hang = false;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: ErrorEvent) => void) | null = null;
  terminated = false;
  constructor(
    readonly url: URL,
    readonly opts: WorkerOptions,
  ) {
    FakeWorker.instances.push(this);
  }
  postMessage(req: EngineRequest) {
    if (FakeWorker.hang) return;
    handle(req).then(
      (result) => this.onmessage?.({ data: { ok: true, result } } as MessageEvent),
      (err: Error) => this.onmessage?.({ data: { ok: false, message: err.message } } as MessageEvent),
    );
  }
  terminate() {
    this.terminated = true;
  }
}

beforeEach(() => {
  FakeWorker.instances = [];
  FakeWorker.hang = false;
  vi.stubGlobal('Worker', FakeWorker);
});
afterEach(() => vi.unstubAllGlobals());

const course = () => parseGpx(demoGpx, 'demo');

describe('engine client', () => {
  it('runs pacing in a module worker and terminates it afterwards', async () => {
    const r = await runPacing('simulate', course(), { kind: 'demo' }, validate(defaultForm()).params!);
    expect(r.baseline.power_w).toBe(286);
    const [w] = FakeWorker.instances;
    expect(w!.opts.type).toBe('module');
    expect(String(w!.url)).toMatch(/engine\/worker\.ts/);
    expect(w!.terminated).toBe(true);
  });

  it('maps worker errors to EngineError', async () => {
    const st = { ...PRESETS.defaults };
    await expect(runDrafting({ kind: 'upload', text: '[]', filename: 'x.json' }, st)).rejects.toThrow(EngineError);
    await expect(runDrafting({ kind: 'upload', text: '[]', filename: 'x.json' }, st)).rejects.toThrow(/JSON object/);
  });

  it('cancels by terminating the worker and rejecting with AbortError', async () => {
    FakeWorker.hang = true;
    const ctrl = new AbortController();
    const p = runPacing('optimize', course(), { kind: 'demo' }, validate(defaultForm()).params!, ctrl.signal);
    ctrl.abort();
    await expect(p).rejects.toMatchObject({ name: 'AbortError' });
    expect(FakeWorker.instances[0]!.terminated).toBe(true);
  });
});
