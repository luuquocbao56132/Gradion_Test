import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import * as api from '../api';

vi.mock('../components/ProjectDetail', async () => {
  const { useState } = await import('react');
  return {
    default: ({ projectId }: { projectId: string; onBack: () => void }) => {
      const [instanceProjectId] = useState(projectId);
      return <p>Instance {instanceProjectId}; route {projectId}</p>;
    },
  };
});

import App from '../App';

afterEach(() => {
  vi.restoreAllMocks();
  window.location.hash = '#/';
});

test('changing detail hashes remounts the project-scoped detail instance', async () => {
  window.location.hash = '#/projects/p1';
  vi.spyOn(api, 'getSession').mockResolvedValue({
    user_id: 'u1', name: 'Ada Lovelace', email: 'ada@example.com',
  });

  render(<App />);
  expect(await screen.findByText('Instance p1; route p1')).toBeInTheDocument();

  await act(async () => {
    window.location.hash = '#/projects/p2';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(await screen.findByText('Instance p2; route p2')).toBeInTheDocument();
  expect(screen.queryByText('Instance p1; route p2')).not.toBeInTheDocument();
});
