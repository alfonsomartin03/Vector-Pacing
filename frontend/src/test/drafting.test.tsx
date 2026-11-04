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
});

function mockApi(analyze: () => { status: number; body: unknown } = () => ({ status: 200, body: draftingResponse })) {
  const fn = vi.fn(async (url: string, _init?: RequestInit) => {
    void _init;
    const r = url.endsWith('/api/drafting/presets') ? { status: 200, body: presets } : analyze();
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

async function openDrafting() {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole('tab', { name: 'Drafting check' }));
  return user;
}

describe('drafting view', () => {
  it('runs the demo with illustrative exempt sections and shows potential drafting', async () => {
    const fetchMock = mockApi();
    const user = await openDrafting();
    await user.click(await screen.findByRole('button', { name: 'Try multi-rider demo' }));
    expect(await screen.findByText('Fixture scenario')).toBeInTheDocument();
    const req = JSON.parse(String(fetchMock.mock.calls[1]![1]!.body));
    expect(req.scenario).toEqual({ kind: 'demo' });
    expect(req.settings.exempt_sections[0].start_m).toBe(3950);
    expect(screen.getByText(/Illustrative settings\./)).toBeInTheDocument();
    // First flagged event is auto-selected and its details are shown.
    expect(screen.getByRole('heading', { name: /E001: R02 behind R01/ })).toBeInTheDocument();
    // Non-flagged intervals are hidden until requested.
    const table = screen.getByRole('table', { name: /Intervals/ });
    expect(within(table).queryByText('E002')).not.toBeInTheDocument();
    await user.click(screen.getByLabelText(/Show non-flagged intervals/));
    expect(within(table).getByText('E002')).toBeInTheDocument();
    await user.click(within(table).getByRole('button', { name: 'Inspect E002' }));
    expect(screen.getByRole('heading', { name: /E002: R03 behind R02/ })).toBeInTheDocument();
  });

  it('marks results stale when settings change and disables export', async () => {
    mockApi();
    const user = await openDrafting();
    await user.click(await screen.findByRole('button', { name: 'Try multi-rider demo' }));
    await screen.findByText('Fixture scenario');
    const exportBtn = screen.getByRole('button', { name: 'Export JSON (full)' });
    expect(exportBtn).toBeEnabled();
    const zone = screen.getByLabelText(/Zone length/);
    await user.clear(zone);
    await user.type(zone, '12');
    expect(screen.getByText(/Results are out of date/)).toBeInTheDocument();
    expect(exportBtn).toBeDisabled();
  });

  it('applies an illustrative preset', async () => {
    mockApi();
    const user = await openDrafting();
    await user.click(await screen.findByRole('button', { name: 'Try multi-rider demo' }));
    await screen.findByText('Fixture scenario');
    await user.selectOptions(screen.getByLabelText('Preset'), 'illustrative_long_zone');
    expect(screen.getByLabelText(/Zone length/)).toHaveValue(15);
    expect(screen.getByText(/Not taken from any governing body/)).toBeInTheDocument();
  });

  it('shows invalid exempt input and blocks re-run', async () => {
    mockApi();
    const user = await openDrafting();
    await user.click(await screen.findByRole('button', { name: 'Try multi-rider demo' }));
    await screen.findByText('Fixture scenario');
    const ta = screen.getByLabelText(/Exempt course sections/);
    await user.clear(ta);
    await user.type(ta, 'nonsense');
    expect(screen.getByText(/Line 1: use/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-run analysis' })).toBeDisabled();
  });

  it('rejects non-JSON uploads and reports server validation errors with retry', async () => {
    let calls = 0;
    mockApi(() => {
      calls += 1;
      return calls === 1 ? { status: 422, body: { detail: "'riders' must list at least two riders." } } : { status: 200, body: draftingResponse };
    });
    await openDrafting();
    await screen.findByRole('button', { name: 'Try multi-rider demo' });
    const input = document.querySelector('#panel-drafting input[type=file]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['{oops'], 'bad.json', { type: 'application/json' })] } });
    expect(await screen.findByText(/is not valid JSON/)).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [new File(['{"format":"x"}'], 'doc.json', { type: 'application/json' })] } });
    expect(await screen.findByText(/at least two riders/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByText('Fixture scenario')).toBeInTheDocument());
  });

  it('keeps pacing and drafting tabs independent and keyboard switchable', async () => {
    mockApi();
    const user = await openDrafting();
    await screen.findByRole('button', { name: 'Try multi-rider demo' });
    const tab = screen.getByRole('tab', { name: 'Drafting check' });
    tab.focus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'Pacing simulator' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: 'Try demo course' })).toBeVisible();
  });
});
