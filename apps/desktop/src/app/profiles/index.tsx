import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import {
  createProfile,
  deleteProfile,
  getProfiles,
  getProfileSetupCommand,
  getProfileSoul,
  type ProfileInfo,
  renameProfile,
  updateProfileSoul
} from '@/hermes'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { AlertTriangle, Pencil, Save, Terminal, Trash2, Users } from '@/lib/icons'
import { PROFILE_SWATCHES, resolveProfileColor } from '@/lib/profile-color'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import {
  $profileColors,
  $profileOrder,
  moveProfile,
  normalizeProfileKey,
  setProfileColor,
  sortByProfileOrder
} from '@/store/profile'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { OverlayMain, OverlayNewButton, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

function isValidProfileName(name: string): boolean {
  return PROFILE_NAME_RE.test(name.trim())
}

interface ProfilesViewProps {
  onClose: () => void
}

export function ProfilesView({ onClose }: ProfilesViewProps) {
  const { t } = useI18n()
  const p = t.profiles
  const [profiles, setProfiles] = useState<null | ProfileInfo[]>(null)
  const [selectedName, setSelectedName] = useState<null | string>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<null | ProfileInfo>(null)
  const [deleting, setDeleting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const { profiles: list } = await getProfiles()
      setProfiles(list)
      setSelectedName(current => {
        if (current && list.some(p => p.name === current)) {
          return current
        }

        return list.find(p => p.is_default)?.name ?? list[0]?.name ?? null
      })
    } catch (err) {
      notifyError(err, p.failedLoad)
    }
  }, [p])

  useRefreshHotkey(refresh)

  useEffect(() => {
    void refresh()
  }, [refresh])

  const selected = useMemo(() => {
    if (!profiles) {
      return null
    }

    return profiles.find(p => p.name === selectedName) ?? profiles[0] ?? null
  }, [profiles, selectedName])

  const order = useStore($profileOrder)
  const colors = useStore($profileColors)

  const orderedProfiles = useMemo(
    () =>
      profiles
        ? [
            ...profiles.filter(profile => profile.is_default),
            ...sortByProfileOrder(
              profiles.filter(profile => !profile.is_default),
              order
            )
          ]
        : [],
    [order, profiles]
  )

  const namedNames = useMemo(
    () => orderedProfiles.filter(profile => !profile.is_default).map(profile => profile.name),
    [orderedProfiles]
  )

  const handleCreate = useCallback(
    async (name: string, cloneFromDefault: boolean) => {
      const trimmed = name.trim()

      if (!isValidProfileName(trimmed)) {
        throw new Error(p.nameHint)
      }

      await createProfile({ name: trimmed, clone_from_default: cloneFromDefault })
      notify({ kind: 'success', title: p.created, message: trimmed })
      setSelectedName(trimmed)
      await refresh()
    },
    [p, refresh]
  )

  const handleRename = useCallback(
    async (from: string, to: string): Promise<void> => {
      const target = to.trim()

      if (target === from) {
        return
      }

      if (!isValidProfileName(target)) {
        throw new Error(p.nameHint)
      }

      await renameProfile(from, target)
      notify({ kind: 'success', title: p.renamed, message: `${from} → ${target}` })
      setSelectedName(target)
      await refresh()
    },
    [p, refresh]
  )

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) {
      return
    }

    setDeleting(true)

    try {
      await deleteProfile(pendingDelete.name)
      notify({ kind: 'success', title: p.deleted, message: pendingDelete.name })
      setPendingDelete(null)
      setSelectedName(null)
      await refresh()
    } catch (err) {
      notifyError(err, p.failedDelete)
    } finally {
      setDeleting(false)
    }
  }, [p, pendingDelete, refresh])

  return (
    <OverlayView closeLabel={p.close} onClose={onClose}>
      {!profiles ? (
        <PageLoader label={p.loading} />
      ) : (
        <OverlaySplitLayout>
          <OverlaySidebar>
            <OverlayNewButton label={p.newProfile} onClick={() => setCreateOpen(true)} />
            {/* The default profile leads; the named ones follow the stored
                order — the same order the profile hotkeys use. */}
            {orderedProfiles.map(profile => {
              const namedIndex = namedNames.indexOf(profile.name)

              return (
                <ProfileRow
                  active={selected?.name === profile.name}
                  color={resolveProfileColor(profile.is_default ? null : profile.name, colors)}
                  colorPicked={Boolean(colors[normalizeProfileKey(profile.name)])}
                  key={profile.name}
                  move={
                    namedIndex < 0 || namedNames.length < 2
                      ? undefined
                      : {
                          canMoveDown: namedIndex < namedNames.length - 1,
                          canMoveUp: namedIndex > 0,
                          onMove: direction => {
                            triggerHaptic('selection')
                            moveProfile(namedNames, profile.name, direction)
                          }
                        }
                  }
                  onSelect={() => setSelectedName(profile.name)}
                  profile={profile}
                />
              )
            })}
            {namedNames.length > 1 && (
              <p className="px-2 pt-2 text-[0.66rem] leading-4 text-muted-foreground">{p.orderHint}</p>
            )}
            {profiles.length === 0 && (
              <p className="px-2 py-4 text-center text-xs text-muted-foreground">{p.noProfiles}</p>
            )}
          </OverlaySidebar>

          <OverlayMain className="px-0">
            {selected ? (
              <ProfileDetail
                key={selected.name}
                onDelete={() => setPendingDelete(selected)}
                onRename={newName => handleRename(selected.name, newName)}
                profile={selected}
              />
            ) : (
              <div className="grid h-full place-items-center px-6 py-12 text-center text-sm text-muted-foreground">
                <div>
                  <Users className="mx-auto size-6 text-muted-foreground/60" />
                  <p className="mt-3">{p.selectPrompt}</p>
                </div>
              </div>
            )}
          </OverlayMain>
        </OverlaySplitLayout>
      )}

      <CreateProfileDialog
          onClose={() => setCreateOpen(false)}
          onCreate={async (name, cloneFromDefault) => handleCreate(name, cloneFromDefault)}
          open={createOpen}
        />

        <Dialog onOpenChange={open => !open && !deleting && setPendingDelete(null)} open={pendingDelete !== null}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>{p.deleteTitle}</DialogTitle>
              <DialogDescription>
                {pendingDelete ? (
                  <>
                    {p.deleteDescPrefix}
                    <span className="font-medium text-foreground">{pendingDelete.name}</span>
                    {p.deleteDescMid}
                    <span className="font-mono text-xs">{pendingDelete.path}</span>
                    {p.deleteDescSuffix}
                  </>
                ) : null}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button disabled={deleting} onClick={() => setPendingDelete(null)} variant="outline">
                {t.common.cancel}
              </Button>
              <Button disabled={deleting} onClick={() => void handleConfirmDelete()} variant="destructive">
                {deleting ? p.deleting : t.common.delete}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
    </OverlayView>
  )
}

