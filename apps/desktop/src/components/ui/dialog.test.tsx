import { cleanup, fireEvent, render } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { Dialog, DialogContent, DialogHeader, DialogTitle } from './dialog'

function Harness({ floating }: { floating: boolean }) {
  const [open, setOpen] = useState(true)

  return (
    <>
      <button type="button">outside</button>
      <span data-testid="state">{open ? 'open' : 'closed'}</span>
      <Dialog floating={floating} onOpenChange={setOpen} open={open}>
        <DialogContent aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Report</DialogTitle>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    </>
  )
}

describe('Dialog floating mode (AIS-398)', () => {
  afterEach(cleanup)

  it('renders without a backdrop and survives clicks outside', () => {
    const rendered = render(<Harness floating />)

    expect(document.querySelector('[data-slot="dialog-overlay"]')).toBeNull()
    expect(document.querySelector('[data-slot="dialog-content"]')?.hasAttribute('data-floating')).toBe(true)

    const outside = rendered.getByText('outside')
    fireEvent.pointerDown(outside)
    fireEvent.mouseDown(outside)
    fireEvent.click(outside)
    fireEvent.focusIn(outside)

    expect(rendered.getByTestId('state').textContent).toBe('open')
  })

  it('ignores Escape typed outside the dialog', () => {
    const rendered = render(<Harness floating />)

    fireEvent.keyDown(rendered.getByText('outside'), { key: 'Escape' })
    expect(rendered.getByTestId('state').textContent).toBe('open')

    fireEvent.keyDown(document.querySelector('[data-slot="dialog-content"]') as Element, { key: 'Escape' })
    expect(rendered.getByTestId('state').textContent).toBe('closed')
  })

  it('keeps the modal backdrop for regular dialogs', () => {
    render(<Harness floating={false} />)

    expect(document.querySelector('[data-slot="dialog-overlay"]')).toBeTruthy()
  })
})
