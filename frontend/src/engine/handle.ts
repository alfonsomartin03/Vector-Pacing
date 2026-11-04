// Request dispatcher shared by the Web Worker and the in-thread fallback (tests / no Worker support).
import type { DraftingResponse, DraftingSettings } from '../drafting/types';
import type { RequestParams } from '../params';
import type { OptimizeResponse, SimulateResponse } from '../types';
import type { CourseModel } from './course';
import { analyzeScenario, parseScenario } from './drafting';
import { scenarioFromFit, type FitUpload } from './fit';
import { optimizePacingRequest, simulatePacing, type CourseSource } from './pacing';
import { EngineError, hashBytes, hashText } from './util';

export type DraftingScenarioInput =
  | { kind: 'demo' }
  | { kind: 'upload'; text: string; filename: string }
  | ({ kind: 'fit' } & FitUpload);

export type EngineRequest =
  | { type: 'simulate' | 'optimize'; course: CourseModel; source: CourseSource; params: RequestParams }
  | { type: 'drafting'; scenario: DraftingScenarioInput; settings: DraftingSettings };

async function scenarioDocument(input: DraftingScenarioInput) {
  if (input.kind === 'fit') {
    const { doc, extraWarnings, source } = scenarioFromFit(input);
    const hash = hashBytes(input.riders.map((r) => r.bytes));
    return { doc, source, hash, extraWarnings };
  }
  if (input.kind === 'demo') {
    // Bundled with the app (lazy chunk); no network request to any server other than the static site.
    const { default: text } = await import('../data/drafting_demo.json?raw');
    return { doc: JSON.parse(text) as unknown, source: 'demo', hash: hashText(text) };
  }
  let doc: unknown;
  try {
    doc = JSON.parse(input.text);
  } catch {
    throw new EngineError(`"${input.filename}" is not valid JSON.`);
  }
  return { doc, source: input.filename, hash: hashText(input.text) };
}

export async function handle(req: EngineRequest): Promise<SimulateResponse | OptimizeResponse | DraftingResponse> {
  switch (req.type) {
    case 'simulate':
      return simulatePacing(req.course, req.source, req.params);
    case 'optimize':
      return optimizePacingRequest(req.course, req.source, req.params);
    case 'drafting': {
      const { doc, source, hash, extraWarnings } = await scenarioDocument(req.scenario);
      const scenario = parseScenario(doc, source, hash);
      extraWarnings?.forEach((w, i) => scenario.riders[i]?.warnings.push(...w));
      return analyzeScenario(scenario, req.settings);
    }
  }
}