interface ProfileRowProps {
  active: boolean
  /** The profile's colour (override or automatic); null for the default profile. */
  color: null | string
  /** Whether the colour is the user's pick (else automatic). */
  colorPicked: boolean
  /** Order controls; absent for the default profile, which always leads. */
  move?: { canMoveDown: boolean; canMoveUp: boolean; onMove: (direction: -1 | 1) => void }
  onSelect: () => void
  profile: ProfileInfo
}

// AIS-794: the account menu replaced the sidebar's profile rail, the only
// place to recolour (long-press) and reorder (drag) profiles. Both live here
// now and write the same stored colour and order the rail did, so the order
// keeps driving the profile hotkeys.
function ProfileRow({ active, color, colorPicked, move, onSelect, profile }: ProfileRowProps) {
  const { t } = useI18n()
  const p = t.profiles

  return (
    <div
      className={cn(
        'group/profile flex w-full items-center gap-1.5 rounded-control-sm pl-1.5 pr-1 transition-colors',
        active ? 'bg-accent text-foreground' : 'text-foreground/85 hover:bg-accent/60'
      )}
      data-testid={`profile-row-${profile.name}`}
    >
      {color ? (
        <ProfileColorPicker color={color} picked={colorPicked} profile={profile.name} />
      ) : (
        <span aria-hidden className="size-3.5 shrink-0 rounded-full border border-(--ui-stroke-secondary)" />
      )}
      <button
        className="flex min-w-0 flex-1 flex-col items-start gap-0.5 py-1.5 text-left"
        onClick={onSelect}
        type="button"
      >
        <span className="flex w-full items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{profile.name}</span>
          {profile.is_default && <span className="text-[0.6rem] text-primary">{p.default}</span>}
        </span>
        <span className="text-[0.66rem] text-muted-foreground">
          {p.skills(profile.skill_count)}
          {profile.has_env ? ` · ${p.env}` : ''}
        </span>
      </button>
      {move && (
        <div
          className={cn(
            'flex shrink-0 items-center transition-opacity focus-within:opacity-100 group-hover/profile:opacity-100',
            active ? 'opacity-100' : 'opacity-0'
          )}
        >
          <Button
            aria-label={p.moveUp(profile.name)}
            disabled={!move.canMoveUp}
            onClick={() => move.onMove(-1)}
            size="icon-xs"
            title={p.moveUp(profile.name)}
            variant="ghost"
          >
            <Codicon name="arrow-up" size="0.75rem" />
          </Button>
          <Button
            aria-label={p.moveDown(profile.name)}
            disabled={!move.canMoveDown}
            onClick={() => move.onMove(1)}
            size="icon-xs"
            title={p.moveDown(profile.name)}
            variant="ghost"
          >
            <Codicon name="arrow-down" size="0.75rem" />
          </Button>
        </div>
      )}
    </div>
  )
}

