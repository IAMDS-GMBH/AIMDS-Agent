import { cn } from '@/lib/utils'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

// Brand badge: the app icon (white rounded tile with the IAMDS wordmark,
// identical to assets/icon.png and the favicon), the same in light and dark.
// AIS-750: the old `nous-girl.jpg` was the bare wordmark cropped edge to edge
// and drawn into an `overflow-hidden` square, so the "I" and "S" were clipped.
// The icon brings its own tile and margin; it is only scaled (object-contain),
// never cropped. Size via className (default size-14).
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img
        alt=""
        className="size-full object-contain"
        data-testid="brand-mark"
        decoding="async"
        draggable={false}
        src={assetPath('apple-touch-icon.png')}
      />
    </span>
  )
}
