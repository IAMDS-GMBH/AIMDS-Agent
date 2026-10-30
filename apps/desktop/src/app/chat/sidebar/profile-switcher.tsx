import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ReportIssueDialog } from '@/components/report-issue-dialog'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { profileColorSoft, resolveProfileColor } from '@/lib/profile-color'
import { cn } from '@/lib/utils'
import {
  $activeGatewayProfile,
  $profileColors,
  $profileCreateRequest,
  $profileOrder,
  $profiles,
  $profileScope,
  ALL_PROFILES,
  normalizeProfileKey,
  refreshActiveProfile,
  selectProfile,
  setShowAllProfiles,
  sortByProfileOrder
} from '@/store/profile'

import { CreateProfileDialog } from '../../profiles/create-profile-dialog'
import { PROFILES_ROUTE, SETTINGS_ACCOUNTS_ROUTE, SETTINGS_ROUTE } from '../../routes'

const initialOf = (name: string) => name.replace(/[^a-z0-9]/gi, '').charAt(0).toUpperCase() || '?'

/**
 * The account menu at the sidebar foot (AIS-794). One button — the active
 * profile — opens one menu: switch profile, manage profiles, Einstellungen,
 * Konten, Hilfe & Feedback. Replaces the home / + / … profile rail. Renaming,
 * colours, deleting and SOUL.md live on the profiles page ("Profile
 * verwalten"); the profile hotkeys keep working as before.
 */
export function AccountMenu() {
  const { t } = useI18n()
  const a = t.sidebar.account
  const navigate = useNavigate()
  const profiles = useStore($profiles)
  const scope = useStore($profileScope)
  const gatewayProfile = useStore($activeGatewayProfile)
  const order = useStore($profileOrder)
  const colors = useStore($profileColors)
  const [createOpen, setCreateOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)

  // Re-pull the running profile + list on mount so a profile created elsewhere
  // shows up; cheap and best-effort.
  useEffect(() => {
    void refreshActiveProfile()
  }, [])

  // Open the create dialog when the `profile.create` hotkey fires (the dialog
  // state lives here, so the global keybind bumps a request atom we watch).
  const createRequest = useStore($profileCreateRequest)
  const lastCreateRef = useRef(createRequest)

  useEffect(() => {
    if (createRequest === lastCreateRef.current) {
      return
    }

    lastCreateRef.current = createRequest
    setCreateOpen(true)
  }, [createRequest])

  const isAll = scope === ALL_PROFILES
  const activeKey = normalizeProfileKey(gatewayProfile)
  const multiProfile = profiles.length > 1
  const defaultProfile = profiles.find(profile => profile.is_default)

  const ordered = [
    ...(defaultProfile ? [defaultProfile] : []),
    ...sortByProfileOrder(
      profiles.filter(profile => !profile.is_default),
      order
    )
  ]

  const activeName = isAll
    ? a.allProfiles
    : (profiles.find(profile => normalizeProfileKey(profile.name) === activeKey)?.name ?? activeKey)

  const activeColor = isAll ? null : resolveProfileColor(activeName, colors)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={a.menu}
            className="flex h-8 w-full min-w-0 items-center gap-2 rounded-control bg-transparent px-1.5 text-left text-[0.8125rem] font-medium text-(--ui-text-secondary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground data-[state=open]:bg-(--ui-control-active-background) data-[state=open]:text-foreground"
            data-testid="account-menu"
            type="button"
          >
            <ProfileAvatar color={activeColor} glyph={isAll ? 'layers' : undefined} name={activeName} />
            <span className="min-w-0 flex-1 truncate">{activeName}</span>
            <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="settings-gear" size="0.875rem" />
          </button>
        </DropdownMenuTrigger>

        {/* The menu sits at the very bottom: pad off the status bar so Radix
            opens it upwards instead of squeezing it. */}
        <DropdownMenuContent
          align="start"
          className="w-60"
          collisionPadding={{ bottom: 44, left: 8, right: 8, top: 8 }}
          side="top"
        >
          <DropdownMenuLabel>{a.profiles}</DropdownMenuLabel>
          {ordered.map(profile => {
            const active = !isAll && normalizeProfileKey(profile.name) === activeKey

            return (
              <DropdownMenuItem
                key={profile.name}
                onSelect={() => {
                  triggerHaptic('selection')
                  selectProfile(profile.name)
                }}
              >
                <ProfileAvatar color={resolveProfileColor(profile.name, colors)} name={profile.name} small />
                <span className="min-w-0 flex-1 truncate">{profile.name}</span>
                {active && <Codicon name="check" size="0.875rem" />}
              </DropdownMenuItem>
            )
          })}
          {multiProfile && (
            <DropdownMenuItem
              onSelect={() => {
                triggerHaptic('selection')
                setShowAllProfiles(true)
              }}
            >
              <ProfileAvatar color={null} glyph="layers" name={a.allProfiles} small />
              <span className="min-w-0 flex-1 truncate">{a.allProfiles}</span>
              {isAll && <Codicon name="check" size="0.875rem" />}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => setCreateOpen(true)}>
            <Codicon name="add" size="0.875rem" />
            <span>{a.newProfile}</span>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => navigate(PROFILES_ROUTE)}>
            <Codicon name="organization" size="0.875rem" />
            <span>{a.manageProfiles}</span>
          </DropdownMenuItem>

          <DropdownMenuSeparator />

          <DropdownMenuItem onSelect={() => navigate(SETTINGS_ROUTE)}>
            <Codicon name="settings-gear" size="0.875rem" />
            <span>{a.settings}</span>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => navigate(SETTINGS_ACCOUNTS_ROUTE)}>
            <Codicon name="account" size="0.875rem" />
            <span>{a.accounts}</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              triggerHaptic('open')
              setReportOpen(true)
            }}
          >
            <Codicon name="feedback" size="0.875rem" />
            <span>{a.helpFeedback}</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Land in the new profile on a fresh chat (selectProfile triggers the
          new-session reset), not stuck on the session you were just in. */}
      <CreateProfileDialog
        onClose={() => setCreateOpen(false)}
        onCreated={async name => {
          await refreshActiveProfile()
          selectProfile(name)
        }}
        open={createOpen}
      />

      <ReportIssueDialog onOpenChange={setReportOpen} open={reportOpen} />
    </>
  )
}

// A profile is its coloured initial tile — the same cue the profiles page uses.
function ProfileAvatar({
  color,
  glyph,
  name,
  small = false
}: {
  color: null | string
  glyph?: string
  name: string
  small?: boolean
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid shrink-0 place-items-center rounded-control-sm font-semibold leading-none',
        small ? 'size-4 text-[0.5625rem]' : 'size-6 text-[0.6875rem]',
        !color && 'bg-(--ui-control-active-background) text-(--ui-text-secondary)'
      )}
      style={color ? { backgroundColor: profileColorSoft(color, 30), color } : undefined}
    >
      {glyph ? <Codicon name={glyph} size={small ? '0.625rem' : '0.75rem'} /> : initialOf(name)}
    </span>
  )
}