// The colour dot opens the same twelve swatches the rail's long-press offered,
// plus "automatic" (the colour derived from the name).
function ProfileColorPicker({ color, picked, profile }: { color: string; picked: boolean; profile: string }) {
  const { t } = useI18n()
  const p = t.profiles
  const [open, setOpen] = useState(false)

  const pick = (next: null | string) => {
    setProfileColor(profile, next)
    triggerHaptic('selection')
    setOpen(false)
  }

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <button
          aria-label={p.colorFor(profile)}
          className="grid size-5 shrink-0 place-items-center rounded-full transition-transform hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          title={p.colorFor(profile)}
          type="button"
        >
          <span aria-hidden className="size-3.5 rounded-full" style={{ backgroundColor: color }} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-2" side="right">
        <div aria-label={p.colorFor(profile)} className="grid grid-cols-6 gap-1.5" role="radiogroup">
          {PROFILE_SWATCHES.map((swatch, index) => {
            const checked = picked && swatch === color
            const name = p.colorNames[index] ?? swatch

            return (
              <button
                aria-checked={checked}
                aria-label={p.pickColor(profile, name)}
                className="size-5 rounded-full transition-transform hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                key={swatch}
                onClick={() => pick(swatch)}
                role="radio"
                style={{
                  backgroundColor: swatch,
                  boxShadow: checked ? '0 0 0 2px var(--ui-bg-elevated), 0 0 0 3.5px currentColor' : undefined,
                  color: swatch
                }}
                title={name}
                type="button"
              />
            )
          })}
        </div>
        <button
          aria-checked={!picked}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-control-sm py-1 text-xs text-(--ui-text-tertiary) transition hover:bg-(--ui-control-hover-background) hover:text-foreground aria-checked:text-foreground"
          onClick={() => pick(null)}
          role="radio"
          type="button"
        >
          <Codicon name="sync" size="0.75rem" />
          {p.colorAutomatic}
        </button>
      </PopoverContent>
    </Popover>
  )
}

