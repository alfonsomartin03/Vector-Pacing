import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { course, optimizeResponse } from './fixtures';

function mockFetch(handler: (url: string, init?: RequestInit) => { status: number; body: unknown }) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const { status, body } = handler(url, init);
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('App workflow', () => {
  it('starts with an obvious demo entry point and empty state', () => {
    render(<App />);
    expect(screen.getByRole('button', { name: 'Try demo course' })).toBeInTheDocument();
    expect(screen.getByText(/No course loaded yet/)).toBeInTheDocument();
  });

  it('demo → optimize → shows comparison; editing inputs marks results stale and disables export', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((url) =>
      url.endsWith('/api/courses/demo') ? { status: 200, body: course } : { status: 200, body: optimizeResponse },
    );
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Try demo course' }));
    expect(await screen.findByRole('heading', { name: 'Fixture course' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Optimize pacing' }));
    expect(await screen.findByText('−12.0 s')).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    const exportJson = screen.getByRole('button', { name: 'Export JSON (full)' });
    expect(exportJson).toBeEnabled();

    const body = JSON.parse(String(fetchMock.mock.calls[1]![1]!.body));
    expect(body.course).toEqual({ kind: 'demo' });
    expect(body.rider.cp_w).toBe(280);

    const cp = screen.getByLabelText(/Critical power/);
    await user.clear(cp);
    await user.type(cp, '300');
    expect(screen.getByText(/Results are out of date/)).toBeInTheDocument();
    expect(exportJson).toBeDisabled();
  });

  it('blocks runs on invalid input with a field message', async () => {
    const user = userEvent.setup();
    mockFetch(() => ({ status: 200, body: course }));
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Try demo course' }));
    const cp = await screen.findByLabelText(/Critical power/);
    await user.clear(cp);
    expect(screen.getByText('Enter a number.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Optimize pacing' })).toBeDisabled();
  });

  it('shows server errors with retry, and retry succeeds', async () => {
    const user = userEvent.setup();
    let calls = 0;
    mockFetch((url) => {
      if (url.endsWith('/api/courses/demo')) return { status: 200, body: course };
      calls += 1;
      return calls === 1 ? { status: 500, body: { detail: 'Boom' } } : { status: 200, body: optimizeResponse };
    });
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Try demo course' }));
    await screen.findByRole('heading', { name: 'Fixture course' });
    await user.click(screen.getByRole('button', { name: 'Optimize pacing' }));
    expect(await screen.findByText(/Optimization failed: Boom/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByText('−12.0 s')).toBeInTheDocument());
  });

  it('reports unreachable backend and invalid upload', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Try demo course' }));
    expect(await screen.findByText(/Could not reach the Vector Pacing API/)).toBeInTheDocument();
    const input = document.querySelector('input[type=file]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['hello'], 'notes.txt', { type: 'text/plain' })] } });
    expect(await screen.findByText(/is not a .gpx file/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose another file' })).toBeInTheDocument();
  });

  it('start over resets to the empty state', async () => {
    const user = userEvent.setup();
    mockFetch(() => ({ status: 200, body: course }));
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Try demo course' }));
    await screen.findByRole('heading', { name: 'Fixture course' });
    await user.click(screen.getByRole('button', { name: 'Start over' }));
    expect(screen.getByText(/No course loaded yet/)).toBeInTheDocument();
  });
});
