import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { buildDraftCsv, buildDraftJson } from '../drafting/export';
import { clock } from '../drafting/format';
import { formFromSettings, parseExempt, validateDraft } from '../drafting/settings';
import { draftingResponse, presets } from './draftingFixtures';

describe('drafting settings', () => {
  it('parses exempt sections and reports bad lines', () => {
    expect(parseExempt('3950-4085 Turnaround\n\n100 – 200')).toEqual({
      sections: [
        { start_m: 3950, end_m: 4085, label: 'Turnaround' },
        { start_m: 100, end_m: 200, label: '' },
      ],
    });
    expect(parseExempt('abc').error).toMatch(/Line 1/);
    expect(parseExempt('50-10').error).toMatch(/greater/);
  });
  it('validates numeric bounds', () => {
    const f = formFromSettings(presets.defaults);
    expect(validateDraft(f).settings?.zone_length_m).toBe(10);
    f.values.zone_length_m = '0';
    f.values.allowed_pass_s = 'x';
    const { errors, settings } = validateDraft(f);
    expect(settings).toBeNull();
    expect(errors.zone_length_m).toMatch(/between 1 and 50/);
    expect(errors.allowed_pass_s).toMatch(/number/);
  });
  it('formats clock times', () => {
    expect(clock(90, '2026-06-14T08:00:00Z')).toBe('08:01:30 UTC');
    expect(clock(90, null)).toBe('+1:30');
  });
});