function ProfileDetail({
  onDelete,
  onRename,
  profile
}: {
  onDelete: () => void
  onRename: (newName: string) => Promise<void>
  profile: ProfileInfo
}) {
  const { t } = useI18n()
  const p = t.profiles
  const [renameOpen, setRenameOpen] = useState(false)
  const [copying, setCopying] = useState(false)

  const handleCopySetup = useCallback(async () => {
    setCopying(true)

    try {
      const { command } = await getProfileSetupCommand(profile.name)
      await navigator.clipboard.writeText(command)
      notify({ kind: 'success', title: p.setupCopied, message: command })
    } catch (err) {
      notifyError(err, p.failedCopy)
    } finally {
      setCopying(false)
    }
  }, [p, profile.name])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 px-6 py-6">
          <header className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-xl font-semibold tracking-tight">{profile.name}</h3>
                  {profile.is_default && (
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[0.65rem] font-medium text-primary">
                      {p.defaultBadge}
                    </span>
                  )}
                  {profile.has_env && (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
                      .env
                    </span>
                  )}
                </div>
                <p className="mt-1 font-mono text-[0.7rem] text-muted-foreground" title={profile.path}>
                  {profile.path}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {!profile.is_default && (
                  <Button onClick={() => setRenameOpen(true)} size="sm" variant="outline">
                    <Pencil />
                    {p.rename}
                  </Button>
                )}
                <Button disabled={copying} onClick={() => void handleCopySetup()} size="sm" variant="outline">
                  <Terminal />
                  {copying ? p.copying : p.copySetup}
                </Button>
                {!profile.is_default && (
                  <Button
                    className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    onClick={onDelete}
                    size="sm"
                    variant="ghost"
                  >
                    <Trash2 />
                    {t.common.delete}
                  </Button>
                )}
              </div>
            </div>

            <dl className="grid gap-2 text-xs sm:grid-cols-2">
              <DetailRow label={p.modelLabel}>
                {profile.model ? (
                  <>
                    <span className="font-mono">{profile.model}</span>
                    {profile.provider && <span className="text-muted-foreground"> · {profile.provider}</span>}
                  </>
                ) : (
                  <span className="text-muted-foreground">{p.notSet}</span>
                )}
              </DetailRow>
              <DetailRow label={p.skillsLabel}>{profile.skill_count}</DetailRow>
            </dl>
          </header>

          <SoulEditor profileName={profile.name} />
        </div>
      </div>

      <RenameProfileDialog
        currentName={profile.name}
        onClose={() => setRenameOpen(false)}
        onRename={async newName => {
          await onRename(newName)
          setRenameOpen(false)
        }}
        open={renameOpen}
      />
    </div>
  )
}

function DetailRow({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-2">
      <dt className="text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</dt>
      <dd className="text-sm text-foreground">{children}</dd>
    </div>
  )
}

