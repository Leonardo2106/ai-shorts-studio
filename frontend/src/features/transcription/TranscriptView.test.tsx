import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { TranscriptView } from './TranscriptView'

it('renders transcript segments with millisecond timestamps', () => {
  render(<TranscriptView transcript={{ id: 't1', project_id: 'p1', media_id: 'm1', source: 'SCREEN', audio_stream_index: 2, language: 'pt', duration_ms: 65432, engine: 'faster-whisper', model: 'small', segments: [{ start_ms: 1234, end_ms: 65432, text: 'Olá mundo.' }] }} />)
  expect(screen.getByText('00:01.234')).toBeInTheDocument()
  expect(screen.getByText('01:05.432')).toBeInTheDocument()
  expect(screen.getByText('Olá mundo.')).toBeInTheDocument()
})
