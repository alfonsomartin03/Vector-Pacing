import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

// These tests run the real in-browser engine (in-thread, since jsdom has no Worker) on the bundled demo.
const sample = (name: string) => readFileSync(resolve(__dirname, '../../../samples', name), 'utf8');

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn(async () => {
    throw new Error('Unexpected network request');
  });
  vi.stubGlobal('fetch', fetchSpy);
});
afterEach(() => {
  expect(fetchSpy).not.toHaveBeenCalled(); // no backend: everything is computed locally
  vi.unstubAllGlobals();
});

async function loadDemo() {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole('button', { name: 'Try demo course' }));
  expect(await screen.findByRole('heading', { name: 'Vector Valley TT (demo)' })).toBeInTheDocument();
  return user;
}

describe('App workflow (browser engine)', () => {
  it('starts with an obvious demo entry point, empty state and no API notice', () => {
    render(<App />);
    expect(screen.getByRole('button', { name: 'Try demo course' })).toBeInTheDocument();
    expect(screen.getByText(/No course loaded yet/)).toBeInTheDocument();
    expect(screen.queryByText(/hosted Vector Pacing API/)).not.toBeInTheDocument();
  });

  it('demo → optimize → shows a verified comparison; editing inputs marks results stale', async () => {
    const user = await loadDemo();
    expect(screen.getByText('32.00 km')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Optimize pacing' }));
    const verdict = await screen.findByRole('region', { name: 'Comparison' }, { timeout: 10_000 });
    expect(within(verdict).getByText(/faster than the baseline under this model/)).toBeInTheDocument();
    expect(screen.getByText(/Auto: the highest whole-watt constant power .* \(286 W\)/)).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /Pacing plan by segment/ })).toBeInTheDocument();
    expect(screen.getByText(/Method: Bounded pairwise power-transfer search \(browser heuristic\)/)).toBeInTheDocument();
    const exportJson = screen.getByRole('button', { name: 'Export JSON (full)' });
    expect(exportJson).toBeEnabled();

    const cp = screen.getByLabelText(/Critical power/);
    await user.clear(cp);
    await user.type(cp, '300');
    expect(screen.getByText(/Results are out of date/)).toBeInTheDocument();
    expect(exportJson).toBeDisabled();
  });

  it('recomputes from changed rider inputs', async () => {
    const user = await loadDemo();
    await user.click(screen.getByRole('button', { name: 'Simulate baseline' }));
    expect(await screen.findByText(/\(286 W\)/)).toBeInTheDocument();
    const cp = screen.getByLabelText(/Critical power/);
    await user.clear(cp);
    await user.type(cp, '320');
    await user.click(screen.getByRole('button', { name: 'Simulate baseline' }));
    await waitFor(() => expect(screen.queryByText(/Results are out of date/)).not.toBeInTheDocument());
    const note = screen.getByText(/Baseline selection: Auto/).textContent!;
    const watts = Number(/\((\d+) W\)/.exec(note)![1]);
    expect(watts).toBeGreaterThan(286);
  });

  it('blocks runs on invalid input with a field message', async () => {
    const user = await loadDemo();
    const cp = screen.getByLabelText(/Critical power/);
    await user.clear(cp);
    expect(screen.getByText('Enter a number.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Optimize pacing' })).toBeDisabled();
  });

  it('shows calculation errors with retry', async () => {
    const user = await loadDemo();
    await user.click(screen.getByLabelText('Manual'));
    const p = screen.getByLabelText(/Baseline constant power/);
    await user.clear(p);
    await user.type(p, '400');
    await user.click(screen.getByRole('button', { name: 'Optimize pacing' }));
    expect(await screen.findByText(/baseline is infeasible/)).toBeInTheDocument();
  });

  it('loads a local GPX file and reports invalid files', async () => {
    render(<App />);
    const input = document.querySelector('#panel-pacing input[type=file]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['hello'], 'notes.txt', { type: 'text/plain' })] } });
    expect(await screen.findByText(/is not a .gpx file/)).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [new File([sample('invalid_malformed.gpx')], 'bad.gpx')] } });
    expect(await screen.findByText(/Could not load "bad.gpx": The file is not well-formed XML/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose another file' })).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [new File([sample('hill_climb_two_segments.gpx')], 'hill.gpx')] } });
    expect(await screen.findByText(/2 track segments were joined/)).toBeInTheDocument();
    expect(screen.getByText('hill.gpx')).toBeInTheDocument();
  });

  it('start over resets to the empty state', async () => {
    const user = await loadDemo();
    await user.click(screen.getByRole('button', { name: 'Start over' }));
    expect(screen.getByText(/No course loaded yet/)).toBeInTheDocument();
  });
});
