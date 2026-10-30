import { render, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { type HastNode, MarkdownTextContent, sectionLabelText } from './markdown-text'

const text = (value: string): HastNode => ({ type: 'text', value })
const el = (tagName: string, ...children: HastNode[]): HastNode => ({ children, tagName, type: 'element' })

async function renderMarkdown(markdown: string) {
  const { container } = render(<MarkdownTextContent isRunning={false} text={markdown} />)

  await waitFor(() => expect(container.querySelector('.aui-md')?.textContent?.trim()).toBeTruthy())

  return container
}

const labels = (container: HTMLElement) =>
  [...container.querySelectorAll('[data-slot="section-label"]')].map(node => [node.tagName, node.textContent])

describe('sectionLabelText (AIS-792)', () => {
  it('reads a paragraph that is only a bold label ending in a colon', () => {
    expect(sectionLabelText(el('p', el('strong', text('Offen:'))))).toBe('Offen')
    expect(sectionLabelText(el('p', el('strong', text('Offen')), text(':')))).toBe('Offen')
    expect(sectionLabelText(el('p', text('\n'), el('strong', text('Nächste Schritte: ')), text(' ')))).toBe(
      'Nächste Schritte'
    )
    expect(sectionLabelText(el('p', el('strong', text('Status '), el('code', text('v2')), text(':'))))).toBe(
      'Status v2'
    )
  })

  it('leaves bold inside sentences, bold without a colon and other markup alone', () => {
    expect(sectionLabelText(el('p', el('strong', text('Offen:')), text(' drei Punkte')))).toBeNull()
    expect(sectionLabelText(el('p', text('Bitte '), el('strong', text('jetzt:')), text(' prüfen')))).toBeNull()
    expect(sectionLabelText(el('p', el('strong', text('Wichtig'))))).toBeNull()
    expect(sectionLabelText(el('p', el('strong', el('a', text('Link:')))))).toBeNull()
    expect(sectionLabelText(el('p', el('strong', text(`${'x'.repeat(90)}:`))))).toBeNull()
    expect(sectionLabelText(el('li', el('strong', text('Offen:'))))).toBeNull()
    expect(sectionLabelText(undefined)).toBeNull()
  })
})

describe('one section style in rendered answers (AIS-792)', () => {
  it('renders h2, h3 and a bold label paragraph as the same section label', async () => {
    const container = await renderMarkdown(
      ['## Offene Punkte', '', 'Text.', '', '### Details', '', 'Mehr.', '', '**Offen:**', '', '- eins', '- zwei'].join(
        '\n'
      )
    )

    expect(labels(container)).toEqual([
      ['H2', 'Offene Punkte'],
      ['H3', 'Details'],
      ['P', 'Offen']
    ])

    const classes = [...container.querySelectorAll('[data-slot="section-label"]')].map(node => node.className)

    expect(new Set(classes).size).toBe(1)
    expect(container.querySelector('p[data-slot="section-label"]')?.getAttribute('role')).toBe('heading')
  })

  it('keeps bold labels inside list items and quotes, and inline bold, as they are', async () => {
    const container = await renderMarkdown(
      ['- **Offen:**', '- zwei', '', '> **Hinweis:**', '', 'Das ist **wichtig:** bitte lesen.'].join('\n')
    )

    expect(labels(container)).toEqual([])
    // Streamdown renders bold as a span.
    expect([...container.querySelectorAll('[data-streamdown="strong"]')].map(node => node.textContent)).toEqual([
      'Offen:',
      'Hinweis:',
      'wichtig:'
    ])
  })
})