describe('drafting export', () => {
  it('JSON includes settings, limitations, disclaimer and gap series', () => {
    const doc = JSON.parse(buildDraftJson(draftingResponse, new Date('2026-01-01T00:00:00Z')));
    expect(doc.schema).toBe('vector-pacing-drafting-export/1');
    expect(doc.settings.exempt_sections[0].label).toMatch(/illustrative/);
    expect(doc.settings_note).toMatch(/illustrative/);
    expect(doc.disclaimer).toMatch(/Not confirmed/);
    expect(doc.limitations).toHaveLength(1);
    expect(doc.events[0].series.gap_m).toHaveLength(4);
    expect(doc.events[0].limitations[0]).toMatch(/uncertainty/);
    expect(doc.dismissed_encounters[0].type).toBe('opposite_direction');
    expect(doc.scenario.course.xy).toBeUndefined();
  });
  it('CSV has settings header and one row per interval with limitations', () => {
    const lines = buildDraftCsv(draftingResponse).trim().split('\n');
    expect(lines[2]).toMatch(/zone 10 m, allowed pass 20 s/);
    expect(lines[3]!.split(',')).toContain('limitations');
    expect(lines).toHaveLength(4 + 2);
    expect(lines[4]).toMatch(/^E001,R02,R01,potential_drafting,08:01:41 UTC/);
    expect(lines[4]).toMatch(/"Combined gap uncertainty/);
  });
  it('CSV has no power-screen columns in the browser demo', () => {
    expect(buildDraftCsv(draftingResponse)).not.toMatch(/power/i);
    expect(JSON.parse(buildDraftJson(draftingResponse, new Date()))).not.toHaveProperty('power_screen');
  });
});

const DEMO_NAME = 'Out-and-back ITT — illustrative drafting scenario';
const sample = (name: string) => readFileSync(resolve(__dirname, '../../../samples', name), 'utf8');

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn(async () => {
    throw new Error('Unexpected network request');
  });
  vi.stubGlobal('fetch', fetchSpy);
});
afterEach(() => {
  expect(fetchSpy).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

async function openDrafting() {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole('tab', { name: 'Drafting check' }));
  return user;
}

async function runDemo() {
  const user = await openDrafting();
  await user.click(screen.getByRole('button', { name: 'Try multi-rider demo' }));
  expect(await screen.findByText(DEMO_NAME, {}, { timeout: 10_000 })).toBeInTheDocument();
  return user;
}

describe('drafting view (browser engine)', () => {
  it('runs the bundled demo with illustrative exempt sections and shows potential drafting', async () => {
    const user = await runDemo();
    expect(screen.getByLabelText(/Exempt course sections/)).toHaveValue('3950-4085 Turnaround (illustrative)');
    expect(screen.getByText(/Illustrative settings\./)).toBeInTheDocument();
    const chips = screen.getByRole('list', { name: 'Interval counts' });
    expect(within(chips).getByText(/Potential drafting:/)).toHaveTextContent('Potential drafting: 1');
    expect(within(chips).getByText(/Exempt section:/)).toHaveTextContent('Exempt section: 2');
    // The flagged interval is auto-selected and its details are shown.
    expect(screen.getByRole('heading', { name: /behind/ })).toBeInTheDocument();
    const table = screen.getByRole('table', { name: /Intervals/ });
    const before = within(table).getAllByRole('row').length;
    await user.click(screen.getByLabelText(/Show non-flagged intervals/));
    expect(within(table).getAllByRole('row').length).toBeGreaterThan(before);
  });

  it('recomputes when settings change, and marks old results stale until then', async () => {
    const user = await runDemo();
    const exportBtn = screen.getByRole('button', { name: 'Export JSON (full)' });
    expect(exportBtn).toBeEnabled();
    const ta = screen.getByLabelText(/Exempt course sections/);
    await user.clear(ta);
    expect(screen.getByText(/Results are out of date/)).toBeInTheDocument();
    expect(exportBtn).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Re-run analysis' }));
    // Without the exempt turnaround, the two exempt intervals become an overtake and an inconclusive one.
    await waitFor(() => expect(screen.getByText(/Exempt section:/)).toHaveTextContent('Exempt section: 0'));
    expect(screen.getByText(/Overtake within allowance:/)).toHaveTextContent('Overtake within allowance: 3');
    expect(screen.queryByText(/Results are out of date/)).not.toBeInTheDocument();
  });

  it('applies an illustrative preset', async () => {
    const user = await runDemo();
    await user.selectOptions(screen.getByLabelText('Preset'), 'illustrative_long_zone');
    expect(screen.getByLabelText(/Zone length/)).toHaveValue(15);
    expect(screen.getByText(/Not taken from any governing body/)).toBeInTheDocument();
  });

  it('shows invalid exempt input and blocks re-run', async () => {
    const user = await runDemo();
    const ta = screen.getByLabelText(/Exempt course sections/);
    await user.clear(ta);
    await user.type(ta, 'nonsense');
    expect(screen.getByText(/Line 1: use/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-run analysis' })).toBeDisabled();
  });

  it('analyses uploaded .fit rides and JSON locally, and rejects unsupported selections', async () => {
    await openDrafting();
    const input = document.querySelector('#panel-drafting input[type=file]') as HTMLInputElement;
    expect(input).toHaveAttribute('accept', '.fit,.gpx,.json');
    const choose = (...files: File[]) => fireEvent.change(input, { target: { files } });
    const fit = (path: string) => new File([readFileSync(resolve(__dirname, '../../../samples', path))], path.split('/').at(-1)!);
    choose(new File(['x'], 'notes.txt'));
    expect(await screen.findByText(/"notes.txt" is not a .fit, .gpx or .json file/)).toBeInTheDocument();
    choose(fit('drafting_with_power/R01.fit'));
    expect(await screen.findByText(/at least two .fit files/)).toBeInTheDocument();
    choose(fit('drafting_two_riders/A.fit'), fit('drafting_invalid.fit'));
    expect(await screen.findByText(/Analysis failed: drafting_invalid.fit: the FIT file is truncated/)).toBeInTheDocument();
    choose(fit('drafting_with_power/R01.fit'), fit('drafting_with_power/R02.fit'), new File([sample('drafting_with_power/course.gpx')], 'course.gpx'));
    expect(await screen.findByText('2 riders from .fit files', {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.getByText(/Potential drafting:/)).toHaveTextContent('Potential drafting: 1');
    expect(screen.getByRole('heading', { name: /R02 behind R01/ })).toBeInTheDocument();
    choose(new File(['{oops'], 'broken.json'));
    expect(await screen.findByText(/Analysis failed: "broken.json" is not valid JSON/)).toBeInTheDocument();
    choose(new File([sample('drafting_two_riders.json')], 'two.json'));
    expect(await screen.findByText(/Two riders \(converted from/)).toBeInTheDocument();
    expect(screen.getByText(/Inconclusive \(data quality\):/)).toHaveTextContent('Inconclusive (data quality): 1');
  });

  it('keeps pacing and drafting tabs independent and keyboard switchable', async () => {
    const user = await openDrafting();
    const tab = screen.getByRole('tab', { name: 'Drafting check' });
    tab.focus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'Pacing simulator' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: 'Try demo course' })).toBeVisible();
  });
});
