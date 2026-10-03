import { act, cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PreviewPane } from './preview-pane'

describe('PreviewPane console state', () => {
  afterEach(() => {
    cleanup()
  })

  it('does not rebuild the pane titlebar group for streamed console logs', () => {
    const setTitlebarToolGroup = vi.fn()

    const rendered = render(
      <PreviewPane
        setTitlebarToolGroup={setTitlebarToolGroup}
        target={{
          kind: 'url',
          label: 'Preview',
          source: 'http://localhost:5174',
          url: 'http://localhost:5174'
        }}
      />
    )

    const initialCalls = setTitlebarToolGroup.mock.calls.length
    const webview = rendered.container.querySelector('webview')

    expect(webview).toBeInstanceOf(HTMLElement)

    act(() => {
      webview?.dispatchEvent(
        Object.assign(new Event('console-message'), {
          level: 0,
          message: 'streamed log line',
          sourceId: 'http://localhost:5174/src/main.tsx'
        })
      )
    })

    expect(setTitlebarToolGroup).toHaveBeenCalledTimes(initialCalls)
  })
})

describe('PreviewPane document kinds (AIS-397)', () => {
  afterEach(() => {
    cleanup()
    window.hermesDesktop = undefined as unknown as typeof window.hermesDesktop
  })

  it('renders a PDF in the web guest without console or devtools tools', () => {
    const setTitlebarToolGroup = vi.fn()

    const rendered = render(
      <PreviewPane
        setTitlebarToolGroup={setTitlebarToolGroup}
        target={{
          kind: 'file',
          label: 'report.pdf',
          path: '/tmp/report.pdf',
          previewKind: 'pdf',
          source: '/tmp/report.pdf',
          url: 'file:///tmp/report.pdf'
        }}
      />
    )

    expect(rendered.container.querySelector('webview')?.getAttribute('src')).toBe('file:///tmp/report.pdf')
    expect(setTitlebarToolGroup).toHaveBeenLastCalledWith(expect.any(String), [])
  })

  it('shows Office files as converted Markdown from the backend', async () => {
    const api = vi.fn().mockResolvedValue({ markdown: '# Quarterly report\n\nRevenue grew.', truncated: false })
    window.hermesDesktop = { api } as unknown as typeof window.hermesDesktop

    const rendered = render(
      <PreviewPane
        target={{
          binary: true,
          kind: 'file',
          label: 'report.docx',
          path: '/tmp/report.docx',
          previewKind: 'document',
          source: '/tmp/report.docx',
          url: 'file:///tmp/report.docx'
        }}
      />
    )

    expect(await rendered.findByText('Quarterly report')).toBeTruthy()
    expect(rendered.container.querySelector('webview')).toBeNull()
    expect(api).toHaveBeenCalledWith({ path: '/api/files/preview-document?path=%2Ftmp%2Freport.docx' })
  })

  it('falls back to the open-in-app state when the conversion fails', async () => {
    const api = vi.fn().mockRejectedValue(new Error('no backend could read report.pptx'))
    window.hermesDesktop = { api, openPath: vi.fn() } as unknown as typeof window.hermesDesktop

    const rendered = render(
      <PreviewPane
        target={{
          kind: 'file',
          label: 'report.pptx',
          path: '/tmp/report.pptx',
          previewKind: 'document',
          source: '/tmp/report.pptx',
          url: 'file:///tmp/report.pptx'
        }}
      />
    )

    expect(await rendered.findByText(/no backend could read report\.pptx/)).toBeTruthy()
  })
})
