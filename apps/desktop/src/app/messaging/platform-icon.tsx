import {
  SiApple,
  SiBilibili,
  SiDiscord,
  SiGmail,
  SiHomeassistant,
  SiMatrix,
  SiMattermost,
  SiQq,
  SiSignal,
  SiTelegram,
  SiWechat,
  SiWhatsapp
} from '@icons-pack/react-simple-icons'
import { type ComponentType, type SVGProps, useState } from 'react'

import { Globe, Link as LinkIcon, MessageSquareText } from '@/lib/icons'
import { cn } from '@/lib/utils'

// We prefer simpleicons.org glyphs when available. For providers that need a
// packaged custom mark, we load an SVG from /public/messaging-icons/.
type IconKind = 'brand' | 'generic'

interface PlatformIconSpec {
  Icon?: ComponentType<SVGProps<SVGSVGElement>>
  color: string
  kind: IconKind
  monogram?: string
  svgPath?: string
}

const PLATFORM_ICONS: Record<string, PlatformIconSpec> = {
  telegram: { Icon: SiTelegram, color: '#26A5E4', kind: 'brand' },
  discord: { Icon: SiDiscord, color: '#5865F2', kind: 'brand' },
  slack: { color: '#4A154B', kind: 'brand', svgPath: '/messaging-icons/slack.svg' },
  mattermost: { Icon: SiMattermost, color: '#0058CC', kind: 'brand' },
  matrix: { Icon: SiMatrix, color: '#000000', kind: 'brand' },
  signal: { Icon: SiSignal, color: '#3A76F0', kind: 'brand' },
  whatsapp: { Icon: SiWhatsapp, color: '#25D366', kind: 'brand' },
  bluebubbles: { Icon: SiApple, color: '#0BD318', kind: 'brand' },
  homeassistant: { Icon: SiHomeassistant, color: '#18BCF2', kind: 'brand' },
  email: { Icon: SiGmail, color: '#EA4335', kind: 'brand' },
  outlook: { color: '#0078D4', kind: 'brand', svgPath: '/messaging-icons/outlook.svg' },
  line: { color: '#06C755', kind: 'brand', svgPath: '/messaging-icons/line.svg' },
  teams: { color: '#6264A7', kind: 'brand', svgPath: '/messaging-icons/teams.svg' },
  sms: { Icon: MessageSquareText, color: '#F43F5E', kind: 'generic' },
  webhook: { Icon: LinkIcon, color: '#71717A', kind: 'generic' },
  api_server: { Icon: Globe, color: '#64748B', kind: 'generic' },
  weixin: { Icon: SiWechat, color: '#07C160', kind: 'brand' },
  qqbot: { Icon: SiQq, color: '#EB1923', kind: 'brand' },
  yuanbao: { Icon: SiBilibili, color: '#FB7299', kind: 'brand' }
}

interface PlatformAvatarProps {
  platformId: string
  platformName: string
  className?: string
}

function resolvePublicAssetPath(path: string): string {
  const rel = path.replace(/^\/+/, '')

  return `${import.meta.env.BASE_URL}${rel}`
}

export function PlatformAvatar({ className, platformId, platformName }: PlatformAvatarProps) {
  const spec = PLATFORM_ICONS[platformId]
  const [svgFailed, setSvgFailed] = useState(false)

  const baseClass = cn(
    'inline-grid size-6 shrink-0 place-items-center rounded-md text-[length:var(--conversation-caption-font-size)] font-medium',
    className
  )

  if (!spec) {
    return (
      <span aria-hidden="true" className={cn(baseClass, 'bg-(--ui-bg-tertiary) text-(--ui-text-tertiary)')}>
        {platformName.charAt(0).toUpperCase()}
      </span>
    )
  }

  const { Icon, color, svgPath } = spec
  const svgSrc = svgPath ? resolvePublicAssetPath(svgPath) : null

  return (
    <span
      aria-hidden="true"
      className={baseClass}
      style={{
        // 16% tint of the brand color so the glyph reads against any surface
        // without the avatar dominating the row.
        backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)`,
        color
      }}
    >
      {Icon ? (
        <Icon className="size-3.5" />
      ) : svgSrc && !svgFailed ? (
        <img alt="" className="size-3.5 object-contain" onError={() => setSvgFailed(true)} src={svgSrc} />
      ) : (
        spec.monogram || platformName.charAt(0).toUpperCase()
      )}
    </span>
  )
}