function SoulEditor({ profileName }: { profileName: string }) {
  const { t } = useI18n()
  const p = t.profiles
  const [content, setContent] = useState('')
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<null | string>(null)
  const requestRef = useRef<string>(profileName)

  useEffect(() => {
    requestRef.current = profileName
    setLoading(true)
    setError(null)
    setContent('')
    setOriginal('')

    void (async () => {
      try {
        const soul = await getProfileSoul(profileName)

        if (requestRef.current === profileName) {
          setContent(soul.content)
          setOriginal(soul.content)
        }
      } catch (err) {
        if (requestRef.current === profileName) {
          setError(err instanceof Error ? err.message : p.failedLoadSoul)
        }
      } finally {
        if (requestRef.current === profileName) {
          setLoading(false)
        }
      }
    })()
  }, [p, profileName])

  const dirty = content !== original
  const isEmpty = !content.trim()

  async function handleSave() {
    setSaving(true)
    setError(null)

    try {
      await updateProfileSoul(profileName, content)
      setOriginal(content)
      notify({ kind: 'success', title: p.soulSaved, message: profileName })
    } catch (err) {
      setError(err instanceof Error ? err.message : p.failedSaveSoul)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">SOUL.md</h4>
          <p className="text-xs text-muted-foreground">{p.soulDesc}</p>
        </div>
        {dirty && <span className="text-[0.65rem] text-muted-foreground">{p.unsavedChanges}</span>}
      </div>

      {loading ? (
        <PageLoader className="min-h-44" label={p.loadingSoul} />
      ) : (
        <Textarea
          className="min-h-72 font-mono text-xs leading-5"
          onChange={event => setContent(event.target.value)}
          placeholder={isEmpty ? p.emptySoul : undefined}
          value={content}
        />
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex justify-end">
        <Button disabled={!dirty || saving || loading} onClick={() => void handleSave()} size="sm">
          <Save />
          {saving ? p.saving : p.saveSoul}
        </Button>
      </div>
    </section>
  )
}

function CreateProfileDialog({
  onClose,
  onCreate,
  open
}: {
  onClose: () => void
  onCreate: (name: string, cloneFromDefault: boolean) => Promise<void>
  open: boolean
}) {
  const { t } = useI18n()
  const p = t.profiles
  const [name, setName] = useState('')
  const [cloneFromDefault, setCloneFromDefault] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    setName('')
    setCloneFromDefault(true)
    setError(null)
    setSaving(false)
  }, [open])

  const trimmed = name.trim()
  const invalid = trimmed !== '' && !isValidProfileName(trimmed)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (!trimmed || invalid) {
      setError(invalid ? p.invalidName(p.nameHint) : p.nameRequired)

      return
    }

    setSaving(true)
    setError(null)

    try {
      await onCreate(trimmed, cloneFromDefault)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : p.failedCreate)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog onOpenChange={value => !value && !saving && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{p.newProfile}</DialogTitle>
          <DialogDescription>{p.createDesc}</DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={handleSubmit}>
          <div className="grid gap-1.5">
            <label className="text-xs font-medium" htmlFor="new-profile-name">
              {p.nameLabel}
            </label>
            <Input
              aria-invalid={invalid}
              autoFocus
              id="new-profile-name"
              onChange={event => setName(event.target.value)}
              placeholder="my-profile"
              value={name}
            />
            <p className={cn('text-[0.66rem] leading-4', invalid ? 'text-destructive' : 'text-muted-foreground')}>
              {p.nameHint}
            </p>
          </div>

          <label className="flex cursor-pointer items-center gap-2 rounded-md border border-border/40 bg-background/50 px-3 py-2 text-sm">
            <input
              checked={cloneFromDefault}
              className="size-4 accent-primary"
              onChange={event => setCloneFromDefault(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="font-medium">{p.cloneFromDefault}</span>
              <span className="ml-2 text-xs text-muted-foreground">{p.cloneFromDefaultDesc}</span>
            </span>
          </label>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <DialogFooter>
            <Button disabled={saving} onClick={onClose} type="button" variant="outline">
              {t.common.cancel}
            </Button>
            <Button disabled={saving || !trimmed || invalid} type="submit">
              {saving ? p.creating : p.createAction}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RenameProfileDialog({
  currentName,
  onClose,
  onRename,
  open
}: {
  currentName: string
  onClose: () => void
  onRename: (newName: string) => Promise<void>
  open: boolean
}) {
  const { t } = useI18n()
  const p = t.profiles
  const [name, setName] = useState(currentName)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    setName(currentName)
    setError(null)
    setSaving(false)
  }, [currentName, open])

  const trimmed = name.trim()
  const unchanged = trimmed === currentName
  const invalid = trimmed !== '' && !unchanged && !isValidProfileName(trimmed)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (unchanged) {
      onClose()

      return
    }

    if (!trimmed || invalid) {
      setError(invalid ? p.invalidName(p.nameHint) : p.nameRequired)

      return
    }

    setSaving(true)
    setError(null)

    try {
      await onRename(trimmed)
    } catch (err) {
      setError(err instanceof Error ? err.message : p.failedRename)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog onOpenChange={value => !value && !saving && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{p.renameTitle}</DialogTitle>
          <DialogDescription>
            {p.renameDescPrefix}
            <span className="font-mono">~/.local/bin</span>
            {p.renameDescSuffix}
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-3" onSubmit={handleSubmit}>
          <div className="grid gap-1.5">
            <label className="text-xs font-medium" htmlFor="rename-profile-name">
              {p.newNameLabel}
            </label>
            <Input
              aria-invalid={invalid}
              autoFocus
              id="rename-profile-name"
              onChange={event => setName(event.target.value)}
              value={name}
            />
            <p className={cn('text-[0.66rem] leading-4', invalid ? 'text-destructive' : 'text-muted-foreground')}>
              {p.nameHint}
            </p>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <DialogFooter>
            <Button disabled={saving} onClick={onClose} type="button" variant="outline">
              {t.common.cancel}
            </Button>
            <Button disabled={saving || invalid || unchanged} type="submit">
              {saving ? p.renaming : p.rename}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
