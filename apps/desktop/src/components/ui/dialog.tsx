import { Dialog as DialogPrimitive } from 'radix-ui'
import * as React from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { X } from '@/lib/icons'
import { beginPointerDrag, clampToViewport, isDragExempt } from '@/lib/pointer-drag'
import { cn } from '@/lib/utils'

// A floating dialog (AIS-398) is non-modal: no backdrop, the app behind it
// stays usable, clicks outside don't close it, and its header drags it.
const DialogFloatingContext = React.createContext(false)

function Dialog({
  floating = false,
  modal,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Root> & { floating?: boolean }) {
  return (
    <DialogFloatingContext.Provider value={floating}>
      <DialogPrimitive.Root data-slot="dialog" modal={floating ? false : modal} {...props} />
    </DialogFloatingContext.Provider>
  )
}

function DialogTrigger({ ...props }: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal({ ...props }: React.ComponentProps<typeof DialogPrimitive.Portal>) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogClose({ ...props }: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      className={cn(
        'fixed inset-0 z-[120] pointer-events-auto bg-black/22 backdrop-blur-[0.125rem] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0',
        className
      )}
      data-slot="dialog-overlay"
      {...props}
    />
  )
}

function DialogContent({
  className,
  children,
  onEscapeKeyDown,
  onFocusOutside,
  onInteractOutside,
  onPointerDown,
  onPointerDownOutside,
  showCloseButton = true,
  style,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  showCloseButton?: boolean
}) {
  const { t } = useI18n()
  const floating = React.useContext(DialogFloatingContext)
  const [offset, setOffset] = React.useState({ x: 0, y: 0 })

  const keepOpen = <E extends { preventDefault: () => void }>(handler?: (event: E) => void) =>
    floating
      ? (event: E) => {
          handler?.(event)
          event.preventDefault()
        }
      : handler

  // Esc typed in the app behind a floating dialog belongs to the app.
  const escapeInsideOnly = (event: KeyboardEvent) => {
    onEscapeKeyDown?.(event)

    if (floating && !(event.target instanceof Element && event.target.closest('[data-slot="dialog-content"]'))) {
      event.preventDefault()
    }
  }

  const startDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    onPointerDown?.(event)

    const target = event.target as Element | null
    const onChrome = target === event.currentTarget || Boolean(target?.closest('[data-slot="dialog-header"]'))

    if (!floating || event.defaultPrevented || !onChrome || isDragExempt(target)) {
      return
    }

    const rect = event.currentTarget.getBoundingClientRect()
    const start = offset

    beginPointerDrag(event, {
      onMove: (dx, dy) => {
        const { left, top } = clampToViewport(rect.left + dx, rect.top + dy, rect.width, rect.height)
        setOffset({ x: start.x + left - rect.left, y: start.y + top - rect.top })
      }
    })
  }

  return (
    <DialogPortal>
      {!floating && <DialogOverlay />}
      <DialogPrimitive.Content
        className={cn(
          // Cap height at 85vh and let long content scroll inside the dialog
          // instead of overflowing off-screen (long cron titles, tool detail
          // dumps, etc.). Individual dialogs can still override via className.
          'fixed left-1/2 top-1/2 z-[130] pointer-events-auto grid max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 gap-3 overflow-y-auto rounded-xl border border-(--stroke-nous) bg-(--ui-chat-bubble-background) p-4 text-[length:var(--conversation-text-font-size)] text-foreground shadow-nous duration-200 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
          className
        )}
        data-floating={floating ? '' : undefined}
        data-slot="dialog-content"
        onEscapeKeyDown={escapeInsideOnly}
        onFocusOutside={keepOpen(onFocusOutside)}
        onInteractOutside={keepOpen(onInteractOutside)}
        onPointerDown={startDrag}
        onPointerDownOutside={keepOpen(onPointerDownOutside)}
        style={
          floating
            ? { translate: `calc(-50% + ${offset.x}px) calc(-50% + ${offset.y}px)`, ...style }
            : style
        }
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close asChild data-slot="dialog-close-button">
            <Button
              aria-label={t.common.close}
              className="absolute right-2.5 top-2.5 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground"
              size="icon-xs"
              variant="ghost"
            >
              <X className="size-4" />
              <span className="sr-only">{t.common.close}</span>
            </Button>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<'div'>) {
  const floating = React.useContext(DialogFloatingContext)

  return (
    <div
      className={cn('flex flex-col gap-1 text-center sm:text-left', floating && 'cursor-grab select-none', className)}
      data-slot="dialog-header"
      {...props}
    />
  )
}

function DialogFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn('flex flex-col-reverse gap-2 sm:flex-row sm:justify-end', className)}
      data-slot="dialog-footer"
      {...props}
    />
  )
}

function DialogTitle({
  className,
  icon: Icon,
  children,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title> & {
  // Pass a lucide icon to get the canonical dialog-header glyph: a plain
  // primary-tinted icon inline with the title (no bg chip / ring). This is the
  // single source of truth for dialog header icons — don't hand-roll wrappers.
  icon?: React.ComponentType<{ className?: string }>
}) {
  return (
    <DialogPrimitive.Title
      className={cn(
        'text-[0.9375rem] font-semibold tracking-tight text-foreground',
        Icon && 'flex items-center gap-2',
        className
      )}
      data-slot="dialog-title"
      {...props}
    >
      {Icon ? <Icon className="size-4 shrink-0 text-primary" /> : null}
      {children}
    </DialogPrimitive.Title>
  )
}

function DialogDescription({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn(
        'text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)',
        className
      )}
      data-slot="dialog-description"
      {...props}
    />
  )
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger
}
